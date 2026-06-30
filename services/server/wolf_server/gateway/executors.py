"""Per-class execution composition — Phase 6-e (ADR 0029).

:func:`wolf_server.gateway.execution.execute_proposal` is generic: it drives an
approved proposal through freshness → perform → verify using *injected*
callables.  This module supplies those callables **per ``action_class``**, for
both the forward action and its reversal, so the API approve handler dispatches
by class instead of hard-coding active-response.

Two reversal models (ADR 0029): active-response is **wolf-pack-bound**
(record-only — :mod:`wolf_server.gateway.reversal`); API-executable classes
(agent_action, rule_tuning, config_change) perform the **real inverse** — their
executors define ``build_reverse`` accordingly.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from wolf_common.errors import WolfError

from wolf_server.gateway.models import ActionProposal, ProposalState
from wolf_server.gateway.reversal import (
    REVERSAL_STATE_COMPLETED,
    reversal_freshness,
    reversal_perform,
    reversal_verify,
)
from wolf_server.wazuh.active_response import interpret_ar_result
from wolf_server.wazuh.agent_actions import OP_ASSIGN_GROUP
from wolf_server.wazuh.capabilities import (
    ACTION_UPDATE_RULES,
    RESOURCE_LOCAL_RULES,
    resolve_agent_groups,
)
from wolf_server.wazuh.rule_tuning import (
    DISABLE_LEVEL,
    LOCAL_RULES_DIRNAME,
    LOCAL_RULES_FILENAME,
    OP_DISABLE_RULE,
    apply_override,
    build_override_block,
    extract_rule_block,
    has_override,
    minimal_override_block,
)


class RulesetValidationError(WolfError):
    """The edited ruleset failed ``GET /manager/configuration/validation`` — the
    write was auto-rolled-back (the prior file restored) and never applied."""

    http_status = 422
    error_code = "ruleset_validation_failed"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _validation_ok(body: dict[str, Any]) -> tuple[bool, str]:
    """Parse ``GET /manager/configuration/validation`` — ok iff no failed items
    and every node reports ``status: OK``."""
    data = body.get("data", {}) if isinstance(body, dict) else {}
    if data.get("total_failed_items"):
        failed = data.get("failed_items", [])
        return False, f"validation failed: {str(failed)[:300]}"
    items = data.get("affected_items", [])
    if isinstance(items, list):
        bad = [it for it in items if isinstance(it, dict) and it.get("status") != "OK"]
        if bad:
            return False, f"node validation not OK: {str(bad)[:300]}"
    return True, "ruleset valid on all nodes"

# The callable shapes execute_proposal consumes (mirrors gateway/execution.py).
Freshness = Callable[[ActionProposal], Awaitable[tuple[bool, str]]]
Perform = Callable[[ActionProposal], Awaitable[dict[str, Any]]]
Verify = Callable[[ActionProposal, dict[str, Any]], Awaitable[tuple[bool, dict[str, Any]]]]
Callables = tuple[Freshness, Perform, Verify]


@dataclass
class ExecContext:
    """What a class executor may use to build its (freshness, perform, verify).

    Wazuh clients are typed ``Any`` to avoid an import cycle (matches
    ``ToolExecContext``); the API layer composes them from the per-org creds.
    """

    read_api: Any  # WazuhServerApiClient (read-only)
    action_api: Any  # WazuhServerApiActionClient (bounded writes)
    capabilities: Any  # CredentialCapabilities
    db: Any  # AsyncSession


class ClassExecutor(Protocol):
    """Builds the execute callables for one action class (forward + reverse)."""

    def build_forward(self, proposal: ActionProposal, ctx: ExecContext) -> Callables: ...
    def build_reverse(self, proposal: ActionProposal, ctx: ExecContext) -> Callables: ...


class UnknownActionClassError(WolfError):
    """No executor is registered for the proposal's action class."""

    http_status = 500
    error_code = "unknown_action_class"


_EXECUTORS: dict[str, ClassExecutor] = {}


def register_executor(action_class: str, executor: ClassExecutor) -> None:
    """Register the execution composer for an action class (ADR 0029)."""
    _EXECUTORS[action_class] = executor


def get_executor(action_class: str) -> ClassExecutor:
    """The executor for ``action_class`` (raises if none — the validator already
    refuses an unregistered class at propose time, so this is a backstop)."""
    executor = _EXECUTORS.get(action_class)
    if executor is None:
        raise UnknownActionClassError(f"No executor registered for action class {action_class!r}.")
    return executor


class _ActiveResponseExecutor:
    """Active-response: forward dispatches the AR command via the bounded write
    client; reverse is **wolf-pack-bound** (record-only — the API can't dispatch
    a `delete`, ADR 0028)."""

    def build_forward(self, proposal: ActionProposal, ctx: ExecContext) -> Callables:
        read_api, action_api, capabilities = ctx.read_api, ctx.action_api, ctx.capabilities

        async def _freshness(p: ActionProposal) -> tuple[bool, str]:
            agent_id = str(p.target.get("agent_id", ""))
            body = await read_api.get("/agents", params={"agents_list": agent_id})
            total = body.get("data", {}).get("total_affected_items", 0)
            if total and total >= 1:
                return True, f"Agent {agent_id} still present."
            return False, f"Agent {agent_id} is no longer visible to the credential."

        async def _perform(p: ActionProposal) -> dict[str, Any]:
            agent_id = str(p.target.get("agent_id", ""))
            params = p.parameters if isinstance(p.parameters, dict) else {}
            raw_args = params.get("arguments", [])
            arguments = [str(a) for a in raw_args] if isinstance(raw_args, list) else []
            srcip = params.get("srcip")
            username = params.get("username")
            # Resolve groups fresh — the capability check expands the grant over
            # current group membership (Wazuh RBAC semantics).
            agent_groups = await resolve_agent_groups(read_api, agent_id)
            result: dict[str, Any] = await action_api.execute_active_response(
                agent_id=agent_id,
                command=p.action,
                capabilities=capabilities,
                agent_groups=agent_groups,
                srcip=srcip if isinstance(srcip, str) else None,
                username=username if isinstance(username, str) else None,
                arguments=arguments,
            )
            return result

        async def _verify(p: ActionProposal, res: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
            # HTTP 200 even on failure — interpret_ar_result reads dispatch from
            # the body and is honest that "dispatched" != "applied on the host".
            return interpret_ar_result(res)

        return _freshness, _perform, _verify

    def build_reverse(self, proposal: ActionProposal, ctx: ExecContext) -> Callables:
        db = ctx.db

        async def _freshness(p: ActionProposal) -> tuple[bool, str]:
            return await reversal_freshness(db, p)

        return _freshness, reversal_perform, reversal_verify


register_executor("active_response", _ActiveResponseExecutor())


class _AgentActionExecutor:
    """agent_action (group management, 6-e.2): forward assigns/removes a group via
    the bounded write client; reverse performs the **inverse op for real** (API-
    executable, not wolf-pack-bound) and marks the result completed so the API
    flips the original to ``rolled_back`` (:func:`reversal.complete_api_reversal`).

    The proposal's ``action`` is the operation (assign_group / remove_group) and a
    reversal proposal already carries the *inverse* op — so reverse reuses the
    forward perform, only tagging the verify result completed."""

    def build_forward(self, proposal: ActionProposal, ctx: ExecContext) -> Callables:
        read_api, action_api, capabilities = ctx.read_api, ctx.action_api, ctx.capabilities

        async def _freshness(p: ActionProposal) -> tuple[bool, str]:
            agent_id = str(p.target.get("agent_id", ""))
            body = await read_api.get("/agents", params={"agents_list": agent_id})
            total = body.get("data", {}).get("total_affected_items", 0)
            if total and total >= 1:
                return True, f"Agent {agent_id} still present."
            return False, f"Agent {agent_id} is no longer visible to the credential."

        async def _perform(p: ActionProposal) -> dict[str, Any]:
            agent_id = str(p.target.get("agent_id", ""))
            params = p.parameters if isinstance(p.parameters, dict) else {}
            group = str(params.get("group", ""))
            agent_groups = await resolve_agent_groups(read_api, agent_id)
            if p.action == OP_ASSIGN_GROUP:
                res: dict[str, Any] = await action_api.assign_agent_group(
                    agent_id=agent_id, group=group, capabilities=capabilities,
                    agent_groups=agent_groups,
                )
            else:
                res = await action_api.remove_agent_group(
                    agent_id=agent_id, group=group, capabilities=capabilities,
                    agent_groups=agent_groups,
                )
            return res

        async def _verify(p: ActionProposal, res: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
            # Authoritative end-state: re-read the agent's groups (the manager
            # reflects the membership change immediately) rather than trusting the
            # write response (doc 04 §verification read).
            agent_id = str(p.target.get("agent_id", ""))
            params = p.parameters if isinstance(p.parameters, dict) else {}
            group = str(params.get("group", ""))
            groups = await resolve_agent_groups(read_api, agent_id)
            in_group = group in groups
            ok = in_group if p.action == OP_ASSIGN_GROUP else not in_group
            detail: dict[str, Any] = {
                "ok": ok,
                "agent_groups": groups,
                "note": f"agent {agent_id} group membership after {p.action} '{group}'.",
            }
            return ok, detail

        return _freshness, _perform, _verify

    def build_reverse(self, proposal: ActionProposal, ctx: ExecContext) -> Callables:
        freshness, perform, verify = self.build_forward(proposal, ctx)

        async def _verify_reverse(
            p: ActionProposal, res: dict[str, Any]
        ) -> tuple[bool, dict[str, Any]]:
            ok, detail = await verify(p, res)
            # Tag a successful API-executable undo so the original flips to
            # rolled_back (vs AR's wolf-pack-pending marker).
            detail["reversal_state"] = REVERSAL_STATE_COMPLETED if ok else "failed"
            return ok, detail

        return freshness, perform, _verify_reverse


register_executor("agent_action", _AgentActionExecutor())


class _RuleTuningExecutor:
    """rule_tuning (6-e.3): forward writes an ``overwrite="yes"`` override into
    ``local_rules.xml`` and APPLIES it (validate → cluster restart, with an
    auto-rollback if the edited ruleset does not compile); reverse performs a
    **real undo** by PUTting the captured ``prior_state`` snapshot back. Both
    directions run through the Server API (snapshot-restore, not wolf-pack-bound —
    ADR 0029 §2), so a succeeded reverse flips the original to ``rolled_back``."""

    @staticmethod
    async def _rule_entries(read_api: Any, rule_id: str) -> list[dict[str, Any]]:
        """All parsed definitions of ``rule_id`` (each: level + filename).

        ``GET /rules`` reflects the ON-DISK ruleset *immediately* (verified live)
        and returns the original AND an ``overwrite="yes"`` entry as SEPARATE
        items — so callers must look across all of them, never ``items[0]``."""
        body = await read_api.get("/rules", params={"rule_ids": rule_id})
        items = body.get("data", {}).get("affected_items", []) if isinstance(body, dict) else []
        return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []

    @classmethod
    async def _resolve_rule(cls, read_api: Any, rule_id: str) -> dict[str, Any] | None:
        """The best single definition to use as the override SOURCE: prefer the
        ``local_rules.xml`` entry (where our override lives + the right body for a
        re-tune); else the first definition (e.g. a stock rule). ``None`` if the
        rule is unknown."""
        items = await cls._rule_entries(read_api, rule_id)
        if not items:
            return None
        for item in items:
            if item.get("filename") == LOCAL_RULES_FILENAME:
                return item
        return items[0]

    @staticmethod
    async def _put_local_rules(ctx: ExecContext, content: str) -> None:
        await ctx.action_api.update_rules_file(
            filename=LOCAL_RULES_FILENAME,
            content=content,
            capabilities=ctx.capabilities,
            relative_dirname=LOCAL_RULES_DIRNAME,
        )

    @classmethod
    async def _write_and_validate(
        cls, ctx: ExecContext, *, new_content: str, rollback_to: str | None
    ) -> str:
        """PUT ``new_content`` → validate → (restore ``rollback_to`` + raise on
        invalid).  Returns the validation detail.  Does NOT restart — the caller
        confirms the write persisted, THEN restarts (so the authoritative check
        runs before the cluster restart briefly takes the API down).  ``rollback_to``
        is the snapshot to auto-restore on a validation failure (None on a reverse —
        the restored file was valid when captured, so a failure is surfaced raw)."""
        await cls._put_local_rules(ctx, new_content)
        valid, detail = _validation_ok(await ctx.read_api.get("/manager/configuration/validation"))
        if not valid:
            if rollback_to is not None:
                await cls._put_local_rules(ctx, rollback_to)
                raise RulesetValidationError(
                    f"Edited ruleset rejected ({detail}); restored the prior "
                    "local_rules.xml. No change applied."
                )
            raise RulesetValidationError(f"Ruleset rejected ({detail}).")
        return detail

    @staticmethod
    async def _read_local_rules(ctx: ExecContext) -> str:
        raw: str = await ctx.read_api.get_raw(
            f"/rules/files/{LOCAL_RULES_FILENAME}",
            params={"raw": "true", "relative_dirname": LOCAL_RULES_DIRNAME},
        )
        return raw

    def build_forward(self, proposal: ActionProposal, ctx: ExecContext) -> Callables:
        read_api = ctx.read_api

        async def _freshness(p: ActionProposal) -> tuple[bool, str]:
            if not ctx.capabilities.can(ACTION_UPDATE_RULES, RESOURCE_LOCAL_RULES):
                return False, "Credential no longer holds rules:update (Superuser-scoped)."
            rule_id = str(p.target.get("rule_id", ""))
            rule = await self._resolve_rule(read_api, rule_id)
            if rule is None:
                return False, f"Rule {rule_id} is no longer present."
            return True, f"Rule {rule_id} present (level {rule.get('level')})."

        async def _perform(p: ActionProposal) -> dict[str, Any]:
            rule_id = str(p.target.get("rule_id", ""))
            params = p.parameters if isinstance(p.parameters, dict) else {}
            level_param = params.get("level")
            level = (
                DISABLE_LEVEL
                if p.action == OP_DISABLE_RULE or not isinstance(level_param, int)
                else level_param
            )
            rule = await self._resolve_rule(read_api, rule_id)
            if rule is None:
                raise RulesetValidationError(f"Rule {rule_id} not found; cannot tune.")
            src_file = str(rule.get("filename") or LOCAL_RULES_FILENAME)
            src_dir = str(rule.get("relative_dirname") or LOCAL_RULES_DIRNAME)

            # Snapshot local_rules.xml (the file we edit) — the undo restore point.
            snapshot = await read_api.get_raw(
                f"/rules/files/{LOCAL_RULES_FILENAME}",
                params={"raw": "true", "relative_dirname": LOCAL_RULES_DIRNAME},
            )
            snap_hash = _sha256(snapshot)
            p.prior_state = {
                "filename": LOCAL_RULES_FILENAME,
                "relative_dirname": LOCAL_RULES_DIRNAME,
                "content": snapshot,
                "sha256": snap_hash,
            }

            # The rule's source body — preserve its matching conditions in the override.
            source_raw = (
                snapshot
                if src_file == LOCAL_RULES_FILENAME
                else await read_api.get_raw(
                    f"/rules/files/{src_file}",
                    params={"raw": "true", "relative_dirname": src_dir},
                )
            )
            block = extract_rule_block(source_raw, rule_id)
            override = (
                build_override_block(block, level=level)
                if block is not None
                else minimal_override_block(
                    rule_id, level=level, description=f"Tuned by Wolf (level {level})"
                )
            )
            new_content = apply_override(snapshot, rule_id, override)
            validation = await self._write_and_validate(
                ctx, new_content=new_content, rollback_to=snapshot
            )

            # AUTHORITATIVE confirm — re-read local_rules.xml and prove OUR override
            # actually persisted (GET reflects the on-disk file immediately). If it
            # didn't, restore + fail honestly rather than report a phantom success.
            if not has_override(await self._read_local_rules(ctx), rule_id):
                await self._put_local_rules(ctx, snapshot)
                raise RulesetValidationError(
                    f"Override for rule {rule_id} did not persist to local_rules.xml; "
                    "restored the prior file. No change applied."
                )

            # Evidence (on-disk, pre-restart): GET /rules returns the original AND the
            # overwrite entry as separate items — collect all so verify checks the
            # target level across them, not items[0] (the phantom-no-op bug).
            entries = await self._rule_entries(read_api, rule_id)
            levels = [{"level": e.get("level"), "filename": e.get("filename")} for e in entries]
            local_levels = [
                e.get("level") for e in entries if e.get("filename") == LOCAL_RULES_FILENAME
            ]

            # Apply: reload the ruleset cluster-wide (analysisd loads it on restart —
            # ~18s in the live probe). The write is already committed + confirmed.
            await ctx.action_api.restart_cluster(capabilities=ctx.capabilities)
            return {
                "rule_id": rule_id,
                "target_level": level,
                "source_filename": src_file,
                "validation": validation,
                "restart_issued": True,
                "prior_sha256": snap_hash,
                "override_written": True,
                "levels": levels,
                "target_level_in_ruleset": level in local_levels,
            }

        async def _verify(p: ActionProposal, res: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
            # Already authoritative: _perform confirmed the override PERSISTED on disk
            # AND the ruleset VALIDATES (it raises + auto-restores otherwise). So reaching
            # here means the change is committed. We do NOT re-read here — that would race
            # the cluster restart _perform just issued (the API is briefly down). Surface
            # the evidence collected pre-restart.
            ok = bool(res.get("override_written"))
            detail: dict[str, Any] = {
                "ok": ok,
                "rule_id": res.get("rule_id"),
                "target_level": res.get("target_level"),
                "override_written": res.get("override_written"),
                "target_level_in_ruleset": res.get("target_level_in_ruleset"),
                "levels": res.get("levels"),
                "filename": LOCAL_RULES_FILENAME,
                "validation": res.get("validation"),
                "restart_issued": res.get("restart_issued", True),
                "note": (
                    "Override written to local_rules.xml + ruleset validated + cluster restart "
                    "issued. The change becomes active ~15-30s after the restart completes "
                    "(live reload measured ~18s)."
                ),
            }
            return ok, detail

        return _freshness, _perform, _verify

    def build_reverse(self, proposal: ActionProposal, ctx: ExecContext) -> Callables:
        db = ctx.db

        async def _freshness(p: ActionProposal) -> tuple[bool, str]:
            original = (
                await db.get(ActionProposal, p.reverses_proposal_id)
                if p.reverses_proposal_id is not None
                else None
            )
            if original is None:
                return False, "The original rule_tuning proposal is no longer present."
            if original.state == ProposalState.rolled_back:
                return False, "The rule change has already been reversed."
            prior = original.prior_state if isinstance(original.prior_state, dict) else None
            if not prior or not prior.get("content"):
                return False, "No captured prior_state snapshot to restore."
            return True, f"Prior local_rules.xml snapshot present (original {original.id})."

        async def _perform(p: ActionProposal) -> dict[str, Any]:
            original = await db.get(ActionProposal, p.reverses_proposal_id)
            prior = (
                original.prior_state
                if original is not None and isinstance(original.prior_state, dict)
                else {}
            )
            content = str(prior.get("content", ""))
            rule_id = str(original.target.get("rule_id", "")) if original is not None else ""
            validation = await self._write_and_validate(ctx, new_content=content, rollback_to=None)
            # Authoritative: confirm the override is GONE (file restored to pre-tuning).
            override_removed = (
                not has_override(await self._read_local_rules(ctx), rule_id) if rule_id else True
            )
            await ctx.action_api.restart_cluster(capabilities=ctx.capabilities)
            return {
                "restored_from": str(original.id) if original is not None else "",
                "rule_id": rule_id,
                "validation": validation,
                "restart_issued": True,
                "override_removed": override_removed,
            }

        async def _verify(p: ActionProposal, res: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
            # Snapshot-restore is an API-executable undo — tag it completed (only when
            # the override is confirmed gone) so the API flips the original to
            # rolled_back (reversal.complete_api_reversal).
            ok = bool(res.get("override_removed", True))
            detail: dict[str, Any] = {
                "ok": ok,
                "restored_from": res.get("restored_from"),
                "rule_id": res.get("rule_id"),
                "override_removed": res.get("override_removed", True),
                "validation": res.get("validation"),
                "restart_issued": res.get("restart_issued", True),
                "reversal_state": REVERSAL_STATE_COMPLETED if ok else "failed",
                "note": (
                    "Restored local_rules.xml to its pre-tuning snapshot (override removed) + "
                    "validated + cluster restart issued. The rule reverts ~15-30s after the "
                    "restart completes."
                ),
            }
            return ok, detail

        return _freshness, _perform, _verify


register_executor("rule_tuning", _RuleTuningExecutor())
