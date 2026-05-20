"""Tool registry — single source of truth for registered tools and their tiers.

The dispatch flow MUST consult this registry for every model-originated tool call.
Execute-tier tools are never visible to the model; this class enforces that at the
read boundary so no other code needs to filter.
"""

from wolf_common.errors import ToolCapabilityError, ToolNotFoundError
from wolf_schema import ToolSchema, ToolTier


class ToolRegistry:
    """In-process registry of all tools and their capability tiers."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSchema] = {}

    def register(self, tool: ToolSchema) -> None:
        """Register a tool.  Raises ValueError on duplicate name."""
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name!r}")
        self._tools[tool.name] = tool

    def lookup(self, name: str) -> ToolSchema:
        """Return the tool or raise ToolNotFoundError."""
        if name not in self._tools:
            raise ToolNotFoundError(f"Unknown tool: {name!r}")
        return self._tools[name]

    def model_tools(self) -> list[ToolSchema]:
        """Return only read and propose tools — safe to include in model schemas.

        Execute tools are intentionally absent.  This is the list the orchestrator
        passes to the model; the model can never call what it cannot see.
        """
        return [t for t in self._tools.values() if t.tier != ToolTier.execute]

    def all_tools(self) -> list[ToolSchema]:
        """Return all registered tools including execute-tier (for gateway use)."""
        return list(self._tools.values())

    def validate_model_call(self, name: str) -> ToolSchema:
        """Validate a model-originated tool call.

        Raises ToolNotFoundError for unknown tools and ToolCapabilityError if the
        model somehow named an execute-tier tool (structural anomaly — always audited).
        """
        tool = self.lookup(name)
        if tool.tier == ToolTier.execute:
            raise ToolCapabilityError(
                f"Model attempted to call execute-tier tool {name!r} — rejected"
            )
        return tool


# Module-level singleton; services import this and register their tools at startup.
registry = ToolRegistry()
