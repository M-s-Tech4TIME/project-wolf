"""Install-level Wazuh ecosystem topology — shape models (Phase 6.6-a, ADR 0020).

The Superuser configures ONE install-wide Wazuh ecosystem (ADR 0020 decision 4:
one install = one Wazuh ecosystem).  Two deployment shapes are supported, per
Wazuh's own docs:

  - **single** — one host runs the indexer, manager (master, no workers) and
    dashboard.
  - **distributed** — an indexer cluster (1+ nodes), a manager cluster (master
    + N workers) and a dashboard host.

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


class IndexerNode(BaseModel):
    """One node of a distributed indexer cluster."""

    url: str
    cluster_name: str = Field(min_length=1, max_length=_MAX_NAME)

    _check_url = field_validator("url")(_validate_http_url)


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
    """Indexer cluster + manager cluster (master + workers) + dashboard."""

    kind: Literal["distributed"] = "distributed"
    indexer_nodes: list[IndexerNode] = Field(min_length=1)
    manager_master_url: str
    manager_worker_urls: list[str] = Field(default_factory=list)
    dashboard_url: str

    _check_urls = field_validator("manager_master_url", "dashboard_url")(_validate_http_url)

    @field_validator("manager_worker_urls")
    @classmethod
    def _check_worker_urls(cls, value: list[str]) -> list[str]:
        return [_validate_http_url(v) for v in value]


# Discriminated union — ``kind`` selects the shape on the way in and out.
WazuhTopology = Annotated[
    SingleHostTopology | DistributedTopology,
    Field(discriminator="kind"),
]

# Single shared adapter for parsing a stored topology document (the JSONB row)
# back into the typed discriminated union.  Used by the topology + credentials
# APIs so there is exactly one parser.
WAZUH_TOPOLOGY_ADAPTER: TypeAdapter[Any] = TypeAdapter(WazuhTopology)
