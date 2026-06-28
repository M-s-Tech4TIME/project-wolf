"""`list_active_blocks` — Wolf's ledger of dispatched, not-yet-reversed blocks.

Slice 6-d (ADR 0028).  Reads the org's ``action_proposals`` for *succeeded*
block actions that have not been reversed, so the model can answer "what is
currently blocked?" and recall *why* each block was made when a user asks to
unblock.

HONEST SCOPE: this is **Wolf's own record of what it dispatched**, not live host
state.  Wolf cannot confirm an IP is still blocked on the host until wolf-pack
(Phase 12).  The description + output say so, so the model never overclaims.
"""

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from wolf_server.gateway.proposals import list_active_blocks
from wolf_server.tools.base import Citation, ReadTool, ToolExecContext

_ACTION_CLASS = "active_response"


class ListActiveBlocksInput(BaseModel):
    limit: int = Field(default=100, ge=1, le=200)


class ActiveBlock(BaseModel):
    proposal_id: str
    agent_id: str
    target: str = Field(description="The blocked IP or disabled username")
    target_kind: str = Field(description="'srcip' or 'username'")
    command: str = Field(description="The active-response command used")
    reason: str = Field(description="Why it was blocked (the original rationale)")
    blocked_at: str | None = Field(default=None, description="When it was dispatched (UTC)")
    auto_unblock_at: str | None = Field(
        default=None, description="When Wolf will auto-reverse a timed block (UTC), if any"
    )


class ListActiveBlocksOutput(BaseModel):
    blocks: list[ActiveBlock]
    note: str = Field(
        default=(
            "Wolf's record of blocks it dispatched and has not reversed — NOT a live "
            "host check. Confirming an IP is still blocked on the host needs wolf-pack."
        )
    )
    citation: Citation


class ListActiveBlocksTool(ReadTool):
    """List the IPs/accounts Wolf has blocked (and not reversed) for this org."""

    name: ClassVar[str] = "list_active_blocks"
    description: ClassVar[str] = (
        "List the source IPs / accounts Wolf has BLOCKED for this organization and "
        "not yet reversed — its own dispatch ledger, with the reason each was "
        "blocked. Use it to answer 'what's currently blocked?' and to recall why a "
        "block was made before unblocking. NOTE: this is Wolf's record of what it "
        "dispatched, not a live host check."
    )
    InputModel: ClassVar[type[BaseModel]] = ListActiveBlocksInput
    OutputModel: ClassVar[type[BaseModel]] = ListActiveBlocksOutput

    async def run(
        self, exec_ctx: ToolExecContext, args: BaseModel
    ) -> ListActiveBlocksOutput:
        assert isinstance(args, ListActiveBlocksInput)  # noqa: S101 — validated by dispatcher
        query: dict[str, Any] = {"limit": args.limit}
        if exec_ctx.db is None:  # pragma: no cover — always wired in the live path
            return ListActiveBlocksOutput(
                blocks=[], citation=self.make_citation(query, result_count=0)
            )
        ctx = exec_ctx.organization
        rows = await list_active_blocks(
            exec_ctx.db,
            organization_id=ctx.organization_id,
            action_class=_ACTION_CLASS,
            limit=args.limit,
        )
        blocks: list[ActiveBlock] = []
        for p in rows:
            params = p.parameters if isinstance(p.parameters, dict) else {}
            srcip = params.get("srcip")
            username = params.get("username")
            target = str(srcip or username or "")
            target_kind = "srcip" if srcip else "username" if username else "none"
            when = p.executed_at or p.created_at
            blocks.append(
                ActiveBlock(
                    proposal_id=str(p.id),
                    agent_id=str(p.target.get("agent_id", "")),
                    target=target,
                    target_kind=target_kind,
                    command=p.action,
                    reason=p.rationale,
                    blocked_at=when.isoformat() if when else None,
                    auto_unblock_at=p.auto_unblock_at.isoformat() if p.auto_unblock_at else None,
                )
            )
        return ListActiveBlocksOutput(
            blocks=blocks, citation=self.make_citation(query, result_count=len(blocks))
        )
