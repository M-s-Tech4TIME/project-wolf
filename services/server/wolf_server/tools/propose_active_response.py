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

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from wolf_server.gateway.models import ActionProposal
from wolf_server.gateway.proposals import (
    create_proposal,
    create_reversal_proposal,
    find_active_block,
)
from wolf_server.gateway.validator import validate_proposal
from wolf_server.tools.base import Citation, ProposeTool, ToolExecContext
from wolf_server.wazuh.active_response import (
    INTENT_LABELS,
    INTENT_TARGETS,
    REVERSE_INTENTS,
    TARGET_SRCIP,
    TARGET_USERNAME,
    classify_os,
    get_ar_command,
    parse_duration,
    resolve_intent_command,
    resolve_method_command,
)
from wolf_server.wazuh.capabilities import (
    ACTION_ACTIVE_RESPONSE,
    fetch_credential_capabilities,
    resolve_agent_groups,
    resolve_agent_os,
)

_ACTION_CLASS = "active_response"


class ProposeActiveResponseInput(BaseModel):
    agent_id: str = Field(
        description=(
            "The EXACT Wazuh agent id the user is targeting — taken from their "
            "request or resolved via list_agents/get_agent_detail. Never default "
            "or guess; if unknown, resolve it first."
        )
    )
    intent: str = Field(
        description=(
            "The high-level action to take — express the INTENT, not a specific "
            "command. Wolf resolves the agent's OS and picks the platform-correct "
            "active-response command itself. One of: 'block_ip' (block a source "
            "IP), 'disable_user' (disable a local account), 'restart' (restart the "
            "Wazuh agent), 'unblock_ip' (UNDO a prior IP block), 'enable_user' "
            "(UNDO a prior account disable). Never name a low-level command "
            "(firewall-drop, netsh, …); for an undo, Wolf recalls why the original "
            "block was made and reverses the exact command it used."
        )
    )
    srcip: str = Field(
        default="",
        description=(
            "For intent 'block_ip': the source IP to block, taken from the user's "
            "request or the alert (IPv4 or IPv6). Do not fill in a placeholder."
        ),
    )
    username: str = Field(
        default="",
        description="For intent 'disable_user': the local username to disable.",
    )
    method: str = Field(
        default="",
        description=(
            "Optional. A specific active-response command to use INSTEAD of Wolf's "
            "automatic platform choice — e.g. 'host-deny', 'route-null', 'ipfw', "
            "'opnsense-fw'. Leave EMPTY normally (let Wolf pick the platform-correct "
            "command). Set it only when the user explicitly asks for a particular "
            "mechanism, or to proceed when the agent's OS could not be auto-detected "
            "(Wolf will use it on the platform the user asserts)."
        ),
    )
    block_duration: str = Field(
        default="",
        description=(
            "Optional, for a TIMED block (intent 'block_ip'/'disable_user'): how "
            "long to keep it in place before Wolf AUTOMATICALLY reverses it — e.g. "
            "'30m', '1h', '2d' (bare numbers are seconds). Set it ONLY when the "
            "user asks to block for a specific time ('block X for an hour'). Leave "
            "EMPTY for an indefinite block. Ignored for 'restart' and for undo "
            "intents."
        ),
    )
    rationale: str = Field(
        default="",
        description=(
            "Why this action is warranted, in plain language. Strongly preferred "
            "— the human approver relies on it. If you omit it the proposal still "
            "queues, but Wolf records that no rationale was given. For an undo, "
            "Wolf recalls the original block's rationale automatically."
        ),
    )
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
    # Like every tool, the propose tool emits a citation so its call shows in
    # the Evidence panel and is grounding-traceable (the proposal IS evidence
    # of an action Wolf took on the user's behalf).
    citation: Citation


class ProposeActiveResponseTool(ProposeTool):
    """Propose an active-response command on one resolved agent (awaits approval)."""

    name: ClassVar[str] = "propose_active_response"
    description: ClassVar[str] = (
        "Propose an active-response action on ONE resolved agent — block an IP, "
        "disable an account, restart an agent, OR UNDO a prior action: 'unblock_ip' "
        "(unblock a blocked IP) and 'enable_user' (re-enable a disabled account) "
        "ARE supported. Does NOT execute — it queues a proposal a human approves. "
        "When it returns permitted=true the proposal IS queued (state 'pending'); "
        "report that — never tell the user an unblock/undo is unsupported."
    )
    InputModel: ClassVar[type[BaseModel]] = ProposeActiveResponseInput
    OutputModel: ClassVar[type[BaseModel]] = ProposeActiveResponseOutput

    async def run(
        self, exec_ctx: ToolExecContext, args: BaseModel
    ) -> ProposeActiveResponseOutput:
        assert isinstance(args, ProposeActiveResponseInput)  # noqa: S101 — validated by dispatcher
        if exec_ctx.db is None:  # pragma: no cover — always wired in the live path
            raise RuntimeError("propose_active_response requires a DB session in the exec context")
        # The call itself is the citation (sanitized args); result_count flips
        # 1 (queued) vs 0 (refused) per outcome below.
        query = args.model_dump(mode="json")
        # An UNDO (slice 6-d) recalls the original block + reverses its exact
        # command — a different path from a fresh forward action.
        if args.intent in REVERSE_INTENTS:
            return await self._propose_reversal(exec_ctx, args, query)
        return await self._propose_forward(exec_ctx, args, query)

    # ── refusal / shared helpers ────────────────────────────────────────────

    def _refused(
        self, query: dict[str, Any], detail: str, summary: str
    ) -> ProposeActiveResponseOutput:
        return ProposeActiveResponseOutput(
            permitted=False,
            state="rejected",
            summary=summary,
            detail=detail,
            citation=self.make_citation(query, result_count=0),
        )

    async def _capability_ok(
        self, exec_ctx: ToolExecContext, agent_id: str
    ) -> tuple[bool, list[str]]:
        """Credential RBAC pre-flight, expanded over the agent's live groups."""
        capabilities = await fetch_credential_capabilities(exec_ctx.server_api)
        agent_groups = await resolve_agent_groups(exec_ctx.server_api, agent_id)
        return (
            capabilities.can_on_agent(ACTION_ACTIVE_RESPONSE, agent_id, agent_groups),
            agent_groups,
        )

    @staticmethod
    def _block_recall(block: ActionProposal) -> str:
        """A one-line reminder of why the original block was made (ADR 0028)."""
        when = block.executed_at or block.created_at
        when_s = when.strftime("%Y-%m-%d %H:%M UTC") if when else "an earlier time"
        ev = block.evidence if isinstance(block.evidence, dict) else {}
        raw_alerts = ev.get("alert_ids")
        alert_ids = raw_alerts if isinstance(raw_alerts, list) else []
        ev_s = f" (evidence: alert(s) {', '.join(str(a) for a in alert_ids)})" if alert_ids else ""
        return f"It was blocked on {when_s} — reason: {block.rationale}{ev_s}."

    # ── forward action (block / disable / restart) ──────────────────────────

    async def _propose_forward(
        self, exec_ctx: ToolExecContext, args: ProposeActiveResponseInput, query: dict[str, Any]
    ) -> ProposeActiveResponseOutput:
        ctx = exec_ctx.organization
        assert exec_ctx.db is not None  # noqa: S101 — guarded in run()
        target: dict[str, Any] = {"agent_id": args.agent_id}

        # Resolve the agent's live OS, then pick the command (6-c). An optional
        # `method` overrides the auto-pick (6-c.2b): platform-fit when the OS is
        # known; the user-guided failover when it is unknown.
        agent_os = await resolve_agent_os(exec_ctx.server_api, args.agent_id)
        os_class = classify_os(agent_os)
        method = args.method.strip()
        if method:
            resolution = resolve_method_command(args.intent, method, os_class)
            method_source = "override" if os_class is not None else "user_asserted"
        else:
            resolution = resolve_intent_command(args.intent, os_class, os_signal=agent_os)
            method_source = "auto"
        if not resolution.ok:
            return self._refused(
                query, resolution.reason,
                "Not proposed — could not select a command for this intent.",
            )
        command = resolution.command

        parameters: dict[str, Any] = {"intent": args.intent, "method_source": method_source}
        if args.srcip.strip():
            parameters["srcip"] = args.srcip.strip()
        if args.username.strip():
            parameters["username"] = args.username.strip()
        if agent_os:
            parameters["agent_os"] = agent_os

        # Timed block (6-d): parse + bound the duration; refuse it on a
        # non-reversible action (a restart has nothing to auto-reverse).
        duration_note = ""
        if args.block_duration.strip():
            cmd = get_ar_command(command)
            if cmd is None or not cmd.reversible:
                return self._refused(
                    query,
                    f"Intent {args.intent!r} (command {command!r}) is not reversible, "
                    "so a block duration can't be honoured — omit block_duration.",
                    "Not proposed — a duration was given for a non-reversible action.",
                )
            try:
                seconds = parse_duration(args.block_duration)
            except ValueError as exc:
                return self._refused(query, str(exc), "Not proposed — invalid block duration.")
            parameters["block_duration_seconds"] = seconds
            duration_note = (
                f" for {args.block_duration.strip()} (Wolf auto-reverses it on expiry)"
            )

        verdict = validate_proposal(
            action_class=_ACTION_CLASS, target=target, action=command, parameters=parameters
        )
        if not verdict.ok:
            return self._refused(
                query, verdict.reason, "Proposal rejected by the action validator."
            )

        ok, agent_groups = await self._capability_ok(exec_ctx, args.agent_id)
        if not ok:
            groups = ", ".join(agent_groups) or "none"
            return self._refused(
                query,
                f"Credential lacks active-response permission on agent {args.agent_id} "
                f"(groups: {groups}). Wolf only offers actions the credential's Wazuh "
                "RBAC permits.",
                "Not proposed — the Wazuh credential is not authorized for this action.",
            )

        # Dedup context: surface an existing active block for the same target so a
        # re-block is a deliberate choice, not an accidental duplicate (ADR 0028).
        dedup_note = ""
        existing = await find_active_block(
            exec_ctx.db,
            organization_id=ctx.organization_id,
            action_class=_ACTION_CLASS,
            agent_id=args.agent_id,
            srcip=parameters.get("srcip"),
            username=parameters.get("username"),
        )
        if existing is not None:
            tgt = parameters.get("srcip") or parameters.get("username")
            dedup_note = (
                f" Note: {tgt} already has an active block on agent {args.agent_id} "
                f"(proposal {existing.id}). {self._block_recall(existing)}"
            )

        intent_label = INTENT_LABELS.get(args.intent, args.intent)
        target_phrase = (
            f" (block {parameters['srcip']})"
            if "srcip" in parameters
            else f" (disable {parameters['username']})"
            if "username" in parameters
            else ""
        )
        if method_source == "user_asserted":
            os_phrase = " (OS not auto-detected — proceeding on the requester's asserted platform)"
        elif method_source == "override":
            os_phrase = f" on a {os_class} agent (operator-chosen method)"
        else:
            os_phrase = f" on a {os_class} agent" if os_class else ""
        expected = args.expected_effect or (
            f"{intent_label} on agent {args.agent_id}{target_phrase}{os_phrase} "
            f"via active-response '{command}'{duration_note}."
        )
        rationale = (
            args.rationale.strip() or "Requested via chat — no explicit rationale was given."
        )
        proposal = await create_proposal(
            exec_ctx.db,
            organization_id=ctx.organization_id,
            requested_by=ctx.user_id,
            action_class=_ACTION_CLASS,
            target=target,
            action=command,
            parameters=parameters,
            rationale=rationale,
            expected_effect=expected,
            evidence={"alert_ids": args.alert_ids},
            session_id=ctx.session_id,
        )
        return ProposeActiveResponseOutput(
            permitted=True,
            state="pending",
            proposal_id=str(proposal.id),
            summary=(
                f"Proposed {intent_label.lower()} on agent {args.agent_id}{target_phrase} "
                f"via '{command}'{duration_note}; awaiting approval before it runs.{dedup_note}"
            ),
            citation=self.make_citation(query, result_count=1),
        )

    # ── reversal (unblock / re-enable) ──────────────────────────────────────

    async def _propose_reversal(
        self, exec_ctx: ToolExecContext, args: ProposeActiveResponseInput, query: dict[str, Any]
    ) -> ProposeActiveResponseOutput:
        ctx = exec_ctx.organization
        assert exec_ctx.db is not None  # noqa: S101 — guarded in run()
        if args.method.strip():
            return self._refused(
                query,
                "An undo reverses the exact command the original block used — don't "
                "pass a `method`. Give just the agent + the IP/username to undo.",
                "Not proposed — `method` is not used for an undo.",
            )
        intent_target = INTENT_TARGETS.get(args.intent)
        srcip = args.srcip.strip() if intent_target == TARGET_SRCIP else ""
        username = args.username.strip() if intent_target == TARGET_USERNAME else ""
        if intent_target == TARGET_SRCIP and not srcip:
            return self._refused(
                query, "To undo an IP block, give the IP to unblock (srcip).",
                "Not proposed — no IP given to unblock.",
            )
        if intent_target == TARGET_USERNAME and not username:
            return self._refused(
                query, "To re-enable an account, give the username.",
                "Not proposed — no username given to re-enable.",
            )

        block = await find_active_block(
            exec_ctx.db,
            organization_id=ctx.organization_id,
            action_class=_ACTION_CLASS,
            agent_id=args.agent_id,
            srcip=srcip or None,
            username=username or None,
        )
        if block is None:
            tgt = srcip or username
            return self._refused(
                query,
                f"I have no record of an active block on {tgt!r} for agent "
                f"{args.agent_id}. Wolf only tracks blocks it dispatched, and it can't "
                "verify live host state until wolf-pack — check `list_active_blocks`. "
                "If it was blocked outside Wolf, there's nothing here to reverse.",
                "Not proposed — no matching active block on record.",
            )

        # The undo runs the SAME command the block used (its delete-inverse).
        command = block.action
        ok, agent_groups = await self._capability_ok(exec_ctx, args.agent_id)
        if not ok:
            groups = ", ".join(agent_groups) or "none"
            return self._refused(
                query,
                f"Credential lacks active-response permission on agent {args.agent_id} "
                f"(groups: {groups}).",
                "Not proposed — the Wazuh credential is not authorized for this action.",
            )

        block_os = block.parameters.get("agent_os") if isinstance(block.parameters, dict) else None
        parameters: dict[str, Any] = {
            "intent": args.intent,
            "method_source": "reversal",
            "reversal": True,
        }
        if srcip:
            parameters["srcip"] = srcip
        if username:
            parameters["username"] = username
        if isinstance(block_os, str):
            parameters["agent_os"] = block_os

        verdict = validate_proposal(
            action_class=_ACTION_CLASS, target=block.target, action=command, parameters=parameters
        )
        if not verdict.ok:
            return self._refused(
                query, verdict.reason, "Proposal rejected by the action validator."
            )

        intent_label = INTENT_LABELS.get(args.intent, args.intent)
        cmd = get_ar_command(command)
        reverses_via = cmd.reverses_via if cmd is not None else ""
        recall = self._block_recall(block)
        rationale = args.rationale.strip() or f"Undo of the earlier block. {recall}"
        expected = args.expected_effect or (
            f"{intent_label} on agent {args.agent_id} ({srcip or username}) — reverses "
            f"'{command}'. {reverses_via} Physical removal is performed by wolf-pack "
            "(Phase 12); the block stays in effect until then."
        )
        orig_alerts = (
            block.evidence.get("alert_ids", []) if isinstance(block.evidence, dict) else []
        )
        evidence = {
            "reverses_proposal_id": str(block.id),
            "original_rationale": block.rationale,
            "original_alert_ids": orig_alerts,
        }
        proposal = await create_reversal_proposal(
            exec_ctx.db,
            block,
            requested_by=ctx.user_id,
            action=command,
            parameters=parameters,
            rationale=rationale,
            expected_effect=expected,
            evidence=evidence,
            session_id=ctx.session_id,
        )
        return ProposeActiveResponseOutput(
            permitted=True,
            state="pending",
            proposal_id=str(proposal.id),
            summary=(
                f"Proposed {intent_label.lower()} on agent {args.agent_id} "
                f"({srcip or username}). {recall} Awaiting approval; the physical "
                "removal runs via wolf-pack."
            ),
            citation=self.make_citation(query, result_count=1),
        )
