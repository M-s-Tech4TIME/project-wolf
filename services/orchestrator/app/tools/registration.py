"""Single function that registers every Phase 2A read tool.

Called once at app startup.  Idempotent only in the sense that it should
not be called twice — the registries raise on duplicate registration.
"""

import structlog

from app.tools.agents import GetAgentDetailTool, ListAgentsTool
from app.tools.alerts import (
    AggregateAlertsTool,
    CountAlertsBySeverityTool,
    GetAgentAlertHistoryTool,
    GetEventTimelineTool,
    SearchAlertsTool,
)
from app.tools.cluster import GetClusterHealthTool
from app.tools.registry import runtime_registry
from app.tools.rules import GetRuleDefinitionTool

logger = structlog.get_logger(__name__)


def register_all_read_tools() -> None:
    """Register the eight Phase 2A read tools with the runtime registry."""
    tools = [
        SearchAlertsTool(),
        AggregateAlertsTool(),
        CountAlertsBySeverityTool(),
        GetEventTimelineTool(),
        GetAgentAlertHistoryTool(),
        ListAgentsTool(),
        GetAgentDetailTool(),
        GetRuleDefinitionTool(),
        GetClusterHealthTool(),
    ]
    for tool in tools:
        runtime_registry.register(tool)
    logger.info("read_tools_registered", count=len(tools), names=[t.name for t in tools])
