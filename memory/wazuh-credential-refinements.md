---
name: wazuh-credential-refinements
description: "Wazuh-integration refinements from the 6.6-f web-test (2026-06-18): (Q1) credential-change validation bug — FIXED; (Q2) multiple comma-separated index patterns + per-index access checking — BUILT; (Q4) surface agent.labels.group in alert citations — still a tool-enrichment item. Plus: clean enumeration of a scoped user's readable indices is NOT available (listing APIs 403)."
metadata:
  node_type: memory
  type: project
---

From the operator's 6.6-f web-test feedback (2026-06-18), after signing off all
checkpoints:

- **(Q1) Credential-change validation — FIXED this session.** Changing a per-org
  Wazuh username with a blank password used to silently keep the OLD stored
  credential (`_resolve_credential` returned the stored username+password,
  ignoring the typed new username). Fixed: keep-existing only applies when the
  username is UNCHANGED; a username change with a blank password is a 422
  ("Changing the {Indexer|Server API} username requires its password"); blank
  password + unchanged username still keeps the stored password. Client-side
  inline validation mirrors it. (In git/CHANGELOG — here only as the anchor for
  the other two.)

- **(Q2) Multiple index patterns + per-index access checking — BUILT
  2026-06-18.** `wazuh_index_filter` now accepts **comma-separated** patterns
  (trimmed/de-duped/normalized; the runtime search spans them via `/a,b/_search`
  — httpx preserves the comma). `probe_indexer_read` uses `_shards.total` as the
  access signal (empirically: this cluster runs `do_not_fail_on_forbidden`, so a
  forbidden/non-matching pattern returns 200 + 0 shards, NOT 403; a wrong exact
  index → 404). `probe_org_credentials(index_patterns=[...])` probes EACH and
  returns `index_results` (per-pattern ✓/✗ in the card). **Clean enumeration of
  a scoped user's readable indices is NOT possible** — `_cat/indices`/`_aliases`
  → 403 for a scoped user, and `_resolve/index/*` over-reports (shows indices it
  can't read); so Wolf checks the patterns you DEFINE rather than auto-listing.
  The index pattern remains a *target selector*, not a restriction (DLS scopes
  the data).

- **(Q4) Surface `agent.labels.group` in citations — tool-enrichment item.**
  `agent.labels.group` is used only as a query FILTER (opt-in
  `inject_group_label_filter`), never silently injected as data. The `AlertHit`
  citation model ([alerts.py](services/server/wolf_server/tools/alerts.py))
  curates `agent_id/agent_name/rule_*/full_log` and omits it, so it never shows
  in citations. Adding it (+ other useful agent labels) belongs in the
  tool-enrichment/refinement phase — see [[grounding-enrichment-tools-future-phase]].

**Opt-in group-label filter — VALIDATED 2026-06-18** against the operator's
`read *` (no-DLS) scenario, via Wolf's real query path on the live cluster:
- broad credential (`admin`, `read *`, no DLS) on `wazuh-alerts-*`: filter OFF →
  10000+ alerts (everything); filter ON `acme` → 132 (scoped); ON `beta` → 0.
- `wolf-acme` (DLS-scoped on `wazuh-alerts*`) → 132 either way (filter redundant,
  matching the UI hint "only if this credential isn't already DLS-scoped").
So the opt-in genuinely scopes a non-DLS credential to the org's label.

**Forward implication (track for tool-enrichment / Wolf's growing query surface):**
the Wazuh-doc per-org recipe grants `read *` with NO DLS, so a per-org user is
DLS-scoped ONLY on the index families that have an explicit DLS block
(`wazuh-alerts*`, `wazuh-monitoring*`). It reads everything else (e.g.
`wazuh-states-inventory-*`, future vuln/SCA indices) UNSCOPED — verified:
`wolf-acme` and `admin` return the SAME count on
`wazuh-states-inventory-hardware-*`. Today Wolf only queries `wazuh-alerts-*`
(safe), but when tools query those other families, isolation must come from
EITHER per-index DLS on those indices OR Wolf's opt-in group-label filter
applied to those queries. The 6.11 provisioning phase
[[wazuh-provisioning-and-collaboration-phases]] should add DLS to all queried
index families, not just alerts.

All bounded by [[single-org-mssp-parity]] and [[wolf-unrestricted-full-power]].
