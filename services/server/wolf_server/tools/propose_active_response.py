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
from wolf_server.tools.base import Citation, ProposeTool, ToolExecContext
from wolf_server.wazuh.active_response import (
    INTENT_LABELS,
    classify_os,
    resolve_intent_command,
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
            "Wazuh agent). Never name a low-level command (firewall-drop, netsh, …)."
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
    rationale: str = Field(
        default="",
        description=(
            "Why this action is warranted, in plain language. Strongly preferred "
            "— the human approver relies on it. If you omit it the proposal still "
            "queues, but Wolf records that no rationale was given."
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
        "Propose an active-response command on ONE resolved agent (block an IP, "
        "disable an account, restart an agent). Does NOT execute — it queues a "
        "proposal a human must approve first."
    )
    InputModel: ClassVar[type[BaseModel]] = ProposeActiveResponseInput
    OutputModel: ClassVar[type[BaseModel]] = ProposeActiveResponseOutput

    async def run(
        self, exec_ctx: ToolExecContext, args: BaseModel
    ) -> ProposeActiveResponseOutput:
        assert isinstance(args, ProposeActiveResponseInput)  # noqa: S101 — validated by dispatcher
        ctx = exec_ctx.organization
        target = {"agent_id": args.agent_id}
        # The call itself is the citation (sanitized args); result_count flips
        # 1 (queued) vs 0 (refused) per outcome below.
        query = args.model_dump(mode="json")

        # Resolve the agent's live OS, then DETERMINISTICALLY pick the
        # platform-correct command for the high-level intent (slice 6-c). The
        # model never names firewall-drop vs netsh — Wolf selects it from the
        # catalog given the agent's OS, so a wrong-platform pick is impossible.
        agent_os = await resolve_agent_os(exec_ctx.server_api, args.agent_id)
        os_class = classify_os(agent_os)
        resolution = resolve_intent_command(args.intent, os_class)
        if not resolution.ok:
            return ProposeActiveResponseOutput(
                permitted=False,
                state="rejected",
                summary="Not proposed — could not select a command for this intent.",
                detail=resolution.reason,
                citation=self.make_citation(query, result_count=0),
            )
        command = resolution.command

        # Structured params frozen into the (content-hashed) proposal: the
        # operator's intent, the target, and the raw OS signal the validator
        # backstop re-derives platform fit from.
        parameters: dict[str, str] = {"intent": args.intent}
        if args.srcip.strip():
            parameters["srcip"] = args.srcip.strip()
        if args.username.strip():
            parameters["username"] = args.username.strip()
        if agent_os:
            parameters["agent_os"] = agent_os

        # 1. Structural action validator (hard gate — never reaches the queue if it
        #    fails). Wolf already picked a platform-correct command, so the
        #    validator's platform-fit check is a defense-in-depth backstop here; it
        #    still enforces target presence / well-formedness (e.g. a valid srcip).
        verdict = validate_proposal(
            action_class=_ACTION_CLASS,
            target=target,
            action=command,
            parameters=parameters,
        )
        if not verdict.ok:
            return ProposeActiveResponseOutput(
                permitted=False,
                state="rejected",
                summary="Proposal rejected by the action validator.",
                detail=verdict.reason,
                citation=self.make_citation(query, result_count=0),
            )

        # 2. Capability pre-flight — the credential must be RBAC-allowed this action,
        #    expanded over the agent's live group memberships (Wazuh RBAC authorizes
        #    an agent action by id OR by any group the agent is in).
        capabilities = await fetch_credential_capabilities(exec_ctx.server_api)
        agent_groups = await resolve_agent_groups(exec_ctx.server_api, args.agent_id)
        if not capabilities.can_on_agent(ACTION_ACTIVE_RESPONSE, args.agent_id, agent_groups):
            groups = ", ".join(agent_groups) or "none"
            return ProposeActiveResponseOutput(
                permitted=False,
                state="rejected",
                summary="Not proposed — the Wazuh credential is not authorized for this action.",
                detail=(
                    f"Credential lacks active-response permission on agent {args.agent_id} "
                    f"(groups: {groups}). "
                    "Wolf only offers actions the credential's Wazuh RBAC permits."
                ),
                citation=self.make_citation(query, result_count=0),
            )

        # 3. Persist the pending proposal (the queue).  db is wired by the dispatcher.
        if exec_ctx.db is None:  # pragma: no cover — always wired in the live path
            raise RuntimeError("propose_active_response requires a DB session in the exec context")
        target_phrase = (
            f" (block {parameters['srcip']})"
            if "srcip" in parameters
            else f" (disable {parameters['username']})"
            if "username" in parameters
            else ""
        )
        # Surface BOTH the operator's intent and the command Wolf selected (plus
        # the OS that drove the choice) so the approver sees exactly what runs.
        intent_label = INTENT_LABELS.get(args.intent, args.intent)
        os_phrase = f" on a {os_class} agent" if os_class else ""
        expected = args.expected_effect or (
            f"{intent_label} on agent {args.agent_id}{target_phrase}{os_phrase} "
            f"via active-response '{command}'."
        )
        # Rationale is optional at the schema level (the model frequently omits
        # it, which must not hard-fail the whole proposal); record an honest
        # placeholder so the approver knows none was given rather than seeing a
        # fabricated one.
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
                f"via '{command}'; awaiting approval before it runs."
            ),
            citation=self.make_citation(query, result_count=1),
        )
