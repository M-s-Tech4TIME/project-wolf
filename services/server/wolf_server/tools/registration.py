"""Single function that registers every read tool.

Called once at app startup.  Idempotent only in the sense that it should
not be called twice — the registries raise on duplicate registration.
"""

import structlog

from wolf_server.tools.agents import GetAgentDetailTool, ListAgentsTool
from wolf_server.tools.alerts import (
    AggregateAlertsTool,
    CountAlertsBySeverityTool,
    GetAgentAlertHistoryTool,
    GetEventTimelineTool,
    SearchAlertsTool,
)
from wolf_server.tools.cluster import GetClusterHealthTool
from wolf_server.tools.knowledge import QueryRunbookTool
from wolf_server.tools.propose_active_response import ProposeActiveResponseTool
from wolf_server.tools.registry import runtime_registry
from wolf_server.tools.rules import GetRuleDefinitionTool

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


def register_all_propose_tools() -> None:
    """Register Wolf's propose-tier tools (Phase 6, ADR 0025).

    Propose tools are shown to the model (a proposal is just data) but change
    nothing themselves — they queue a proposal a human must approve.  Execution
    happens only in `wolf_server.gateway.execution`, post-approval.
    """
    tools = [
        ProposeActiveResponseTool(),
    ]
    for tool in tools:
        runtime_registry.register(tool)
    logger.info("propose_tools_registered", count=len(tools), names=[t.name for t in tools])
