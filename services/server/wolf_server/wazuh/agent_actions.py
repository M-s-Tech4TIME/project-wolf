"""Agent-action operation catalog — Phase 6-e.2 (ADR 0029).

``agent_action`` targets an AGENT (so it reuses AR's ``can_on_agent`` group-aware
capability check).  v1 = **group management**: assign an agent to a group or
remove it from one — each the *exact inverse* of the other, which makes it the
reversible showcase (quarantine an agent into an ``isolated`` group, then move it
back).  Gated by the ``agent:modify_group`` RBAC action.

Unlike active-response, these run for real **both ways** through the Server API
(``PUT`` / ``DELETE /agents/{id}/group/{group}``), so the reversal is API-inverse,
not wolf-pack-bound (ADR 0029 §2).
"""

from __future__ import annotations

import re

# Operations (v1). Each is the other's exact inverse.
OP_ASSIGN_GROUP = "assign_group"
OP_REMOVE_GROUP = "remove_group"

# The inverse operation a reversal performs.
INVERSE_OP: dict[str, str] = {
    OP_ASSIGN_GROUP: OP_REMOVE_GROUP,
    OP_REMOVE_GROUP: OP_ASSIGN_GROUP,
}
AGENT_ACTION_OPS = frozenset(INVERSE_OP)

# Approver-facing phrasing.
OP_LABELS: dict[str, str] = {
    OP_ASSIGN_GROUP: "Assign agent to group",
    OP_REMOVE_GROUP: "Remove agent from group",
}

# Wazuh group names: a leading word char then word chars / ``.`` / ``-`` (Wazuh
# enforces a similar charset, max 255).  A conservative validator keeps a
# malformed / path-y group name out of the URL we build.
_GROUP_RE = re.compile(r"^\w[\w.\-]{0,254}$")


def is_valid_group(name: str) -> bool:
    """True for a syntactically valid Wazuh group name."""
    return bool(_GROUP_RE.match(name or ""))
