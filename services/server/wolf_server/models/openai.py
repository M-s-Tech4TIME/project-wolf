"""OpenAI adapter — calls the Chat Completions API via httpx.

Works with any OpenAI-compatible endpoint: OpenAI, Azure OpenAI, vLLM,
LM Studio, LocalAI, OpenRouter, etc.  Pass base_url to point at a different
host.
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
from wolf_server.models.interface import default_descriptor_for

_API_BASE = "https://api.openai.com"


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


class OpenAIAdapter:
    """ModelProvider implementation for OpenAI-compatible Chat Completions endpoints."""

    def __init__(
        self,
        api_key: str,
        model_id: str = "gpt-4o",
        *,
        base_url: str = _API_BASE,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model_id = model_id
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "content-type": "application/json",
            },
            timeout=120.0,
        )
        self._descriptor = default_descriptor_for(model_id, "openai")

    def capability(self) -> CapabilityDescriptor:
        return self._descriptor

    async def _raw_chat(self, request: ChatRequest) -> ChatResponse:
        messages = [_message_to_openai(m) for m in request.messages]
        payload: dict[str, Any] = {
            "model": self._model_id,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        if request.tools:
            payload["tools"] = [_canonical_to_openai_tool(t) for t in request.tools]

        response = await self._client.post("/v1/chat/completions", json=payload)
        response.raise_for_status()
        return _parse_openai_response(response.json(), self._model_id)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        from wolf_schema.capability import NativeToolCalling

        if self._descriptor.native_tool_calling == NativeToolCalling.none:
            return await chat_with_fallback(self._raw_chat, request)
        return await self._raw_chat(request)

    def stream(self, request: ChatRequest) -> AsyncIterator[str]:
        async def _gen() -> AsyncIterator[str]:
            response = await self.chat(request)
            yield response.content

        return _gen()
