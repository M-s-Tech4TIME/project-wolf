"""`propose_active_response` — the first propose-tier tool (Phase 6, ADR 0025).

The model calls this to PROPOSE an active-response command on a resolved agent.
It changes nothing itself: it runs the structural action validator + a
capability pre-flight (the credential must be RBAC-allowed to run AR on the
target), then persists a *pending* proposal into the approval queue and returns
a summary.  A human with ``ACTION_APPROVE`` later approves it, and only then
does the gateway execute it (doc 04).

Target resolution happens UPSTREAM via the read tools (`list_agents` /
`get_agent_detail`): the model passes an already-resolved ``agent_id``, never a
free-text host name (doc 04 §Wrong-target resolution).
"""

from typing import ClassVar

from pydantic import BaseModel, Field

from wolf_server.gateway.proposals import create_proposal
from wolf_server.gateway.validator import validate_proposal
from wolf_server.tools.base import ProposeTool, ToolExecContext
from wolf_server.wazuh.capabilities import (
    ACTION_ACTIVE_RESPONSE,
    fetch_credential_capabilities,
)

_ACTION_CLASS = "active_response"


class ProposeActiveResponseInput(BaseModel):
    agent_id: str = Field(description="Resolved Wazuh agent id (from list_agents), e.g. '001'")
    command: str = Field(
        description="Active-response command id (e.g. 'firewall-drop') — never invented"
    )
    rationale: str = Field(description="Why this action is warranted, in plain language")
    expected_effect: str = Field(
        default="",
        description="What the action will do, in plain language (for the approver)",
    )
    alert_ids: list[str] = Field(
        default_factory=list,
        description="Alert / event ids this proposal is grounded in",
    )


class ProposeActiveResponseOutput(BaseModel):
    permitted: bool = Field(description="Whether the proposal was accepted into the queue")
    state: str = Field(description="Proposal state ('pending') or 'rejected'")
    proposal_id: str = Field(default="", description="The queued proposal id, when permitted")
    summary: str = Field(description="One-line summary of the outcome")
    detail: str = Field(default="", description="Reason, when not permitted")


class ProposeActiveResponseTool(ProposeTool):
    """Propose an active-response command on one resolved agent (awaits approval)."""

    name: ClassVar[str] = "propose_active_response"
    description: ClassVar[str] = (
        "Propose an active-response command (e.g. firewall-drop) on ONE resolved "
        "agent. Does NOT execute — it queues a proposal a human must approve first."
    )
    InputModel: ClassVar[type[BaseModel]] = ProposeActiveResponseInput
    OutputModel: ClassVar[type[BaseModel]] = ProposeActiveResponseOutput

    async def run(
        self, exec_ctx: ToolExecContext, args: BaseModel
    ) -> ProposeActiveResponseOutput:
        assert isinstance(args, ProposeActiveResponseInput)  # noqa: S101 — validated by dispatcher
        ctx = exec_ctx.organization
        target = {"agent_id": args.agent_id}

        # 1. Structural action validator (hard gate — never reaches the queue if it fails).
        verdict = validate_proposal(
            action_class=_ACTION_CLASS,
            target=target,
            action=args.command,
            parameters={},
        )
        if not verdict.ok:
            return ProposeActiveResponseOutput(
                permitted=False,
                state="rejected",
                summary="Proposal rejected by the action validator.",
                detail=verdict.reason,
            )

        # 2. Capability pre-flight — the credential must be RBAC-allowed this action.
        capabilities = await fetch_credential_capabilities(exec_ctx.server_api)
        if not capabilities.can(ACTION_ACTIVE_RESPONSE, f"agent:id:{args.agent_id}"):
            return ProposeActiveResponseOutput(
                permitted=False,
                state="rejected",
                summary="Not proposed — the Wazuh credential is not authorized for this action.",
                detail=(
                    f"Credential lacks active-response permission on agent:id:{args.agent_id}. "
                    "Wolf only offers actions the credential's Wazuh RBAC permits."
                ),
            )

        # 3. Persist the pending proposal (the queue).  db is wired by the dispatcher.
        if exec_ctx.db is None:  # pragma: no cover — always wired in the live path
            raise RuntimeError("propose_active_response requires a DB session in the exec context")
        expected = args.expected_effect or (
            f"Run active-response '{args.command}' on agent {args.agent_id}."
        )
        proposal = await create_proposal(
            exec_ctx.db,
            organization_id=ctx.organization_id,
            requested_by=ctx.user_id,
            action_class=_ACTION_CLASS,
            target=target,
            action=args.command,
            rationale=args.rationale,
            expected_effect=expected,
            evidence={"alert_ids": args.alert_ids},
            session_id=ctx.session_id,
        )
        return ProposeActiveResponseOutput(
            permitted=True,
            state="pending",
            proposal_id=str(proposal.id),
            summary=(
                f"Proposed '{args.command}' on agent {args.agent_id}; "
                "awaiting approval before it runs."
            ),
        )
