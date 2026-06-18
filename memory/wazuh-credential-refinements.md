---
name: wazuh-credential-refinements
description: "Tracked Wazuh-integration refinements from the 6.6-f web-test (2026-06-18): (Q1) credential-change validation bug — FIXED this session; (Q2) index pattern is a target selector not a restriction — consider default-and-hide + dynamic index discovery; (Q4) surface agent.labels.group in alert citations — tool-enrichment item."
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

- **(Q2) Index pattern is a *target selector*, not a restriction.**
  `wazuh_index_filter` (`opensearch_index_pattern`) just says WHICH index to
  `_search` (default `wazuh-alerts-*`); the credential's DLS does the scoping.
  Operator asked why keep it / can't we discover it dynamically. Direction:
  treat it as a sane default that's rarely touched (advanced/optional override
  for pooled-index/custom-index setups). Full dynamic discovery of a user's
  readable indices is partly possible (OpenSearch `authinfo`/account → roles)
  but mapping roles→index-patterns generally needs admin, so it's a stretch.
  Tracked refinement, not yet built.

- **(Q4) Surface `agent.labels.group` in citations — tool-enrichment item.**
  `agent.labels.group` is used only as a query FILTER (opt-in
  `inject_group_label_filter`), never silently injected as data. The `AlertHit`
  citation model ([alerts.py](services/server/wolf_server/tools/alerts.py))
  curates `agent_id/agent_name/rule_*/full_log` and omits it, so it never shows
  in citations. Adding it (+ other useful agent labels) belongs in the
  tool-enrichment/refinement phase — see [[grounding-enrichment-tools-future-phase]].

All bounded by [[single-org-mssp-parity]] and [[wolf-unrestricted-full-power]].
