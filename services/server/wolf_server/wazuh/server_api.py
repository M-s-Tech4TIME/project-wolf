"""Wazuh Server API HTTP client — read-only endpoints.

The Server API is the introspection surface for Wazuh: fleet inventory, rule
definitions, decoders, cluster health, SCA results.  This client uses ONLY
GET endpoints.  Any attempt to issue a non-GET request is rejected at the
method boundary — defense against a future code change that might forget
this is the read-side client.

Authentication: the Server API issues short-lived JWTs in exchange for
username/password.  This client transparently refreshes the JWT on 401.
"""

from collections.abc import Sequence
from typing import Any

import httpx
import structlog
from wolf_common.errors import WolfError

from wolf_server.wazuh.active_response import build_ar_body
from wolf_server.wazuh.capabilities import (
    ACTION_ACTIVE_RESPONSE,
    ACTION_MODIFY_GROUP,
    CredentialCapabilities,
)
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


class WazuhActionNotPermittedError(WolfError):
    """The credential's RBAC does not permit the requested write action."""

    http_status = 403
    error_code = "wazuh_action_not_permitted"


class WazuhServerApiActionClient:
    """Org-bound Wazuh Server API client with a DELIBERATE, bounded WRITE surface.

    Phase 6 (ADR 0025) — the capability-driven counterpart to the read-only
    :class:`WazuhServerApiClient`, which is kept exactly as-is.  This client
    exposes ONLY a whitelist of named write actions, each **capability-checked
    against the credential's pre-flighted RBAC** before issuing.  It is invoked
    only by ``wolf_server.gateway.execution`` (never by the model, never on the
    read path) — there is no generic ``post``/``put``/``delete`` method, so a
    future code change can't accidentally widen the write surface.
    """

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

    async def __aenter__(self) -> "WazuhServerApiActionClient":
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.aclose()

    # ── Whitelisted write actions ──────────────────────────────────────────

    async def execute_active_response(
        self,
        *,
        agent_id: str,
        command: str,
        capabilities: CredentialCapabilities,
        agent_groups: Sequence[str],
        srcip: str | None = None,
        username: str | None = None,
        arguments: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run an active-response command on one resolved agent.

        Capability-checked the way Wazuh RBAC evaluates it: the credential must
        be allowed ``active-response:command`` on ``agent:id:<agent_id>`` OR on
        ``agent:group:<g>`` for any group the agent belongs to (``agent_groups``,
        resolved fresh by the caller) — else :class:`WazuhActionNotPermittedError`
        (fail-closed) BEFORE any request.  A per-org credential grants by group,
        so the group expansion is what makes a real per-org write reachable.

        The body is built by :func:`build_ar_body` to match what Wazuh 4.x
        accepts: ``!``-prefixed command (run it now), the target in
        ``alert.data.srcip`` / ``alert.data.dstuser``, no ``custom``/``timeout``
        (rejected by the API).
        """
        if not capabilities.can_on_agent(ACTION_ACTIVE_RESPONSE, agent_id, agent_groups):
            groups = ", ".join(agent_groups) or "none"
            raise WazuhActionNotPermittedError(
                f"Credential is not authorized for active-response on agent {agent_id} "
                f"(groups: {groups})."
            )
        body = build_ar_body(command, srcip=srcip, username=username, arguments=arguments)
        return await self._write(
            "PUT", "/active-response", params={"agents_list": agent_id}, json_body=body
        )

    async def assign_agent_group(
        self,
        *,
        agent_id: str,
        group: str,
        capabilities: CredentialCapabilities,
        agent_groups: Sequence[str],
    ) -> dict[str, Any]:
        """Add an agent to a group (``PUT /agents/{id}/group/{group}``) — 6-e.2.

        Capability-checked exactly as Wazuh RBAC evaluates it: the credential must
        be allowed ``agent:modify_group`` on ``agent:id:<id>`` OR on
        ``agent:group:<g>`` for any group the agent is currently in — else
        :class:`WazuhActionNotPermittedError` (fail-closed) BEFORE any request.
        """
        self._require_modify_group(agent_id, capabilities, agent_groups)
        return await self._write("PUT", f"/agents/{agent_id}/group/{group}")

    async def remove_agent_group(
        self,
        *,
        agent_id: str,
        group: str,
        capabilities: CredentialCapabilities,
        agent_groups: Sequence[str],
    ) -> dict[str, Any]:
        """Remove an agent from a group (``DELETE /agents/{id}/group/{group}``) —
        6-e.2; the exact inverse of :meth:`assign_agent_group`.  Same
        capability check (``agent:modify_group``)."""
        self._require_modify_group(agent_id, capabilities, agent_groups)
        return await self._write("DELETE", f"/agents/{agent_id}/group/{group}")

    def _require_modify_group(
        self, agent_id: str, capabilities: CredentialCapabilities, agent_groups: Sequence[str]
    ) -> None:
        if not capabilities.can_on_agent(ACTION_MODIFY_GROUP, agent_id, agent_groups):
            groups = ", ".join(agent_groups) or "none"
            raise WazuhActionNotPermittedError(
                f"Credential is not authorized to modify groups on agent {agent_id} "
                f"(groups: {groups})."
            )

    # ── Internals ──────────────────────────────────────────────────────────

    async def _write(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._token is None:
            await self._authenticate()
        headers = {"Authorization": f"Bearer {self._token}"}
        response = await self._client.request(
            method, path, params=params, json=json_body, headers=headers
        )
        if response.status_code == 401:
            await self._authenticate()
            headers = {"Authorization": f"Bearer {self._token}"}
            response = await self._client.request(
                method, path, params=params, json=json_body, headers=headers
            )
        if response.status_code >= 400:
            logger.warning(
                "wazuh_action_http_error",
                status_code=response.status_code,
                path=path,
                organization_id=str(self._connection.organization_id),
            )
            raise WazuhServerApiError(
                f"Server API write returned {response.status_code}: {response.text[:200]}"
            )
        body: dict[str, Any] = response.json()
        return body

    async def _authenticate(self) -> None:
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
