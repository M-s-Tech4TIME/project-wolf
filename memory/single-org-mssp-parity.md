---
name: single-org-mssp-parity
description: "STANDING PRINCIPLE (2026-06-18): anything achievable in an MSSP (multi-org) deployment must ALSO be achievable in a single-organization (non-MSSP) deployment — design for both at once. A single internal org monitoring its own infra (every user sees all agents, no per-agent-group RBAC) must work as well as the MSSP shape."
metadata:
  node_type: memory
  type: feedback
---

Operator principle, stated 2026-06-18: *"Things achievable through MSSP must
also be achievable without MSSP (single-organization use achievable, keeping
MSSP in mind at the same time)."*

The scenario to always support: a single internal organization monitoring its
own infrastructure, where every Wazuh user (admin or readonly) can see ALL
enrolled agents — no per-agent-group isolation needed.

**Current state (Phase 6.6-f) already satisfies this** — confirm + preserve it:
the credential-driven model is shape-agnostic. Single-org = one Wolf org + one
**broad-access** Wazuh credential (e.g. `agent:read` on `agent:group:*` /
`agent:id:*`, indexer role with NO DLS) + the group-label filter **OFF**
(default). The scope probe's `unrestricted` path already reports "not restricted
to specific agent groups" for such a credential, and the indexer probe reports
it can read everything. MSSP = many orgs each with a scoped credential + DLS.
Wolf imposes nothing either way — Wazuh's credential decides.

**How to apply:** every Wazuh-integration feature (incl. future 6.11 provisioning,
tool enrichment, actions) must work with a broad single-org credential, not just
a group-scoped MSSP one. Don't bake MSSP-only assumptions (e.g. "an org always
maps to exactly one agent group / one label") into required fields or logic —
keep them optional, defaulting to the single-org "sees everything" case.
