"""Wolf canonical schema — shared types across all services and tools."""

from wolf_schema.capability import (
    AgentStrategy,
    CapabilityDescriptor,
    NativeToolCalling,
    ReasoningTier,
    StructuredOutput,
)
from wolf_schema.chat import ChatRequest, ChatResponse, Message, MessageRole
from wolf_schema.tools import ToolCall, ToolResult, ToolSchema, ToolTier

__all__ = [
    "AgentStrategy",
    "CapabilityDescriptor",
    "ChatRequest",
    "ChatResponse",
    "Message",
    "MessageRole",
    "NativeToolCalling",
    "ReasoningTier",
    "StructuredOutput",
    "ToolCall",
    "ToolResult",
    "ToolSchema",
    "ToolTier",
]
