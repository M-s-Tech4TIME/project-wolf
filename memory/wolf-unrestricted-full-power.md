---
name: wolf-unrestricted-full-power
description: "FOUNDATIONAL DIRECTIVE (2026-06-18): Wolf is NOT a read-only agent. It should be fully unrestricted + fully empowered; the restriction comes from Wazuh's own RBAC (the per-org credential's capabilities), NOT from Wolf limiting itself. Wolf fully utilizes whatever the specified Wazuh user can do (read/write/manage/actions per its policies/roles/permissions)."
metadata:
  node_type: memory
  type: feedback
---

Operator's evolved position, stated 2026-06-18 after watching credential-driven
isolation work in Phase 6.6-f. **Supersedes the original "Wolf is read-only"
framing.**

The directive: Wolf should be a standalone, fully-empowered agentic AI — packed
with knowledge, wisdom, smartness, and its full potential — NOT artificially
restricted. The boundary already exists, externally, in **Wazuh's RBAC** (the
per-org credential's roles/policies/permissions + index DLS). Wolf limiting
itself on top of that is redundant AND caps Wolf's value. So Wolf should fully
utilize whatever the specified Wazuh user is capable of: if the credential can
restart an agent / run active-response / manage groups, Wolf can drive that.

**Why:** 6.6-f proved the credential IS the wall — acme's user physically can't
see beyond `agent.labels.group:acme` no matter what Wolf does. Restriction is
Wazuh's job; capability is Wolf's. The operator reframed the project on this.

**How to apply (going forward):**
- Reshapes **Phase 6 (Propose tools + Approval Gateway)** — the hard read-only
  posture + propose-then-approve wall becomes capability-driven: Wolf may ACT
  within what the Wazuh credential is authorized to do (gate/approval becomes a
  policy choice, not a built-in cap). Consult the credential's effective
  policies (`/security/users/me/policies`, the 6.6-f introspection) to know
  what an action is even permitted before offering/doing it.
- The currently **read-only** `WazuhServerApiClient` (rejects POST/PUT/DELETE)
  will gain credential-bounded write paths. Do NOT rip this out ad-hoc — it's a
  real architectural shift; land it via an ADR when Phase 6 opens.
- Relates to Phase 13 (auto-execution), [[wolf-pack]] (outbound actions), and
  the 6.11 provisioning phase [[wazuh-provisioning-and-collaboration-phases]]
  (Wolf's first WRITE authority over Wazuh — same philosophy).
- Reconcile with [[quality-secure-coding-discipline]] + [[integrity-across-the-stack]]:
  unrestricted ≠ unsafe. Audit every action; capability comes from the
  credential; safety/quality discipline still applies.
