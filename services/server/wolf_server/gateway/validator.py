"""Action validator — the hard gate before a proposal enters the queue.

ADR 0017 subsystem 3: a proposal is checked *before* it becomes ``pending``.
v1 ships the **deterministic structural** checks (they don't need an LLM and
are the load-bearing safety properties from doc 04):

  - **resolved target** — the propose tool must have resolved an unambiguous
    agent id (doc 04 §Wrong-target resolution); a name-only target is rejected.
  - **bounded blast radius** — the action must not target "all agents" / a
    wildcard (doc 04 §collusion/blast radius); one resolved agent at a time.
  - **allowed action** — the action/command must be in the action class's
    allow-list, never invented (doc 04 §The proposal object).

Hard gate, no bypass (ADR 0017 Round 3): a failing draft never reaches the
queue; the caller surfaces the reason inline.

The LLM-as-judge *intent-alignment* check ("does this match what the operator
asked for in THIS conversation?") is a tracked follow-on — it needs the
conversation context + the judge model wired into the propose path.  The
structural checks below stand on their own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from wolf_server.wazuh.active_response import (
    AR_COMMANDS,
    TARGET_SRCIP,
    TARGET_USERNAME,
    classify_os,
    get_ar_command,
    is_valid_ip,
)

# Wildcard / fleet-wide target tokens that must never appear in a single
# proposal's resolved target or agents_list.
_BLAST_TOKENS = frozenset({"*", "all"})

# Allowed active-response commands = the catalog (single source of truth).
# Each carries platform + required-target metadata used by the checks below.
ALLOWED_ACTIVE_RESPONSE_COMMANDS = frozenset(AR_COMMANDS)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: str = ""


def _is_blast(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() in _BLAST_TOKENS


def validate_proposal(
    *,
    action_class: str,
    target: dict[str, Any],
    action: str,
    parameters: dict[str, Any],
) -> ValidationResult:
    """Run the deterministic structural checks.  ``ok=False`` blocks the queue."""
    # 1. Resolved, unambiguous target.
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

    # 2. Bounded blast radius — no fleet-wide parameter.
    for key in ("agents_list", "agent_id", "agents"):
        if _is_blast(parameters.get(key)):
            return ValidationResult(
                ok=False,
                reason=f"Parameter {key!r} targets all agents — blast radius is unbounded.",
            )

    # 3. Allowed action per class (never an invented command).
    if action_class != "active_response":
        return ValidationResult(ok=False, reason=f"Unknown action class {action_class!r}.")

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

    # 4. Required target per command — the action must carry what it acts on,
    #    well-formed (so a doomed/ambiguous dispatch never reaches approval).
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
    elif cmd.target == TARGET_USERNAME:
        if not isinstance(username, str) or not username.strip():
            return ValidationResult(
                ok=False,
                reason=f"{action!r} disables an account but no 'username' was provided.",
            )

    # 5. Platform fit — lenient: refuse ONLY a confirmed mismatch (e.g.
    #    firewall-drop on a Windows agent). An unknown/unresolved OS does NOT
    #    block (the credential + human approver remain the backstops).
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
