---
name: wolf-pack
description: "Phase 12 — native Wolf-companion daemon (wolf-pack agent) on every Wazuh host. Bidirectional: ships rules/decoders/SCA/vulnerability/inventory INTO Wolf; executes propose-approved actions OUTBOUND from Wolf. Renamed from wolf-knowledge-relay per ADR 0017 (ACCEPTED 2026-06-11) — scope expanded to bidirectional"
metadata: 
  node_type: memory
  type: project
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

## Status update (2026-06-11)

Originally captured 2026-05-30 as "Wolf Knowledge Relay" (`wolf-relay`).
**Renamed to `wolf-pack` per ADR 0017 (ACCEPTED 2026-06-11)** with
scope expansion to bidirectional — agents on Wazuh hosts both INGEST
into Wolf (the original Knowledge Relay scope) AND EXECUTE actions
OUTBOUND from Wolf via the wolf-gateway approval flow (the new scope).

- **Phase**: now formally **Phase 12 (renamed)** in
  `docs/10-build-roadmap.md`
- **Detailed design**: future dedicated ADR expected ~0023 at
  phase-open time (numbering shifted from the original
  ADR-0017-draft suggestion of 0020 because 0018/0019/0020 are now
  used by Bootstrap-Superuser+RBAC / Web-first-configurability /
  Wazuh-component-mapping respectively)
- **Naming**: working names below ("wolf-relay") are obsolete; the
  agent is `wolf-pack`. References below to "wolf-relay" can be
  read as "wolf-pack" — the body of this memory is preserved as the
  original brainstorm + research that fed into ADR 0017's Phase 12.
- **Terminology**: this file pre-dates the 2026-06-10 tenant →
  organization rename. References to `tenant_id` / "per-tenant"
  should be read as `organization_id` / "per-organization" in any
  forward-looking implementation. Code-side rename happens in
  Phase 6.4 (per ADR 0018).
- **Dependencies confirmed**: hard dependency on Phase 5.4 HTTPS
  (delivered) + Phase 5.6 mTLS (delivered) + Phase 6 wolf-gateway
  (designed; for the new outbound command flow).

The rest of this file is the original 2026-05-30 brainstorm,
preserved for historical context + the detailed source-category
catalog (Categories A-G). When Phase 12 opens, the dedicated ADR
~0023 will be the source of truth + this memory entry will be
referenced for background.

---

## Original brainstorm (2026-05-30)

User proposed (2026-05-30) a deliberately big, deliberately load-bearing addition to Wolf: a **separate native program** that lives on Wazuh hosts and feeds Wolf the entire Wazuh ecosystem as embedded knowledge. Not an AI itself — a deterministic, persistent telemetry daemon, like `wazuh-agent` runs alongside what it observes.

**This is the operationalised industrial-scale version of [[grounding-enrichment-tools-future-phase]]** — instead of adding one new tool at a time, we add a feed source that hydrates thousands of evidence rows the grounding judge can verify against.

## What the relay ships

User clarified (2026-05-30) that the initial list was a starter, not the catalogue. Full sweep below — everything the relay *could* ingest, grouped by category, with relative priority and what to ingest vs deliberately exclude.

### Category A — STATIC / config files on the Manager (ship on change)

| Source | Path | Records | Priority | Why Wolf wants it |
|---|---|---|---|---|
| Built-in rules | `/var/ossec/ruleset/rules/*.xml` | ~3500 | **MVP** | Grounds rule semantics ("rule 5710 triggers on …", chaining via `if_sid`, `frequency`, `timeframe`) |
| Custom rules | `/var/ossec/etc/rules/*.xml` | per tenant | **MVP** | Tenant-private — operator-authored detection logic |
| Built-in decoders | `/var/ossec/ruleset/decoders/*.xml` | ~400 | **MVP** | Grounds field-extraction claims |
| Custom decoders | `/var/ossec/etc/decoders/*.xml` | per tenant | **MVP** | Tenant-private |
| MITRE / compliance tags inside rule XML | embedded in rule tags (`<mitre>`, `<group>` containing `pci_dss_…`, `gdpr_…`, `hipaa_…`, `nist_800_53_…`, `tsc_…`) | per-rule | **MVP** | Falls out of rule ingest; grounds framework claims |
| SCA policies + checks | `/var/ossec/ruleset/sca/*.yml` | ~30 × hundreds | **Full** | Grounds CIS / hardening posture with check id, rationale, remediation |
| CDB lists | `/var/ossec/etc/lists/*.cdb` + `.txt` source | varies | **Full** | Operator IP / hash / user allow- and block-lists; explains "why didn't this alert fire?" |
| Manager config | `/var/ossec/etc/ossec.conf` | one file | **Full** | Wodles enabled, logging level, integrations, AR allowed-agents — grounds "is X monitoring enabled?" |
| Agent shared config | `/var/ossec/etc/shared/<group>/agent.conf` | per group | **Full** | Per-group syscheck / rootcheck / wodle config — grounds per-agent monitoring scope |
| Local internal options | `/var/ossec/etc/local_internal_options.conf` | one file | low | Tuning overrides; explains anomalies in component behaviour |
| Wodle configs | inside `ossec.conf` (`<wodle name="…">`) | many | **Full** | Cloud connectors (AWS, GCP, Azure, GitHub, Office365), CIS-CAT, OpenSCAP, vulnerability detector, command monitoring — grounds "Wolf can / cannot see X cloud" claims |
| Active-response definitions | `<active-response>` blocks in `ossec.conf` + scripts in `/var/ossec/active-response/bin/` | varies | **Full** | The catalogue of *available* actions — critical context for the future Phase 6 propose-and-approve flow (a runbook step "block IP" can only propose what an actual AR script implements) |
| Integration definitions | `/var/ossec/integrations/` + `<integration>` in `ossec.conf` | varies | **Full** | Slack / PagerDuty / VirusTotal / custom Python — explains downstream propagation |
| Agentless monitoring | `/var/ossec/agentless/*.exp` + queue state | varies | low | Less-used; ship if present |

### Category B — DYNAMIC state from the Indexer (poll periodically, delta-ship)

| Source | Index pattern | Cadence | Priority | Why Wolf wants it |
|---|---|---|---|---|
| Per-agent vulnerability findings | `wazuh-states-vulnerabilities-*` | hourly delta | **MVP** | Grounds "this host has CVE-X" precisely |
| Inventory: packages | `wazuh-states-inventory-packages-*` | daily delta | **Full** | "Is `openssh-server X.Y` on agent 003?" |
| Inventory: ports | `wazuh-states-inventory-ports-*` | hourly delta | **Full** | "Is port 22 exposed on agent 003?" |
| Inventory: processes | `wazuh-states-inventory-processes-*` | high-frequency (skip baseline, ship anomalies?) | low | Volume risk — possibly opt-in only |
| Inventory: system / OS | `wazuh-states-inventory-system-*` | weekly | **Full** | OS family / version for capability claims |
| Inventory: hardware | `wazuh-states-inventory-hardware-*` | weekly | **Full** | CPU / RAM / disk for capacity claims |
| Inventory: hotfixes (Windows) | `wazuh-states-inventory-hotfixes-*` | daily | **Full** | KB patches — grounds patch-posture claims |
| Inventory: networks | `wazuh-states-inventory-networks-*` | weekly | **Full** | Interface inventory |
| Inventory: protocols | `wazuh-states-inventory-protocols-*` | weekly | low | Routing tables |
| FIM file state | `wazuh-states-fim-files-*` | snapshot weekly + change events | **Full** | "Has `/etc/passwd` on agent 003 been modified?" |
| FIM registry state | `wazuh-states-fim-registry-*` | weekly | **Full** | Windows FIM equivalent |
| SCA scan results per agent | `wazuh-states-sca-*` | daily | **Full** | NOT the same as SCA policy catalogue — these are pass/fail outcomes per-host. Grounds "agent 003 fails CIS check 1.1.1" |
| Agent monitoring (status over time) | `wazuh-monitoring-*` | optional | low | Already accessible via list_agents tool; ingest only if grounding asks "was agent 003 disconnected last Tuesday?" |

### Category C — API-derived metadata from the Manager (poll or on-demand)

| Source | Endpoint | Priority | Note |
|---|---|---|---|
| Agent fleet + groups | `/agents`, `/agents/groups` | **MVP** | Today's `list_agents` tool returns a page; ship the full fleet snapshot for grounding |
| Per-agent applied config | `/agents/{id}/config/{section}/{component}` | **Full** | What's *actually* applied vs what's in shared config — explains drift |
| Cluster topology | `/cluster/nodes`, `/cluster/status` | **Full** | Master / worker IDs, sync status — grounds cluster-shape claims |
| Manager status + components | `/manager/status`, `/manager/info`, `/manager/active-configuration` | **Full** | Which daemons running, version, uptime |
| Rules + decoders + lists metadata | `/rules`, `/rules/files`, `/decoders`, `/decoders/files`, `/lists` | low | Redundant with Category A file ingest; keep as fallback if file access denied |
| MITRE reference | `/mitre/groups`, `/mitre/techniques`, `/mitre/mitigations` | **Full** | Wazuh ships its own MITRE database — keeps Wolf's mapping current without external pull |

### Category D — Indexer-side topology (poll once, re-poll on cluster change)

| Source | Priority | Why |
|---|---|---|
| Index templates + mappings (`_template`, `_mapping`) | **Full** | Field shape per index — lets Wolf construct better queries and validates "field X exists on alerts" claims |
| ISM policies (lifecycle) | low | Operator hygiene info |
| Cluster health, node list, shards | low | Operational; surface if grounding asks |

### Category E — Dashboard-side (lowest priority, ingest selectively)

| Source | Note |
|---|---|
| Saved searches + saved visualisations | Could ground "you already have a dashboard for X" claims; opt-in per tenant |
| Index patterns | Mostly redundant with Indexer mappings |
| Reports configuration | Useful for "scheduled report contains X" claims; opt-in |

### Category F — Host-level observability (the machine *running* Wazuh)

| Source | Priority | Note |
|---|---|---|
| OS / kernel / uptime of manager host | low | Grounds "the manager is on Ubuntu 24.04" claims |
| Disk / memory / CPU pressure | low | Grounds "the indexer is short on disk" operational claims |
| Service health (`systemctl is-active wazuh-manager`) | **Full** | Relay self-knowledge — ship as a heartbeat |

### Category G — Threat-intel / feed data (mostly already in Category B)

The Wazuh Vulnerability Detector ingests NVD, Red Hat OVAL, Debian, Ubuntu, MS, Arch, ALAS feeds and writes to the vulnerability state indices. We ship from the indices, not the raw feeds — same data, far less volume.

The MITRE ATT&CK database is similarly shipped via the API (Category C). No need to re-pull from upstream.

---

## What the relay deliberately does NOT ship

| Excluded | Why |
|---|---|
| `/var/ossec/etc/client.keys` | Agent enrollment secrets — never leaves the manager |
| `/var/ossec/api/configuration/security/users` (password hashes) | Credential material |
| Any private key under `/var/ossec/etc/sslmanager.key`, `/etc/filebeat/`, etc. | Credentials |
| Live `wazuh-alerts-*` time-series in bulk | Volume; already accessible per-query through Wolf's `search_alerts` / `aggregate_alerts` / `get_event_timeline` / `get_agent_alert_history` tools |
| `wazuh-archives-*` (every log line, not just alerts) | Volume — terabytes; out of scope unless future need arises |
| Wodle output payloads (raw cloud event dumps) | Already flows into alerts when relevant |
| `/var/ossec/logs/*` (ossec.log, api.log) | Operational telemetry, not knowledge; can be tailed live by a future ops tool but doesn't belong in the grounding evidence pool |

---

## Extensibility — operator can add sources

The relay should be **plugin-shaped**: one Python (or Go) module per source category, registered in a manifest. An operator can drop in a new module for a custom feed without forking the relay. The Wolf-side ingest endpoint validates each chunk against its declared source_type's Pydantic schema, so adding a new source_type is purely additive — no migration.

This matters because future Wazuh versions add new indices and new config sections; the relay should follow without a Wolf release.

## Architecture sketch

```
┌─────────────────────────────────────────────┐
│  Wazuh manager / indexer host               │
│                                             │
│  /var/ossec/ruleset/   (rules, decoders, SCA)│
│  Wazuh Indexer        (vuln + inventory)    │
│                                             │
│  ┌─────────────────────┐                    │
│  │  wolf-relay         │  systemd unit      │
│  │                     │  installed via     │
│  │  - inotify on files │  .deb / .rpm       │
│  │  - poll indexer     │                    │
│  │  - diff + ship      │                    │
│  │  - mTLS auth        │                    │
│  └────────┬────────────┘                    │
└───────────┼─────────────────────────────────┘
            │ HTTPS (mTLS — Phase 5.4 issued cert)
            ▼
┌─────────────────────────────────────────────┐
│  Wolf orchestrator                          │
│                                             │
│  POST /api/v1/relay/ingest                  │
│      ↓                                      │
│  parse → chunk → embed → upsert into        │
│  knowledge_chunks (new source_types,        │
│  carries tenant_id, idempotent)             │
│                                             │
│  → grounding judge's evidence pool grows    │
│    by orders of magnitude                   │
└─────────────────────────────────────────────┘
```

## Single-instance vs cluster Wazuh

| Topology | Relay deployment |
|---|---|
| **Single-instance** | One relay on the all-in-one host. Reads local files + local indexer. |
| **Cluster** | One relay per server node (each reads its local custom rules) + one relay per indexer node (for state indices). Wolf-side dedupe via stable chunk hash so the same built-in rule isn't embedded N times. |

User-configurable through the Wolf Settings → **Wolf Configuration** panel (currently stubbed in the chat-header gear menu): per-tenant deployment shape, server node list (with master/worker role), indexer node list, ingestion cadence, which source types to enable.

## Hard dependency on Phase 5.4

The relay ships sensitive security data (rule logic, vulnerability state, inventory, compliance posture) over the network. **It must use mTLS, not plain HTTP**, even on a trusted LAN — a security tool that ships its data in cleartext is a contradiction.

This makes Phase 5.4 (Native HTTPS + `wolf-cert`) a hard prerequisite, with one small addition: `wolf-cert` gains a `wolf-cert issue-relay <tenant>` subcommand that mints a relay client cert + bundles the Wolf root CA for the relay to trust the orchestrator.

**Recommended phase order:** 5.0c-f → Phase 5.4 (HTTPS + relay-cert provisioning) → **this Knowledge Relay phase** → Phase 5 (RBAC, which gates the Settings UI for relay enrollment) → Phase 5.5 (Knowledge Management UI for hand-authored runbooks, complementing the auto-fed knowledge) → grounding-enrichment tools → Phase 6.

**LOCKED 2026-05-30:** user picked this order. Phase 5.4 grows by one subcommand (`wolf-cert issue-relay <tenant>`) to mint relay client certs; no rework needed when the Relay phase starts.

## MVP vs full

To keep this from becoming a one-year project, split it:

| Phase | Scope |
|---|---|
| **MVP** | Relay reads rules + decoders + MITRE / compliance maps from local XML. Ships via mTLS to Wolf. Wolf ingests into `wazuh_rule` and `wazuh_decoder` source types. Two new tools: `lookup_rule(rule_id)`, `lookup_decoder(name)`. Single-instance Wazuh only. One tenant. CLI-driven enrollment (`wolf-relay enroll --tenant acme --token …`). |
| **Full** | + SCA policies / checks · + vulnerability + inventory ingestion from indexer · + cluster awareness with dedupe · + new tools (`find_rules_by_compliance`, `find_rules_by_mitre`, `lookup_cve`, etc.) · + Settings UI panel for visual enrollment + deployment topology · + delta-shipping with inotify · + per-source ingestion cadence config |

## Risks to design around (not blockers, but real)

1. **Wazuh version coupling** — rule / decoder / SCA formats can shift across Wazuh major versions. Relay needs a version-aware parser strategy (per-major-version parsing module, declared compatibility range).
2. **Volume** — full Wazuh ruleset (~3500 rules × embedding-vector size) + SCA + vuln + inventory = significant storage and embedding cost. Plan: tenant-private chunks (default) so shared content doesn't bloat per-tenant indices; for built-in rules, consider storing once with `tenant_id NULL` (shared) and only tenant-stamping custom rules + state data.
3. **Live updates** — operators add custom rules. Use inotify on `/var/ossec/etc/`. Throttle: re-embed at most every 30s per file to absorb rapid edits.
4. **Per-tenant isolation** — every shipped chunk carries `tenant_id`; the relay's mTLS cert binds it to exactly one tenant; the ingest endpoint refuses cross-tenant writes. Mirror the same defence-in-depth from doc 05.
5. **CVE feed scale** — NVD has ~250K CVEs. Don't embed them all upfront; only embed CVEs that are currently *flagged on this tenant's agents* (the indexer already filters). Re-evaluate if grounding needs general CVE lookup.
6. **Cluster dedupe** — stable chunk hash = SHA-256 of `(source_type, normalised_content, chunk_metadata.canonical_id)`. Upsert by hash. Built-in rule shipped from 3 cluster nodes lands once.
7. **Failure modes** — relay must be resilient to Wolf being down: local queue with disk-backed retry (e.g. SQLite spool). Don't lose data, don't OOM the host.
8. **Telemetry of the relay itself** — its own health (last ingest time, queue depth, parse failures) should be visible in Wolf, so an operator can see if a relay went silent.

## Naming

Candidates: `wolf-relay` · `wolf-knowledge-relay` · `wolf-feeder` · `wolfd` · `wolf-agent` (avoid — overloaded with Wazuh's "agent" concept). **Working name: `wolf-relay`** unless the user picks otherwise.

## Cross-references

- [[grounding-enrichment-tools-future-phase]] — this is the industrial-scale realisation of that idea; the dedicated-phase track essentially becomes this.
- [[native-https-and-wolf-cert]] — hard prerequisite; Phase 5.4 grows by one subcommand to issue relay certs.
- [[runbook-authoring-and-actionable-runbooks]] — complementary, not overlapping. Runbooks are operator-authored procedural knowledge; relay-shipped data is system-authored factual knowledge.
- [[wolf-color-palette]], [[per-slice-web-test-checkpoints]], [[integrity-across-the-stack]] — standing rules; apply across phases.
