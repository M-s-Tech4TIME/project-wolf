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
    ACTION_CLUSTER_RESTART,
    ACTION_MODIFY_GROUP,
    ACTION_UPDATE_RULES,
    RESOURCE_LOCAL_RULES,
    RESOURCE_NODE_ANY,
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

    async def get_raw(self, path: str, *, params: dict[str, Any] | None = None) -> str:
        """Issue a read-only GET that returns the RAW response body as text.

        Some endpoints (``GET /rules/files/{name}?raw=true``) return the file's
        exact bytes (XML), not a JSON envelope — the rule_tuning snapshot needs
        them verbatim to restore later.  Still GET-only (the read-side contract
        is preserved); only the decoding differs from :meth:`get`.
        """
        if self._token is None:
            await self._authenticate()
        headers = {"Authorization": f"Bearer {self._token}"}
        response = await self._client.request("GET", path, params=params, headers=headers)
        if response.status_code == 401:
            await self._authenticate()
            headers = {"Authorization": f"Bearer {self._token}"}
            response = await self._client.request("GET", path, params=params, headers=headers)
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
        return response.text

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

    async def update_rules_file(
        self,
        *,
        filename: str,
        content: str,
        capabilities: CredentialCapabilities,
        relative_dirname: str = "etc/rules",
    ) -> dict[str, Any]:
        """Overwrite a custom rule file (``PUT /rules/files/{filename}``) — 6-e.3.

        Manager-GLOBAL: capability-checked against ``rules:update`` on
        ``rule:file:<filename>`` (the admin grants it on ``*:*:*``; a per-org
        credential holds it on nothing → :class:`WazuhActionNotPermittedError`,
        fail-closed, BEFORE any request).  The body is the RAW file content
        (``application/octet-stream``), not JSON; ``overwrite=true`` replaces the
        existing file.  Wazuh validates the XML syntax on upload and rejects a
        malformed file with a 4xx (surfaced as :class:`WazuhServerApiError`)."""
        if not capabilities.can(ACTION_UPDATE_RULES, f"rule:file:{filename}"):
            raise WazuhActionNotPermittedError(
                f"Credential is not authorized to update rule file {filename!r} "
                "(rules:update). Rule tuning is manager-global / Superuser-scoped."
            )
        return await self._write_raw(
            "PUT",
            f"/rules/files/{filename}",
            params={"overwrite": "true", "relative_dirname": relative_dirname},
            body=content.encode("utf-8"),
        )

    async def restart_cluster(self, *, capabilities: CredentialCapabilities) -> dict[str, Any]:
        """Restart the manager cluster (``PUT /cluster/restart``) to load a changed
        ruleset — 6-e.3.  Capability-checked against ``cluster:restart`` (admin
        grants it on ``*:*:*``).  Returns once the restart is *issued* (Wazuh
        schedules it across nodes); it does not block until the cluster is back."""
        if not capabilities.can(ACTION_CLUSTER_RESTART, RESOURCE_NODE_ANY):
            raise WazuhActionNotPermittedError(
                "Credential is not authorized to restart the cluster (cluster:restart)."
            )
        return await self._write("PUT", "/cluster/restart")

    def require_update_rules(self, capabilities: CredentialCapabilities) -> None:
        """Pre-flight the rule_tuning write surface (``rules:update`` on
        local_rules.xml) — raises :class:`WazuhActionNotPermittedError` if the
        credential lacks it.  Used by the executor's freshness gate."""
        if not capabilities.can(ACTION_UPDATE_RULES, RESOURCE_LOCAL_RULES):
            raise WazuhActionNotPermittedError(
                "Credential is not authorized to update rules (rules:update). "
                "Rule tuning is manager-global / Superuser-scoped."
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

    async def _write_raw(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: bytes,
    ) -> dict[str, Any]:
        """Like :meth:`_write` but sends a RAW body (file upload), not JSON."""
        headers = {"Content-Type": "application/octet-stream"}
        if self._token is None:
            await self._authenticate()
        auth_headers = {**headers, "Authorization": f"Bearer {self._token}"}
        response = await self._client.request(method, path, params=params, content=body,
                                              headers=auth_headers)
        if response.status_code == 401:
            await self._authenticate()
            auth_headers = {**headers, "Authorization": f"Bearer {self._token}"}
            response = await self._client.request(method, path, params=params, content=body,
                                                  headers=auth_headers)
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
        result: dict[str, Any] = response.json()
        return result

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
