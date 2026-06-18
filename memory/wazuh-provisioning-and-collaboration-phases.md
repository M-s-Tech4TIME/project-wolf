---
name: wazuh-provisioning-and-collaboration-phases
description: "Two operator-requested future phases (2026-06-18): 6.11 Wolf-assisted Wazuh RBAC provisioning + diagnostics (Superuser-only, Wolf writes the per-org isolation recipe into Wazuh) and 6.12 cross-role assistance/escalation (per-org user ↔ Superuser collaboration on troubleshooting/analytics/cases)."
metadata:
  node_type: memory
  type: project
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

Requested 2026-06-18 right after Phase 6.6-f shipped, when the operator
remarked that hand-crafting per-org Wazuh RBAC felt critical + complex and that
Wolf had clearly "figured it out smartly" during the 6.6-f diagnosis. Both are
in [docs/10-build-roadmap.md](docs/10-build-roadmap.md); details there.

**Phase 6.11 — Wolf-assisted Wazuh RBAC provisioning & diagnostics
(Superuser-only).** Encode the MSSP isolation recipe Wolf already derived in
6.6-f so the Superuser doesn't hand-build it. Uses the install-topology Wazuh
admin creds (reserved for install-level work — [[phase-6.6-web-test-plan]]).
- **Provision (generative):** create the agent group + Server-API
  policy/role/`wolf-<org>` user scoped to `agent:group:<org>` + Indexer
  user/role/**DLS** (`agent.labels.group:<org>`)/role-mapping, write the
  generated creds into Wolf's per-org secrets, stamp the 6.6-f config, probe.
- **Doctor (diagnostic, low-risk, could ship first):** introspect an existing
  per-org credential (`/security/users/me/policies`, index `_count`, DLS,
  group membership, role mapping) → report misconfig + remediation.
- **ADR-worthy:** FIRST time Wolf gets *write* authority over a customer's
  Wazuh security config (today read-only). Bound it: Superuser-only + audit
  every call; **preview/dry-run before apply**; idempotent + `--update`-safe;
  **never touch Wazuh default superusers**; a **deprovision** path; a **manual
  recipe export** fallback. Python core + shell wrapper
  ([[shell-wrapper-required-pattern]]) + Superuser GUI
  ([[web-first-configurability]]). Deps: ADR 0020 + 6.6-a (shipped).

**Phase 6.12 — Cross-role assistance & escalation (per-org ↔ Superuser
collaboration).** A per-org Analyst/Engineer requests the Superuser's help from
inside a conversation/case (troubleshooting, analytics, case work needing
Superuser authority or cross-org visibility); Superuser assistance inbox
(notification-driven) → open with the org's scoped context, respond,
co-investigate, take a scoped action; collaborative thread so both work it
together. Builds on the existing **time-limited Superuser-access grant +
transparency banner** (ADR 0018 / Phase 6.5-f, `superuser_access`) — here
org-INITIATED request vs admin-initiated grant. Isolation: every cross-org
view/action audited + scoped to the request; banner shows when a Superuser is
engaged. Deps: Notification infra 6.7 + SSE 6.8 ([[notification-and-realtime-phases]])
— sequences AFTER them. Likely its own ADR.
