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

**LANDED 2026-06-18 — Phase 6 OPENED via ADR 0025 + foundational slice 6-a.**
The reframe is now implemented (decisions: A2 execute in-process in wolf-server,
NOT a separate gateway service; B1 every write needs explicit human approval, no
autonomous writes in v1; C1 ADR + one action end-to-end). Shipped:
`wazuh/capabilities.py` (RBAC introspection via `/security/users/me/policies` →
`can()`/`available_action_classes()`, fail-closed); the in-process
`wolf_server/gateway/` (proposal + state machine `0015`, validator hard gate,
approval w/ separation-of-duties, execution = hash-integrity → freshness →
bounded write → verification → audit); a deliberate capability-checked
`WazuhServerApiActionClient` ALONGSIDE the kept read-only client (NOT an ad-hoc
opening); `propose_active_response` (tier=propose); RBAC `ACTION_PROPOSE`/
`ACTION_APPROVE` (no execute role). doc 04's safety machinery is preserved; only
doc 03 fact #3 (credential physically read-only) was inverted. See ADR 0025 +
CHANGELOG 2026-06-18.

**6-a.1 (2026-06-19) — group-aware capability gate, found by the live smoke.**
The read-only capability-denial smoke against the real cluster (run BEFORE 6-b)
caught a correctness gap: a per-org credential grants `active-response:command`
on **`agent:group:<org>`** (e.g. `wolf-acme` → `agent:group:acme`), NOT on
`agent:id:*`. The id-only `can(AR, "agent:id:<id>")` pre-flight would have
**falsely refused every AR acme was genuinely authorized for** — 6-b would have
been dead-on-arrival. Fix: the gate mirrors Wazuh RBAC's agent resource
expansion — allowed on `agent:id:<id>` (or wildcard) OR on `agent:group:<g>` for
ANY group the target agent is in (`CredentialCapabilities.can_on_agent`,
deny-wins across the union); the agent's groups are resolved FRESH at decision
time (`resolve_agent_groups`, fail-closed) in both the propose pre-flight and
execution. Operator validated by removing AR from `wolf-beta` (acme/beta
otherwise identical): acme ALLOW on its group agent, REFUSE out-of-scope; beta
REFUSE (no AR action class). **Lesson: capability checks must match how Wazuh
ACTUALLY evaluates RBAC (group expansion), not a literal id lookup — verify
against a real per-org credential, not just a broad/admin one.** Reinforces
[[scope-and-validation-discipline]] (empirical, real-system validation) +
[[single-org-mssp-parity]] (broad `agent:id:*` single-org cred still works).

**How to apply (remaining):**
- Reshapes **Phase 6** — the hard read-only + propose-then-approve WALL is now
  capability-driven (gate/approval is a policy choice, not a built-in cap). The
  pattern is set by slice 6-a; FOLLOW-ONS repeat it: the approval-queue GUI
  (6-b), the other action classes (`rule_tuning`/`agent_action`/`config_change`),
  and severity-tiered authority / four-eyes / crown-jewel (policy hooks; B1
  default = approval-for-all). Always pre-flight the credential's effective
  policies before offering/doing a write — via `can_on_agent` (group-aware) for
  agent-targeted actions, never a literal `agent:id` lookup.
- The read-only `WazuhServerApiClient` was KEPT; the write surface is the
  separate capability-checked `WazuhServerApiActionClient`. Extend writes ONLY
  by adding named, capability-checked methods there — never by widening the read
  client's guard.
- Relates to Phase 13 (auto-execution), [[wolf-pack]] (outbound actions), and
  the 6.11 provisioning phase [[wazuh-provisioning-and-collaboration-phases]]
  (Wolf's first WRITE authority over Wazuh — same philosophy).
- Reconcile with [[quality-secure-coding-discipline]] + [[integrity-across-the-stack]]:
  unrestricted ≠ unsafe. Audit every action; capability comes from the
  credential; safety/quality discipline still applies.
