"""Ollama adapter — calls the local Ollama server via httpx.

Proves the "no paid dependency required" promise: Wolf always has a fully
local model path that costs nothing beyond hardware.

Ollama's tool-call format is similar to OpenAI's but arguments arrive as
a dict (not a JSON string), and token counts use different field names.
Models without native tool-calling use the structured-output fallback.
"""

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

from app.models.fallback import chat_with_fallback
from app.models.interface import default_descriptor_for

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
        content = (
            result.content
            if isinstance(result.content, str)
            else str(result.content)
        )
        return {"role": "tool", "content": content}

    if msg.role == MessageRole.assistant and msg.tool_calls:
        # Ollama expects tool_calls as a list of objects with a "function" key
        calls = [
            {"function": {"name": tc.name, "arguments": tc.arguments}}
            for tc in msg.tool_calls
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
    ) -> None:
        self._model_id = model_id
        self._base_url = base_url.rstrip("/")
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
        payload: dict[str, Any] = {
            "model": self._model_id,
            "messages": messages,
            "stream": False,
            "options": {"temperature": request.temperature},
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
        async def _gen() -> AsyncIterator[str]:
            response = await self.chat(request)
            yield response.content

        return _gen()
