"""Runtime registry of tool implementations — single source of truth at execution.

The wire-format `ToolRegistry` from `wolf_server/models/registry.py` holds
`ToolSchema` objects (what the model is told about).  This module holds the
runnable `ReadTool` instances (how wolf-server executes them).  Registering a
tool here also registers its schema with the wire-format registry — so the
two cannot drift.
"""

from wolf_common.errors import ToolNotFoundError

from wolf_server.models.registry import registry as schema_registry
from wolf_server.tools.base import ReadTool


class ToolRunnerRegistry:
    """In-process mapping of tool name → executable ReadTool instance."""

    def __init__(self) -> None:
        self._runners: dict[str, ReadTool] = {}

    def register(self, tool: ReadTool) -> None:
        """Register a tool: stores the runner here AND the schema in the wire registry."""
        if tool.name in self._runners:
            raise ValueError(f"Tool runner already registered: {tool.name!r}")
        self._runners[tool.name] = tool
        schema_registry.register(tool.schema())

    def get(self, name: str) -> ReadTool:
        if name not in self._runners:
            raise ToolNotFoundError(f"No runner for tool: {name!r}")
        return self._runners[name]

    def names(self) -> list[str]:
        return list(self._runners.keys())

    def clear(self) -> None:
        """Remove every registered runner — for test isolation only."""
        self._runners.clear()


runtime_registry = ToolRunnerRegistry()
