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
    ACTION_UPDATE_MANAGER_CONFIG,
    ACTION_UPDATE_RULES,
    RESOURCE_ANY,
    RESOURCE_LOCAL_RULES,
    resolve_agent_groups,
)
from wolf_server.wazuh.config_change import (
    OP_UPDATE_SECTION,
    OP_UPSERT_BLOCK,
    block_persisted,
    block_removed,
    build_candidate,
    find_identified_blocks,
    find_section_blocks,
    section_persisted,
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
                    agent_id=agent_id,
                    group=group,
                    capabilities=capabilities,
                    agent_groups=agent_groups,
                )
            else:
                res = await action_api.remove_agent_group(
                    agent_id=agent_id,
                    group=group,
                    capabilities=capabilities,
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


class ConfigValidationError(WolfError):
    """The edited ossec.conf failed validation / persistence — the write was
    auto-rolled-back (the prior file restored) and never applied."""

    http_status = 422
    error_code = "config_validation_failed"


class _ConfigChangeExecutor:
    """config_change (6-e.4, generalized 6-f.4 / ADR 0032 B): forward applies ONE
    authored change to the master's ``ossec.conf`` — replace/add a
    single-instance section (``update_section``) or add/update/remove ONE
    block-identity-addressed instance of a repeated section (``upsert_block`` /
    ``remove_block``) — and APPLIES it (validate → cluster restart, with
    auto-rollback if the edited configuration does not validate); reverse
    performs a **real undo** by PUTting the captured ``prior_state`` whole-file
    snapshot back (snapshot-restore, ADR 0029 §2), so a succeeded reverse flips
    the original to ``rolled_back``.

    Staleness is real here: the proposal froze the targeted content at propose
    time (``current_content`` — the approver's diff base; ``""`` when the change
    ADDS something new); if the live target no longer matches, the config
    changed under the proposal and freshness refuses (re-propose against the
    current file)."""

    @staticmethod
    async def _read_config(ctx: ExecContext) -> str:
        raw: str = await ctx.read_api.get_raw("/manager/configuration", params={"raw": "true"})
        return raw

    @staticmethod
    def _live_target_blocks(raw: str, p: ActionProposal) -> list[str]:
        """The live block(s) the proposal addresses — occurrence-based for
        ``update_section``, identity-keyed for the B2 ops."""
        section = str(p.target.get("section", ""))
        if p.action == OP_UPDATE_SECTION:
            return find_section_blocks(raw, section)
        return find_identified_blocks(raw, section, str(p.target.get("block_key", "")))

    @classmethod
    def _target_fresh(cls, raw: str, p: ActionProposal) -> tuple[bool, str]:
        """Frozen-vs-live staleness for any forward op: an ADD (frozen ``""``)
        needs the target still absent; an update/remove needs exactly one live
        match still equal to the frozen content."""
        section = str(p.target.get("section", ""))
        block_key = str(p.target.get("block_key", ""))
        described = f"<{section}>" + (f" '{block_key}'" if block_key else "")
        params = p.parameters if isinstance(p.parameters, dict) else {}
        frozen = str(params.get("current_content", "")).strip()
        blocks = cls._live_target_blocks(raw, p)
        if not frozen:
            if blocks:
                return False, (
                    f"{described} now exists in ossec.conf but this proposal ADDS it "
                    "— the config changed since it was proposed. Re-propose against "
                    "the current configuration."
                )
            return True, f"{described} still absent — the add applies cleanly."
        if len(blocks) != 1:
            return False, (
                f"{described} appears {len(blocks)} time(s) in ossec.conf — it must "
                "appear exactly once to be edited."
            )
        if blocks[0].strip() != frozen:
            return False, (
                f"{described} has changed since this was proposed — the approver's "
                "diff is stale. Re-propose against the current configuration."
            )
        return True, f"{described} present once and unchanged since proposal."

    @staticmethod
    async def _put_config(ctx: ExecContext, content: str) -> None:
        await ctx.action_api.update_manager_configuration(
            content=content, capabilities=ctx.capabilities
        )

    @classmethod
    async def _write_and_validate(
        cls, ctx: ExecContext, *, new_content: str, rollback_to: str | None
    ) -> str:
        """PUT ``new_content`` → validate → (restore ``rollback_to`` + raise on
        invalid).  Returns the validation detail.  Does NOT restart — the caller
        confirms persistence first, THEN restarts (same ordering as rule_tuning:
        the authoritative check must not race the restart's API downtime)."""
        await cls._put_config(ctx, new_content)
        valid, detail = _validation_ok(await ctx.read_api.get("/manager/configuration/validation"))
        if not valid:
            if rollback_to is not None:
                await cls._put_config(ctx, rollback_to)
                raise ConfigValidationError(
                    f"Edited configuration rejected ({detail}); restored the prior "
                    "ossec.conf. No change applied."
                )
            raise ConfigValidationError(f"Configuration rejected ({detail}).")
        return detail

    @staticmethod
    def _change_persisted(raw: str, p: ActionProposal, new_block: str) -> bool:
        """Op-aware persistence proof against the re-read file: the replaced/added
        content matches (reformatting-tolerant), or the removed instance is gone."""
        section = str(p.target.get("section", ""))
        block_key = str(p.target.get("block_key", ""))
        if p.action == OP_UPDATE_SECTION:
            return section_persisted(raw, section, new_block)
        if p.action == OP_UPSERT_BLOCK:
            return block_persisted(raw, section, block_key, new_block)
        return block_removed(raw, section, block_key)

    def build_forward(self, proposal: ActionProposal, ctx: ExecContext) -> Callables:
        async def _freshness(p: ActionProposal) -> tuple[bool, str]:
            if not ctx.capabilities.can(ACTION_UPDATE_MANAGER_CONFIG, RESOURCE_ANY):
                return False, (
                    "Credential no longer holds manager:update_config (Superuser-scoped)."
                )
            raw = await self._read_config(ctx)
            return self._target_fresh(raw, p)

        async def _perform(p: ActionProposal) -> dict[str, Any]:
            section = str(p.target.get("section", ""))
            block_key = str(p.target.get("block_key", ""))
            params = p.parameters if isinstance(p.parameters, dict) else {}
            new_block = str(params.get("section_content", "")).strip()
            described = f"<{section}>" + (f" '{block_key}'" if block_key else "")

            # Snapshot the WHOLE ossec.conf — the undo restore point.
            snapshot = await self._read_config(ctx)
            snap_hash = _sha256(snapshot)
            p.prior_state = {
                "kind": "manager_configuration",
                "content": snapshot,
                "sha256": snap_hash,
            }

            # The SAME transformation the propose tool dry-ran (build_candidate:
            # replace/add the single-instance section, or upsert/remove the
            # identity-keyed instance — ADR 0032 B).
            new_content = build_candidate(snapshot, p.action, section, block_key, new_block)
            if new_content is None:
                raise ConfigValidationError(
                    f"The change no longer applies cleanly to ossec.conf "
                    f"({p.action} on {described}); cannot edit. No change applied."
                )
            validation = await self._write_and_validate(
                ctx, new_content=new_content, rollback_to=snapshot
            )

            # AUTHORITATIVE confirm — re-read ossec.conf and prove OUR change
            # actually persisted. If it didn't, restore + fail honestly rather
            # than report a phantom success (the 6-e.3 lesson). The check is
            # reformatting-tolerant (the manager re-indents the file on write, so
            # a literal substring match false-negatives a change that applied —
            # the live 6-e.4 failure) and op-aware (an upsert proves the keyed
            # block matches; a removal proves the keyed block is GONE). On a real
            # miss the content read back is surfaced so an exotic transform is
            # diagnosable.
            reread = await self._read_config(ctx)
            if not self._change_persisted(reread, p, new_block):
                await self._put_config(ctx, snapshot)
                live = self._live_target_blocks(reread, p)
                seen = live[0].strip()[:300] if len(live) == 1 else f"{len(live)} occurrence(s)"
                raise ConfigValidationError(
                    f"Change to {described} did not persist to ossec.conf; restored "
                    f"the prior file. No change applied. (Target read back: {seen})"
                )

            # Apply: the manager only loads ossec.conf on restart (~18s live).
            await ctx.action_api.restart_cluster(capabilities=ctx.capabilities)
            return {
                "section": section,
                "block_key": block_key,
                "operation": p.action,
                "validation": validation,
                "restart_issued": True,
                "prior_sha256": snap_hash,
                "new_sha256": _sha256(reread),
                "section_updated": True,
            }

        async def _verify(p: ActionProposal, res: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
            # Already authoritative: _perform confirmed the change PERSISTED and the
            # config VALIDATES (it raises + auto-restores otherwise). No re-read here
            # — it would race the restart _perform just issued.
            ok = bool(res.get("section_updated"))
            detail: dict[str, Any] = {
                "ok": ok,
                "section": res.get("section"),
                "block_key": res.get("block_key"),
                "operation": res.get("operation"),
                "section_updated": res.get("section_updated"),
                "validation": res.get("validation"),
                "restart_issued": res.get("restart_issued", True),
                "prior_sha256": res.get("prior_sha256"),
                "new_sha256": res.get("new_sha256"),
                "note": (
                    "Change applied to ossec.conf (master node) + configuration "
                    "validated + cluster restart issued. The change becomes active "
                    "~15-30s after the restart completes."
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
                return False, "The original config_change proposal is no longer present."
            if original.state == ProposalState.rolled_back:
                return False, "The configuration change has already been reversed."
            prior = original.prior_state if isinstance(original.prior_state, dict) else None
            if not prior or not prior.get("content"):
                return False, "No captured prior_state snapshot to restore."
            return True, f"Prior ossec.conf snapshot present (original {original.id})."

        async def _perform(p: ActionProposal) -> dict[str, Any]:
            original = await db.get(ActionProposal, p.reverses_proposal_id)
            prior = (
                original.prior_state
                if original is not None and isinstance(original.prior_state, dict)
                else {}
            )
            content = str(prior.get("content", ""))
            section = str(original.target.get("section", "")) if original is not None else ""
            validation = await self._write_and_validate(ctx, new_content=content, rollback_to=None)
            # Authoritative: the re-read file must BE the snapshot (hash equality —
            # stronger than a substring check; restore is whole-file).
            restored = _sha256(await self._read_config(ctx)) == _sha256(content)
            await ctx.action_api.restart_cluster(capabilities=ctx.capabilities)
            return {
                "restored_from": str(original.id) if original is not None else "",
                "section": section,
                "validation": validation,
                "restart_issued": True,
                "config_restored": restored,
            }

        async def _verify(p: ActionProposal, res: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
            # Snapshot-restore is an API-executable undo — tag it completed (only
            # when the file hash-matches the snapshot) so the API flips the
            # original to rolled_back (reversal.complete_api_reversal).
            ok = bool(res.get("config_restored", False))
            detail: dict[str, Any] = {
                "ok": ok,
                "restored_from": res.get("restored_from"),
                "section": res.get("section"),
                "config_restored": res.get("config_restored", False),
                "validation": res.get("validation"),
                "restart_issued": res.get("restart_issued", True),
                "reversal_state": REVERSAL_STATE_COMPLETED if ok else "failed",
                "note": (
                    "Restored ossec.conf to its pre-change snapshot (hash-verified) + "
                    "validated + cluster restart issued. The configuration reverts "
                    "~15-30s after the restart completes."
                ),
            }
            return ok, detail

        return _freshness, _perform, _verify


register_executor("config_change", _ConfigChangeExecutor())
