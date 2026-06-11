"""Cluster health read tool — Wazuh Server API tier."""

from typing import Any

from pydantic import BaseModel, Field

from wolf_server.tools.base import Citation, ReadTool, ToolExecContext


class GetClusterHealthInput(BaseModel):
    """No arguments — returns the current cluster health snapshot."""

    pass  # noqa: PIE790 — explicit no-args


class NodeHealth(BaseModel):
    name: str
    type: str | None = None
    version: str | None = None
    ip: str | None = None
    status: str | None = None


class GetClusterHealthOutput(BaseModel):
    manager_healthy: bool = Field(
        description=(
            "Whether the Wazuh manager API responded successfully. TRUE = "
            "the manager is running and serving requests. This is "
            "independent of clustering: a standalone manager can be "
            "healthy without any cluster configured."
        ),
    )
    cluster_enabled: bool = Field(
        description=(
            "Whether multi-node clustering is configured. FALSE means "
            "this is a standalone deployment (no other server/indexer "
            "nodes are joined). Whether that is intentional or a problem "
            "depends on the operator's deployment plan."
        ),
    )
    cluster_running: bool = Field(
        description=(
            "Whether the cluster daemon is running. Only meaningful when cluster_enabled is TRUE."
        ),
    )
    summary: str = Field(
        description=(
            "One-sentence factual summary. States the manager status and "
            "the clustering status without editorialising about whether "
            "either is good or bad."
        ),
    )
    nodes: list[NodeHealth]
    indexer_status: str | None = None
    citation: Citation
    raw: dict[str, Any] = Field(default_factory=dict)


class GetClusterHealthTool(ReadTool):
    name = "get_cluster_health"
    description = (
        "Returns Wazuh manager status and clustering state. The "
        "`manager_healthy` field answers 'is the manager up'. The "
        "`cluster_enabled` / `cluster_running` / `nodes` fields describe "
        "the clustering picture: a standalone manager has "
        "cluster_enabled=false with zero nodes, which is a deployment "
        "choice, not necessarily a fault. Report both pieces if the user "
        "asks about cluster health."
    )
    InputModel = GetClusterHealthInput
    OutputModel = GetClusterHealthOutput

    async def run(self, exec_ctx: ToolExecContext, args: BaseModel) -> BaseModel:
        # Status endpoint
        manager_healthy = True
        try:
            status_body = await exec_ctx.server_api.get("/cluster/status")
        except Exception:
            manager_healthy = False
            status_body = {}

        status_data = status_body.get("data", {})
        cluster_enabled = bool(status_data.get("enabled") == "yes")
        cluster_running = bool(status_data.get("running") == "yes")

        nodes: list[NodeHealth] = []
        if cluster_enabled:
            nodes_body = await exec_ctx.server_api.get("/cluster/nodes")
            items = nodes_body.get("data", {}).get("affected_items", []) or []
            nodes = [
                NodeHealth(
                    name=str(n.get("name", "")),
                    type=n.get("type"),
                    version=n.get("version"),
                    ip=n.get("ip"),
                    status=n.get("status"),
                )
                for n in items
            ]

        if not manager_healthy:
            summary = "Wazuh manager API did not respond."
        elif not cluster_enabled:
            summary = (
                "Wazuh manager is running. Clustering is not enabled — this "
                "is a standalone deployment with no other server or indexer "
                "nodes joined."
            )
        elif cluster_running and nodes:
            summary = (
                f"Wazuh manager is running. Clustering is enabled with {len(nodes)} node(s) joined."
            )
        else:
            summary = (
                "Wazuh manager is running, but clustering is enabled and "
                "the cluster daemon is not running."
            )

        return GetClusterHealthOutput(
            manager_healthy=manager_healthy,
            cluster_enabled=cluster_enabled,
            cluster_running=cluster_running,
            summary=summary,
            nodes=nodes,
            indexer_status=status_data.get("indexer"),
            citation=self.make_citation(args.model_dump(mode="json"), result_count=len(nodes)),
            raw=status_body,
        )
