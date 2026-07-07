"""`propose_config_change` — author a manager ossec.conf change (6-e.4 → 6-f.6, ADR 0032 B).

The model authors a change to the manager's ``ossec.conf`` — free-form within
rails (any section outside the break-the-manager blocklist), covering both
single-instance sections (``update_section``, which ADDS the section when it is
absent) and repeated / merge-semantic sections addressed by **block-identity**
(``upsert_block`` / ``remove_block`` + ``block_key`` — one specific
``<integration>`` by its ``<name>``, or by ANY field value unique to the
instance when identities collide, 6-f.5; an ambiguous key is refused WITH each
instance's distinguishing fields so the model can re-address precisely).  Like
every propose tool it changes nothing itself: it validates +
capability-pre-flights + queues a *pending* proposal a human must approve.

**Deployment-aware (6-f.6).**  The tool detects the deployment type at propose
time (``GET /cluster/status`` + ``/cluster/nodes``).  On an all-in-one manager
it captures + dry-runs against the single ossec.conf.  On a distributed cluster
it captures + dry-runs against EVERY node's own file (the cluster does not sync
ossec.conf — a master-only write would leave workers on the old config), and
records the per-node diff base + scope in the proposal (``deployment`` +
``node_current_contents``).  Scope respects instance locality: a single-instance
section is uniform cluster-wide; a repeated instance is changed only on the
nodes that carry it (an upsert of an instance present nowhere is a cluster-wide
ADD).  Distributed apply needs ``cluster:update_config`` in addition.

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
    ACTION_UPDATE_CLUSTER_CONFIG,
    ACTION_UPDATE_MANAGER_CONFIG,
    RESOURCE_ANY,
    RESOURCE_NODE_ANY,
    fetch_credential_capabilities,
)
from wolf_server.wazuh.cluster import ManagerNode, get_cluster_nodes, node_configuration_path
from wolf_server.wazuh.config_change import (
    BLOCKED_SECTIONS,
    OP_LABELS,
    OP_REMOVE_BLOCK,
    OP_RESTORE_CONFIG,
    OP_UPDATE_SECTION,
    OP_UPSERT_BLOCK,
    build_candidate,
    describe_instances,
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
            "(e.g. integration / localfile / command) addressed by 'block_key'; "
            "provide 'section_content'. "
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
            "'block_key' value (as its identity element or a field value)."
        ),
    )
    block_key: str = Field(
        default="",
        description=(
            "For 'upsert_block' / 'remove_block': a value identifying the ONE "
            "instance to change — its identity element (an <integration>'s <name>, "
            "a <localfile>'s <location>, a <command>'s <name>) or, when several "
            "instances share that identity, ANY field value unique to the instance "
            "(e.g. its <hook_url> or <api_key>). Take it from the user's request "
            "or the researched configuration; never guess. If the key is ambiguous "
            "the tool lists each instance's distinguishing fields — re-call with "
            "one of them."
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
        "update or remove ONE instance of a repeated section (e.g. integration / "
        "localfile / command) addressed by 'block_key' — its identity (an "
        "integration's <name>) or any field value unique to the instance (its "
        "<hook_url>, <api_key>, …) when names collide. Does NOT execute — the "
        "flow is: (1) call WITHOUT "
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

        # 4. Deployment detection (6-f.6) — the apply surface depends on it:
        #    an all-in-one manager takes the change directly; a distributed
        #    cluster takes it on EVERY node's own file (ossec.conf is NOT
        #    cluster-synced — probed live 2026-07-06). Detection failure
        #    refuses: never a blind master-only write that leaves workers
        #    diverged.
        try:
            nodes = await get_cluster_nodes(exec_ctx.server_api)
        except Exception as exc:  # noqa: BLE001 — surfaced as a guided refusal
            return self._refused(
                query,
                f"Could not determine the Wazuh deployment type ({exc}); a config "
                "change is applied per deployment (all-in-one directly, distributed "
                "per cluster node) and cannot proceed blind.",
                "Not proposed — the deployment type could not be determined.",
            )
        new_block = str(parameters.get("section_content", ""))

        # 5. Capture the CURRENT content — the analyst's preview base, the
        #    approver's diff base, the executor's staleness check — and DRY-RUN
        #    the exact transformation the executor will apply (B1.3; the
        #    manager-side validation still runs at execute). Distributed: both
        #    happen per node.
        scope_note = ""
        scope_detail = ""
        if nodes:
            # Distributed: cluster:update_config is required to write a node's
            # file (distinct from manager:update_config; fail closed).
            if not capabilities.can(ACTION_UPDATE_CLUSTER_CONFIG, RESOURCE_NODE_ANY):
                return self._refused(
                    query,
                    "This is a distributed Wazuh deployment; applying a config "
                    "change per node needs cluster:update_config, which this "
                    "credential lacks. Distributed configuration changes are "
                    "Superuser-scoped.",
                    "Not proposed — the credential cannot write per-node cluster configuration.",
                )
            captured_cluster = await self._capture_cluster(
                exec_ctx, query, nodes, op, section, block_key, new_block
            )
            if isinstance(captured_cluster, ProposeConfigChangeOutput):
                return captured_cluster  # a guided refusal
            current, node_current = captured_cluster
            parameters["deployment"] = "cluster"
            parameters["node_current_contents"] = node_current
            in_scope = list(node_current)
            scope_note = (
                f" on {len(in_scope)} of the {len(nodes)} manager cluster nodes "
                f"({', '.join(in_scope)})"
            )
            skipped = [n.name for n in nodes if n.name not in node_current]
            if skipped:
                scope_detail = (
                    " Nodes not touched (the target is not present on them, so they "
                    "are already in the desired state): " + ", ".join(skipped) + "."
                )
        else:
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
            candidate = build_candidate(raw, op, section, block_key, new_block)
            if candidate is None:
                return self._refused(
                    query,
                    f"The change could not be applied to the current ossec.conf "
                    f"(dry-run failed for {OP_LABELS.get(op, op)!r} on <{section}>"
                    + (f" '{block_key}'" if block_key else "")
                    + "); the file may be malformed or the target ambiguous.",
                    "Not proposed — the change does not apply cleanly to the "
                    "current configuration.",
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
                    f"{describe}{scope_note}. Show the analyst the exact current vs "
                    "proposed content, ask them to confirm, then re-call with "
                    "user_confirmed=true."
                ),
                detail=(
                    "Awaiting the analyst's explicit confirmation of the exact "
                    "change (current_content holds the live content it replaces)." + scope_detail
                ),
                current_content=current,
                citation=self.make_citation(query, result_count=1),
            )

        parameters["current_content"] = current
        rationale = (
            args.rationale.strip() or "Requested via chat — no explicit rationale was given."
        )
        expected = args.expected_effect or (
            f"{OP_LABELS.get(op, op)}: {describe}{scope_note} (manager-global; "
            "applied via a cluster restart)."
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
                f"Proposed: {describe}{scope_note} in ossec.conf; awaiting approval "
                "before it is applied (manager-global change, applied via a cluster "
                "restart). The approver sees the exact current vs proposed content."
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
                discriminators = describe_instances(blocks)
                hint = (
                    f" Use 'upsert_block' with 'block_key' set to a value unique to "
                    f"ONE instance ({discriminators})."
                    if discriminators
                    else (
                        " The instances are indistinguishable (no field value is "
                        "unique to one of them) — the file needs a hand fix first."
                    )
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
            discriminators = describe_instances(matches)
            guidance = (
                f" Each instance differs by: {discriminators}. Re-call with "
                "'block_key' set to one of these uniquely-identifying values "
                "(e.g. the instance's <hook_url> or other distinguishing field)."
                if discriminators
                else (
                    " The matching instances are truly indistinguishable (no field "
                    "value is unique to one of them) — the file needs a hand fix "
                    "before Wolf can address that instance."
                )
            )
            return self._refused(
                query,
                f"{len(matches)} <{section}> blocks match the key {block_key!r} — "
                "ambiguous." + guidance,
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

    async def _capture_cluster(
        self,
        exec_ctx: ToolExecContext,
        query: dict[str, Any],
        nodes: list[ManagerNode],
        op: str,
        section: str,
        block_key: str,
        new_block: str,
    ) -> tuple[str, dict[str, str]] | ProposeConfigChangeOutput:
        """Per-node capture + dry-run for a distributed deployment (6-f.6).

        Returns ``(preview_current, node_targets)`` where ``node_targets`` maps
        each IN-SCOPE node → its own current target content (the per-node
        staleness base; ``""`` when the change ADDS to that node), or a guided
        refusal.  Scope respects instance locality: a single-instance section
        (``update_section``) is uniform cluster-wide (every node in scope; ADD
        where absent); a repeated instance (``upsert_block`` / ``remove_block``)
        touches only the nodes that actually carry it — EXCEPT an upsert whose
        instance exists on NO node, which is a cluster-wide ADD.  A node where
        the op can't apply cleanly (ambiguous key / >1 section block) refuses,
        naming the node."""
        node_raw: dict[str, str] = {}
        for node in nodes:
            try:
                node_raw[node.name] = await exec_ctx.server_api.get_raw(
                    node_configuration_path(node.name), params={"raw": "true"}
                )
            except Exception as exc:  # noqa: BLE001 — surfaced as a guided refusal
                return self._refused(
                    query,
                    f"Could not read the configuration of cluster node "
                    f"{node.name!r} ({exc}); a distributed config change needs "
                    "every node's current content.",
                    f"Not proposed — node {node.name!r} configuration unreadable.",
                )

        if op == OP_UPDATE_SECTION:
            targets: dict[str, str] = {}
            for name, raw in node_raw.items():
                blocks = find_section_blocks(raw, section)
                if len(blocks) > 1:
                    return self._refused(
                        query,
                        f"Section <{section}> appears {len(blocks)} times on cluster "
                        f"node {name!r}; replacing 'the' block is ambiguous. Use "
                        "'upsert_block' with a unique field value.",
                        f"Not proposed — <{section}> appears more than once on {name!r}.",
                    )
                targets[name] = blocks[0].strip() if blocks else ""
        else:
            per_node: dict[str, list[str]] = {}
            for name, raw in node_raw.items():
                matches = find_identified_blocks(raw, section, block_key)
                if len(matches) > 1:
                    discriminators = describe_instances(matches)
                    guidance = (
                        f" Each differs by: {discriminators}. Re-call with a unique value."
                        if discriminators
                        else f" They are indistinguishable — hand-fix node {name!r} first."
                    )
                    return self._refused(
                        query,
                        f"{len(matches)} <{section}> blocks match {block_key!r} on "
                        f"cluster node {name!r} — ambiguous." + guidance,
                        f"Not proposed — multiple <{section}> blocks match "
                        f"'{block_key}' on {name!r}.",
                    )
                per_node[name] = matches
            total = sum(len(m) for m in per_node.values())
            if op == OP_REMOVE_BLOCK and total == 0:
                return self._refused(
                    query,
                    f"No <{section}> block with key {block_key!r} exists on any "
                    "cluster node — nothing to remove.",
                    f"Not proposed — no <{section}> block matches '{block_key}'.",
                )
            if op == OP_UPSERT_BLOCK and total == 0:
                # New instance, present nowhere → a uniform cluster-wide ADD.
                targets = dict.fromkeys(node_raw, "")
            else:
                # Update/remove touches only the nodes that carry the instance.
                targets = {name: m[0].strip() for name, m in per_node.items() if m}

        for name in targets:
            if build_candidate(node_raw[name], op, section, block_key, new_block) is None:
                return self._refused(
                    query,
                    f"The change does not apply cleanly to cluster node {name!r} "
                    f"({OP_LABELS.get(op, op)!r} on <{section}>"
                    + (f" '{block_key}'" if block_key else "")
                    + "); that node's file may be malformed or the target ambiguous.",
                    f"Not proposed — the change does not apply cleanly on {name!r}.",
                )

        # Preview base for the analyst: the master's target content (fall back
        # to the first in-scope node when the master isn't touched).
        master_name = nodes[0].name
        preview_current = targets.get(master_name, next(iter(targets.values())))
        return preview_current, targets

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
