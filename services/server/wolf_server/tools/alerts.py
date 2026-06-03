"""Alert-tier read tools: search, aggregate, timeline, agent history.

These tools query the Wazuh OpenSearch (Indexer) tier through the tenant-
scoped query builder.  Every tool result carries a citation so downstream
grounding validation can verify factual claims.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from wolf_server.guardrails.limits import enforce_limits
from wolf_server.tools.base import Citation, ReadTool, ToolExecContext
from wolf_server.tools.timefmt import default_time_to, parse_time_field

_TIME_FIELD_HELP = (
    "Accepts ISO 8601 datetime (e.g. '2026-05-21T10:00:00Z') OR a "
    "relative expression like 'now', 'now-15m', 'now-1h', 'now-24h', "
    "'now-7d', 'now-6mo', 'now-1y'. Units: m=minutes, h=hours, d=days, "
    "w=weeks, mo=months, y=years."
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


_AGENT_NAME_CACHE_NS = "agent_name_lookup"
_AGENT_NAME_CACHE_TTL = 60.0  # seconds; short enough that agent fleet
                              # changes converge within ~1 minute.


async def _resolve_agent_name_to_id(
    agent_name: str, exec_ctx: ToolExecContext
) -> str | None:
    """Look up a Wazuh agent's numeric id by its human-readable name.

    Uses the Server API's `/agents?name=<n>` filter (case-insensitive).
    Returns the id if found, None otherwise. Raises only on transport
    errors — a missing-name is NOT an exception (the agent really may
    not exist; the caller will then run an unfiltered or no-results
    query and the validator will catch the resulting under-grounding).

    Phase 4 Slice 3: result is now CACHED per-tenant via the tenant-
    scoped cache (60-second TTL). Within a single chat loop the same
    agent_name is often resolved multiple times — caching turns N
    Server-API GETs into 1. The cache key includes the tenant_id by
    construction (doc 05 §Caching across tenants), so tenant A's cache
    of "linux-test-agent → 001" cannot satisfy tenant B's lookup of
    the same name — each tenant probes its own Wazuh deployment.

    A short TTL bounds the staleness risk: if an agent is deleted +
    re-registered with a different ID, the cache converges within
    60 seconds. Operationally acceptable.
    """
    if exec_ctx.cache is not None:
        cached = await exec_ctx.cache.get(
            exec_ctx.tenant.tenant_id, _AGENT_NAME_CACHE_NS, agent_name
        )
        if cached is not None:
            # Cache stores either the resolved id (str) OR the marker
            # `__NOT_FOUND__` for "we asked, no such agent." Distinguishing
            # them from cache miss (None) avoids re-probing the API on
            # repeated lookups of a name that doesn't exist.
            return None if cached == "__NOT_FOUND__" else str(cached)

    body = await exec_ctx.server_api.get("/agents", params={"name": agent_name})
    items = body.get("data", {}).get("affected_items", []) or []
    resolved: str | None = (
        str(items[0].get("id")) if items and items[0].get("id") is not None else None
    )

    if exec_ctx.cache is not None:
        await exec_ctx.cache.set(
            exec_ctx.tenant.tenant_id,
            _AGENT_NAME_CACHE_NS,
            agent_name,
            resolved if resolved is not None else "__NOT_FOUND__",
            ttl_seconds=_AGENT_NAME_CACHE_TTL,
        )
    return resolved


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
    agent_id: str | None = Field(
        default=None,
        description=(
            "Filter to one agent by its numeric ID (e.g. '001'). "
            "Get this from list_agents — NOT the human-readable agent name."
        ),
    )
    agent_name: str | None = Field(
        default=None,
        description=(
            "Filter to one agent by its human-readable name (e.g. "
            "'linux-test-agent'). Wolf resolves the name to a numeric "
            "agent_id via list_agents before querying. Use this when you "
            "know the agent by name but not by ID."
        ),
    )
    rule_id: int | None = Field(default=None, description="Filter to one rule ID")
    min_level: int | None = Field(default=None, description="Minimum alert level (0-15)")
    attack_technique: str | None = Field(
        default=None, description="MITRE ATT&CK technique ID, e.g. T1110"
    )
    free_text: str | None = Field(default=None, description="Match in log/description text")
    size: int = Field(default=100, ge=1, le=1000, description="Max results per page")
    cursor: list[Any] | None = Field(
        default=None,
        description=(
            "Leave null for the first page. To get more results, pass back "
            "the `next_cursor` from the previous result unchanged. For pure "
            "counts over a wide window, use count_alerts_by_severity instead."
        ),
    )

    @field_validator("time_from", "time_to", mode="before")
    @classmethod
    def _coerce_time(cls, v: Any) -> Any:
        return parse_time_field(v)

    @field_validator("min_level", mode="before")
    @classmethod
    def _coerce_min_level(cls, v: Any) -> Any:
        # Small models sometimes emit a list of levels they want (e.g.
        # [7, 8, 9, 10, 11] for "medium").  min_level is conceptually
        # a single inclusive lower threshold, so take the minimum of any
        # list-shaped input and let the rest of the schema enforce range.
        if isinstance(v, list) and v:
            try:
                return min(int(x) for x in v)
            except (ValueError, TypeError):
                return v
        return v


class AlertRuleSummary(BaseModel):
    rule_id: str
    description: str | None = None
    count: int


class AlertAgentSummary(BaseModel):
    agent_id: str
    agent_name: str | None = None
    count: int


class SearchAlertsSummary(BaseModel):
    """Aggregations over the hits in THIS page.

    Computed client-side so the model can ground per-rule and per-agent
    claims directly — without making a separate aggregate_alerts call and
    without inventing breakdowns the raw hit list doesn't already imply.
    For multi-page results, this reflects only the current page; for the
    full picture call count_alerts_by_severity or aggregate_alerts.
    """

    per_rule: list[AlertRuleSummary] = Field(
        default_factory=list,
        description="Per-rule counts in this page, descending by count. "
        "Use these to ground 'rule X: N alerts' claims; do not invent your own breakdown.",
    )
    per_agent: list[AlertAgentSummary] = Field(
        default_factory=list,
        description="Per-agent counts in this page, descending by count.",
    )
    earliest_timestamp: str | None = Field(
        default=None,
        description="Earliest timestamp seen in this page; null when empty.",
    )
    latest_timestamp: str | None = Field(
        default=None,
        description="Latest timestamp seen in this page; null when empty.",
    )


def _compute_alert_summary(hits: list[AlertHit]) -> SearchAlertsSummary:
    """Aggregate per-rule / per-agent counts and time bounds from a hit list."""
    if not hits:
        return SearchAlertsSummary()
    rule_counts: dict[str, int] = {}
    rule_desc: dict[str, str | None] = {}
    agent_counts: dict[str, int] = {}
    agent_names: dict[str, str | None] = {}
    timestamps: list[str] = []
    for h in hits:
        if h.rule_id is not None:
            rule_counts[h.rule_id] = rule_counts.get(h.rule_id, 0) + 1
            if h.rule_id not in rule_desc:
                rule_desc[h.rule_id] = h.rule_description
        if h.agent_id is not None:
            agent_counts[h.agent_id] = agent_counts.get(h.agent_id, 0) + 1
            if h.agent_id not in agent_names:
                agent_names[h.agent_id] = h.agent_name
        if h.timestamp:
            timestamps.append(h.timestamp)
    per_rule = sorted(
        (
            AlertRuleSummary(rule_id=rid, description=rule_desc[rid], count=c)
            for rid, c in rule_counts.items()
        ),
        key=lambda r: r.count,
        reverse=True,
    )
    per_agent = sorted(
        (
            AlertAgentSummary(agent_id=aid, agent_name=agent_names[aid], count=c)
            for aid, c in agent_counts.items()
        ),
        key=lambda a: a.count,
        reverse=True,
    )
    return SearchAlertsSummary(
        per_rule=per_rule,
        per_agent=per_agent,
        earliest_timestamp=min(timestamps) if timestamps else None,
        latest_timestamp=max(timestamps) if timestamps else None,
    )


class SearchAlertsOutput(BaseModel):
    hits: list[AlertHit]
    summary: SearchAlertsSummary = Field(
        default_factory=SearchAlertsSummary,
        description=(
            "Aggregations over this page's hits (per-rule, per-agent, time "
            "range). Use these to ground breakdown claims directly instead "
            "of computing them yourself from the raw hits."
        ),
    )
    total: int = Field(
        description=(
            "Total alerts matching the query across ALL pages (not just this "
            "page). Use this to gauge scale and decide whether to paginate."
        )
    )
    has_more: bool = Field(
        description=(
            "True if more pages remain. When true, call search_alerts again "
            "with `cursor=next_cursor` to continue; when false you have seen "
            "every matching alert in the window."
        )
    )
    next_cursor: list[Any] | None = Field(
        default=None,
        description="Opaque cursor for the next page; null when has_more is false.",
    )
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
        resolved_agent_id = args.agent_id
        if not resolved_agent_id and args.agent_name:
            # Resolve human-readable name → numeric ID via Server API.
            # Small models routinely pass the name where the API needs
            # the ID; this resolution short-circuits the silent 0-hits
            # failure that confused the Slice 3 end-to-end retest.
            resolved_agent_id = await _resolve_agent_name_to_id(
                args.agent_name, exec_ctx
            )
        query = exec_ctx.opensearch.query_builder.search_alerts(
            time_from=args.time_from,
            time_to=args.time_to,
            agent_id=resolved_agent_id,
            rule_id=args.rule_id,
            min_level=args.min_level,
            attack_technique=args.attack_technique,
            free_text=args.free_text,
            size=args.size,
            search_after=args.cursor,
        )
        body = await exec_ctx.opensearch.execute(query)
        hits_raw = body.get("hits", {}).get("hits", [])
        total = body.get("hits", {}).get("total", {})
        total_value = total.get("value", len(hits_raw)) if isinstance(total, dict) else int(total)
        alerts = [_hit_to_alert(h) for h in hits_raw]
        # A full page means more rows may follow; the cursor is the sort
        # array of the last hit, fed straight back as search_after. A short
        # page (or empty) means the walk is complete.
        has_more = len(hits_raw) == args.size
        next_cursor = (
            hits_raw[-1].get("sort") if has_more and hits_raw else None
        )
        # Never advertise more without a usable cursor to continue with.
        if next_cursor is None:
            has_more = False
        return SearchAlertsOutput(
            hits=alerts,
            summary=_compute_alert_summary(alerts),
            total=int(total_value),
            has_more=has_more,
            next_cursor=next_cursor,
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
    size: int = Field(default=50, ge=1, le=10000, description="Max buckets to return")

    @field_validator("time_from", "time_to", mode="before")
    @classmethod
    def _coerce_time(cls, v: Any) -> Any:
        return parse_time_field(v)


class AggregateBucket(BaseModel):
    key: str
    count: int


class AggregateAlertsOutput(BaseModel):
    buckets: list[AggregateBucket]
    total: int = Field(
        default=0,
        description="Sum of doc_count across all returned buckets. Use this "
        "to ground 'total' / 'across' claims instead of summing manually.",
    )
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
            enforce_time_window=False,  # bucket-bounded; any range is safe
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
            total=sum(b.count for b in buckets),
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
    summary: SearchAlertsSummary = Field(
        default_factory=SearchAlertsSummary,
        description="Per-rule / per-agent / time-range aggregations over "
        "the events; use to ground breakdown claims directly.",
    )
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
            summary=_compute_alert_summary(events),
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
    summary: SearchAlertsSummary = Field(
        default_factory=SearchAlertsSummary,
        description="Per-rule / per-agent / time-range aggregations over "
        "the returned alerts; use to ground breakdown claims directly.",
    )
    citation: Citation


# ─── count_alerts_by_severity ─────────────────────────────────────────────────


class SeverityCounts(BaseModel):
    """Alert counts bucketed by Wazuh severity (derived from rule.level)."""

    critical: int = Field(description="rule.level 15 or higher")
    high: int = Field(description="rule.level 12, 13, or 14")
    medium: int = Field(description="rule.level 7, 8, 9, 10, or 11")
    low: int = Field(description="rule.level 0 through 6")
    total: int


class CountAlertsBySeverityInput(BaseModel):
    """Count alerts in a time window, bucketed by Wazuh severity."""

    time_from: datetime = Field(description=f"Window start. {_TIME_FIELD_HELP}")
    time_to: datetime = Field(
        default_factory=default_time_to,
        description=f"Window end; defaults to 'now'. {_TIME_FIELD_HELP}",
    )
    agent_id: str | None = Field(
        default=None, description="Optional: filter to one agent."
    )

    @field_validator("time_from", "time_to", mode="before")
    @classmethod
    def _coerce_time(cls, v: Any) -> Any:
        return parse_time_field(v)


class CountAlertsBySeverityOutput(BaseModel):
    counts: SeverityCounts
    citation: Citation


class CountAlertsBySeverityTool(ReadTool):
    name = "count_alerts_by_severity"
    description = (
        "Returns alert counts bucketed by Wazuh severity in a time "
        "window. Severity comes from rule.level: Critical (15+), "
        "High (12-14), Medium (7-11), Low (0-6). USE THIS for any "
        "question about how many alerts of each severity — do not "
        "try to compute severity buckets yourself with min_level or "
        "aggregate_alerts; this tool does the bucketing server-side "
        "and returns one clean object."
    )
    InputModel = CountAlertsBySeverityInput
    OutputModel = CountAlertsBySeverityOutput

    async def run(self, exec_ctx: ToolExecContext, args: BaseModel) -> BaseModel:
        assert isinstance(args, CountAlertsBySeverityInput)
        enforce_limits(
            time_from=args.time_from,
            time_to=args.time_to,
            limits=exec_ctx.limits,
            enforce_time_window=False,  # bucket-bounded; any range is safe
        )
        # Aggregate by rule.level — Wazuh levels are 0-15, so 20 buckets
        # is plenty of headroom for any extension or unexpected value.
        query = exec_ctx.opensearch.query_builder.aggregate_alerts(
            time_from=args.time_from,
            time_to=args.time_to,
            group_by="rule.level",
            agent_id=args.agent_id,
            size=20,
        )
        body = await exec_ctx.opensearch.execute(query)
        raw_buckets = (
            body.get("aggregations", {}).get("buckets", {}).get("buckets", [])
        )
        critical = high = medium = low = 0
        for b in raw_buckets:
            try:
                level = int(b.get("key", -1))
            except (ValueError, TypeError):
                continue
            count = int(b.get("doc_count", 0))
            if level >= 15:
                critical += count
            elif level >= 12:
                high += count
            elif level >= 7:
                medium += count
            elif level >= 0:
                low += count

        counts = SeverityCounts(
            critical=critical,
            high=high,
            medium=medium,
            low=low,
            total=critical + high + medium + low,
        )
        return CountAlertsBySeverityOutput(
            counts=counts,
            citation=self.make_citation(
                args.model_dump(mode="json"),
                result_count=counts.total,
            ),
        )


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
            summary=_compute_alert_summary(alerts),
            citation=self.make_citation(
                args.model_dump(mode="json"),
                result_count=len(alerts),
            ),
        )
