"""Anthropic adapter — calls the Claude Messages API via httpx.

No Anthropic SDK dependency required; uses httpx which is already in
wolf-server's dependencies.  The adapter converts the canonical ChatRequest /
ChatResponse to and from the Anthropic wire format.
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

_API_BASE = "https://api.anthropic.com"
_ANTHROPIC_VERSION = "2023-06-01"


def _canonical_to_anthropic_tool(tool: ToolSchema) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
    }


def _build_anthropic_messages(
    messages: list[Message],
) -> tuple[str, list[dict[str, Any]]]:
    """Split messages into (system_text, api_messages).

    Anthropic places the system prompt outside the messages array.
    """
    system_parts: list[str] = []
    api_messages: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == MessageRole.system:
            system_parts.append(msg.content)
            continue

        if msg.role == MessageRole.tool and msg.tool_results:
            # Tool results come back as user messages with tool_result content
            content_blocks: list[dict[str, Any]] = []
            for result in msg.tool_results:
                block: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": result.tool_call_id,
                    "content": (
                        result.content
                        if isinstance(result.content, str)
                        else json.dumps(result.content)
                    ),
                }
                if result.error:
                    block["is_error"] = True
                content_blocks.append(block)
            api_messages.append({"role": "user", "content": content_blocks})
            continue

        if msg.role == MessageRole.assistant and msg.tool_calls:
            blocks: list[dict[str, Any]] = []
            if msg.content:
                blocks.append({"type": "text", "text": msg.content})
            for tc in msg.tool_calls:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    }
                )
            api_messages.append({"role": "assistant", "content": blocks})
            continue

        api_messages.append({"role": msg.role.value, "content": msg.content})

    return "\n\n".join(system_parts), api_messages


def _parse_anthropic_response(body: dict[str, Any], model_id: str) -> ChatResponse:
    content_blocks: list[dict[str, Any]] = body.get("content", [])
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []

    for block in content_blocks:
        if block.get("type") == "text":
            text_parts.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            tool_calls.append(
                ToolCall(
                    id=block.get("id", str(uuid.uuid4())),
                    name=block["name"],
                    arguments=block.get("input", {}),
                )
            )

    usage: dict[str, Any] = body.get("usage", {})
    return ChatResponse(
        content=" ".join(text_parts),
        tool_calls=tool_calls,
        input_tokens=int(usage.get("input_tokens", 0)),
        output_tokens=int(usage.get("output_tokens", 0)),
        stop_reason=body.get("stop_reason", "end_turn"),
        model_id=model_id,
    )


class AnthropicAdapter:
    """ModelProvider implementation for the Anthropic Claude API."""

    def __init__(
        self,
        api_key: str,
        model_id: str = "claude-sonnet-4-6",
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
                "x-api-key": self._api_key,
                "anthropic-version": _ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            timeout=120.0,
        )
        self._descriptor = default_descriptor_for(model_id, "anthropic")

    def capability(self) -> CapabilityDescriptor:
        return self._descriptor

    async def _raw_chat(self, request: ChatRequest) -> ChatResponse:
        system_text, api_messages = _build_anthropic_messages(request.messages)

        payload: dict[str, Any] = {
            "model": self._model_id,
            "max_tokens": request.max_tokens,
            "messages": api_messages,
        }
        if system_text:
            payload["system"] = system_text
        if request.tools:
            payload["tools"] = [
                _canonical_to_anthropic_tool(t) for t in request.tools
            ]

        response = await self._client.post("/v1/messages", json=payload)
        response.raise_for_status()
        return _parse_anthropic_response(response.json(), self._model_id)

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
