"""Wazuh OpenSearch (Indexer) HTTP client — read-only, organization-bound.

Constructed per-request with a `WazuhConnection`.  Wraps `httpx.AsyncClient`
with HTTP Basic auth and TLS.  Every query goes through the
`OrganizationScopedQueryBuilder`; there is no `raw_query()` method.

When the org's optional group-label filter is enabled (Phase 6.6-f), an
independent data-layer re-check confirms that every hit's `agent.labels.group`
(when present) is within the connection's allowed labels.  If they disagree the
call fails closed with a `OrganizationMismatchError` — see doc 05 §Independent
data-layer re-check.  When the filter is OFF the per-org credential is the
boundary and the re-check is skipped.
"""

from typing import Any

import httpx
import structlog
from wolf_common.errors import OrganizationMismatchError, WolfError

from wolf_server.wazuh.config import WazuhConnection
from wolf_server.wazuh.query_builder import OrganizationScopedQueryBuilder

logger = structlog.get_logger(__name__)

_TIMEOUT_SECONDS = 30.0


class WazuhOpenSearchError(WolfError):
    """OpenSearch returned an unexpected response."""

    http_status = 502
    error_code = "wazuh_opensearch_error"


class WazuhOpenSearchClient:
    """Organization-bound OpenSearch client for read-only alert queries."""

    def __init__(
        self,
        connection: WazuhConnection,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._connection = connection
        self._qb = OrganizationScopedQueryBuilder(
            connection.organization_id,
            inject_group_label_filter=connection.inject_group_label_filter,
            agent_group_labels=connection.agent_group_labels,
        )
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=connection.opensearch_url,
            auth=(connection.opensearch_username, connection.opensearch_password),
            verify=connection.verify_tls,
            timeout=_TIMEOUT_SECONDS,
        )

    @property
    def query_builder(self) -> OrganizationScopedQueryBuilder:
        """Expose the organization-bound query builder — the only way to build queries."""
        return self._qb

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "WazuhOpenSearchClient":
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.aclose()

    # ── Search execution ───────────────────────────────────────────────────

    async def execute(self, query: dict[str, Any]) -> dict[str, Any]:
        """Run a pre-built query.  The query MUST come from `self.query_builder`."""
        self._assert_group_label_filter_present(query)
        index = self._connection.opensearch_index_pattern
        response = await self._client.post(f"/{index}/_search", json=query)
        if response.status_code >= 400:
            logger.warning(
                "wazuh_opensearch_http_error",
                status_code=response.status_code,
                organization_id=str(self._connection.organization_id),
            )
            raise WazuhOpenSearchError(
                f"OpenSearch returned {response.status_code}: {response.text[:200]}"
            )
        body: dict[str, Any] = response.json()
        self._assert_group_label_match(body)
        return body

    # ── Safety re-checks ───────────────────────────────────────────────────

    def _group_filter_active(self) -> bool:
        """True only when the org opted into the agent.labels.group filter."""
        return bool(
            self._connection.inject_group_label_filter and self._connection.agent_group_labels
        )

    def _assert_group_label_filter_present(self, query: dict[str, Any]) -> None:
        """Sanity check: the query must carry the group-label filter clause.

        Defense in depth — the query builder always includes it when the org's
        group-label filter is enabled, but a hand-crafted query slipping through
        would be rejected here.

        Skipped entirely when the org's WazuhConfig has
        `inject_group_label_filter=False` (or no labels) — in that mode the
        credential is the isolation boundary and no per-query filter is expected.
        """
        if not self._group_filter_active():
            return
        expected = set(self._connection.agent_group_labels)
        filters = query.get("query", {}).get("bool", {}).get("filter", [])
        for clause in filters:
            terms = clause.get("terms", {}) if isinstance(clause, dict) else {}
            values = terms.get("agent.labels.group")
            if isinstance(values, list) and set(values) == expected:
                return
        raise OrganizationMismatchError(
            "OpenSearch query missing mandatory agent.labels.group filter — rejected"
        )

    def _assert_group_label_match(self, body: dict[str, Any]) -> None:
        """Verify every returned doc's `agent.labels.group` is in the allowed set.

        Only enforced when the org's group-label filter is enabled.  A returned
        doc whose nested `agent.labels.group` (when present) falls outside the
        allowed labels fails the request closed.  When the filter is OFF the
        per-org credential is the boundary and this is skipped.
        """
        if not self._group_filter_active():
            return
        allowed = set(self._connection.agent_group_labels)
        hits = body.get("hits", {}).get("hits", [])
        for hit in hits:
            source = hit.get("_source", {}) if isinstance(hit, dict) else {}
            label = _nested_group_label(source)
            if label is not None and label not in allowed:
                logger.error(
                    "group_label_mismatch_on_return",
                    expected=sorted(allowed),
                    received=label,
                )
                raise OrganizationMismatchError(
                    f"OpenSearch returned doc with agent.labels.group {label!r}, "
                    f"expected one of {sorted(allowed)!r}"
                )


def _nested_group_label(source: dict[str, Any]) -> str | None:
    """Pull `agent.labels.group` out of a hit `_source`, tolerating absence."""
    agent = source.get("agent")
    if not isinstance(agent, dict):
        return None
    labels = agent.get("labels")
    if not isinstance(labels, dict):
        return None
    group = labels.get("group")
    return group if isinstance(group, str) else None
