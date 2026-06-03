"""Wazuh OpenSearch (Indexer) HTTP client — read-only, tenant-bound.

Constructed per-request with a `WazuhConnection`.  Wraps `httpx.AsyncClient`
with HTTP Basic auth and TLS.  Every query goes through the
`TenantScopedQueryBuilder`; there is no `raw_query()` method.

After a response returns, an independent data-layer re-check confirms that
every hit's `tenant_id` field (when present) matches the connection's
tenant_id.  If they disagree the call fails closed with a
`TenantMismatchError` — see doc 05 §Independent data-layer re-check.
"""

from typing import Any

import httpx
import structlog
from wolf_common.errors import TenantMismatchError, WolfError

from wolf_server.wazuh.config import WazuhConnection
from wolf_server.wazuh.query_builder import TenantScopedQueryBuilder

logger = structlog.get_logger(__name__)

_TIMEOUT_SECONDS = 30.0


class WazuhOpenSearchError(WolfError):
    """OpenSearch returned an unexpected response."""

    http_status = 502
    error_code = "wazuh_opensearch_error"


class WazuhOpenSearchClient:
    """Tenant-bound OpenSearch client for read-only alert queries."""

    def __init__(
        self,
        connection: WazuhConnection,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._connection = connection
        self._qb = TenantScopedQueryBuilder(
            connection.tenant_id,
            inject_tenant_filter=connection.inject_tenant_filter,
        )
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=connection.opensearch_url,
            auth=(connection.opensearch_username, connection.opensearch_password),
            verify=connection.verify_tls,
            timeout=_TIMEOUT_SECONDS,
        )

    @property
    def query_builder(self) -> TenantScopedQueryBuilder:
        """Expose the tenant-bound query builder — the only way to build queries."""
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
        self._assert_tenant_filter_present(query)
        index = self._connection.opensearch_index_pattern
        response = await self._client.post(f"/{index}/_search", json=query)
        if response.status_code >= 400:
            logger.warning(
                "wazuh_opensearch_http_error",
                status_code=response.status_code,
                tenant_id=str(self._connection.tenant_id),
            )
            raise WazuhOpenSearchError(
                f"OpenSearch returned {response.status_code}: {response.text[:200]}"
            )
        body: dict[str, Any] = response.json()
        self._assert_tenant_match(body)
        return body

    # ── Safety re-checks ───────────────────────────────────────────────────

    def _assert_tenant_filter_present(self, query: dict[str, Any]) -> None:
        """Sanity check: the query must contain the tenant filter clause.

        Defense in depth — the query builder always includes it (when
        injection is configured for this tenant), but a hand-crafted
        query slipping through would be rejected here.

        Skipped entirely when the tenant's WazuhConfig has
        `inject_tenant_filter=False` — in that mode the credential is
        the isolation boundary and no per-query filter is expected.
        """
        if not self._connection.inject_tenant_filter:
            return
        filters = query.get("query", {}).get("bool", {}).get("filter", [])
        expected = str(self._connection.tenant_id)
        for clause in filters:
            term = clause.get("term", {}) if isinstance(clause, dict) else {}
            if term.get("tenant_id") == expected:
                return
        raise TenantMismatchError(
            "OpenSearch query missing mandatory tenant_id filter — rejected"
        )

    def _assert_tenant_match(self, body: dict[str, Any]) -> None:
        """Verify every returned doc's tenant_id (if present) matches.

        Wazuh deployments that do not stamp a `tenant_id` on alerts will not
        trigger this check.  Deployments that do — and return a mismatched
        document — fail the request closed.
        """
        expected = str(self._connection.tenant_id)
        hits = body.get("hits", {}).get("hits", [])
        for hit in hits:
            source = hit.get("_source", {}) if isinstance(hit, dict) else {}
            doc_tenant = source.get("tenant_id")
            if doc_tenant is not None and str(doc_tenant) != expected:
                logger.error(
                    "tenant_mismatch_on_return",
                    expected=expected,
                    received=str(doc_tenant),
                )
                raise TenantMismatchError(
                    f"OpenSearch returned doc for tenant {doc_tenant!r}, expected {expected!r}"
                )
