"""Alert-tier read tools: search, aggregate, timeline, agent history.

These tools query the Wazuh OpenSearch (Indexer) tier through the tenant-
scoped query builder.  Every tool result carries a citation so downstream
grounding validation can verify factual claims.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.guardrails.limits import enforce_limits
from app.tools.base import Citation, ReadTool, ToolExecContext
from app.tools.timefmt import default_time_to, parse_time_field

_TIME_FIELD_HELP = (
    "Accepts ISO 8601 datetime (e.g. '2026-05-21T10:00:00Z') OR a "
    "relative expression like 'now', 'now-15m', 'now-1h', 'now-24h', "
    "'now-7d'."
)

# ─── Shared output building blocks ────────────────────────────────────────────


class AlertHit(BaseModel):
    """A single alert document, normalized from the OpenSearch _source."""

    id: str = Field(description="Alert document ID")
    timestamp: str = Field(description="ISO-8601 timestamp")
    agent_id: str | None = None
    agent_name: str | None = None
    rule_id: str | None = None
    rule_level: int | None = None
    rule_description: str | None = None
    mitre_techniques: list[str] = Field(default_factory=list)
    full_log: str | None = None


def _hit_to_alert(hit: dict[str, Any]) -> AlertHit:
    source = hit.get("_source", {})
    agent = source.get("agent", {})
    rule = source.get("rule", {})
    mitre = rule.get("mitre", {}) or {}
    techniques = mitre.get("id") or []
    if isinstance(techniques, str):
        techniques = [techniques]
    return AlertHit(
        id=str(hit.get("_id", "")),
        timestamp=str(source.get("timestamp", "")),
        agent_id=agent.get("id"),
        agent_name=agent.get("name"),
        rule_id=str(rule.get("id")) if rule.get("id") is not None else None,
        rule_level=rule.get("level"),
        rule_description=rule.get("description"),
        mitre_techniques=list(techniques),
        full_log=source.get("full_log"),
    )


# ─── search_alerts ────────────────────────────────────────────────────────────


class SearchAlertsInput(BaseModel):
    """Query alerts by time range and optional filters."""

    time_from: datetime = Field(
        description=f"Inclusive start of the time window. {_TIME_FIELD_HELP}",
    )
    time_to: datetime = Field(
        default_factory=default_time_to,
        description=(
            "Inclusive end of the time window. Defaults to 'now' if omitted. "
            f"{_TIME_FIELD_HELP}"
        ),
    )
    agent_id: str | None = Field(default=None, description="Filter to one agent")
    rule_id: int | None = Field(default=None, description="Filter to one rule ID")
    min_level: int | None = Field(default=None, description="Minimum alert level (0-15)")
    attack_technique: str | None = Field(
        default=None, description="MITRE ATT&CK technique ID, e.g. T1110"
    )
    free_text: str | None = Field(default=None, description="Match in log/description text")
    size: int = Field(default=100, ge=1, le=1000, description="Max results to return")

    @field_validator("time_from", "time_to", mode="before")
    @classmethod
    def _coerce_time(cls, v: Any) -> Any:
        return parse_time_field(v)


class SearchAlertsOutput(BaseModel):
    hits: list[AlertHit]
    total: int
    citation: Citation


class SearchAlertsTool(ReadTool):
    name = "search_alerts"
    description = (
        "Query alerts by time range, agent, rule ID, level, ATT&CK technique, "
        "or free text.  Returns paginated results."
    )
    InputModel = SearchAlertsInput
    OutputModel = SearchAlertsOutput

    async def run(self, exec_ctx: ToolExecContext, args: BaseModel) -> BaseModel:
        assert isinstance(args, SearchAlertsInput)
        enforce_limits(
            time_from=args.time_from,
            time_to=args.time_to,
            requested_size=args.size,
            limits=exec_ctx.limits,
        )
        query = exec_ctx.opensearch.query_builder.search_alerts(
            time_from=args.time_from,
            time_to=args.time_to,
            agent_id=args.agent_id,
            rule_id=args.rule_id,
            min_level=args.min_level,
            attack_technique=args.attack_technique,
            free_text=args.free_text,
            size=args.size,
        )
        body = await exec_ctx.opensearch.execute(query)
        hits_raw = body.get("hits", {}).get("hits", [])
        total = body.get("hits", {}).get("total", {})
        total_value = total.get("value", len(hits_raw)) if isinstance(total, dict) else int(total)
        alerts = [_hit_to_alert(h) for h in hits_raw]
        return SearchAlertsOutput(
            hits=alerts,
            total=int(total_value),
            citation=self.make_citation(
                args.model_dump(mode="json"),
                result_count=len(alerts),
            ),
        )


# ─── aggregate_alerts ─────────────────────────────────────────────────────────


class AggregateAlertsInput(BaseModel):
    """Bucketed counts of alerts grouped by a field."""

    time_from: datetime = Field(description=f"Window start. {_TIME_FIELD_HELP}")
    time_to: datetime = Field(
        default_factory=default_time_to,
        description=f"Window end; defaults to 'now'. {_TIME_FIELD_HELP}",
    )
    group_by: str = Field(
        description="Field to group by, e.g. 'agent.name', 'rule.id', 'rule.level'"
    )
    agent_id: str | None = None
    size: int = Field(default=50, ge=1, le=500)

    @field_validator("time_from", "time_to", mode="before")
    @classmethod
    def _coerce_time(cls, v: Any) -> Any:
        return parse_time_field(v)


class AggregateBucket(BaseModel):
    key: str
    count: int


class AggregateAlertsOutput(BaseModel):
    buckets: list[AggregateBucket]
    citation: Citation


class AggregateAlertsTool(ReadTool):
    name = "aggregate_alerts"
    description = (
        "Bucketed counts of alerts over a query — alerts per agent, per rule, "
        "or per any field — for triage and trend views."
    )
    InputModel = AggregateAlertsInput
    OutputModel = AggregateAlertsOutput

    async def run(self, exec_ctx: ToolExecContext, args: BaseModel) -> BaseModel:
        assert isinstance(args, AggregateAlertsInput)
        enforce_limits(
            time_from=args.time_from,
            time_to=args.time_to,
            requested_size=args.size,
            limits=exec_ctx.limits,
        )
        query = exec_ctx.opensearch.query_builder.aggregate_alerts(
            time_from=args.time_from,
            time_to=args.time_to,
            group_by=args.group_by,
            agent_id=args.agent_id,
            size=args.size,
        )
        body = await exec_ctx.opensearch.execute(query)
        raw_buckets = (
            body.get("aggregations", {}).get("buckets", {}).get("buckets", [])
        )
        buckets = [
            AggregateBucket(key=str(b["key"]), count=int(b.get("doc_count", 0)))
            for b in raw_buckets
        ]
        return AggregateAlertsOutput(
            buckets=buckets,
            citation=self.make_citation(
                args.model_dump(mode="json"),
                result_count=len(buckets),
            ),
        )


# ─── get_event_timeline ───────────────────────────────────────────────────────


class GetEventTimelineInput(BaseModel):
    """Chronological events for one host across a window."""

    time_from: datetime = Field(description=f"Window start. {_TIME_FIELD_HELP}")
    time_to: datetime = Field(
        default_factory=default_time_to,
        description=f"Window end; defaults to 'now'. {_TIME_FIELD_HELP}",
    )
    agent_id: str
    size: int = Field(default=200, ge=1, le=1000)

    @field_validator("time_from", "time_to", mode="before")
    @classmethod
    def _coerce_time(cls, v: Any) -> Any:
        return parse_time_field(v)


class GetEventTimelineOutput(BaseModel):
    events: list[AlertHit]
    citation: Citation


class GetEventTimelineTool(ReadTool):
    name = "get_event_timeline"
    description = (
        "Ordered sequence of events for one host or entity across a window.  "
        "The backbone of investigation."
    )
    InputModel = GetEventTimelineInput
    OutputModel = GetEventTimelineOutput

    async def run(self, exec_ctx: ToolExecContext, args: BaseModel) -> BaseModel:
        assert isinstance(args, GetEventTimelineInput)
        enforce_limits(
            time_from=args.time_from,
            time_to=args.time_to,
            requested_size=args.size,
            limits=exec_ctx.limits,
        )
        query = exec_ctx.opensearch.query_builder.event_timeline(
            time_from=args.time_from,
            time_to=args.time_to,
            agent_id=args.agent_id,
            size=args.size,
        )
        body = await exec_ctx.opensearch.execute(query)
        hits_raw = body.get("hits", {}).get("hits", [])
        events = [_hit_to_alert(h) for h in hits_raw]
        return GetEventTimelineOutput(
            events=events,
            citation=self.make_citation(
                args.model_dump(mode="json"),
                result_count=len(events),
            ),
        )


# ─── get_agent_alert_history ──────────────────────────────────────────────────


class GetAgentAlertHistoryInput(BaseModel):
    """Alert history for one agent."""

    time_from: datetime = Field(description=f"Window start. {_TIME_FIELD_HELP}")
    time_to: datetime = Field(
        default_factory=default_time_to,
        description=f"Window end; defaults to 'now'. {_TIME_FIELD_HELP}",
    )
    agent_id: str
    size: int = Field(default=200, ge=1, le=1000)

    @field_validator("time_from", "time_to", mode="before")
    @classmethod
    def _coerce_time(cls, v: Any) -> Any:
        return parse_time_field(v)


class GetAgentAlertHistoryOutput(BaseModel):
    alerts: list[AlertHit]
    citation: Citation


class GetAgentAlertHistoryTool(ReadTool):
    name = "get_agent_alert_history"
    description = "Alert history for one agent in a time window, newest first."
    InputModel = GetAgentAlertHistoryInput
    OutputModel = GetAgentAlertHistoryOutput

    async def run(self, exec_ctx: ToolExecContext, args: BaseModel) -> BaseModel:
        assert isinstance(args, GetAgentAlertHistoryInput)
        enforce_limits(
            time_from=args.time_from,
            time_to=args.time_to,
            requested_size=args.size,
            limits=exec_ctx.limits,
        )
        query = exec_ctx.opensearch.query_builder.agent_alert_history(
            time_from=args.time_from,
            time_to=args.time_to,
            agent_id=args.agent_id,
            size=args.size,
        )
        body = await exec_ctx.opensearch.execute(query)
        hits_raw = body.get("hits", {}).get("hits", [])
        alerts = [_hit_to_alert(h) for h in hits_raw]
        return GetAgentAlertHistoryOutput(
            alerts=alerts,
            citation=self.make_citation(
                args.model_dump(mode="json"),
                result_count=len(alerts),
            ),
        )
