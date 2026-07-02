"""Action validator — the hard gate before a proposal enters the queue.

ADR 0017 subsystem 3: a proposal is checked *before* it becomes ``pending``.
v1 ships the **deterministic structural** checks (no LLM needed; the load-bearing
safety properties from doc 04):

  - **resolved target** — the propose tool must have resolved an unambiguous
    target (doc 04 §Wrong-target resolution); a name-only target is rejected.
  - **bounded blast radius** — no "all"/wildcard target (doc 04 §blast radius).
  - **allowed action** — the action must be in the class's allow-list, never
    invented (doc 04 §The proposal object).

Hard gate, no bypass (ADR 0017 Round 3): a failing draft never reaches the queue.

Per-class (Phase 6-e, ADR 0029): :func:`validate_proposal` dispatches by
``action_class`` to a registered structural validator.  Active-response's checks
live in :func:`_validate_active_response`; new classes register their own.  The
LLM-as-judge intent-alignment check is a tracked follow-on (ADR 0017 / Phase 7.5).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from wolf_server.wazuh.active_response import (
    AR_COMMANDS,
    TARGET_SRCIP,
    TARGET_USERNAME,
    classify_os,
    get_ar_command,
    is_valid_ip,
)
from wolf_server.wazuh.agent_actions import AGENT_ACTION_OPS, is_valid_group
from wolf_server.wazuh.config_change import (
    CONFIG_CHANGE_FORWARD_OPS,
    EDITABLE_SECTIONS,
    is_valid_section_block,
)
from wolf_server.wazuh.rule_tuning import (
    OP_ADJUST_LEVEL,
    RULE_TUNING_FORWARD_OPS,
    is_valid_level,
    is_valid_rule_id,
)

# Wildcard / fleet-wide target tokens that must never appear in a single
# proposal's resolved target or agents_list.
_BLAST_TOKENS = frozenset({"*", "all"})

# Allowed active-response commands = the catalog (single source of truth).
ALLOWED_ACTIVE_RESPONSE_COMMANDS = frozenset(AR_COMMANDS)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: str = ""


class ProposalValidator(Protocol):
    """A per-class structural validator (registered in :data:`_VALIDATORS`)."""

    def __call__(
        self, *, target: dict[str, Any], action: str, parameters: dict[str, Any]
    ) -> ValidationResult: ...


_VALIDATORS: dict[str, ProposalValidator] = {}


def register_validator(action_class: str, validator: ProposalValidator) -> None:
    """Register the structural validator for an action class (ADR 0029)."""
    _VALIDATORS[action_class] = validator


def _is_blast(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() in _BLAST_TOKENS


def require_resolved_agent_target(
    target: dict[str, Any], parameters: dict[str, Any]
) -> ValidationResult | None:
    """Generic agent-scoped pre-checks: a resolved, non-wildcard ``agent_id`` and
    a bounded blast radius (no fleet-wide parameter).  Returns a *failing*
    :class:`ValidationResult`, or ``None`` when the target is acceptable.  Shared
    by agent-scoped classes (active_response, agent_action — ADR 0029)."""
    agent_id = target.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id.strip():
        return ValidationResult(
            ok=False,
            reason="Target is not resolved to a specific agent id — refusing to propose.",
        )
    if _is_blast(agent_id):
        return ValidationResult(
            ok=False, reason="Target agent id is a wildcard — blast radius is unbounded."
        )
    for key in ("agents_list", "agent_id", "agents"):
        if _is_blast(parameters.get(key)):
            return ValidationResult(
                ok=False,
                reason=f"Parameter {key!r} targets all agents — blast radius is unbounded.",
            )
    return None


def validate_proposal(
    *,
    action_class: str,
    target: dict[str, Any],
    action: str,
    parameters: dict[str, Any],
) -> ValidationResult:
    """Run the registered structural validator for ``action_class``.

    ``ok=False`` blocks the queue.  An unregistered class is refused (never an
    invented action class)."""
    validator = _VALIDATORS.get(action_class)
    if validator is None:
        return ValidationResult(ok=False, reason=f"Unknown action class {action_class!r}.")
    return validator(target=target, action=action, parameters=parameters)


def _validate_active_response(
    *, target: dict[str, Any], action: str, parameters: dict[str, Any]
) -> ValidationResult:
    """Structural checks for the active-response class (the 6-a→6-d logic)."""
    # 1+2. Resolved, unambiguous, bounded-blast agent target.
    refusal = require_resolved_agent_target(target, parameters)
    if refusal is not None:
        return refusal

    # 3. Allowed action — a known catalog command, never invented.
    cmd = get_ar_command(action)
    if cmd is None:
        return ValidationResult(
            ok=False,
            reason=(
                f"Active-response command {action!r} is not in the catalog; "
                "the model may only propose a known command "
                f"({', '.join(sorted(AR_COMMANDS))})."
            ),
        )

    # 4. Required target per command — well-formed (so a doomed/ambiguous
    #    dispatch never reaches approval).
    srcip = parameters.get("srcip")
    username = parameters.get("username")
    if cmd.target == TARGET_SRCIP:
        if not isinstance(srcip, str) or not srcip.strip():
            return ValidationResult(
                ok=False,
                reason=f"{action!r} blocks a source IP but no 'srcip' was provided.",
            )
        if not is_valid_ip(srcip.strip()):
            return ValidationResult(
                ok=False, reason=f"'srcip' {srcip!r} is not a valid IP address."
            )
    elif cmd.target == TARGET_USERNAME and (not isinstance(username, str) or not username.strip()):
        return ValidationResult(
            ok=False,
            reason=f"{action!r} disables an account but no 'username' was provided.",
        )

    # 5. Platform fit — lenient: refuse ONLY a confirmed mismatch (e.g.
    #    firewall-drop on a Windows agent). An unknown OS does NOT block.
    os_signals = parameters.get("agent_os")
    os_class = classify_os(os_signals if isinstance(os_signals, str) else None)
    if os_class is not None and os_class not in cmd.platforms:
        return ValidationResult(
            ok=False,
            reason=(
                f"{action!r} targets {'/'.join(sorted(cmd.platforms))} but agent is "
                f"{os_class}. Use a {os_class}-compatible command "
                f"(e.g. {'netsh' if os_class == 'windows' else 'firewall-drop'})."
            ),
        )

    return ValidationResult(ok=True)


register_validator("active_response", _validate_active_response)


def _validate_agent_action(
    *, target: dict[str, Any], action: str, parameters: dict[str, Any]
) -> ValidationResult:
    """Structural checks for agent_action (group management, 6-e.2)."""
    refusal = require_resolved_agent_target(target, parameters)
    if refusal is not None:
        return refusal
    if action not in AGENT_ACTION_OPS:
        return ValidationResult(
            ok=False,
            reason=(
                f"Unknown agent action {action!r}; supported: "
                f"{', '.join(sorted(AGENT_ACTION_OPS))}."
            ),
        )
    group = parameters.get("group")
    if not isinstance(group, str) or not group.strip():
        return ValidationResult(
            ok=False, reason=f"{action!r} needs a target group ('group')."
        )
    if not is_valid_group(group.strip()):
        return ValidationResult(
            ok=False, reason=f"Group name {group!r} is not a valid Wazuh group name."
        )
    return ValidationResult(ok=True)


register_validator("agent_action", _validate_agent_action)


def _validate_rule_tuning(
    *, target: dict[str, Any], action: str, parameters: dict[str, Any]
) -> ValidationResult:
    """Structural checks for rule_tuning (6-e.3).

    The target is a single resolved RULE id (manager-global, not agent-scoped),
    the action a known forward op, and ``adjust_level`` needs a valid 0..16 level.
    ``restore_rules`` is reversal-only (created via ``create_reversal_proposal``,
    which bypasses this validator), so a *forward* restore is refused here."""
    rule_id = target.get("rule_id")
    if not isinstance(rule_id, str) or not rule_id.strip():
        return ValidationResult(
            ok=False,
            reason="Target is not resolved to a specific rule id — refusing to propose.",
        )
    if _is_blast(rule_id):
        return ValidationResult(
            ok=False, reason="Target rule id is a wildcard — blast radius is unbounded."
        )
    if not is_valid_rule_id(rule_id):
        return ValidationResult(
            ok=False, reason=f"Rule id {rule_id!r} is not a valid Wazuh rule id (1..999999)."
        )
    if action not in RULE_TUNING_FORWARD_OPS:
        return ValidationResult(
            ok=False,
            reason=(
                f"Unknown rule_tuning operation {action!r}; supported: "
                f"{', '.join(sorted(RULE_TUNING_FORWARD_OPS))}."
            ),
        )
    if action == OP_ADJUST_LEVEL:
        level = parameters.get("level")
        if not is_valid_level(level):
            return ValidationResult(
                ok=False,
                reason=f"{action!r} needs a 'level' integer between 0 and 16 (got {level!r}).",
            )
    return ValidationResult(ok=True)


register_validator("rule_tuning", _validate_rule_tuning)


def _validate_config_change(
    *, target: dict[str, Any], action: str, parameters: dict[str, Any]
) -> ValidationResult:
    """Structural checks for config_change (6-e.4) — the tightest gate of any class.

    The target is ONE allowlisted, single-instance ossec.conf section
    (manager-global; the break-the-manager sections are not in the allowlist),
    the action a known forward op, and the replacement content must be exactly
    one well-formed ``<section>`` block within the review-size cap.
    ``restore_config`` is reversal-only (created via ``create_reversal_proposal``,
    which bypasses this validator), so a *forward* restore is refused here."""
    section = target.get("section")
    if not isinstance(section, str) or not section.strip():
        return ValidationResult(
            ok=False,
            reason="Target is not resolved to a specific configuration section.",
        )
    section = section.strip()
    if _is_blast(section):
        return ValidationResult(
            ok=False, reason="Target section is a wildcard — blast radius is unbounded."
        )
    if section not in EDITABLE_SECTIONS:
        return ValidationResult(
            ok=False,
            reason=(
                f"Section {section!r} is not editable in v1; editable sections: "
                f"{', '.join(sorted(EDITABLE_SECTIONS))}. Cluster/auth/indexer/"
                "ruleset and repeated sections (global, localfile, …) stay "
                "hand-edited."
            ),
        )
    if action not in CONFIG_CHANGE_FORWARD_OPS:
        return ValidationResult(
            ok=False,
            reason=(
                f"Unknown config_change operation {action!r}; supported: "
                f"{', '.join(sorted(CONFIG_CHANGE_FORWARD_OPS))}."
            ),
        )
    content = parameters.get("section_content")
    if not isinstance(content, str):
        return ValidationResult(
            ok=False,
            reason=f"{action!r} needs 'section_content' — the full replacement block.",
        )
    ok, reason = is_valid_section_block(section, content)
    if not ok:
        return ValidationResult(ok=False, reason=reason)
    return ValidationResult(ok=True)


register_validator("config_change", _validate_config_change)
