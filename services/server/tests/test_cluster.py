"""Wazuh deployment detection — cluster.py (6-f.6).

get_cluster_nodes reads GET /cluster/status + /cluster/nodes: [] for an
all-in-one manager (status not enabled/running), the master-first node list for
a distributed cluster, and RAISES on an unusable inventory (never a silent
master-only fallback). Node names are path-validated before any URL use.
"""

import pytest
from wolf_server.wazuh.cluster import (
    get_cluster_nodes,
    is_valid_node_name,
    node_configuration_path,
)


class _ReadApi:
    def __init__(self, status: dict, nodes: dict) -> None:
        self._status = status
        self._nodes = nodes

    async def get(self, path: str, *, params=None) -> dict:
        if path == "/cluster/status":
            return self._status
        if path == "/cluster/nodes":
            return self._nodes
        return {"data": {}}


def _status(enabled: str, running: str) -> dict:
    return {"data": {"enabled": enabled, "running": running}}


def _nodes(*items: tuple[str, str]) -> dict:
    return {"data": {"affected_items": [{"name": n, "type": t} for n, t in items]}}


@pytest.mark.asyncio
async def test_all_in_one_returns_empty() -> None:
    api = _ReadApi(_status("no", "no"), _nodes())
    assert await get_cluster_nodes(api) == []


@pytest.mark.asyncio
async def test_enabled_but_not_running_returns_empty() -> None:
    api = _ReadApi(_status("yes", "no"), _nodes(("m", "master")))
    assert await get_cluster_nodes(api) == []


@pytest.mark.asyncio
async def test_distributed_returns_nodes_master_first() -> None:
    api = _ReadApi(
        _status("yes", "yes"),
        _nodes(
            ("worker-node-2", "worker"),
            ("wazuh-master-node", "master"),
            ("worker-node-1", "worker"),
        ),
    )
    nodes = await get_cluster_nodes(api)
    assert [n.name for n in nodes] == ["wazuh-master-node", "worker-node-1", "worker-node-2"]
    assert nodes[0].node_type == "master"


@pytest.mark.asyncio
async def test_empty_inventory_while_clustered_raises() -> None:
    api = _ReadApi(_status("yes", "yes"), _nodes())
    with pytest.raises(ValueError, match="unusable"):
        await get_cluster_nodes(api)


@pytest.mark.asyncio
async def test_invalid_node_name_raises() -> None:
    api = _ReadApi(_status("yes", "yes"), _nodes(("../evil", "master")))
    with pytest.raises(ValueError, match="unusable"):
        await get_cluster_nodes(api)


def test_valid_node_names() -> None:
    assert is_valid_node_name("wazuh-master-node")
    assert is_valid_node_name("worker_1.node")
    assert not is_valid_node_name("../evil")
    assert not is_valid_node_name("a/b")
    assert not is_valid_node_name("")
    assert not is_valid_node_name("has space")


def test_node_configuration_path_validates() -> None:
    assert node_configuration_path("worker-node-1") == "/cluster/worker-node-1/configuration"
    with pytest.raises(ValueError, match="Invalid cluster node name"):
        node_configuration_path("../etc")
