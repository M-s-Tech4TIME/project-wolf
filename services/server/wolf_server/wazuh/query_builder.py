"""Organization-scoped OpenSearch query builder.

Isolation is two-layered (doc 05 §Four enforcement points):

  1. Credential isolation (PRIMARY): the connection uses the org's own Wazuh
     credential, whose Wazuh-side RBAC + index DLS decide what it can read — the
     cluster physically rejects anything out of scope.  This alone is the
     boundary for the common per-org-credential deployment.
  2. Optional query layer (Phase 6.6-f, ADR 0020): when an org's credential is
     NOT itself DLS-scoped, the builder can inject
     `terms: {agent.labels.group: [<labels>]}` — the REAL Wazuh field — into
     every query, scoping it to the org's configured group label(s).  Off by
     default; when on, no method produces a query without the clause.

Callers pass *what* to search; the builder decides *where*.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any


class OrganizationScopedQueryBuilder:
    """Build OpenSearch queries, optionally forcing the group-label filter.

    Construct one per request, bound to the request's organization context.
    Reusing a builder across organizations is a bug — there is no setter for
    `organization_id`.
    """

    def __init__(
        self,
        organization_id: uuid.UUID,
        *,
        inject_group_label_filter: bool = False,
        agent_group_labels: Sequence[str] = (),
    ) -> None:
        self._organization_id = str(organization_id)
        self._inject_group_label_filter = inject_group_label_filter
        self._agent_group_labels = list(agent_group_labels)

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
        search_after: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Build a query for the `search_alerts` tool.

        ``search_after`` enables gap-free cursor pagination across an entire
        time window regardless of volume (no 10k ``from``/``size`` ceiling,
        and no skip/duplicate when new docs arrive mid-walk). It is the
        ``sort`` array of the last hit from the previous page; the sort key
        below pairs ``timestamp`` with the unique ``_id`` so the ordering is
        a total order — a requirement for ``search_after`` to be correct.
        """
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
            must.append(self._free_text_clause(free_text))

        order = "desc" if sort_desc else "asc"
        query: dict[str, Any] = {
            "size": size,
            "sort": [{"timestamp": {"order": order}}, {"_id": {"order": order}}],
            "query": {"bool": {"filter": filters, "must": must}},
        }
        if search_after is not None:
            query["search_after"] = search_after
        return query

    @staticmethod
    def _free_text_clause(free_text: str) -> dict[str, Any]:
        """Build a free-text match clause that works against Wazuh's mapping.

        Two Wazuh-specific traps a naive ``multi_match`` falls into:

          1. ``rule.description`` is mapped as ``keyword`` (NOT ``text``), so
             an analyzed match never partial-matches it — the most human-
             readable field is silently unsearchable. We hit it with a
             case-insensitive ``wildcard`` per token instead.
          2. ``full_log`` is ``text`` under the standard analyzer, which does
             not stem or sub-tokenize — "ssh" does not match the token
             "sshd". We still run an analyzed ``match`` on it (operator OR)
             to catch log-body terms the description omits.

        The clauses are OR-ed (``minimum_should_match: 1``): a hit in either
        field qualifies. Hyphens are normalized to spaces so "brute-force"
        tokenizes the same way a user types it.
        """
        normalized = free_text.lower().replace("-", " ")
        tokens = [t for t in normalized.split() if t]
        should: list[dict[str, Any]] = [
            {"match": {"full_log": {"query": free_text, "operator": "or"}}}
        ]
        for token in tokens:
            should.append(
                {
                    "wildcard": {
                        "rule.description": {
                            "value": f"*{token}*",
                            "case_insensitive": True,
                        }
                    }
                }
            )
        return {"bool": {"should": should, "minimum_should_match": 1}}

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
    def inject_group_label_filter(self) -> bool:
        """Whether this builder forces the agent.labels.group filter on queries."""
        return self._inject_group_label_filter

    @property
    def agent_group_labels(self) -> list[str]:
        """The group label(s) the forced filter scopes to (empty when not set)."""
        return list(self._agent_group_labels)

    def _mandatory_filters(self) -> list[dict[str, Any]]:
        """The forced filter clauses prepended to every query.

        When `inject_group_label_filter` is TRUE and at least one label is
        configured, contains a `terms: {agent.labels.group: [<labels>]}` clause
        — the real Wazuh field, OR-combined across labels — scoping every query
        to the org's agent group label(s).

        When FALSE (default) or no labels are configured, returns an empty list:
        the per-org *credential* (its Wazuh RBAC/DLS) is the isolation boundary,
        so no Wolf-side filter is imposed.
        """
        if not self._inject_group_label_filter or not self._agent_group_labels:
            return []
        return [{"terms": {"agent.labels.group": list(self._agent_group_labels)}}]

    def _timestamp_range(self, time_from: datetime, time_to: datetime) -> dict[str, Any]:
        return {
            "range": {
                "timestamp": {
                    "gte": time_from.isoformat(),
                    "lte": time_to.isoformat(),
                }
            }
        }
