"""Tool implementations — read-only Wazuh tools and the dispatcher.

Tools are the agent's hands.  Each tool has a strict typed input and output
schema (Pydantic), declares its capability tier, and is registered with the
single source of truth (`runtime_registry`).  The model only ever sees
read+propose tools; execute tools live in a separate service entirely
(`services/gateway`).
"""

from app.tools.base import Citation, ReadTool, ToolExecContext
from app.tools.dispatcher import dispatch_tool_call
from app.tools.registry import ToolRunnerRegistry, runtime_registry

__all__ = [
    "Citation",
    "ReadTool",
    "ToolExecContext",
    "ToolRunnerRegistry",
    "dispatch_tool_call",
    "runtime_registry",
]
