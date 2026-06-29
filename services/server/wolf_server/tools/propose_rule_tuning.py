"""`propose_rule_tuning` — propose a Wazuh rule-level change (Phase 6-e.3, ADR 0029).

The model proposes disabling a noisy rule (set its alert level to 0) or adjusting
a rule's level, applied as an ``overwrite="yes"`` override in ``local_rules.xml``.
Like every propose tool it changes nothing itself: it validates + capability-
pre-flights + queues a *pending* proposal a human must approve.  Proposing the
``restore_rules`` operation for a rule Wolf previously tuned is an UNDO — Wolf
links it (provenance), recalls why the change was made, and the reversal restores
the captured ``local_rules.xml`` snapshot (a real undo, snapshot-restore).

rule_tuning is manager-GLOBAL (one rule file shared by every org), so it is gated
by the ``rules:update`` RBAC action — held only by a Superuser/admin credential.
A per-org credential without it is refused at the pre-flight (the capability-
driven gate handles the scoping; ADR 0025/0029).
"""

from typing import Any, ClassVar

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from wolf_server.gateway.models import ActionProposal
from wolf_server.gateway.proposals import (
    create_proposal,
    create_reversal_proposal,
    find_active_action,
)
from wolf_server.gateway.validator import validate_proposal
from wolf_server.organization.context import OrganizationContext
from wolf_server.tools.base import Citation, ProposeTool, ToolExecContext
from wolf_server.wazuh.capabilities import (
    ACTION_UPDATE_RULES,
    RESOURCE_LOCAL_RULES,
    fetch_credential_capabilities,
)
from wolf_server.wazuh.rule_tuning import (
    DISABLE_LEVEL,
    OP_ADJUST_LEVEL,
    OP_DISABLE_RULE,
    OP_LABELS,
    OP_RESTORE_RULES,
)

_ACTION_CLASS = "rule_tuning"


class ProposeRuleTuningInput(BaseModel):
    rule_id: str = Field(
        description=(
            "The EXACT Wazuh rule id to tune — from the user's request or resolved "
            "via get_rule_definition/search_alerts. Never default or guess."
        )
    )
    operation: str = Field(
        description=(
            "'disable_rule' (silence a noisy rule — set its level to 0), "
            "'adjust_level' (change its alert level — provide 'level'), or "
            "'restore_rules' to UNDO a rule change Wolf previously made on this rule "
            "(Wolf links it, recalls why, and restores the prior rules file)."
        )
    )
    level: int | None = Field(
        default=None,
        description="The new alert level (0-16) — required for 'adjust_level'.",
    )
    rationale: str = Field(
        default="",
        description=(
            "Why this tuning is warranted, in plain language — the approver relies "
            "on it. For an undo, Wolf recalls the original change's rationale."
        ),
    )
    expected_effect: str = Field(
        default="", description="What the change will do, in plain language (for the approver)."
    )
    alert_ids: list[str] = Field(
        default_factory=list, description="Alert / event ids this proposal is grounded in."
    )


class ProposeRuleTuningOutput(BaseModel):
    permitted: bool = Field(description="Whether the proposal was accepted into the queue")
    state: str = Field(description="Proposal state ('pending') or 'rejected'")
    proposal_id: str = Field(default="", description="The queued proposal id, when permitted")
    summary: str = Field(description="One-line summary of the outcome")
    detail: str = Field(default="", description="Reason, when not permitted")
    citation: Citation


class ProposeRuleTuningTool(ProposeTool):
    """Propose a rule-level change on one resolved rule (awaits approval)."""

    name: ClassVar[str] = "propose_rule_tuning"
    description: ClassVar[str] = (
        "Propose tuning ONE Wazuh detection rule — disable a noisy rule (level 0) "
        "or adjust its alert level — applied as an override in local_rules.xml. "
        "Does NOT execute — it queues a proposal a human approves. Use "
        "operation 'restore_rules' to UNDO a rule change Wolf made earlier (Wolf "
        "links it + recalls why + restores the prior file). Rule tuning is "
        "manager-global / Superuser-scoped; if the credential lacks rules:update "
        "the proposal is refused. When it returns permitted=true the proposal IS "
        "queued (state 'pending'); report that."
    )
    InputModel: ClassVar[type[BaseModel]] = ProposeRuleTuningInput
    OutputModel: ClassVar[type[BaseModel]] = ProposeRuleTuningOutput

    def _refused(
        self, query: dict[str, Any], detail: str, summary: str
    ) -> ProposeRuleTuningOutput:
        return ProposeRuleTuningOutput(
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
    ) -> ProposeRuleTuningOutput:
        assert isinstance(args, ProposeRuleTuningInput)  # noqa: S101 — validated by dispatcher
        if exec_ctx.db is None:  # pragma: no cover — always wired in the live path
            raise RuntimeError("propose_rule_tuning requires a DB session in the exec context")
        ctx = exec_ctx.organization
        query = args.model_dump(mode="json")
        op = args.operation.strip()
        rule_id = args.rule_id.strip()

        # 1. Capability pre-flight — rules:update on local_rules.xml (Superuser-scoped).
        #    Checked first: it applies to every op, including the undo (also a write).
        capabilities = await fetch_credential_capabilities(exec_ctx.server_api)
        if not capabilities.can(ACTION_UPDATE_RULES, RESOURCE_LOCAL_RULES):
            return self._refused(
                query,
                "Credential lacks rules:update on local_rules.xml. Rule tuning is "
                "manager-global / Superuser-scoped; Wolf only offers actions the "
                "credential's Wazuh RBAC permits.",
                "Not proposed — the Wazuh credential is not authorized for rule tuning.",
            )

        # 2. Undo path — restore_rules reverses an active prior Wolf rule change.
        if op == OP_RESTORE_RULES:
            return await self._propose_restore(exec_ctx.db, ctx, args, query, rule_id)

        # 3. Forward path — disable_rule / adjust_level.
        level = DISABLE_LEVEL if op == OP_DISABLE_RULE else args.level
        parameters: dict[str, Any] = {"level": level}
        target: dict[str, Any] = {"rule_id": rule_id}

        verdict = validate_proposal(
            action_class=_ACTION_CLASS, target=target, action=op, parameters=parameters
        )
        if not verdict.ok:
            return self._refused(
                query, verdict.reason, "Proposal rejected by the action validator."
            )

        label = OP_LABELS.get(op, op)
        descr = f"rule {rule_id}" + (f" → level {level}" if op == OP_ADJUST_LEVEL else "")
        rationale = (
            args.rationale.strip() or "Requested via chat — no explicit rationale was given."
        )
        expected = args.expected_effect or f"{label}: {descr} (override in local_rules.xml)."
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
        return ProposeRuleTuningOutput(
            permitted=True,
            state="pending",
            proposal_id=str(proposal.id),
            summary=(
                f"Proposed {label.lower()}: {descr}; awaiting approval before it is applied "
                "(manager-global change, applied via a cluster restart)."
            ),
            citation=self.make_citation(query, result_count=1),
        )

    async def _propose_restore(
        self,
        db: AsyncSession,
        ctx: OrganizationContext,
        args: ProposeRuleTuningInput,
        query: dict[str, Any],
        rule_id: str,
    ) -> ProposeRuleTuningOutput:
        """Reverse the most-recent active Wolf rule_tuning on ``rule_id`` (undo)."""

        def _on_rule(p: ActionProposal) -> bool:
            return str(p.target.get("rule_id", "")) == rule_id

        prior = await find_active_action(
            db,
            organization_id=ctx.organization_id,
            action_class=_ACTION_CLASS,
            matcher=_on_rule,
        )
        if prior is None:
            return self._refused(
                query,
                f"No active Wolf rule change on rule {rule_id} to restore.",
                "Nothing to undo — Wolf has no active tuning recorded for this rule.",
            )
        recall = self._recall(prior)
        expected = args.expected_effect or (
            f"Restore rule {rule_id} to its prior state — undoes the earlier "
            f"{prior.action.replace('_', ' ')} (proposal {prior.id})."
        )
        proposal = await create_reversal_proposal(
            db,
            prior,
            requested_by=ctx.user_id,
            action=OP_RESTORE_RULES,
            parameters={},
            rationale=args.rationale.strip() or f"Undo of the earlier rule change. {recall}",
            expected_effect=expected,
            evidence={
                "reverses_proposal_id": str(prior.id),
                "original_rationale": prior.rationale,
            },
            session_id=ctx.session_id,
        )
        return ProposeRuleTuningOutput(
            permitted=True,
            state="pending",
            proposal_id=str(proposal.id),
            summary=(
                f"Proposed restoring rule {rule_id} (undoes proposal {prior.id}). {recall} "
                "Awaiting approval."
            ),
            citation=self.make_citation(query, result_count=1),
        )
