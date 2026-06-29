"""OpenAI adapter — calls the Chat Completions API via httpx.

Works with any OpenAI-compatible endpoint: OpenAI, Azure OpenAI, vLLM,
LM Studio, LocalAI, OpenRouter, etc.  Pass base_url to point at a different
host, and extra_headers for host-specific attribution (OpenRouter wants
``HTTP-Referer`` / ``X-Title``).

Implements both the blocking ``chat`` and the progressive ``chat_stream``
(real SSE token deltas + accumulated tool-calls), so an OpenAI-compatible
provider gets the same token-by-token UX + grounding modes as local Ollama.
"""

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from wolf_common.errors import WolfError
from wolf_schema import (
    CapabilityDescriptor,
    ChatRequest,
    ChatResponse,
    ToolCall,
    ToolSchema,
)
from wolf_schema.chat import Message, MessageRole

from wolf_server.models.fallback import chat_with_fallback
from wolf_server.models.interface import (
    ChatStreamDelta,
    ChatStreamDone,
    ChatStreamEvent,
    default_descriptor_for,
)

_API_BASE = "https://api.openai.com"


class ModelProviderRateLimitError(WolfError):
    """The model provider rejected the request for rate/quota reasons (HTTP 429).

    For OpenRouter free-tier models this is the daily request cap; the message
    points the operator at the local Ollama fallback so a capped key degrades
    to a clear, actionable error rather than an opaque 500."""

    http_status = 429
    error_code = "model_provider_rate_limited"


class ModelProviderRequestError(WolfError):
    """The model provider returned an unexpected error (non-429 4xx/5xx)."""

    http_status = 502
    error_code = "model_provider_error"


def _provider_error(status_code: int, text: str) -> WolfError:
    """Map an upstream HTTP error to a clear Wolf error."""
    if status_code == 429:
        return ModelProviderRateLimitError(
            "Model provider rate limit reached (HTTP 429). For OpenRouter free-tier "
            "models this is the daily request cap — wait for the reset or switch the "
            f"provider back to local Ollama. Upstream: {text[:160]}"
        )
    return ModelProviderRequestError(f"Model provider returned {status_code}: {text[:200]}")


def _canonical_to_openai_tool(tool: ToolSchema) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


def _message_to_openai(msg: Message) -> dict[str, Any]:
    if msg.role == MessageRole.tool and msg.tool_results:
        # OpenAI expects one message per tool result with role="tool"
        result = msg.tool_results[0]
        content = result.content if isinstance(result.content, str) else json.dumps(result.content)
        return {
            "role": "tool",
            "tool_call_id": result.tool_call_id,
            "content": content,
        }

    if msg.role == MessageRole.assistant and msg.tool_calls:
        openai_calls = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments),
                },
            }
            for tc in msg.tool_calls
        ]
        out: dict[str, Any] = {"role": "assistant", "tool_calls": openai_calls}
        if msg.content:
            out["content"] = msg.content
        return out

    return {"role": msg.role.value, "content": msg.content}


def _parse_openai_response(body: dict[str, Any], model_id: str) -> ChatResponse:
    choice: dict[str, Any] = body["choices"][0]
    message: dict[str, Any] = choice["message"]
    content: str = message.get("content") or ""

    tool_calls: list[ToolCall] = []
    for tc in message.get("tool_calls") or []:
        fn: dict[str, Any] = tc["function"]
        try:
            arguments: dict[str, Any] = json.loads(fn.get("arguments", "{}"))
        except json.JSONDecodeError:
            arguments = {}
        tool_calls.append(
            ToolCall(
                id=tc.get("id", str(uuid.uuid4())),
                name=fn["name"],
                arguments=arguments,
            )
        )

    usage: dict[str, Any] = body.get("usage", {})
    return ChatResponse(
        content=content,
        tool_calls=tool_calls,
        input_tokens=int(usage.get("prompt_tokens", 0)),
        output_tokens=int(usage.get("completion_tokens", 0)),
        stop_reason=choice.get("finish_reason", "stop"),
        model_id=model_id,
    )


def _assemble_streamed_tool_calls(acc: dict[int, dict[str, Any]]) -> list[ToolCall]:
    """Build ToolCalls from accumulated streaming deltas (arguments arrive as
    concatenated JSON-string fragments, keyed by the delta ``index``)."""
    tool_calls: list[ToolCall] = []
    for idx in sorted(acc):
        slot = acc[idx]
        raw_args = slot.get("arguments", "")
        try:
            arguments = json.loads(raw_args) if raw_args else {}
        except json.JSONDecodeError:
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        tool_calls.append(
            ToolCall(
                id=slot.get("id") or str(uuid.uuid4()),
                name=str(slot.get("name", "")),
                arguments=arguments,
            )
        )
    return tool_calls


class OpenAIAdapter:
    """ModelProvider implementation for OpenAI-compatible Chat Completions endpoints."""

    def __init__(
        self,
        api_key: str,
        model_id: str = "gpt-4o",
        *,
        base_url: str = _API_BASE,
        client: httpx.AsyncClient | None = None,
        extra_headers: dict[str, str] | None = None,
        provider: str = "openai",
    ) -> None:
        self._api_key = api_key
        self._model_id = model_id
        self._base_url = base_url.rstrip("/")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=120.0,
        )
        self._descriptor = default_descriptor_for(model_id, provider)

    def capability(self) -> CapabilityDescriptor:
        return self._descriptor

    def _build_payload(self, request: ChatRequest, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model_id,
            "messages": [_message_to_openai(m) for m in request.messages],
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        if request.tools:
            payload["tools"] = [_canonical_to_openai_tool(t) for t in request.tools]
        if stream:
            payload["stream"] = True
            # Ask OpenAI-compatible servers to include token usage in the final
            # streamed chunk (OpenRouter honors this) so accounting matches the
            # blocking path.
            payload["stream_options"] = {"include_usage": True}
        return payload

    async def _raw_chat(self, request: ChatRequest) -> ChatResponse:
        response = await self._client.post(
            "/v1/chat/completions", json=self._build_payload(request, stream=False)
        )
        if response.status_code >= 400:
            raise _provider_error(response.status_code, response.text)
        return _parse_openai_response(response.json(), self._model_id)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        from wolf_schema.capability import NativeToolCalling

        if self._descriptor.native_tool_calling == NativeToolCalling.none:
            return await chat_with_fallback(self._raw_chat, request)
        return await self._raw_chat(request)

    def stream(self, request: ChatRequest) -> AsyncIterator[str]:
        # Legacy single-chunk path (ModelProvider protocol). The progressive
        # path is chat_stream below.
        async def _gen() -> AsyncIterator[str]:
            response = await self.chat(request)
            yield response.content

        return _gen()

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        """Stream a chat completion as token deltas + a final done event.

        Parses the OpenAI Server-Sent-Events stream: each ``data:`` line carries
        a ``choices[0].delta`` with partial ``content`` and/or ``tool_calls``
        fragments (arguments arrive as concatenated JSON-string pieces keyed by
        ``index``). Content deltas are yielded verbatim for the progressive UI;
        tool-calls + usage are accumulated and assembled into a single
        :class:`ChatStreamDone` so the agent loop's downstream logic is identical
        to the blocking path (mirrors the Ollama adapter, Slice 5.0c-d)."""
        from wolf_schema.capability import NativeToolCalling

        if self._descriptor.native_tool_calling == NativeToolCalling.none:
            # No native tools → the structured-output fallback isn't streamable;
            # emit a single done so the loop's contract still holds.
            yield ChatStreamDone(response=await self.chat(request))
            return

        accumulated_content = ""
        tool_acc: dict[int, dict[str, Any]] = {}
        usage: dict[str, Any] = {}
        finish_reason = "stop"

        async with self._client.stream(
            "POST", "/v1/chat/completions", json=self._build_payload(request, stream=True)
        ) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", "replace")
                raise _provider_error(response.status_code, body)
            async for raw_line in response.aiter_lines():
                line = raw_line.strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if chunk.get("usage"):
                    usage = chunk["usage"]
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}
                content_delta = delta.get("content") or ""
                if content_delta:
                    accumulated_content += content_delta
                    yield ChatStreamDelta(content_delta=content_delta)
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    slot = tool_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["arguments"] += fn["arguments"]
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]

        yield ChatStreamDone(
            response=ChatResponse(
                content=accumulated_content,
                tool_calls=_assemble_streamed_tool_calls(tool_acc),
                input_tokens=int(usage.get("prompt_tokens", 0)),
                output_tokens=int(usage.get("completion_tokens", 0)),
                stop_reason=finish_reason,
                model_id=self._model_id,
            )
        )
