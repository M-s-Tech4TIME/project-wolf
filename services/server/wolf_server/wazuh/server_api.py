"""Wazuh Server API HTTP client — read-only endpoints.

The Server API is the introspection surface for Wazuh: fleet inventory, rule
definitions, decoders, cluster health, SCA results.  This client uses ONLY
GET endpoints.  Any attempt to issue a non-GET request is rejected at the
method boundary — defense against a future code change that might forget
this is the read-side client.

Authentication: the Server API issues short-lived JWTs in exchange for
username/password.  This client transparently refreshes the JWT on 401.
"""

from typing import Any

import httpx
import structlog
from wolf_common.errors import WolfError

from wolf_server.wazuh.config import WazuhConnection

logger = structlog.get_logger(__name__)

_TIMEOUT_SECONDS = 30.0


class WazuhServerApiError(WolfError):
    """Server API returned an unexpected response."""

    http_status = 502
    error_code = "wazuh_server_api_error"


class WazuhServerApiClient:
    """Organization-bound, read-only Wazuh Server API client."""

    def __init__(
        self,
        connection: WazuhConnection,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._connection = connection
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=connection.server_api_url,
            verify=connection.verify_tls,
            timeout=_TIMEOUT_SECONDS,
        )
        self._token: str | None = None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "WazuhServerApiClient":
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.aclose()

    # ── Public GETs ───────────────────────────────────────────────────────

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Issue a read-only GET to the Server API.

        Only GET is exposed — there is no `post`, `put`, or `delete` method
        on this client.  Mutations to Wazuh state happen only through the
        Approval Gateway (doc 03 §The execute boundary).
        """
        return await self._request("GET", path, params=params)

    # ── Internals ─────────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if method != "GET":
            # Belt-and-braces: only GET should ever be called on this client.
            raise WazuhServerApiError(f"WazuhServerApiClient is read-only; {method} not permitted")

        if self._token is None:
            await self._authenticate()

        headers = {"Authorization": f"Bearer {self._token}"}
        response = await self._client.request(method, path, params=params, headers=headers)

        if response.status_code == 401:
            # Token expired or revoked — re-auth once and retry.
            await self._authenticate()
            headers = {"Authorization": f"Bearer {self._token}"}
            response = await self._client.request(method, path, params=params, headers=headers)

        if response.status_code >= 400:
            logger.warning(
                "wazuh_server_api_http_error",
                status_code=response.status_code,
                path=path,
                organization_id=str(self._connection.organization_id),
            )
            raise WazuhServerApiError(
                f"Server API returned {response.status_code}: {response.text[:200]}"
            )

        body: dict[str, Any] = response.json()
        return body

    async def _authenticate(self) -> None:
        """Exchange username/password for a short-lived JWT."""
        response = await self._client.post(
            "/security/user/authenticate",
            auth=(
                self._connection.server_api_username,
                self._connection.server_api_password,
            ),
        )
        if response.status_code >= 400:
            raise WazuhServerApiError(f"Server API authentication failed: {response.status_code}")
        token = response.json().get("data", {}).get("token")
        if not token:
            raise WazuhServerApiError("Server API auth response missing token")
        self._token = str(token)
