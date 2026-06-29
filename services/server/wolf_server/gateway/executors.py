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

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from wolf_common.errors import WolfError

from wolf_server.gateway.models import ActionProposal
from wolf_server.gateway.reversal import (
    REVERSAL_STATE_COMPLETED,
    reversal_freshness,
    reversal_perform,
    reversal_verify,
)
from wolf_server.wazuh.active_response import interpret_ar_result
from wolf_server.wazuh.agent_actions import OP_ASSIGN_GROUP
from wolf_server.wazuh.capabilities import resolve_agent_groups

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
