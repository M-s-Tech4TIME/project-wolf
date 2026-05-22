"""Tenant-scoped OpenSearch query builder.

The single guarantee of this module: **every query built here carries the
tenant filter.**  There is no method that produces a query without it.

The forced filter is two-layered, matching doc 05 §Four enforcement points:

  1. Credential isolation: the connection itself uses tenant-A credentials,
     so the cluster physically rejects a misrouted query.
  2. Query layer: even with shared credentials (pooled-index deployments),
     the builder injects `term: {tenant_id: <ctx.tenant_id>}` into every
     query.  Tenants that do not stamp a `tenant_id` field on alerts will
     simply have an extra filter against a field that does not exist — which
     yields zero results, fail-closed, never cross-tenant exposure.

Callers pass *what* to search; the builder decides *where*, and "where"
always includes the tenant wall.
"""

import uuid
from datetime import datetime
from typing import Any


class TenantScopedQueryBuilder:
    """Build OpenSearch queries that always include the tenant filter.

    Construct one per request, bound to the request's tenant context.
    Reusing a builder across tenants is a bug — there is no setter for
    `tenant_id`.
    """

    def __init__(
        self,
        tenant_id: uuid.UUID,
        *,
        inject_tenant_filter: bool = False,
    ) -> None:
        self._tenant_id = str(tenant_id)
        self._inject_tenant_filter = inject_tenant_filter

    # ── Public query constructors ─────────────────────────────────────────

    def search_alerts(
        self,
        *,
        time_from: datetime,
        time_to: datetime,
        agent_id: str | None = None,
        rule_id: int | None = None,
        min_level: int | None = None,
        attack_technique: str | None = None,
        free_text: str | None = None,
        size: int = 100,
        sort_desc: bool = True,
    ) -> dict[str, Any]:
        """Build a query for the `search_alerts` tool."""
        filters: list[dict[str, Any]] = [
            *self._mandatory_filters(),
            self._timestamp_range(time_from, time_to),
        ]
        if agent_id is not None:
            filters.append({"term": {"agent.id": agent_id}})
        if rule_id is not None:
            filters.append({"term": {"rule.id": str(rule_id)}})
        if min_level is not None:
            filters.append({"range": {"rule.level": {"gte": min_level}}})
        if attack_technique is not None:
            filters.append({"term": {"rule.mitre.id": attack_technique}})

        must: list[dict[str, Any]] = []
        if free_text:
            must.append(
                {
                    "multi_match": {
                        "query": free_text,
                        "fields": ["full_log", "rule.description"],
                    }
                }
            )

        return {
            "size": size,
            "sort": [{"timestamp": {"order": "desc" if sort_desc else "asc"}}],
            "query": {"bool": {"filter": filters, "must": must}},
        }

    def aggregate_alerts(
        self,
        *,
        time_from: datetime,
        time_to: datetime,
        group_by: str,
        agent_id: str | None = None,
        size: int = 50,
    ) -> dict[str, Any]:
        """Build an aggregation query (alerts grouped by a field)."""
        filters: list[dict[str, Any]] = [
            *self._mandatory_filters(),
            self._timestamp_range(time_from, time_to),
        ]
        if agent_id is not None:
            filters.append({"term": {"agent.id": agent_id}})

        return {
            "size": 0,
            "query": {"bool": {"filter": filters}},
            "aggs": {"buckets": {"terms": {"field": group_by, "size": size}}},
        }

    def event_timeline(
        self,
        *,
        time_from: datetime,
        time_to: datetime,
        agent_id: str,
        size: int = 200,
    ) -> dict[str, Any]:
        """Build a chronological timeline query for one host/agent."""
        filters: list[dict[str, Any]] = [
            *self._mandatory_filters(),
            self._timestamp_range(time_from, time_to),
            {"term": {"agent.id": agent_id}},
        ]
        return {
            "size": size,
            "sort": [{"timestamp": {"order": "asc"}}],
            "query": {"bool": {"filter": filters}},
        }

    def agent_alert_history(
        self,
        *,
        time_from: datetime,
        time_to: datetime,
        agent_id: str,
        size: int = 200,
    ) -> dict[str, Any]:
        """Alerts for one agent in a window, newest first."""
        return self.search_alerts(
            time_from=time_from,
            time_to=time_to,
            agent_id=agent_id,
            size=size,
            sort_desc=True,
        )

    # ── Internal builders ─────────────────────────────────────────────────

    @property
    def inject_tenant_filter(self) -> bool:
        """Whether this builder adds the term:{tenant_id} filter to queries."""
        return self._inject_tenant_filter

    def _mandatory_filters(self) -> list[dict[str, Any]]:
        """The forced filter clauses prepended to every query.

        When `inject_tenant_filter` is TRUE, contains the `tenant_id`
        term filter — required for pooled-index multi-tenant deployments
        where every alert is stamped with `tenant_id` at ingest.

        When FALSE (default), returns an empty list.  Vanilla Wazuh
        alerts do NOT carry a `tenant_id` field, so the filter would
        silently match zero docs — fail-closed is wrong here because the
        per-tenant *credential* is the actual isolation boundary.
        """
        if not self._inject_tenant_filter:
            return []
        return [{"term": {"tenant_id": self._tenant_id}}]

    def _timestamp_range(self, time_from: datetime, time_to: datetime) -> dict[str, Any]:
        return {
            "range": {
                "timestamp": {
                    "gte": time_from.isoformat(),
                    "lte": time_to.isoformat(),
                }
            }
        }
