"""Wazuh manager deployment detection — single/all-in-one vs distributed cluster.

The operator directive behind 6-f.6: a configuration change must be applied
per the DEPLOYMENT TYPE — an all-in-one manager takes the change directly
(``PUT /manager/configuration``), while a distributed manager cluster needs it
applied to EVERY node, because **Wazuh's cluster sync does not replicate
ossec.conf** (it syncs rules/decoders/CDB-lists/agent-groups only).  Probed
live on the operator's 3-node cluster 2026-07-06: the nodes' ossec.conf files
genuinely diverge (master 15 318 B / 5 integrations; workers 13 488 B and
13 079 B / 4 each), ``GET /cluster/{node_id}/configuration?raw=true`` reads a
node's own file, ``PUT /cluster/{node_id}/configuration`` writes it
(``application/octet-stream``; RBAC ``cluster:update_config``), and ONE
``GET /cluster/configuration/validation`` validates all nodes with per-node
statuses.

Detection is deliberately a small, fresh, per-use read (never cached): the
deployment can change between propose and approve, and staleness there must
refuse honestly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Node names come from the manager's own /cluster/nodes inventory, but they are
# interpolated into request PATHS — validate defensively before any use.
_NODE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def is_valid_node_name(name: str) -> bool:
    """Syntactic validity of a cluster node name before it enters a URL path."""
    return bool(_NODE_NAME_RE.fullmatch(name or ""))


@dataclass(frozen=True)
class ManagerNode:
    """One manager-cluster node from ``GET /cluster/nodes``."""

    name: str
    node_type: str  # "master" | "worker"


async def get_cluster_nodes(read_api: Any) -> list[ManagerNode]:
    """The manager cluster's nodes (master FIRST), or ``[]`` for a
    single/all-in-one deployment.

    ``[]`` means ``GET /cluster/status`` reported the cluster not enabled or
    not running — the manager-scoped endpoints are then the correct (and only)
    apply path.  Transport/parse failures RAISE: the caller must refuse rather
    than silently fall back to a master-only write that would leave workers
    diverged (the exact gap 6-f.6 closes).
    """
    status = await read_api.get("/cluster/status")
    data = status.get("data", {}) if isinstance(status, dict) else {}
    if data.get("enabled") != "yes" or data.get("running") != "yes":
        return []
    inventory = await read_api.get("/cluster/nodes")
    items = (
        inventory.get("data", {}).get("affected_items", [])
        if isinstance(inventory, dict)
        else []
    )
    nodes = [
        ManagerNode(name=str(item.get("name", "")), node_type=str(item.get("type", "")))
        for item in items
        if isinstance(item, dict)
    ]
    bad = [n.name for n in nodes if not is_valid_node_name(n.name)]
    if bad or not nodes:
        raise ValueError(
            f"Cluster node inventory unusable (nodes={len(nodes)}, invalid names={bad!r})"
        )
    nodes.sort(key=lambda n: (n.node_type != "master", n.name))
    return nodes


def node_configuration_path(node_name: str) -> str:
    """The per-node configuration endpoint path, name-validated."""
    if not is_valid_node_name(node_name):
        raise ValueError(f"Invalid cluster node name {node_name!r}")
    return f"/cluster/{node_name}/configuration"
