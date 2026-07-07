---
name: web-research-phase
description: "ACTIVE PHASE, ADR 0032 (2026-07-03): Wolf web-research + config-authoring generalization — provider-agnostic web_search/web_fetch/bounded web_crawl (agent-loop-chained, model-decided like Claude), SearXNG self-hosted FREE DEFAULT behind a pluggable SearchProvider adapter (Brave/Tavily optional per-org), docs-first→community fallback, citations→existing evidence panel. wolf-search = its OWN native-venv Debian package mirroring wolf-database + wolf-server SIDECAR (loopback single-server AND default-distributed; mTLS-required only for an optional dedicated tier). 14-class security taxonomy. Config-authoring = research→confirm→dry-run→propose + block-identity for repeated <integration>. 6-f.1 ✅ 6-f.2 ✅ 6-f.3 ✅ (operator web-test PASSED); 6-f.4 ✅ SHIPPED 2026-07-05 (blocklist+add-if-absent, block-identity upsert/remove, two-phase confirm-diff in the tool, research-to-act posture; operator web-test = virustotal end-to-end pending)."
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
  temp NOPASSWD drop-in, announced each step, rule removed+verified. **PACKAGING HALF SHIPPED
  (2026-07-03) → 6-f.2 CLOSED**: `debian/wolf-search.*` — Architecture:all, Depends = the official
  apt list, .deb carries ONLY Wolf-owned artifacts (wrapper+templates+unit); **postinst = the exact
  host recipe pinned to `747cec4c`** (runuser not sudo; pip -e $SRC not cd; settings/ini
  install-ONCE so operator edits + live secret survive upgrades); postinst needs NETWORK
  (github+PyPI) — ratified trade-off, air-gap skips it (`Recommends` in `wolf` meta, NOT Depends);
  upgrades incremental (postrm keeps checkout on upgrade, removes /usr/local/searxng on remove,
  /etc/searxng only on purge, searxng user never). CI four→FIVE .debs (smoke-deb + release.yml);
  smoke-deb-install asserts every postinst effect then STARTS the service + `wolf-search health`
  END-TO-END on the clean runner (only component needing no operator env; timeout 15→25);
  smoke-systemd covers the 4th unit template. NOTE for this host: when installing the .deb here
  later, remove the manual `/usr/local/bin/wolf-search` (shadows the .deb's `/usr/bin` copy in PATH)
  · 6-f.3 **SHIPPED 2026-07-05; operator web-test PASSED 2026-07-05** (decoder/rule +
  integration research on the live cluster — docs-first citations, official badges, clickable
  evidence confirmed; observation: well-sourced answers still mostly Uncertain/Not-Verified →
  grounding-enrichment became committed Phase 6.13, see [[grounding-enrichment-tools-future-phase]]):
  the 3 tools
  end-to-end. `research/` request path: `weburl` (SSRF guard — every resolved address vetted,
  connect PINNED to the vetted IP w/ hostname in Host+SNI so TLS verify still runs; redirects
  re-validated per hop; CPython gotcha: v4 multicast is `is_global=True` → rejected explicitly;
  203.0.113.x doc-range IPs are is_private → use real public IPs in test fixtures) · `extract`
  (stdlib-only HTML→text/links; no bs4/lxml — lean wheels) · `fetcher` (DECOMPRESSED byte-cap
  streaming abort, content-type enforcement, whole-fetch deadline, injectable client+resolver)
  · `policy` (docs-first tiers official_docs/official/official_github(path-aware)/community;
  suffix-anchored matching; blocklist ships EMPTY — operator curation → Phase 6.10; stdlib
  eTLD+1 approximation) · `crawl` (robots fail-open convention, sitemap via regex `<loc>` —
  entity-bomb-immune, same-registrable-domain, seed always first, off-domain filtering at POP
  time so the skip counter fires, 120s deadline) · `context` (per-request ResearchContext w/
  budget; async CM owns client lifecycles). Tools: envelope `[BEGIN/END UNTRUSTED WEB CONTENT]`
  16K fetch / 3K crawl-page caps; web_search = one Citation PER result (plural `citations`
  field, loop collects both); model inputs narrow NEVER widen server caps. KEY WIRING FACTS:
  registration gated on the FLAG alone (reachability = call-time degradation — deliberate A1
  refinement, no boot-order coupling per ADR 0016); `WEB_RESEARCH_SUFFIX` prompt section rides
  the same gate; NEW `ToolDegradedError` (tools/base.py) + dispatcher branch `tool.call.degraded`
  = expected failures degrade cleanly (WolfError still re-raises; budget = GuardrailViolation);
  Citation gained url/title/source; evidence panel renders links + official badges. 7 A7 knobs
  (WEB_SEARCH_MAX_RESULTS 8, WEB_SEARCH_BUDGET_PER_REQUEST 12, WEB_FETCH_MAX_BYTES 2MB,
  WEB_FETCH_TIMEOUT_SECONDS 20, WEB_CRAWL_MAX_DEPTH 2, WEB_CRAWL_MAX_PAGES 12,
  WEB_CRAWL_PER_HOST_RATE 1s). 866 tests/0 skips (+68 hermetic). LIVE: qwen3:8b chained 1
  search + 3 fetches unprompted, documentation.wazuh.com organic #1, cited answer 53s;
  `.env` now has WEB_SEARCH_ENABLED=1 (stays ON — feature is operator-accepted). Live-probe recipe:
  login needs the mTLS client cert `.local/certs/dashboard-client/{cert,key}.pem` + POST
  `/api/v1/auth/login` (cookie session) + `X-Organization-Id` header on `/api/v1/chat`
  · 6-f.4 **SHIPPED 2026-07-05** (operator web-test pending): config-authoring generalization +
  research-first posture. B3: `EDITABLE_SECTIONS` allowlist → `BLOCKED_SECTIONS`
  {auth,cluster,indexer,rule_test,ruleset}; update_section ADDS an absent section (insert before
  final `</ossec_config>`). B2 (virustotal fix): `IDENTITY_KEYS` {integration→name,
  localfile→location, command→name}; ops `upsert_block`/`remove_block`+`block_key`; upsert
  content must CARRY the addressed identity; duplicate-key refuses; identity-scoped
  reformatting-tolerant persist proofs (`block_persisted`/`block_removed`); restore_config
  matches optional block_key. B1: confirm-diff is **TWO-PHASE IN THE TOOL** — unconfirmed call
  = full author-time work (capability+validate+live read+`build_candidate` dry-run, SAME
  transformation the executor runs) → `state="needs_confirmation"`+current_content, queues
  NOTHING; only `user_confirmed=true` queues. KEY REFINEMENT: author-time manager validation
  impossible without a write (`/manager/configuration/validation` validates ON-DISK only) →
  dry-run = the transformation; manager validation stays at execute w/ auto-rollback. Prompts:
  SYSTEM_PROMPT #4 = THE AUTHORING LOOP; WEB_RESEARCH_SUFFIX += RESEARCH-TO-ACT
  ([[web-research-as-universal-power]]). GUI: block-op targets/diffs/removal cards. 896 tests/0
  skip (+30). Gates: mypy --strict, no skips, full suite
  + cross-org, restart via `systemctl --user restart wolf-server.service` for web-tests.

**Reusable reference from the 6-e.4 web-test.** (1) Wazuh **re-serialises ossec.conf on write** →
verify config/rule persistence STRUCTURALLY (whitespace-tolerant / `has_override`), NEVER literal
substring (the `<sca>` "did not persist" false-negative; fixed commit ecc3562). (2) "SCA-only" was
**model steering, not a code limit** (7 sections allowlisted). (3) 30-min approval TTL is
`PROPOSAL_TTL_SECONDS`; staleness still guarded at execute by each class's freshness re-check.

**6-f.5 SHIPPED 2026-07-06** (the 6-f.4 web-test feedback fixes, ADR 0032 addendum 2026-07-06):
unbounded agent persistence + any-unique-field block disambiguation — full detail in
[[no-hard-step-caps-unbounded-persistence]] + [[block-disambiguation-any-unique-field]].
908 tests / 0 skips.

**6-f.6 SHIPPED 2026-07-07** (deployment-aware config application, ADR 0032 addendum 2026-07-06): config changes apply per deployment type — all-in-one direct, distributed per cluster node (ossec.conf NOT cluster-synced, live-confirmed) — full detail in [[deployment-aware-config-application]]. 924 tests / 0 skips. NEXT in 6-f: the operator's Nemotron 3 Ultra/Super (free) model-switch evaluation ([[model-switch-nemotron-after-slices]]).

Links: [[grounding-enrichment-tools-future-phase]], [[runbook-authoring-and-actionable-runbooks]],
[[phase-6e3-rule-tuning-web-test]], [[grounding-execution-modes]], [[config-settings-system-phase]],
[[ollama-num-ctx-tool-truncation]], [[native-https-and-wolf-cert]], [[same-network-gate-deferred]].
