"""Reusable Wazuh endpoint probes — Phase 6.6, ADR 0020.

Each function authenticates against (or simply reaches) a single Wazuh
endpoint and returns a structured :class:`EndpointProbeResult`.  A probe
**never raises** on an auth/HTTP/transport failure — it captures the outcome
in the result so the *caller* decides whether a given failure is a hard block
(install-level topology save, ADR 0020 decision 3 → hard-fail) or a soft
warning (per-org credentials save → soft-fail).  Probes only raise on
programmer error.

Probe shapes mirror the long-proven
``bootstrap_organization._validate_wazuh_connection`` so behaviour is
identical to the validated CLI bootstrap path:

  - **Indexer (OpenSearch)** — ``GET /`` with basic auth.  200 = ok; 403 is
    tolerated (the cluster-monitor permission is often not granted to a
    read-only role — the credential still *authenticated*, which is the
    property we check); 401 = bad credentials.
  - **Manager (Server API)** — ``POST /security/user/authenticate`` with basic
    auth.  200 = ok (Wazuh issues a JWT); 401 = bad credentials.  Note the
    Server API has its OWN user database, separate from the Indexer.
  - **Dashboard** — unauthenticated ``GET`` of the configured URL.  The
    dashboard has no Wolf-held credentials (it is a link target); "reachable
    and not a 5xx" is the contract.  Redirects to a login page (3xx) and the
    login page itself (200) both count as reachable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpx

# Matches bootstrap_organization._validate_wazuh_connection.
_TIMEOUT = httpx.Timeout(connect=10.0, read=15.0, write=10.0, pool=10.0)

ProbeRole = Literal["indexer", "manager", "dashboard"]


@dataclass(frozen=True)
class EndpointProbeResult:
    """Outcome of probing a single Wazuh endpoint.

    ``ok`` is the only field a caller must branch on; ``detail`` is a
    single-sentence, operator-facing explanation suitable for surfacing in the
    UI verbatim, and ``status_code`` is populated when the endpoint answered
    (None on a transport failure).
    """

    role: ProbeRole
    url: str
    ok: bool
    detail: str
    status_code: int | None = None


def _client(verify_tls: bool) -> httpx.AsyncClient:
    return httpx.AsyncClient(verify=verify_tls, timeout=_TIMEOUT)


async def probe_indexer(
    url: str,
    username: str,
    password: str,
    *,
    verify_tls: bool,
    client: httpx.AsyncClient | None = None,
) -> EndpointProbeResult:
    """Probe a Wazuh Indexer (OpenSearch) endpoint with basic auth."""
    owns_client = client is None
    client = client or _client(verify_tls)
    try:
        try:
            response = await client.get(url.rstrip("/") + "/", auth=(username, password))
        except httpx.RequestError as exc:
            return EndpointProbeResult(
                role="indexer",
                url=url,
                ok=False,
                detail=f"Indexer at {url} is unreachable: {type(exc).__name__}: {exc}",
            )
        if response.status_code == 401:
            return EndpointProbeResult(
                role="indexer",
                url=url,
                ok=False,
                status_code=401,
                detail=(
                    f"Indexer at {url} rejected the credentials (HTTP 401). "
                    f"Verify the user exists in the OpenSearch security plugin "
                    f"and the password is correct."
                ),
            )
        if response.status_code not in (200, 403):
            return EndpointProbeResult(
                role="indexer",
                url=url,
                ok=False,
                status_code=response.status_code,
                detail=(
                    f"Indexer at {url} returned unexpected HTTP "
                    f"{response.status_code}; expected 200 or 403."
                ),
            )
        return EndpointProbeResult(
            role="indexer",
            url=url,
            ok=True,
            status_code=response.status_code,
            detail=f"Indexer at {url} authenticated (HTTP {response.status_code}).",
        )
    finally:
        if owns_client:
            await client.aclose()


async def probe_manager_api(
    url: str,
    username: str,
    password: str,
    *,
    verify_tls: bool,
    client: httpx.AsyncClient | None = None,
) -> EndpointProbeResult:
    """Probe a Wazuh Server API endpoint via ``/security/user/authenticate``."""
    owns_client = client is None
    client = client or _client(verify_tls)
    try:
        try:
            response = await client.post(
                url.rstrip("/") + "/security/user/authenticate",
                auth=(username, password),
            )
        except httpx.RequestError as exc:
            return EndpointProbeResult(
                role="manager",
                url=url,
                ok=False,
                detail=f"Server API at {url} is unreachable: {type(exc).__name__}: {exc}",
            )
        if response.status_code == 401:
            return EndpointProbeResult(
                role="manager",
                url=url,
                ok=False,
                status_code=401,
                detail=(
                    f"Server API at {url} rejected the credentials (HTTP 401). "
                    f"Note: the Wazuh Server API has its OWN user database, "
                    f"separate from the Indexer — the Indexer user may not exist "
                    f"on the Server API (e.g. 'admin' vs 'wazuh-wui')."
                ),
            )
        if response.status_code != 200:
            return EndpointProbeResult(
                role="manager",
                url=url,
                ok=False,
                status_code=response.status_code,
                detail=(
                    f"Server API at {url} returned unexpected HTTP "
                    f"{response.status_code}; expected 200."
                ),
            )
        return EndpointProbeResult(
            role="manager",
            url=url,
            ok=True,
            status_code=200,
            detail=f"Server API at {url} authenticated (HTTP 200).",
        )
    finally:
        if owns_client:
            await client.aclose()


async def probe_dashboard(
    url: str,
    *,
    verify_tls: bool,
    client: httpx.AsyncClient | None = None,
) -> EndpointProbeResult:
    """Probe a Wazuh Dashboard URL — reachable and not a 5xx (no credentials)."""
    owns_client = client is None
    client = client or _client(verify_tls)
    try:
        try:
            response = await client.get(url, follow_redirects=False)
        except httpx.RequestError as exc:
            return EndpointProbeResult(
                role="dashboard",
                url=url,
                ok=False,
                detail=f"Dashboard at {url} is unreachable: {type(exc).__name__}: {exc}",
            )
        if response.status_code >= 500:
            return EndpointProbeResult(
                role="dashboard",
                url=url,
                ok=False,
                status_code=response.status_code,
                detail=(
                    f"Dashboard at {url} returned HTTP {response.status_code}; "
                    f"the host is reachable but the service is erroring."
                ),
            )
        return EndpointProbeResult(
            role="dashboard",
            url=url,
            ok=True,
            status_code=response.status_code,
            detail=f"Dashboard at {url} is reachable (HTTP {response.status_code}).",
        )
    finally:
        if owns_client:
            await client.aclose()
