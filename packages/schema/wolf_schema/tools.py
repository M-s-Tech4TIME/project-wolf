"""Canonical tool types: tier, schema, call, result."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class ToolTier(StrEnum):
    read = "read"
    propose = "propose"
    execute = "execute"


class ToolSchema(BaseModel):
    """Canonical tool definition stored in the tool registry."""

    name: str
    description: str
    tier: ToolTier
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


class ToolCall(BaseModel):
    """A tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


class ToolResult(BaseModel):
    """Result returned after running a tool."""

    tool_call_id: str
    name: str
    content: Any
    error: str | None = None
