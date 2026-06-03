"""Tests for the Slice 5.0b.4 tool-output summaries.

Every list-returning tool now ships an aggregated summary in its output so
the grounding judge can verify per-rule / per-agent / per-source-type
claims against structured data, instead of having to recount the raw hits
(which it sometimes can't) or having the chat model fabricate the
breakdown.

These tests cover the pure aggregation helpers in isolation; the
end-to-end "judge can ground the claim" behaviour is validated live.
"""

from wolf_server.tools.agents import (
    AgentFleetSummary,
    AgentSummary,
    _compute_agent_fleet_summary,
)
from wolf_server.tools.alerts import (
    AlertHit,
    SearchAlertsSummary,
    _compute_alert_summary,
)
from wolf_server.tools.knowledge import (
    KnowledgeHit,
    KnowledgeRetrievalSummary,
    _compute_runbook_summary,
)

# ─── alerts: _compute_alert_summary (Search / Timeline / History share this) ─


def _alert(
    rule_id: str,
    rule_description: str | None = None,
    agent_id: str = "001",
    agent_name: str = "linux-test-agent",
    timestamp: str = "2026-05-28T08:12:46Z",
) -> AlertHit:
    return AlertHit(
        id=f"hit-{rule_id}-{timestamp}",
        timestamp=timestamp,
        agent_id=agent_id,
        agent_name=agent_name,
        rule_id=rule_id,
        rule_description=rule_description,
    )


def test_alert_summary_counts_per_rule_descending() -> None:
    hits = [
        _alert("5760", "sshd: authentication failed."),
        _alert("5760", "sshd: authentication failed."),
        _alert("5760", "sshd: authentication failed."),
        _alert("5763", "sshd: brute force trying to get access."),
        _alert("5758", "Maximum authentication attempts exceeded."),
        _alert("5758", "Maximum authentication attempts exceeded."),
    ]
    summary = _compute_alert_summary(hits)
    assert [r.rule_id for r in summary.per_rule] == ["5760", "5758", "5763"]
    assert [r.count for r in summary.per_rule] == [3, 2, 1]
    # Descriptions captured from the first hit of each rule.
    assert summary.per_rule[0].description == "sshd: authentication failed."


def test_alert_summary_groups_by_agent_when_mixed() -> None:
    hits = [
        _alert("5760", agent_id="001", agent_name="linux-test-agent"),
        _alert("5760", agent_id="001", agent_name="linux-test-agent"),
        _alert("5503", agent_id="002", agent_name="web-prod-01"),
    ]
    summary = _compute_alert_summary(hits)
    assert summary.per_agent[0].agent_id == "001"
    assert summary.per_agent[0].count == 2
    assert summary.per_agent[1].agent_id == "002"
    assert summary.per_agent[1].count == 1


def test_alert_summary_time_bounds() -> None:
    hits = [
        _alert("5760", timestamp="2026-05-28T08:12:46Z"),
        _alert("5760", timestamp="2026-05-28T08:12:52Z"),
        _alert("5760", timestamp="2026-05-28T08:12:49Z"),
    ]
    summary = _compute_alert_summary(hits)
    assert summary.earliest_timestamp == "2026-05-28T08:12:46Z"
    assert summary.latest_timestamp == "2026-05-28T08:12:52Z"


def test_alert_summary_empty_inputs() -> None:
    summary = _compute_alert_summary([])
    assert summary == SearchAlertsSummary()
    assert summary.per_rule == []
    assert summary.earliest_timestamp is None


# ─── agents: _compute_agent_fleet_summary ────────────────────────────────────


def _agent(agent_id: str, status: str, os_platform: str | None = "ubuntu") -> AgentSummary:
    return AgentSummary(
        id=agent_id, name=f"host-{agent_id}", status=status, os_platform=os_platform
    )


def test_agent_fleet_summary_groups_by_status_and_os() -> None:
    agents = [
        _agent("001", "active", "ubuntu"),
        _agent("002", "active", "ubuntu"),
        _agent("003", "active", "windows"),
        _agent("004", "disconnected", "ubuntu"),
        _agent("005", "never_connected", None),
    ]
    s = _compute_agent_fleet_summary(agents)
    assert s.by_status == {"active": 3, "disconnected": 1, "never_connected": 1}
    assert s.by_os == {"ubuntu": 3, "windows": 1, "unknown": 1}


def test_agent_fleet_summary_empty_inputs() -> None:
    assert _compute_agent_fleet_summary([]) == AgentFleetSummary()


# ─── knowledge: _compute_runbook_summary ─────────────────────────────────────


def _chunk(source_type: str, distance: float) -> KnowledgeHit:
    return KnowledgeHit(
        chunk_id=f"c-{source_type}-{distance}",
        source_type=source_type,
        content="…",
        distance=distance,
    )


def test_runbook_summary_groups_by_source_type_and_picks_best_distance() -> None:
    hits = [
        _chunk("runbook", 0.22),
        _chunk("runbook", 0.35),
        _chunk("past_incident", 0.40),
        _chunk("attack_technique", 0.18),  # closest match
    ]
    s = _compute_runbook_summary(hits)
    assert s.by_source_type == {
        "runbook": 2, "past_incident": 1, "attack_technique": 1
    }
    assert s.best_distance == 0.18


def test_runbook_summary_empty_inputs() -> None:
    s = _compute_runbook_summary([])
    assert s == KnowledgeRetrievalSummary()
    assert s.best_distance is None
