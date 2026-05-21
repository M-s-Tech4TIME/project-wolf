"""Agent (fleet inventory) read tools — Wazuh Server API tier."""

from typing import Any

from pydantic import BaseModel, Field

from app.tools.base import Citation, ReadTool, ToolExecContext

# ─── Shared output types ──────────────────────────────────────────────────────


class AgentSummary(BaseModel):
    id: str
    name: str
    status: str
    os_platform: str | None = None
    os_version: str | None = None
    group: list[str] = Field(default_factory=list)
    last_keep_alive: str | None = None
    ip: str | None = None


class AgentDetail(BaseModel):
    id: str
    name: str
    status: str
    os: dict[str, Any] = Field(default_factory=dict)
    group: list[str] = Field(default_factory=list)
    last_keep_alive: str | None = None
    date_add: str | None = None
    version: str | None = None
    config_sum: str | None = None
    merged_sum: str | None = None
    sync_status: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict, description="Full Server API payload")


def _summarize_agent(data: dict[str, Any]) -> AgentSummary:
    os_data = data.get("os") or {}
    groups = data.get("group") or []
    if isinstance(groups, str):
        groups = [groups]
    return AgentSummary(
        id=str(data.get("id", "")),
        name=str(data.get("name", "")),
        status=str(data.get("status", "")),
        os_platform=os_data.get("platform"),
        os_version=os_data.get("version"),
        group=list(groups),
        last_keep_alive=data.get("lastKeepAlive"),
        ip=data.get("ip"),
    )


# ─── list_agents ──────────────────────────────────────────────────────────────


class ListAgentsInput(BaseModel):
    status: str | None = Field(default=None, description="active | disconnected | never_connected")
    group: str | None = Field(default=None, description="Filter to one group")
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class ListAgentsOutput(BaseModel):
    agents: list[AgentSummary]
    total: int
    citation: Citation


class ListAgentsTool(ReadTool):
    name = "list_agents"
    description = "Fleet inventory: agents with status, OS, group, last-seen."
    InputModel = ListAgentsInput
    OutputModel = ListAgentsOutput

    async def run(self, exec_ctx: ToolExecContext, args: BaseModel) -> BaseModel:
        assert isinstance(args, ListAgentsInput)
        params: dict[str, Any] = {"limit": args.limit, "offset": args.offset}
        if args.status:
            params["status"] = args.status
        if args.group:
            params["group"] = args.group

        body = await exec_ctx.server_api.get("/agents", params=params)
        data = body.get("data", {})
        items = data.get("affected_items", []) or []
        total = int(data.get("total_affected_items", len(items)))
        agents = [_summarize_agent(a) for a in items]

        return ListAgentsOutput(
            agents=agents,
            total=total,
            citation=self.make_citation(
                args.model_dump(mode="json"), result_count=len(agents)
            ),
        )


# ─── get_agent_detail ─────────────────────────────────────────────────────────


class GetAgentDetailInput(BaseModel):
    agent_id: str = Field(description="Wazuh agent ID, e.g. '001'")


class GetAgentDetailOutput(BaseModel):
    agent: AgentDetail
    citation: Citation


class GetAgentDetailTool(ReadTool):
    name = "get_agent_detail"
    description = "Deep detail on one agent — OS, group, sync state, version."
    InputModel = GetAgentDetailInput
    OutputModel = GetAgentDetailOutput

    async def run(self, exec_ctx: ToolExecContext, args: BaseModel) -> BaseModel:
        assert isinstance(args, GetAgentDetailInput)
        body = await exec_ctx.server_api.get("/agents", params={"agents_list": args.agent_id})
        data = body.get("data", {})
        items = data.get("affected_items", []) or []
        if not items:
            return GetAgentDetailOutput(
                agent=AgentDetail(id=args.agent_id, name="", status="not_found"),
                citation=self.make_citation(
                    args.model_dump(mode="json"), result_count=0
                ),
            )
        item = items[0]
        groups = item.get("group") or []
        if isinstance(groups, str):
            groups = [groups]
        agent = AgentDetail(
            id=str(item.get("id", args.agent_id)),
            name=str(item.get("name", "")),
            status=str(item.get("status", "")),
            os=item.get("os") or {},
            group=list(groups),
            last_keep_alive=item.get("lastKeepAlive"),
            date_add=item.get("dateAdd"),
            version=item.get("version"),
            config_sum=item.get("configSum"),
            merged_sum=item.get("mergedSum"),
            sync_status=item.get("syncStatus"),
            raw=item,
        )
        return GetAgentDetailOutput(
            agent=agent,
            citation=self.make_citation(
                args.model_dump(mode="json"), result_count=1
            ),
        )
