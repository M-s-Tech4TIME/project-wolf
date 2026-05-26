"""Single function that registers every read tool.

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
from app.tools.knowledge import QueryRunbookTool
from app.tools.registry import runtime_registry
from app.tools.rules import GetRuleDefinitionTool

logger = structlog.get_logger(__name__)


def register_all_read_tools() -> None:
    """Register Wolf's read tools.

    Phase 2A: 9 Wazuh-backed read tools (alerts, agents, rules, cluster).
    Phase 3: query_runbook (RAG over knowledge corpora).
    """
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
        QueryRunbookTool(),
    ]
    for tool in tools:
        runtime_registry.register(tool)
    logger.info("read_tools_registered", count=len(tools), names=[t.name for t in tools])
