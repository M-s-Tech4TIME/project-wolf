"""Install-level Wazuh ecosystem topology — shape models (Phase 6.6-a, ADR 0020).

The Superuser configures ONE install-wide Wazuh ecosystem (ADR 0020 decision 4:
one install = one Wazuh ecosystem).  Two deployment shapes are supported, per
Wazuh's own docs:

  - **single** — one host runs the indexer, manager (master, no workers) and
    dashboard.
  - **distributed** — an indexer cluster (1+ nodes), a manager cluster (master
    + N workers) and 1+ dashboard hosts.

Distributed nodes carry an OPTIONAL human-friendly ``name`` label (operator
feedback 2026-06-17, refining ADR 0020 — the original per-indexer-node
``cluster_name`` was required + indexer-only; it is now an optional label on
every component, and a cluster may declare multiple dashboards).

These pydantic models are the single source of truth for the *structural* shape
(URLs + cluster membership).  They are shared by the API request/response layer
and by the JSON document persisted in ``wazuh_ecosystem_topology.topology`` — so
shape validation lives in exactly one place.  Credentials are NOT part of the
topology document; they are split out to the secrets backend (ADR 0020
decision 7).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, field_validator

_MAX_URL = 500
_MAX_NAME = 200


def _validate_http_url(value: str) -> str:
    """Bound length and require an http(s) scheme; reject obvious garbage.

    Kept deliberately permissive on host shape (self-hosted Wazuh commonly
    uses bare hostnames, IPs, and non-standard ports) — we validate scheme +
    length here and let the probe-on-save prove actual reachability.
    """
    stripped = value.strip()
    if not stripped:
        raise ValueError("URL must not be empty")
    if len(stripped) > _MAX_URL:
        raise ValueError(f"URL must be at most {_MAX_URL} characters")
    if not (stripped.startswith("http://") or stripped.startswith("https://")):
        raise ValueError("URL must start with http:// or https://")
    return stripped


class WazuhNode(BaseModel):
    """One addressable Wazuh component in a distributed cluster.

    ``name`` is an OPTIONAL human-friendly label (e.g. "indexer-eu-1",
    "master", "dashboard-dr") — surfaced per-component in the UI ("Indexer
    name", "Master node name", "Worker node name", "Dashboard name"). It is
    metadata only; routing/probing use ``url``.
    """

    url: str
    name: str | None = Field(default=None, max_length=_MAX_NAME)

    _check_url = field_validator("url")(_validate_http_url)

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class SingleHostTopology(BaseModel):
    """All Wazuh components on one host."""

    kind: Literal["single"] = "single"
    indexer_url: str
    manager_url: str
    dashboard_url: str

    _check_urls = field_validator("indexer_url", "manager_url", "dashboard_url")(
        _validate_http_url
    )


class DistributedTopology(BaseModel):
    """Indexer cluster + manager cluster (master + workers) + 1+ dashboards.

    Every component is a :class:`WazuhNode` (url + optional name). At least one
    indexer node and at least one dashboard are required; workers are optional
    (0+). A worker that fails its probe is a warning, not a save blocker;
    indexer nodes, the master and every dashboard are blockers.
    """

    kind: Literal["distributed"] = "distributed"
    indexer_nodes: list[WazuhNode] = Field(min_length=1)
    manager_master: WazuhNode
    manager_workers: list[WazuhNode] = Field(default_factory=list)
    dashboards: list[WazuhNode] = Field(min_length=1)


# Discriminated union — ``kind`` selects the shape on the way in and out.
WazuhTopology = Annotated[
    SingleHostTopology | DistributedTopology,
    Field(discriminator="kind"),
]

# Single shared adapter for parsing a stored topology document (the JSONB row)
# back into the typed discriminated union.  Used by the topology + credentials
# APIs so there is exactly one parser.
WAZUH_TOPOLOGY_ADAPTER: TypeAdapter[Any] = TypeAdapter(WazuhTopology)
