# ADR 0020 — Superuser-owned Wazuh component mapping (install + per-org)

**Status:** ACCEPTED (2026-06-10)
**Revision history:**
- 2026-06-10 PROPOSED v1 (split out from ADR 0018 Round 1 review:
  install-level + per-org design, 7 open architectural decisions)
- 2026-06-10 PROPOSED v2 (Round 2 review with operator: all 7 open
  decisions resolved — see §"Resolved architectural decisions" below)
- 2026-06-10 **ACCEPTED** (operator sign-off after 1-round review;
  Phase 6.6 unblocked + sequenced after Phase 6.5)
**Authors:** Wolf Maintainers
**Extends:** ADR 0010 (multi-organization isolation), ADR 0016 (component
architecture), ADR 0018 (Bootstrap Superuser + RBAC + Login UX — defines
the Superuser role this ADR concentrates Wazuh-mapping authority into)
**Related:** ADR 0017 (Wolf Central Brain — relies on per-org Wazuh
credentials for per-org isolation chain), ADR 0019 (Web-first
configurability — Wazuh mapping is one of the catalog rows that must
have a GUI surface)
**Originally part of:** ADR 0018, split out 2026-06-10 per operator
direction.

---

## Context

The operator made an explicit design call on 2026-06-10:

> "The Superuser is responsible to configure and map Wazuh components
> explicitly, which means this is the only user that can connect and map
> the Wazuh components."

This is a security-driven concentration of authority. Wazuh credentials
are the most security-sensitive integration point in Wolf — they grant
access to security telemetry (alerts, agent inventory, rules, decoders,
SCA, vulnerabilities). Mishandling them lets one compromised user pivot
into the operator's security observability stack.

Today's Wolf has a per-tenant `connection_profiles` table configured via
API only. There's no GUI surface, no install-level ecosystem topology
model, and no concentration of authority (any tenant Engineer can
configure their org's Wazuh credentials).

This ADR fixes all three: (a) Superuser-only authority, (b) install-level
ecosystem topology model, (c) GUI surface (per ADR 0019's discipline).

---

## Decision: Superuser owns ALL Wazuh component configuration

The Superuser is the ONLY role allowed to configure + map Wazuh
components. This applies at BOTH layers:

1. **Install level** — the Wazuh ecosystem topology (where the indexers,
   managers, and dashboard physically live)
2. **Organization level** — per-org Wazuh API credentials (which org gets
   to query which slice of Wazuh data)

No other role — not Admin, not Engineer, not Responder, not Analyst — can
configure either layer.

---

## Why centralize at the Superuser

| Reason | Detail |
|---|---|
| **Wazuh credentials = most security-sensitive integration point** | They grant access to ALL Wazuh telemetry. A compromised credential is a pivot into the operator's security observability. Centralizing in the Superuser reduces the attack surface from "every Engineer in every org" to "one Superuser per install". |
| **MSSP scenario** | The MSSP's central security team configures Wazuh-side RBAC for each customer; the same team configures Wolf-side per-org Wazuh credentials. Single chain of custody. |
| **Audit clarity** | The Superuser is the ONLY identity that touches Wazuh credentials → the audit log for credential changes has exactly one possible actor. |
| **Bootstrap simplicity** | A fresh install needs SOMEONE to set up Wazuh before anyone can use Wolf. That someone is unambiguously the Superuser (who exists at install time per ADR 0018). |
| **Failure-mode containment** | If an Engineer account is compromised in an org, the attacker cannot rotate Wazuh credentials to lock out the legitimate operator. |

### Why NOT the Engineer role

The Engineer role configures org-INTERNAL things: RAG corpora, prompts,
model selection, embedding provider, wolf-pack deployment. All of these
touch only Wolf's own state. Wazuh credentials touch the operator's
EXTERNAL Wazuh infrastructure — qualitatively different.

Concentrating Wazuh credentials at the Superuser level:
- Reduces blast radius if an Engineer account is compromised
- Centralizes responsibility for the Wazuh integration
- Matches how MSSPs typically operate (central security team handles
  external-system integrations; per-customer engineers handle internal
  customization)

---

## Install-level: Wazuh ecosystem topology

The Superuser configures the install's Wazuh ecosystem ONCE during
initial setup (or whenever the underlying Wazuh topology changes). Two
supported deployment shapes per Wazuh's own docs:

### Single-host deployment

```
┌─────────────────────────────────────────┐
│ One Wazuh host                          │
│  ├─ Indexer (OpenSearch)                │
│  ├─ Manager (master, no workers)        │
│  └─ Dashboard                           │
└─────────────────────────────────────────┘
```

Wolf configuration fields:
- `indexer_url`
- `indexer_admin_user` + `indexer_admin_password`
- `manager_url`
- `manager_api_user` + `manager_api_password`
- `dashboard_url`

Simple form. One probe-on-save validates all five endpoints respond.

### Distributed deployment

```
┌─────────────────┐     ┌──────────────────────────────────┐
│ Indexer cluster │     │ Manager cluster                  │
│  (1+ nodes)     │     │  ├─ Master node (cluster head)   │
│                 │     │  └─ Worker nodes (N)             │
└────────┬────────┘     └────────────────────┬─────────────┘
         │                                   │
         └───────────┬───────────────────────┘
                     │
            ┌────────┴────────┐
            │ Dashboard host  │
            └─────────────────┘
```

Wolf configuration fields:
- `indexer_nodes` (list of `{url, cluster_name}`)
- `indexer_admin_user` + `indexer_admin_password` (one set; shared
  across cluster)
- `manager_master_url`
- `manager_worker_urls` (list; declared as cluster members)
- `manager_api_user` + `manager_api_password`
- `dashboard_url`

Probe-on-save validates each indexer node, the master, each worker, and
the dashboard. Per-endpoint pass/fail surfaces in the UI; save blocked
if master or dashboard fails (worker failures are warnings, not blockers
— a worker can be temporarily down).

### Topology re-configuration

Changing the topology (single → distributed, or adding/removing nodes)
is a Superuser-only operation that:
1. Validates the new topology via probes (same flow as initial save)
2. Saves the new topology in the DB (canonical state per ADR 0019)
3. Emits an audit event with before/after topology snapshots
4. Forces all active org sessions to re-resolve their Wazuh routing on
   their next query (no service restart required; the topology is
   read fresh per query)

---

## Organization-level: per-org Wazuh credentials

For each Organization, the Superuser configures the **per-org Wazuh API
credentials**. NOT the org's Admin. NOT the org's Engineer. Only the
Superuser.

### Per-org credential fields

| Field | Purpose |
|---|---|
| `wazuh_api_user` | Restricted by the MSSP's Wazuh admin to the org's data slice via Wazuh groups + index DLS |
| `wazuh_api_password` | Stored in the secrets backend, per-org (encrypted at rest) |
| `wazuh_index_filter` | OPTIONAL explicit index pattern (for orgs that span multiple Wazuh indices, e.g., `wazuh-alerts-customer-acme-*`) |
| `wazuh_agent_groups` | OPTIONAL list of Wazuh agent groups this org sees (defaults to "any group accessible by the credential") |

### Runtime usage

When the org's Engineer / Analyst chats with Wolf:
1. Wolf resolves the user's active OrganizationContext (per ADR 0018
   login UX)
2. Wolf loads the per-org Wazuh credentials from the secrets backend
3. Wolf composes a query against the install's Wazuh ecosystem topology
   (indexer URL chosen by routing logic for distributed deployments)
4. Wolf executes the query using the per-org credentials (which the
   Wazuh server enforces with its own RBAC)
5. Wolf returns ONLY the data the per-org credential is authorized to
   see

**Wolf never holds a "master" Wazuh credential that sees all orgs.** End-
to-end isolation = Wazuh-side RBAC × per-org Wolf credentials × Wolf's
forced organization_id SQL filter (ADR 0010).

### Credential rotation

Rotating a per-org credential is Superuser-only:
1. Superuser issues new credentials in Wazuh (out of band — that's a
   Wazuh-side admin task)
2. Superuser updates the per-org config in Wolf via the GUI
3. Wolf probes the new credential (test query)
4. Save on probe success; audit event emitted with rotation timestamp
5. Active org sessions re-resolve credentials on their next query (no
   service restart; credentials are read fresh per query)

If a probe fails, the save is rejected and the old credential remains
active — no half-state where Wolf can't reach Wazuh.

---

## GUI surfaces (per ADR 0019)

Both layers (install + org) have GUI surfaces under Superuser settings.

### Install-level: "Wazuh Ecosystem" page

Visible only to Superuser. Layout:
- Top: radio "Single host" / "Distributed"
- Body: form fields per the selected topology
- Footer: "Test connection" + "Save" buttons
- Probe results inline (per-endpoint pass/fail)

### Organization-level: per-org Wazuh tab

Visible only to Superuser. Within each Organization's settings page, a
"Wazuh Credentials" tab. Layout:
- Form fields: `wazuh_api_user`, `wazuh_api_password`, `wazuh_index_filter`
  (optional), `wazuh_agent_groups` (optional)
- "Test credentials" button (probes Wazuh as the org would, returns
  pass/fail + scope summary: "credential sees N agents across M groups")
- "Save" button (blocked if probe fails)
- Rotation log (audit events for this org's Wazuh credential changes)

---

## Implementation sequencing

Implemented as **Phase 6.6 — Superuser-owned Wazuh component mapping**.
Sequenced AFTER Phase 6.5 (ADR 0018) so the Superuser + RBAC model is
in place before this UI uses it.

Sub-slices:

1. **6.6-a — Backend: install-level Wazuh ecosystem config**
   - DB schema: `wazuh_ecosystem_topology` table (single-row, install-wide)
   - API: `GET / PUT /api/v1/superuser/wazuh-topology`
   - Probe logic (re-use existing `wazuh/probe.py` style)
   - Audit-event emission on topology change

2. **6.6-b — UI: install-level Wazuh ecosystem page**
   - Superuser-only Settings → Wazuh Ecosystem page
   - Single/Distributed topology builder
   - Per-endpoint probe results
   - Save flow with validation

3. **6.6-c — Backend: per-org Wazuh credentials refactor**
   - Migrate existing `connection_profiles` to per-org credential model
   - API: `GET / PUT /api/v1/superuser/organizations/{id}/wazuh-credentials`
     (Superuser-only — Admin/Engineer rejected at decorator)
   - Probe logic for per-org credentials (returns scope summary)
   - Audit-event emission on credential change/rotation

4. **6.6-d — UI: per-org Wazuh credentials tab**
   - Superuser-only "Wazuh Credentials" tab within each org's settings
   - Form + probe + save flow
   - Rotation log display

5. **6.6-e — Runtime: per-query credential + topology resolution**
   - Update the existing Wazuh query path to read topology + credentials
     fresh per query
   - Routing logic for distributed deployments (pick an indexer node;
     fall back to others on failure)
   - End-to-end test: probe both layers from a chat query

Estimated scope: 3-5 sessions across 5 sub-slices.

---

## Resolved architectural decisions (Round 2 review with operator, 2026-06-10)

All 7 originally-open architectural decisions resolved.

1. **Indexer node selection — Random.** Pick a random indexer node per
   query; fall back to other nodes on failure. Simplest correct
   default. Indexer clusters are designed for load distribution;
   randomness gives even spread naturally. Stickiness-by-org adds
   state (which-node-was-last-used per org) for marginal cache-
   locality benefit — premature optimization for v1.

2. **Credential storage backend — Postgres + Fernet (existing pattern).**
   Continue using `services/server/wolf_server/secrets/` with
   Fernet-encrypted storage in Postgres. Operator-controlled key, no
   external dependency, airgap-deployable, matches existing Wolf
   pattern. Vault / AWS Secrets Manager are best-in-class but add
   significant operational burden (Vault deployment, sealing /
   unsealing, IAM setup) for v1. Migration path to Vault preserved
   if a future operator needs it.

3. **Probe-on-save — Hard fail for install-level, soft fail for
   per-org credentials.**
   - **Install-level topology save**: REJECTED if any endpoint
     probe fails. Wolf cannot function with an unreachable Wazuh
     ecosystem; hard-fail prevents bad state.
   - **Per-org credentials save**: SUCCEEDS with a warning if the
     probe fails (e.g., `wazuh_api_user` not yet provisioned on
     the Wazuh side). The Superuser can save the config now +
     verify later when the Wazuh-side admin has provisioned the
     credential. UI shows "Credentials saved (probe failed —
     verify after Wazuh-side setup)".

4. **Multi-Wazuh-cluster support — One install = one Wazuh ecosystem.**
   The topology table is single-row; an install commits to one
   Wazuh deployment. Customers with multiple Wazuh ecosystems spin
   up multiple Wolf installs. Multi-ecosystem support is not in
   v1 scope. Easy to extend later (ecosystem selector + ecosystem-
   per-org routing) if real-world demand emerges.

5. **Wazuh dashboard URL — Single shared.** All orgs link to the same
   Wazuh dashboard host. Wazuh's own RBAC controls what each user
   sees there. Per-org dashboard URLs (federated deployments with
   per-customer dashboards) is a real but rare case; can be added
   as a per-org override in a later slice if needed. v1 is single
   shared.

6. **Topology change — No restart required.** Wolf reads the
   topology fresh per query (microseconds DB overhead, negligible).
   Topology changes take effect on the next query. Zero downtime;
   matches ADR 0019's principle ("DB is source of truth; surfaces
   are views over the same state, read fresh"). The audit log
   captures the topology change — restart is not what makes the
   cutover auditable. Restart-required option is preserved as an
   operator-configurable strictness flag for future regulated-
   industry deployments, but v1 default is no-restart.

7. **Credential storage location — Secrets backend only.** The
   `wazuh_api_password` never appears in the same DB row as org
   metadata. Org metadata (org name, settings) and credentials
   (passwords, tokens) live in separate tables. Org metadata is
   queryable + auditable; credentials are accessed only via the
   secrets API. Cleanest separation; matches principle of least
   exposure.

### Other confirmations (from the Round-2 ADR scope check)

- **Install-level + org-level split** is the right abstraction
  (vs per-org-only with no install-level concept) — install-level
  models the Wazuh ecosystem TOPOLOGY (where the indexers,
  managers, dashboard physically live); org-level models the
  authentication credentials each org uses to query that ecosystem.
  Two different concerns.
- **Single-host + distributed shapes** are the right two to support
  for v1. Hosted-Wazuh-cloud (e.g., Wazuh Cloud) is treated as a
  variant of single-host (URL points to the cloud endpoint instead
  of a self-hosted host). No third deployment shape needed at v1.
- **Per-query credential + topology resolution** (the runtime model)
  is the right design. Caching could be added later as an
  optimization but is unnecessary at v1 scale.

---

## Status, sign-off, next steps

This ADR is now **ACCEPTED**. Operator sign-off after 1-round review
(2026-06-10) closed every previously-open architectural decision.

- Phase 6.6 (Superuser-owned Wazuh component mapping) becomes a real
  work unit, sequenced AFTER Phase 6.5 (per ADR 0018) so the
  Superuser + RBAC + per-tab header model is in place before this UI uses it
- 5 sub-slices, 3-5 sessions, per the "Implementation sequencing" section
- Existing per-tenant `connection_profiles` table refactor happens
  in sub-slice 6.6-c (per-org credentials backend)

No code ships from this commit; design only.
