"""Provider-agnostic chat protocol types."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from wolf_schema.tools import ToolCall, ToolResult, ToolSchema


class MessageRole(StrEnum):
    system = "system"
    user = "user"
    assistant = "assistant"
    tool = "tool"


class Message(BaseModel):
    """A single message in the conversation history."""

    role: MessageRole
    content: str = ""
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    tool_results: list[ToolResult] | None = None


class ChatRequest(BaseModel):
    """Provider-agnostic chat completion request."""

    messages: list[Message]
    tools: list[ToolSchema] | None = None
    max_tokens: int = 4096
    temperature: float = 0.0
    structured_output_schema: dict[str, Any] | None = None


class ChatResponse(BaseModel):
    """Provider-agnostic chat completion response."""

    content: str
    tool_calls: list[ToolCall] = []
    input_tokens: int
    output_tokens: int
    stop_reason: str
    model_id: str
