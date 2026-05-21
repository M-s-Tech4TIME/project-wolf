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
    enabled: bool
    running: bool
    nodes: list[NodeHealth]
    indexer_status: str | None = None
    citation: Citation
    raw: dict[str, Any] = Field(default_factory=dict)


class GetClusterHealthTool(ReadTool):
    name = "get_cluster_health"
    description = "Manager / cluster node status and indexer health."
    InputModel = GetClusterHealthInput
    OutputModel = GetClusterHealthOutput

    async def run(self, exec_ctx: ToolExecContext, args: BaseModel) -> BaseModel:
        # Status endpoint
        status_body = await exec_ctx.server_api.get("/cluster/status")
        status_data = status_body.get("data", {})
        enabled = bool(status_data.get("enabled") == "yes")
        running = bool(status_data.get("running") == "yes")

        nodes: list[NodeHealth] = []
        if enabled:
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

        return GetClusterHealthOutput(
            enabled=enabled,
            running=running,
            nodes=nodes,
            indexer_status=status_data.get("indexer"),
            citation=self.make_citation(
                args.model_dump(mode="json"), result_count=len(nodes)
            ),
            raw=status_body,
        )
