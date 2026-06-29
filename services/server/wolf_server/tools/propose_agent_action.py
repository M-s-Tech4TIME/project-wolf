"""`propose_agent_action` — propose an agent GROUP change (Phase 6-e.2, ADR 0029).

The model proposes assigning an agent to a group or removing it from one (e.g.
quarantine an agent into an ``isolated`` group, then move it back).  Like every
propose tool it changes nothing itself: it validates + capability-pre-flights +
queues a *pending* proposal a human must approve.  Proposing the **opposite**
operation for the same agent + group is recognised as an UNDO of a prior Wolf
action — Wolf links it (provenance) and recalls why the original was made.

``agent:modify_group`` is typically Superuser/admin-scoped (it's a manager-side
membership change), so a per-org credential without it is refused at the
pre-flight — the capability-driven gate (ADR 0025/0029) handles the scoping.
"""

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from wolf_server.gateway.models import ActionProposal
from wolf_server.gateway.proposals import (
    create_proposal,
    create_reversal_proposal,
    find_active_action,
)
from wolf_server.gateway.validator import validate_proposal
from wolf_server.tools.base import Citation, ProposeTool, ToolExecContext
from wolf_server.wazuh.agent_actions import INVERSE_OP, OP_LABELS
from wolf_server.wazuh.capabilities import (
    ACTION_MODIFY_GROUP,
    fetch_credential_capabilities,
    resolve_agent_groups,
)

_ACTION_CLASS = "agent_action"


class ProposeAgentActionInput(BaseModel):
    agent_id: str = Field(
        description=(
            "The EXACT Wazuh agent id to act on — from the user's request or "
            "resolved via list_agents/get_agent_detail. Never default or guess."
        )
    )
    operation: str = Field(
        description=(
            "'assign_group' (add the agent to a group) or 'remove_group' (remove "
            "it from one). To UNDO a prior group change, propose the OPPOSITE "
            "operation for the same agent + group — Wolf links it and recalls why "
            "the original change was made."
        )
    )
    group: str = Field(
        description="The Wazuh group name to assign the agent to / remove it from."
    )
    rationale: str = Field(
        default="",
        description=(
            "Why this change is warranted, in plain language — the approver relies "
            "on it. For an undo, Wolf recalls the original change's rationale."
        ),
    )
    expected_effect: str = Field(
        default="", description="What the change will do, in plain language (for the approver)."
    )
    alert_ids: list[str] = Field(
        default_factory=list, description="Alert / event ids this proposal is grounded in."
    )


class ProposeAgentActionOutput(BaseModel):
    permitted: bool = Field(description="Whether the proposal was accepted into the queue")
    state: str = Field(description="Proposal state ('pending') or 'rejected'")
    proposal_id: str = Field(default="", description="The queued proposal id, when permitted")
    summary: str = Field(description="One-line summary of the outcome")
    detail: str = Field(default="", description="Reason, when not permitted")
    citation: Citation


class ProposeAgentActionTool(ProposeTool):
    """Propose an agent group change on one resolved agent (awaits approval)."""

    name: ClassVar[str] = "propose_agent_action"
    description: ClassVar[str] = (
        "Propose an agent GROUP change on ONE resolved agent — assign it to a "
        "group or remove it (e.g. quarantine into an 'isolated' group, then move "
        "it back). Does NOT execute — it queues a proposal a human approves. "
        "Proposing the OPPOSITE operation UNDOES a prior one (Wolf links it + "
        "recalls why). When it returns permitted=true the proposal IS queued "
        "(state 'pending'); report that."
    )
    InputModel: ClassVar[type[BaseModel]] = ProposeAgentActionInput
    OutputModel: ClassVar[type[BaseModel]] = ProposeAgentActionOutput

    def _refused(
        self, query: dict[str, Any], detail: str, summary: str
    ) -> ProposeAgentActionOutput:
        return ProposeAgentActionOutput(
            permitted=False,
            state="rejected",
            summary=summary,
            detail=detail,
            citation=self.make_citation(query, result_count=0),
        )

    @staticmethod
    def _recall(prior: ActionProposal) -> str:
        when = prior.executed_at or prior.created_at
        when_s = when.strftime("%Y-%m-%d %H:%M UTC") if when else "an earlier time"
        return f"It was {prior.action.replace('_', ' ')} on {when_s} — reason: {prior.rationale}."

    async def run(
        self, exec_ctx: ToolExecContext, args: BaseModel
    ) -> ProposeAgentActionOutput:
        assert isinstance(args, ProposeAgentActionInput)  # noqa: S101 — validated by dispatcher
        if exec_ctx.db is None:  # pragma: no cover — always wired in the live path
            raise RuntimeError("propose_agent_action requires a DB session in the exec context")
        ctx = exec_ctx.organization
        query = args.model_dump(mode="json")
        op = args.operation.strip()
        group = args.group.strip()
        target: dict[str, Any] = {"agent_id": args.agent_id}
        parameters: dict[str, Any] = {"group": group}

        # 1. Structural validator (operation known, group well-formed, target resolved).
        verdict = validate_proposal(
            action_class=_ACTION_CLASS, target=target, action=op, parameters=parameters
        )
        if not verdict.ok:
            return self._refused(
                query, verdict.reason, "Proposal rejected by the action validator."
            )

        # 2. Capability pre-flight — agent:modify_group on the agent (id or group).
        capabilities = await fetch_credential_capabilities(exec_ctx.server_api)
        agent_groups = await resolve_agent_groups(exec_ctx.server_api, args.agent_id)
        if not capabilities.can_on_agent(ACTION_MODIFY_GROUP, args.agent_id, agent_groups):
            groups = ", ".join(agent_groups) or "none"
            return self._refused(
                query,
                f"Credential lacks agent:modify_group on agent {args.agent_id} "
                f"(groups: {groups}). Group management is typically Superuser-scoped; "
                "Wolf only offers actions the credential's Wazuh RBAC permits.",
                "Not proposed — the Wazuh credential is not authorized for this action.",
            )

        label = OP_LABELS.get(op, op)

        # 3. Undo detection — does this operation invert an active prior Wolf action
        #    (same agent + group, opposite op)? If so, link + recall (provenance).
        inverse_op = INVERSE_OP.get(op)

        def _inverts(p: ActionProposal) -> bool:
            params = p.parameters if isinstance(p.parameters, dict) else {}
            return (
                p.action == inverse_op
                and str(p.target.get("agent_id", "")) == args.agent_id
                and params.get("group") == group
            )

        prior = (
            await find_active_action(
                exec_ctx.db,
                organization_id=ctx.organization_id,
                action_class=_ACTION_CLASS,
                matcher=_inverts,
            )
            if inverse_op is not None
            else None
        )

        if prior is not None:
            recall = self._recall(prior)
            expected = args.expected_effect or (
                f"{label} '{group}' on agent {args.agent_id} — undoes the earlier "
                f"{prior.action.replace('_', ' ')} (proposal {prior.id})."
            )
            proposal = await create_reversal_proposal(
                exec_ctx.db,
                prior,
                requested_by=ctx.user_id,
                action=op,
                parameters=parameters,
                rationale=args.rationale.strip() or f"Undo of the earlier group change. {recall}",
                expected_effect=expected,
                evidence={
                    "reverses_proposal_id": str(prior.id),
                    "original_rationale": prior.rationale,
                },
                session_id=ctx.session_id,
            )
            summary = (
                f"Proposed {label.lower()} '{group}' on agent {args.agent_id} "
                f"(undoes proposal {prior.id}). {recall} Awaiting approval."
            )
        else:
            rationale = (
                args.rationale.strip() or "Requested via chat — no explicit rationale was given."
            )
            expected = args.expected_effect or f"{label} '{group}' on agent {args.agent_id}."
            proposal = await create_proposal(
                exec_ctx.db,
                organization_id=ctx.organization_id,
                requested_by=ctx.user_id,
                action_class=_ACTION_CLASS,
                target=target,
                action=op,
                parameters=parameters,
                rationale=rationale,
                expected_effect=expected,
                evidence={"alert_ids": args.alert_ids},
                session_id=ctx.session_id,
            )
            summary = (
                f"Proposed {label.lower()} '{group}' on agent {args.agent_id}; "
                "awaiting approval before it runs."
            )

        return ProposeAgentActionOutput(
            permitted=True,
            state="pending",
            proposal_id=str(proposal.id),
            summary=summary,
            citation=self.make_citation(query, result_count=1),
        )
