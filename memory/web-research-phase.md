---
name: web-research-phase
description: "PLANNED PHASE, ADR 0032 LANDED (2026-07-03): Wolf web-research + config-authoring generalization — provider-agnostic web_search/web_fetch/bounded web_crawl (agent-loop-chained, model-decided like Claude), SearXNG self-hosted FREE DEFAULT behind a pluggable SearchProvider adapter (Brave/Tavily optional per-org), docs-first→community fallback, citations→existing evidence panel. wolf-search = its OWN native-venv Debian package mirroring wolf-database + wolf-server SIDECAR (loopback single-server AND default-distributed; mTLS-required only for an optional dedicated tier). 14-class security taxonomy. Config-authoring = research→confirm→dry-run→propose + block-identity for repeated <integration>. Slices 6-f.1–6-f.4, NOT started."
metadata:
  node_type: memory
  type: project
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

**Operator directive (2026-07-03, out of the 6-e.4 config_change web-test).** Two asks that
converge into ONE capability — **Wolf as a research-capable Wazuh expert**:

1. **Edit ANYTHING from a description.** config_change v1 is deliberately narrow (7 allowlisted
   single-instance sections; repeated/merge-semantic like `<integration>` excluded; no
   free-form). Operator wants any request actioned — precise ("here's the block, apply it") OR
   descriptive ("harden FIM, figure out where/how") — via research → understand → **confirm with
   the user** → **dry-run validate** → propose. "Robust, redundant, sophisticated."
2. **Internet research like Claude.** Search anything Wazuh (rules, decoders + references, config,
   blog posts, community guidelines, docs — nothing left behind), **official docs FIRST**, broaden
   to community on a miss, answer WITH references. Studied Anthropic's mechanism: model-DECIDED
   agentic tool, progressive multi-search, `web_fetch` for depth, first-class citations,
   allowed/blocked domains — Wolf implements the PATTERN client-side (can't use Anthropic's
   server-side tool; must stay provider-agnostic to Ollama).

**STATUS: ADR 0032 written + LANDED** (`docs/decisions/0032-web-research-and-config-authoring.md`,
2026-07-03). Design ratified; implementation (slices 6-f.1–6-f.4) NOT started. Locked decisions:

- **SearXNG self-hosted = FREE DEFAULT** behind a pluggable `SearchProvider` adapter (mirrors the
  `ModelProvider` abstraction; `research/interface.py` + `research/registry.py`). Brave (ZDR, fast)
  + Tavily (AI-extraction) = optional per-org hosted backends (keys via secrets backend). Search is
  **opt-in** (`WEB_SEARCH_ENABLED=0` default). NOT Anthropic's server-side web_search.
- **Three tools** the agent loop chains: `web_search`, `web_fetch`, and **bounded `web_crawl`**
  (operator-requested "read a site fully" — query-driven, same-registrable-domain, depth/page-capped,
  sitemap-first, robots-respecting, rate-limited; **NEVER an unbounded spider**; the model judges
  relevance at each hop). All `ReadTool`/`tier=read`, emit `Citation`s into the EXISTING
  evidence/grounding panel (ADR 0026) → grounding enrichment ([[grounding-enrichment-tools-future-phase]]).
- **`wolf-search` = its own component** (operator-chosen): **native venv** (NOT a container — keeps
  Wolf container-free), its **own Debian package MIRRORING `wolf-database`** (`debian/wolf-database.*`
  is the precedent — a bundled third-party runtime Wolf owns), `Recommends` not `Depends` (air-gap /
  hosted-backend installs skip it). Native-venv install recipe = the `wolf-search.postinst`
  (ADR Appendix A; the operator's install and the shipped package are the SAME artifact — stood up
  at slice 6-f.2, empirically grounds the adapter's `/search?format=json` parsing).
- **Deployment topology (both scenarios).** `SEARXNG_URL` seam flips like `DATABASE_URL`/`OLLAMA_BASE_URL`
  (ADR 0016 per-component pattern). wolf-search is **wolf-server's SIDECAR** — ONLY wolf-server calls
  it (adapter lives there); talks to no other component. → **All-in-one AND default distributed =
  co-locate with wolf-server, loopback-bound, ZERO network exposure** (SearXNG has NO native auth, so
  keep it off the net). Only an **optional dedicated/HA search tier** leaves loopback → binds the
  **private NIC + mTLS-required** (wolf-cert mesh, ADR 0023; mTLS IS its auth) + firewall + TLS.
  Connectivity matrix: inbound from wolf-server only; outbound to the internet only.
- **Docs-first ladder:** documentation.wazuh.com → wazuh.com/blog → github.com/wazuh → community
  only on a miss; tiered allowlist + blocklist; official-doc citations visually distinguished.
- **14-class security taxonomy** (operator: address EVERY concern, not just 3). 1 SSRF (+DNS-rebinding,
  IP-encoding, redirect-revalidation, pin resolved IP, block wolf-search/DB/Wazuh/metadata) · 2 indirect
  prompt injection (untrusted-data envelope; no fetched content causes a state change — human-approval
  gate) · 3 query egress (docs-first, minimize, per-org policy, ZDR backends) · 4 decompression/parse
  bombs (cap DECOMPRESSED size, schema-validate SearXNG JSON) · 5 resource+**context-window** budgets
  (protects num_ctx — the tool-truncation regression [[ollama-num-ctx-tool-truncation]]) · 6
  denial-of-wallet (**paid backends ONLY** — SearXNG has no wallet) · 7 secret handling · 8 service
  hardening (topology-appropriate bind, systemd sandbox, pinned version + Dependabot) · 9 MSSP isolation
  + fairness · 10 TLS+URL validation (eTLD+1, defeat homograph) · 11 crawler politeness (robots, rate,
  UA, no evasion) · 12 audit/log hygiene (redact secrets/content, defeat log-forging) · 13 cache safety ·
  14 graceful degradation (never hang the stream [[model-failure-resilience-and-openrouter-free-reality]]).
  **"Free vs bounded" clarifier in the ADR:** SearXNG web access is FREE + UNCAPPED; the budgets are
  self-protection (finite model context; a runaway crawl mustn't crash wolf-server) + tenant-fairness,
  NOT a per-analyst paywall; default generous + operator-tunable ([[config-settings-system-phase]] GUI).
- **Config-authoring generalization (B, consumes A):** research → **confirm the diff with the user** →
  **dry-run validate** (`/manager/configuration/validation`, pre-proposal) → propose (existing ADR
  0025/0029 approval queue, `manager:update_config`, snapshot-restore reversal already built).
  Repeated/merge-semantic sections via **block-identity** (address `<integration><name>virustotal</name>`
  by stable key, not position → the virustotal fix); free-form within rails; break-the-manager sections
  (cluster/auth/indexer/ruleset) STAY blocked.
- **Slices:** 6-f.1 ✅ SHIPPED (2026-07-03): `research/` scaffolding — `SearchProvider` protocol +
  `SearxngProvider` (schema-validated JSON parse, injectable httpx client = OllamaAdapter stub pattern)
  + `get_search_provider_for_organization` resolver (per-org seam + secrets reserved; fails closed when
  disabled); config seam `WEB_SEARCH_ENABLED` (default OFF)/`WEB_SEARCH_PROVIDER`/`SEARXNG_URL`; INERT
  at runtime until 6-f.3; +15 hermetic tests (798 total/0 skip); `research/` ADDED to ci.yml strict set;
  fixed Makefile typecheck DRIFT (was missing gateway/grounding/tools vs CI). NOTE: pin env-sensitive
  Settings defaults via `Settings.model_fields[...]` in tests, NOT a bare `Settings()` (reads `.env` —
  the 6-f.3 web-test flips WEB_SEARCH_ENABLED there)
  · 6-f.2 **HOST HALF DONE (2026-07-03)**: wolf-search LIVE at `127.0.0.1:1307` — installed strictly
  per the OFFICIAL SearXNG docs (operator directive): user `searxng`, source `/usr/local/searxng/searxng-src`
  **pinned commit `747cec4c` = 2026.7.3**, venv `searx-pyenv`, config `/etc/searxng/settings.yml`
  (root:searxng 640 — holds secret_key). FOUR deltas only (rest official-default; fine-tune later):
  port 1307 (operator-chosen) + loopback bind · formats +json (html-only 403s the API — reproduced) ·
  limiter:false + valkey removed (startup ERROR w/o valkey — reproduced). Runner = **uWSGI not Granian**
  (Granian officially container-only); official Debian ini VERBATIM at `/etc/uwsgi/apps-available/searxng.ini`,
  ONE deviation `http-socket = 127.0.0.1:1307` (no nginx — public-instance-only component), NOT in
  apps-enabled (distro uwsgi must never double-run); dedicated **wolf-search.service** unit (uWSGI drops
  to searxng). `wolf-search` wrapper (`deploy/bin/wolf-search` → /usr/local/bin): **health**/status/
  version/logs/service-ops (health check renamed from `doctor` same-day by operator request);
  **health check healthy**. EMPIRICAL: unmodified SearxngProvider parsed the live JSON 5/5,
  documentation.wazuh.com organic #1. Repo: deploy/searxng/{settings.yml template, searxng-uwsgi.ini} +
  deploy/systemd/system/wolf-search.service; `searxng_url` default → 1307. SUDO: password declined,
  temp NOPASSWD drop-in, announced each step, rule removed+verified. **PACKAGING HALF = NEXT**:
  debian/wolf-search.* postinst = this recipe pinned to 747cec4c, Recommends in `wolf` meta-package,
  deb-smoke four→five .debs
  · 6-f.3 the 3 tools + full A6 security + docs-first + citations · 6-f.4 config-authoring
  generalization. Gates: mypy --strict, no skips, full suite + cross-org, restart via
  `systemctl --user restart wolf-server.service` for web-tests.

**Reusable reference from the 6-e.4 web-test.** (1) Wazuh **re-serialises ossec.conf on write** →
verify config/rule persistence STRUCTURALLY (whitespace-tolerant / `has_override`), NEVER literal
substring (the `<sca>` "did not persist" false-negative; fixed commit ecc3562). (2) "SCA-only" was
**model steering, not a code limit** (7 sections allowlisted). (3) 30-min approval TTL is
`PROPOSAL_TTL_SECONDS`; staleness still guarded at execute by each class's freshness re-check.

Links: [[grounding-enrichment-tools-future-phase]], [[runbook-authoring-and-actionable-runbooks]],
[[phase-6e3-rule-tuning-web-test]], [[grounding-execution-modes]], [[config-settings-system-phase]],
[[ollama-num-ctx-tool-truncation]], [[native-https-and-wolf-cert]], [[same-network-gate-deferred]].
