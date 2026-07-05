"""`propose_config_change` — author a manager ossec.conf change (6-e.4 → 6-f.4, ADR 0032 B).

The model authors a change to the manager's ``ossec.conf`` — free-form within
rails (any section outside the break-the-manager blocklist), covering both
single-instance sections (``update_section``, which ADDS the section when it is
absent) and repeated / merge-semantic sections addressed by **block-identity**
(``upsert_block`` / ``remove_block`` + ``block_key`` — e.g. one specific
``<integration>`` by its ``<name>``).  Like every propose tool it changes
nothing itself: it validates + capability-pre-flights + queues a *pending*
proposal a human must approve.

**The authoring loop is two-phase (B1 confirm-diff):** a call WITHOUT
``user_confirmed=true`` performs the full author-time work — structural
validation, a live read of the current content, and a dry-run of the exact
transformation the executor will apply — and returns a PREVIEW
(``state="needs_confirmation"`` + ``current_content``) without queueing
anything.  The model shows the analyst the exact current → proposed change,
gets their explicit confirmation in the conversation, and only then re-calls
with ``user_confirmed=true`` to queue the proposal.  The manager-side
validation (``GET /manager/configuration/validation``) still runs at execute
time with auto-rollback — it validates the on-disk file, so a propose-time
manager dry-run would require a write, which a propose tool must never do.

Proposing ``restore_config`` for a change Wolf previously made is an UNDO —
Wolf links it (provenance), recalls why the change was made, and the reversal
restores the captured whole-file snapshot (a real undo, snapshot-restore).

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
    BLOCKED_SECTIONS,
    IDENTITY_KEYS,
    OP_LABELS,
    OP_REMOVE_BLOCK,
    OP_RESTORE_CONFIG,
    OP_UPDATE_SECTION,
    OP_UPSERT_BLOCK,
    build_candidate,
    find_identified_blocks,
    find_section_blocks,
)

_ACTION_CLASS = "config_change"


class ProposeConfigChangeInput(BaseModel):
    section: str = Field(
        description=(
            "The ossec.conf section to change (its element name, e.g. 'sca', "
            "'syscheck', 'integration', 'localfile'). Any section is authorable "
            "EXCEPT the break-the-manager set: " + ", ".join(sorted(BLOCKED_SECTIONS)) + "."
        )
    )
    operation: str = Field(
        description=(
            "'update_section' — replace a single-instance section wholesale (or ADD "
            "it when absent); provide 'section_content'. "
            "'upsert_block' — add or update ONE instance of a repeated section "
            "(integration / localfile / command) addressed by 'block_key'; provide "
            "'section_content'. "
            "'remove_block' — remove ONE instance addressed by 'block_key'. "
            "'restore_config' — UNDO a configuration change Wolf previously made "
            "(Wolf links it, recalls why, and restores the prior ossec.conf)."
        )
    )
    section_content: str = Field(
        default="",
        description=(
            "The FULL replacement <section>…</section> block (required for "
            "'update_section' and 'upsert_block'). Must be exactly one well-formed "
            "block for the target section; for 'upsert_block' it must contain the "
            "identity element matching 'block_key'."
        ),
    )
    block_key: str = Field(
        default="",
        description=(
            "For 'upsert_block' / 'remove_block': the stable identity of the ONE "
            "instance to change — an <integration>'s <name>, a <localfile>'s "
            "<location>, a <command>'s <name>. Take it from the user's request or "
            "the researched configuration; never guess."
        ),
    )
    user_confirmed: bool = Field(
        default=False,
        description=(
            "Set true ONLY after the analyst explicitly confirmed the exact change "
            "in this conversation. FIRST call without it: the tool returns "
            "state='needs_confirmation' with the section's CURRENT content — show "
            "the analyst the current vs proposed change and ask; re-call with "
            "user_confirmed=true only after they agree."
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
    state: str = Field(
        description="Proposal state ('pending'), 'needs_confirmation' (preview), or 'rejected'"
    )
    proposal_id: str = Field(default="", description="The queued proposal id, when permitted")
    summary: str = Field(description="One-line summary of the outcome")
    detail: str = Field(default="", description="Reason, when not permitted")
    current_content: str = Field(
        default="",
        description=(
            "The live current content of the targeted section/block (empty when the "
            "change ADDS something new) — in a preview, show it to the analyst "
            "against the proposed content."
        ),
    )
    citation: Citation


class ProposeConfigChangeTool(ProposeTool):
    """Author an ossec.conf change: preview → analyst confirms → queue for approval."""

    name: ClassVar[str] = "propose_config_change"
    description: ClassVar[str] = (
        "Author a change to the Wazuh manager's ossec.conf configuration — any "
        "section EXCEPT " + ", ".join(sorted(BLOCKED_SECTIONS)) + " (those stay "
        "hand-edited). Operations: 'update_section' replaces (or adds) a "
        "single-instance section wholesale; 'upsert_block'/'remove_block' add, "
        "update or remove ONE instance of a repeated section (integration / "
        "localfile / command) addressed by 'block_key' (e.g. an integration's "
        "<name>). Does NOT execute — the flow is: (1) call WITHOUT "
        "user_confirmed to get a PREVIEW with the current content; (2) show the "
        "analyst the exact current vs proposed change and ask them to confirm; "
        "(3) re-call with user_confirmed=true — it queues a proposal a human "
        "approves. Use operation 'restore_config' to UNDO a config change Wolf "
        "made earlier (Wolf links it + recalls why + restores the prior file). "
        "Configuration changes are manager-global / Superuser-scoped; if the "
        "credential lacks manager:update_config the proposal is refused. When it "
        "returns permitted=true the proposal IS queued (state 'pending'); report "
        "that."
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

    @staticmethod
    def _describe(op: str, section: str, block_key: str, *, adding: bool) -> str:
        """Approver/analyst-facing phrasing of what the change touches."""
        if op == OP_UPDATE_SECTION:
            return f"add <{section}> to ossec.conf" if adding else f"update <{section}>"
        instance = f"<{section}> block '{block_key}'"
        if op == OP_REMOVE_BLOCK:
            return f"remove the {instance}"
        return f"add the {instance}" if adding else f"update the {instance}"

    async def run(self, exec_ctx: ToolExecContext, args: BaseModel) -> ProposeConfigChangeOutput:
        assert isinstance(args, ProposeConfigChangeInput)  # noqa: S101 — validated by dispatcher
        if exec_ctx.db is None:  # pragma: no cover — always wired in the live path
            raise RuntimeError("propose_config_change requires a DB session in the exec context")
        ctx = exec_ctx.organization
        query = args.model_dump(mode="json")
        op = args.operation.strip()
        section = args.section.strip()
        block_key = args.block_key.strip()

        # 1. Capability pre-flight — manager:update_config (Superuser-scoped).
        #    Checked first: it applies to every op, including the undo (also a write).
        capabilities = await fetch_credential_capabilities(exec_ctx.server_api)
        if not capabilities.can(ACTION_UPDATE_MANAGER_CONFIG, RESOURCE_ANY):
            return self._refused(
                query,
                "Credential lacks manager:update_config. Configuration changes are "
                "manager-global / Superuser-scoped; Wolf only offers actions the "
                "credential's Wazuh RBAC permits.",
                "Not proposed — the Wazuh credential is not authorized for configuration changes.",
            )

        # 2. Undo path — restore_config reverses an active prior Wolf config change.
        if op == OP_RESTORE_CONFIG:
            return await self._propose_restore(exec_ctx.db, ctx, args, query, section, block_key)

        # 3. Structural gate first (blocklist, op, block shape, identity match) so
        #    a doomed proposal never costs a config read.
        target: dict[str, Any] = {"section": section}
        if op in (OP_UPSERT_BLOCK, OP_REMOVE_BLOCK):
            target["block_key"] = block_key
        parameters: dict[str, Any] = {}
        if op != OP_REMOVE_BLOCK:
            parameters["section_content"] = args.section_content.strip()
        verdict = validate_proposal(
            action_class=_ACTION_CLASS, target=target, action=op, parameters=parameters
        )
        if not verdict.ok:
            return self._refused(
                query, verdict.reason, "Proposal rejected by the action validator."
            )

        # 4. Read the live config + capture the CURRENT content — the analyst's
        #    preview base, the approver's diff base, the executor's staleness check.
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
        captured = self._capture_current(raw, op, section, block_key)
        if isinstance(captured, ProposeConfigChangeOutput):
            return captured  # a guided refusal
        current = captured

        # 5. Author-time DRY-RUN — apply the exact transformation the executor
        #    will apply; a change that cannot be applied is refused NOW, not at
        #    approve time (B1.3; the manager-side validation still runs at execute).
        new_block = str(parameters.get("section_content", ""))
        candidate = build_candidate(raw, op, section, block_key, new_block)
        if candidate is None:
            return self._refused(
                query,
                f"The change could not be applied to the current ossec.conf "
                f"(dry-run failed for {OP_LABELS.get(op, op)!r} on <{section}>"
                + (f" '{block_key}'" if block_key else "")
                + "); the file may be malformed or the target ambiguous.",
                "Not proposed — the change does not apply cleanly to the current configuration.",
            )

        adding = current == ""
        describe = self._describe(op, section, block_key, adding=adding)

        # 6. Confirm-diff gate (B1.2): without explicit analyst confirmation this
        #    is a PREVIEW — nothing is queued.
        if not args.user_confirmed:
            return ProposeConfigChangeOutput(
                permitted=False,
                state="needs_confirmation",
                summary=(
                    f"PREVIEW ONLY — nothing was proposed yet. The change would "
                    f"{describe}. Show the analyst the exact current vs proposed "
                    "content, ask them to confirm, then re-call with "
                    "user_confirmed=true."
                ),
                detail=(
                    "Awaiting the analyst's explicit confirmation of the exact "
                    "change (current_content holds the live content it replaces)."
                ),
                current_content=current,
                citation=self.make_citation(query, result_count=1),
            )

        parameters["current_content"] = current
        rationale = (
            args.rationale.strip() or "Requested via chat — no explicit rationale was given."
        )
        expected = args.expected_effect or (
            f"{OP_LABELS.get(op, op)}: {describe} (manager-global; applied via a cluster restart)."
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
                f"Proposed: {describe} in ossec.conf; awaiting approval before it "
                "is applied (manager-global change, applied via a cluster restart). "
                "The approver sees the exact current vs proposed content."
            ),
            current_content=current,
            citation=self.make_citation(query, result_count=1),
        )

    def _capture_current(
        self, raw: str, op: str, section: str, block_key: str
    ) -> str | ProposeConfigChangeOutput:
        """The live content the change replaces ('' when it ADDS something new),
        or a guided refusal when the target is absent/ambiguous for the op."""
        query = {"section": section, "operation": op, "block_key": block_key}
        if op == OP_UPDATE_SECTION:
            blocks = find_section_blocks(raw, section)
            if len(blocks) > 1:
                hint = (
                    f" Use 'upsert_block' with the instance's <{IDENTITY_KEYS[section]}> "
                    "as 'block_key' to address one of them."
                    if section in IDENTITY_KEYS
                    else ""
                )
                return self._refused(
                    query,
                    f"Section <{section}> appears {len(blocks)} times in ossec.conf; "
                    "replacing 'the' block is ambiguous under Wazuh's merge "
                    "semantics." + hint,
                    f"Not proposed — <{section}> appears more than once.",
                )
            return blocks[0].strip() if blocks else ""
        matches = find_identified_blocks(raw, section, block_key)
        if len(matches) > 1:
            return self._refused(
                query,
                f"{len(matches)} <{section}> blocks carry the key {block_key!r} — "
                "the file is ambiguous and needs a hand fix before Wolf can "
                "address that instance.",
                f"Not proposed — multiple <{section}> blocks match '{block_key}'.",
            )
        if op == OP_REMOVE_BLOCK and not matches:
            return self._refused(
                query,
                f"No <{section}> block with key {block_key!r} exists in ossec.conf — "
                "nothing to remove.",
                f"Not proposed — no <{section}> block matches '{block_key}'.",
            )
        return matches[0].strip() if matches else ""

    async def _propose_restore(
        self,
        db: AsyncSession,
        ctx: OrganizationContext,
        args: ProposeConfigChangeInput,
        query: dict[str, Any],
        section: str,
        block_key: str,
    ) -> ProposeConfigChangeOutput:
        """Reverse the most-recent active Wolf config_change on ``section`` (and,
        when given, the specific ``block_key`` instance) — the undo."""

        def _on_target(p: ActionProposal) -> bool:
            if str(p.target.get("section", "")) != section:
                return False
            if block_key:
                return str(p.target.get("block_key", "")) == block_key
            return True

        prior = await find_active_action(
            db,
            organization_id=ctx.organization_id,
            action_class=_ACTION_CLASS,
            matcher=_on_target,
        )
        described = f"<{section}>" + (f" '{block_key}'" if block_key else "")
        if prior is None:
            return self._refused(
                query,
                f"No active Wolf configuration change on {described} to restore.",
                "Nothing to undo — Wolf has no active config change recorded for this target.",
            )
        recall = self._recall(prior)
        expected = args.expected_effect or (
            f"Restore ossec.conf to its prior state — undoes the earlier "
            f"{prior.action.replace('_', ' ')} on {described} (proposal {prior.id})."
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
                f"{described}). {recall} Awaiting approval."
            ),
            citation=self.make_citation(query, result_count=1),
        )
