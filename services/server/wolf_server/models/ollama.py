"""Ollama adapter — calls the local Ollama server via httpx.

Proves the "no paid dependency required" promise: Wolf always has a fully
local model path that costs nothing beyond hardware.

Ollama's tool-call format is similar to OpenAI's but arguments arrive as
a dict (not a JSON string), and token counts use different field names.
Models without native tool-calling use the structured-output fallback.
"""

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
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

_DEFAULT_BASE = "http://localhost:11434"


def _canonical_to_ollama_tool(tool: ToolSchema) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


def _message_to_ollama(msg: Message) -> dict[str, Any]:
    if msg.role == MessageRole.tool and msg.tool_results:
        result = msg.tool_results[0]
        content = result.content if isinstance(result.content, str) else str(result.content)
        return {"role": "tool", "content": content}

    if msg.role == MessageRole.assistant and msg.tool_calls:
        # Ollama expects tool_calls as a list of objects with a "function" key
        calls = [
            {"function": {"name": tc.name, "arguments": tc.arguments}} for tc in msg.tool_calls
        ]
        out: dict[str, Any] = {"role": "assistant", "tool_calls": calls}
        if msg.content:
            out["content"] = msg.content
        return out

    return {"role": msg.role.value, "content": msg.content}


def _parse_ollama_response(body: dict[str, Any], model_id: str) -> ChatResponse:
    message: dict[str, Any] = body.get("message", {})
    content: str = message.get("content") or ""

    tool_calls: list[ToolCall] = []
    for tc in message.get("tool_calls") or []:
        fn: dict[str, Any] = tc.get("function", {})
        arguments: object = fn.get("arguments", {})
        if not isinstance(arguments, dict):
            arguments = {}
        tool_calls.append(
            ToolCall(
                id=str(uuid.uuid4()),
                name=fn.get("name", ""),
                arguments=arguments,
            )
        )

    return ChatResponse(
        content=content,
        tool_calls=tool_calls,
        input_tokens=int(body.get("prompt_eval_count", 0)),
        output_tokens=int(body.get("eval_count", 0)),
        stop_reason="stop" if body.get("done") else "length",
        model_id=model_id,
    )


class OllamaAdapter:
    """ModelProvider implementation for a locally running Ollama server."""

    def __init__(
        self,
        model_id: str = "llama3.2",
        *,
        base_url: str = _DEFAULT_BASE,
        client: httpx.AsyncClient | None = None,
        num_ctx: int | None = None,
    ) -> None:
        # num_ctx (Ollama option) overrides the model's default context
        # window for this adapter. Useful for the grounding judge, whose
        # prompt + 5 KB evidence + claims can exceed qwen3:8b's default
        # 4096-token Ollama context and cause empty completions. None
        # leaves Ollama's default in place. Slice 5.0b.4.
        self._model_id = model_id
        self._base_url = base_url.rstrip("/")
        self._num_ctx = num_ctx
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            # 600s: generous for cold-loads on memory-pressured GPUs where
            # Ollama must evict another model first (e.g. qwen3:8b judge
            # swapping with qwen3:4b chat on a 6 GB card — see ADR 0015).
            # Warm inference takes seconds; this ceiling only kicks in when
            # the model is genuinely being read off disk. Slice 5.0b.3.
            timeout=600.0,
        )
        self._descriptor = default_descriptor_for(model_id, "ollama")

    def capability(self) -> CapabilityDescriptor:
        return self._descriptor

    async def _raw_chat(self, request: ChatRequest) -> ChatResponse:
        messages = [_message_to_ollama(m) for m in request.messages]
        options: dict[str, Any] = {"temperature": request.temperature}
        if self._num_ctx is not None:
            options["num_ctx"] = self._num_ctx
        payload: dict[str, Any] = {
            "model": self._model_id,
            "messages": messages,
            "stream": False,
            "options": options,
        }
        if request.tools:
            payload["tools"] = [_canonical_to_ollama_tool(t) for t in request.tools]

        response = await self._client.post("/api/chat", json=payload)
        response.raise_for_status()
        return _parse_ollama_response(response.json(), self._model_id)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        from wolf_schema.capability import NativeToolCalling

        if self._descriptor.native_tool_calling == NativeToolCalling.none:
            return await chat_with_fallback(self._raw_chat, request)
        return await self._raw_chat(request)

    def stream(self, request: ChatRequest) -> AsyncIterator[str]:
        # Kept for backward compatibility with the ModelProvider protocol;
        # the real progressive path is `chat_stream` below. Slice 5.0c-d.
        async def _gen() -> AsyncIterator[str]:
            response = await self.chat(request)
            yield response.content

        return _gen()

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        """Stream a chat completion as token deltas + a final done event.

        Uses Ollama's native ``stream: true`` mode. Each newline-delimited
        JSON chunk from the server contains a partial ``message.content``;
        we yield each delta verbatim so the agent loop can forward them as
        SSE events to wolf-dashboard (Slice 5.0c-d). Once the server sends
        ``done: true``, we assemble the full :class:`ChatResponse` from the
        accumulated content + the final-chunk metadata and yield it as a
        single :class:`ChatStreamDone` so the loop's downstream logic
        (tool-call dispatch, token accounting) stays identical to the
        non-streaming path.
        """
        messages = [_message_to_ollama(m) for m in request.messages]
        options: dict[str, Any] = {"temperature": request.temperature}
        if self._num_ctx is not None:
            options["num_ctx"] = self._num_ctx
        payload: dict[str, Any] = {
            "model": self._model_id,
            "messages": messages,
            "stream": True,
            "options": options,
        }
        if request.tools:
            payload["tools"] = [_canonical_to_ollama_tool(t) for t in request.tools]

        accumulated_content = ""
        accumulated_tool_calls: list[dict[str, Any]] = []
        prompt_eval_count = 0
        eval_count = 0
        saw_done = False

        async with self._client.stream("POST", "/api/chat", json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    # Defensive: skip malformed lines rather than break
                    # the whole stream. Real Ollama always emits valid
                    # JSON, but a misbehaving proxy could chunk weirdly.
                    continue
                message = chunk.get("message", {})
                content_delta = str(message.get("content") or "")
                if content_delta:
                    accumulated_content += content_delta
                    yield ChatStreamDelta(content_delta=content_delta)
                tool_calls_chunk = message.get("tool_calls") or []
                if tool_calls_chunk:
                    accumulated_tool_calls.extend(tool_calls_chunk)
                if chunk.get("done"):
                    prompt_eval_count = int(chunk.get("prompt_eval_count", 0))
                    eval_count = int(chunk.get("eval_count", 0))
                    saw_done = True
                    break

        # Assemble the final response — same shape as _parse_ollama_response.
        tool_calls: list[ToolCall] = []
        for tc in accumulated_tool_calls:
            fn = tc.get("function", {}) if isinstance(tc, dict) else {}
            arguments = fn.get("arguments", {})
            if not isinstance(arguments, dict):
                arguments = {}
            tool_calls.append(
                ToolCall(
                    id=str(uuid.uuid4()),
                    name=str(fn.get("name", "")),
                    arguments=arguments,
                )
            )

        yield ChatStreamDone(
            response=ChatResponse(
                content=accumulated_content,
                tool_calls=tool_calls,
                input_tokens=prompt_eval_count,
                output_tokens=eval_count,
                stop_reason="stop" if saw_done else "length",
                model_id=self._model_id,
            )
        )
