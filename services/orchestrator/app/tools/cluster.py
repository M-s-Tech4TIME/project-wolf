"""Cluster health read tool — Wazuh Server API tier."""

from typing import Any

from pydantic import BaseModel, Field

from app.tools.base import Citation, ReadTool, ToolExecContext


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
    cluster_enabled: bool = Field(
        description=(
            "Whether multi-node clustering is configured. FALSE is normal "
            "for a single-host Wazuh setup and does NOT mean the manager "
            "is down."
        ),
    )
    cluster_running: bool = Field(
        description=(
            "Whether the cluster daemon is running. Only meaningful when "
            "cluster_enabled is TRUE."
        ),
    )
    manager_healthy: bool = Field(
        description=(
            "Whether the Wazuh manager itself responded successfully to "
            "this status query. TRUE means the manager is up and serving "
            "the API, regardless of clustering configuration."
        ),
    )
    summary: str = Field(
        description="Plain-language summary safe to quote in an answer.",
    )
    nodes: list[NodeHealth]
    indexer_status: str | None = None
    citation: Citation
    raw: dict[str, Any] = Field(default_factory=dict)


class GetClusterHealthTool(ReadTool):
    name = "get_cluster_health"
    description = (
        "Manager and cluster node status. Note: in a single-host Wazuh "
        "setup, cluster_enabled is FALSE by design — clustering only "
        "applies when multiple managers are joined. The manager_healthy "
        "field is the right signal for 'is Wazuh working?'."
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
            summary = "Manager API did not respond — Wazuh appears to be down."
        elif not cluster_enabled:
            summary = (
                "Single-host setup: clustering is disabled by design, and "
                "the Wazuh manager itself is running and reachable."
            )
        elif cluster_running and nodes:
            summary = (
                f"Multi-node cluster is enabled and running with "
                f"{len(nodes)} node(s)."
            )
        else:
            summary = (
                "Clustering is enabled but the cluster daemon is not "
                "running — investigate."
            )

        return GetClusterHealthOutput(
            cluster_enabled=cluster_enabled,
            cluster_running=cluster_running,
            manager_healthy=manager_healthy,
            summary=summary,
            nodes=nodes,
            indexer_status=status_data.get("indexer"),
            citation=self.make_citation(
                args.model_dump(mode="json"), result_count=len(nodes)
            ),
            raw=status_body,
        )
