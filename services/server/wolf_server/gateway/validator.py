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

# Wildcard / fleet-wide target tokens that must never appear in a single
# proposal's resolved target or agents_list.
_BLAST_TOKENS = frozenset({"*", "all"})

# Allowed active-response commands (v1).  In a later slice this is sourced from
# the live `GET /active-response` command list per ADR 0020 / doc 04 ("a command
# id returned by list_active_response_commands — never an invented command").
ALLOWED_ACTIVE_RESPONSE_COMMANDS = frozenset(
    {"firewall-drop", "host-deny", "disable-account", "restart-wazuh"}
)


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
    if action_class == "active_response":
        if action not in ALLOWED_ACTIVE_RESPONSE_COMMANDS:
            return ValidationResult(
                ok=False,
                reason=(
                    f"Active-response command {action!r} is not in the allow-list; "
                    "the model may only propose a known command."
                ),
            )
    else:
        return ValidationResult(ok=False, reason=f"Unknown action class {action_class!r}.")

    return ValidationResult(ok=True)
