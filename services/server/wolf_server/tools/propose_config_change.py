"""`propose_config_change` — propose a manager ossec.conf edit (Phase 6-e.4, ADR 0029).

The model proposes replacing ONE allowlisted, single-instance section of the
manager's ``ossec.conf`` (e.g. tune ``<sca>``, ``<syscheck>``,
``<vulnerability-detection>``).  Like every propose tool it changes nothing
itself: it validates + capability-pre-flights + queues a *pending* proposal a
human must approve.  The section's CURRENT content is captured here, at propose
time, so the approver reviews an exact old → new diff — and the executor
refuses a stale proposal if the live section changed after it was queued.

Proposing the ``restore_config`` operation for a section Wolf previously changed
is an UNDO — Wolf links it (provenance), recalls why the change was made, and
the reversal restores the captured whole-file snapshot (a real undo,
snapshot-restore).

config_change is manager-GLOBAL (one ossec.conf governing the shared manager)
and the highest-blast-radius class, gated by ``manager:update_config`` — held
only by a Superuser/admin credential.  A per-org credential is refused at the
pre-flight (ADR 0025/0029).
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
    ACTION_UPDATE_MANAGER_CONFIG,
    RESOURCE_ANY,
    fetch_credential_capabilities,
)
from wolf_server.wazuh.config_change import (
    EDITABLE_SECTIONS,
    OP_LABELS,
    OP_RESTORE_CONFIG,
    find_section_blocks,
)

_ACTION_CLASS = "config_change"


class ProposeConfigChangeInput(BaseModel):
    section: str = Field(
        description=(
            "The ossec.conf section to change — one of: "
            + ", ".join(sorted(EDITABLE_SECTIONS))
            + ". Only these single-instance sections are editable."
        )
    )
    operation: str = Field(
        description=(
            "'update_section' (replace the section with new content — provide "
            "'section_content'), or 'restore_config' to UNDO a configuration change "
            "Wolf previously made on this section (Wolf links it, recalls why, and "
            "restores the prior ossec.conf)."
        )
    )
    section_content: str = Field(
        default="",
        description=(
            "The FULL replacement <section>…</section> block (required for "
            "'update_section'). Must be exactly one well-formed block for the "
            "target section — it replaces the section wholesale."
        ),
    )
    rationale: str = Field(
        default="",
        description=(
            "Why this configuration change is warranted, in plain language — the "
            "approver relies on it. For an undo, Wolf recalls the original rationale."
        ),
    )
    expected_effect: str = Field(
        default="", description="What the change will do, in plain language (for the approver)."
    )
    alert_ids: list[str] = Field(
        default_factory=list, description="Alert / event ids this proposal is grounded in."
    )


class ProposeConfigChangeOutput(BaseModel):
    permitted: bool = Field(description="Whether the proposal was accepted into the queue")
    state: str = Field(description="Proposal state ('pending') or 'rejected'")
    proposal_id: str = Field(default="", description="The queued proposal id, when permitted")
    summary: str = Field(description="One-line summary of the outcome")
    detail: str = Field(default="", description="Reason, when not permitted")
    citation: Citation


class ProposeConfigChangeTool(ProposeTool):
    """Propose editing one allowlisted ossec.conf section (awaits approval)."""

    name: ClassVar[str] = "propose_config_change"
    description: ClassVar[str] = (
        "Propose changing ONE section of the Wazuh manager's ossec.conf "
        "configuration (editable sections: "
        + ", ".join(sorted(EDITABLE_SECTIONS))
        + "). Provide the FULL replacement <section> block. Does NOT execute — it "
        "queues a proposal a human approves; the approver sees the exact current "
        "vs proposed content. Use operation 'restore_config' to UNDO a config "
        "change Wolf made earlier (Wolf links it + recalls why + restores the "
        "prior file). Configuration changes are manager-global / Superuser-scoped; "
        "if the credential lacks manager:update_config the proposal is refused. "
        "When it returns permitted=true the proposal IS queued (state 'pending'); "
        "report that."
    )
    InputModel: ClassVar[type[BaseModel]] = ProposeConfigChangeInput
    OutputModel: ClassVar[type[BaseModel]] = ProposeConfigChangeOutput

    def _refused(
        self, query: dict[str, Any], detail: str, summary: str
    ) -> ProposeConfigChangeOutput:
        return ProposeConfigChangeOutput(
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
    ) -> ProposeConfigChangeOutput:
        assert isinstance(args, ProposeConfigChangeInput)  # noqa: S101 — validated by dispatcher
        if exec_ctx.db is None:  # pragma: no cover — always wired in the live path
            raise RuntimeError("propose_config_change requires a DB session in the exec context")
        ctx = exec_ctx.organization
        query = args.model_dump(mode="json")
        op = args.operation.strip()
        section = args.section.strip()

        # 1. Capability pre-flight — manager:update_config (Superuser-scoped).
        #    Checked first: it applies to every op, including the undo (also a write).
        capabilities = await fetch_credential_capabilities(exec_ctx.server_api)
        if not capabilities.can(ACTION_UPDATE_MANAGER_CONFIG, RESOURCE_ANY):
            return self._refused(
                query,
                "Credential lacks manager:update_config. Configuration changes are "
                "manager-global / Superuser-scoped; Wolf only offers actions the "
                "credential's Wazuh RBAC permits.",
                "Not proposed — the Wazuh credential is not authorized for "
                "configuration changes.",
            )

        # 2. Undo path — restore_config reverses an active prior Wolf config change.
        if op == OP_RESTORE_CONFIG:
            return await self._propose_restore(exec_ctx.db, ctx, args, query, section)

        # 3. Forward path — update_section. Structural gate first (allowlist,
        #    op, well-formed block) so a doomed proposal never costs a config read.
        target: dict[str, Any] = {"section": section}
        parameters: dict[str, Any] = {"section_content": args.section_content.strip()}
        verdict = validate_proposal(
            action_class=_ACTION_CLASS, target=target, action=op, parameters=parameters
        )
        if not verdict.ok:
            return self._refused(
                query, verdict.reason, "Proposal rejected by the action validator."
            )

        # 4. Capture the CURRENT section content — the approver's diff base and
        #    the executor's staleness check. Requires exactly one live occurrence.
        try:
            raw: str = await exec_ctx.server_api.get_raw(
                "/manager/configuration", params={"raw": "true"}
            )
        except Exception as exc:  # noqa: BLE001 — surfaced as a guided refusal
            return self._refused(
                query,
                f"Could not read the current manager configuration ({exc}); a "
                "config change cannot be proposed without its current content.",
                "Not proposed — the current configuration could not be read.",
            )
        blocks = find_section_blocks(raw, section)
        if len(blocks) == 0:
            return self._refused(
                query,
                f"Section <{section}> is not present in ossec.conf. v1 edits "
                "existing sections only (adding new sections stays hand-edited).",
                f"Not proposed — <{section}> is not in the current configuration.",
            )
        if len(blocks) > 1:
            return self._refused(
                query,
                f"Section <{section}> appears {len(blocks)} times in ossec.conf; "
                "repeated sections are merge-semantic in Wazuh and not editable "
                "as a single block in v1.",
                f"Not proposed — <{section}> appears more than once.",
            )
        parameters["current_content"] = blocks[0].strip()

        label = OP_LABELS.get(op, op)
        rationale = (
            args.rationale.strip() or "Requested via chat — no explicit rationale was given."
        )
        expected = args.expected_effect or (
            f"{label}: <{section}> replaced in ossec.conf (manager-global; applied "
            "via a cluster restart)."
        )
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
        return ProposeConfigChangeOutput(
            permitted=True,
            state="pending",
            proposal_id=str(proposal.id),
            summary=(
                f"Proposed updating <{section}> in ossec.conf; awaiting approval before "
                "it is applied (manager-global change, applied via a cluster restart). "
                "The approver sees the exact current vs proposed content."
            ),
            citation=self.make_citation(query, result_count=1),
        )

    async def _propose_restore(
        self,
        db: AsyncSession,
        ctx: OrganizationContext,
        args: ProposeConfigChangeInput,
        query: dict[str, Any],
        section: str,
    ) -> ProposeConfigChangeOutput:
        """Reverse the most-recent active Wolf config_change on ``section`` (undo)."""

        def _on_section(p: ActionProposal) -> bool:
            return str(p.target.get("section", "")) == section

        prior = await find_active_action(
            db,
            organization_id=ctx.organization_id,
            action_class=_ACTION_CLASS,
            matcher=_on_section,
        )
        if prior is None:
            return self._refused(
                query,
                f"No active Wolf configuration change on <{section}> to restore.",
                "Nothing to undo — Wolf has no active config change recorded for "
                "this section.",
            )
        recall = self._recall(prior)
        expected = args.expected_effect or (
            f"Restore ossec.conf to its prior state — undoes the earlier "
            f"{prior.action.replace('_', ' ')} on <{section}> (proposal {prior.id})."
        )
        proposal = await create_reversal_proposal(
            db,
            prior,
            requested_by=ctx.user_id,
            action=OP_RESTORE_CONFIG,
            parameters={},
            rationale=args.rationale.strip() or f"Undo of the earlier config change. {recall}",
            expected_effect=expected,
            evidence={
                "reverses_proposal_id": str(prior.id),
                "original_rationale": prior.rationale,
            },
            session_id=ctx.session_id,
        )
        return ProposeConfigChangeOutput(
            permitted=True,
            state="pending",
            proposal_id=str(proposal.id),
            summary=(
                f"Proposed restoring ossec.conf (undoes proposal {prior.id} on "
                f"<{section}>). {recall} Awaiting approval."
            ),
            citation=self.make_citation(query, result_count=1),
        )
