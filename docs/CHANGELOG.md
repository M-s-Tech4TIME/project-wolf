# Wolf — Development Changelog

> **This is the append-only history of the Wolf project.** Every Claude Code
> session, every meaningful human change, every decision — appended here as
> the work happens.
>
> **Rules for this file:**
>
> - Append only. Never delete or rewrite past entries.
> - Newest entries at the top. Reverse chronological.
> - Every session adds at least one entry, even if "no code changes — just
>   investigation."
> - Be specific. "Updated config" is useless; "Set DEFAULT_MODEL_ID=qwen3:4b
>   in services/orchestrator/app/config.py after probe results showed
>   reasoning_tier=basic on this hardware" is useful.
> - For decisions that change architecture or defaults, also write a full ADR
>   in `docs/decisions/` and reference its filename here.
>
> For *current* project state, see `PROGRESS.md` (live, updated, not
> chronological).

---

## Entry template

Copy this block and fill in at the start of each session entry:

```
## YYYY-MM-DD — [Session brief title]

**Session type:** [claude-code / human / mixed]
**Phase:** [from roadmap]
**Duration:** [approx — for capacity tracking]
**Branch / commit:** [git ref where work ended]

### What we did
- [bullet — concrete action]
- [bullet — concrete action]

### What we decided
- [decision, with reason; link to ADR if applicable]

### What broke / what we discovered
- [unexpected issue, finding, surprise]

### What's next
- [next-action item — should match PROGRESS.md "What's next"]
```

---

## 2026-05-27 — Phase 4.1: two-tenant live DB + RAG cross-tenant tests

**Session type:** claude-code (continuation; first Phase 4 slice)
**Phase:** Phase 4 — multi-tenancy hardening (per `docs/10-build-roadmap.md`)
**Duration:** ~45 min
**Branch / commit:** `main` — starting commit `2197d97`, this entry's
commit pending.

**Phase-numbering correction (do not skip this):** earlier Phase 3
sessions referred to "Phase 4" as the propose-tools + approval-gateway
work. That was wrong per the actual roadmap, which orders:

| Phase | What | Status |
|---|---|---|
| Phase 3 | Knowledge & RAG | ✅ shipped |
| **Phase 4** | **Multi-tenancy hardening** | ← actually next |
| Phase 5 | Cases and reporting | |
| **Phase 6** | **Propose tools + Approval Gateway** | ← what was mis-framed as "Phase 4" |

Older CHANGELOG entries are append-only per `docs/11-claude-code-instructions.md`
and stay as-shipped; PROGRESS.md updated this session to match the
roadmap's actual ordering. Reading the older "Phase 4" references in
prior CHANGELOG entries: they meant the propose-tools work, which is
Phase 6.

### What we did

- **Bootstrapped a second tenant `beta`** alongside `acme` so Phase 4's
  isolation work has actual two-tenant live state to exercise against.
  Both tenants point at the same dev Wazuh deployment (`192.168.245.128`)
  for simplicity; their separation is enforced by tenant_id stamping
  at the application layer, not by per-tenant Wazuh instances (the
  "bridge model" from doc 05).
- **Seeded beta with its own private chunks** via the existing dev-seed
  CLI. The seed CLI templates the tenant slug into the runbook/incident
  content (`{TENANT}_SOC SSH brute-force runbook`), so beta's chunks
  are textually similar to acme's but tagged with beta's tenant_id and
  reference "BETA SOC" / "BETA SSH sweep" — distinguishable evidence
  of isolation.
- **Live DB state after seeding:**

  | tenant | source_type | chunks |
  |---|---|---|
  | acme | past_incident | 1 |
  | acme | runbook | 2 |
  | beta | past_incident | 1 |
  | beta | runbook | 2 |
  | (shared) | attack | 700 |
  | (shared) | wazuh_doc | 4476 |

  Note the shared corpora grew slightly from Slice 3's baseline because
  the dev-seed CLI re-inserts its 6 inline shared chunks on each run.
  Not a problem; the tests don't depend on shared-chunk uniqueness.
- **Extended `tests/test_cross_tenant_isolation.py`** with 3 new tests
  covering the Phase-3 RAG path the original Phase-2-era suite predated:
  - `test_pgvector_store_search_constrains_results_to_requesting_tenant`
    — source-level invariant. Asserts that every candidate-fetcher
    method (`_vector_candidates`, `_fts_candidates`,
    `_vector_aux_candidates`) contains the tenant-scoping WHERE clause
    in its source. A future contributor would have to delete the clause
    to break isolation; the source-grep check catches it without needing
    a live DB.
  - `test_pgvector_chunk_input_validation_blocks_cross_tenant_writes` —
    validates that ChunkInput's tenant_id-vs-source_type rule (shared
    corpus must have NULL tenant_id; tenant-private corpus requires a
    tenant_id) raises at the data layer. Prevents the inverse mistake
    of cross-tenant writes.
  - `test_pgvector_search_call_path_includes_requesting_tenant_id` —
    sanity-checks the search() call shape: each leg-helper receives
    the REQUESTING tenant's id and ONLY that id.
- **Live cross-tenant verification** (one-shot script, not test
  fixture): with the live dev DB in chained-retrieval mode
  (BM25 + v1.5 + v2-moe per ADR 0014), ran the same query as both
  tenants:
  - "SSH brute-force runbook steps" as acme → returned only ACME-tagged
    chunks (ACME SOC SSH brute-force response, ACME SOC Brute-force
    triage, INC-2026-0042 ACME SSH sweep).
  - Same query as beta → returned only BETA-tagged chunks (BETA SOC SSH
    brute-force response, BETA SOC Brute-force triage, INC-2026-0042
    BETA SSH sweep).
  - Zero cross-tenant leakage observed.
- **Tests**: 7 tests now in test_cross_tenant_isolation.py (4 prior + 3
  new). `make check` 183 passed (180 prior + 3 new). Lint + mypy strict
  still clean.
- **Updated PROGRESS.md** to clarify the actual roadmap-ordered Phase
  4-5-6 sequence, replacing earlier "Phase 4 = propose tools" drift.

### What we decided

- **Beta tenant points at the same Wazuh deployment as acme.** In
  production an MSSP would have per-tenant Wazuh deployments; for the
  dev DB the application-layer isolation is the load-bearing
  enforcement, and reusing the existing Wazuh keeps the dev setup
  simple. Application-layer tenant_id scoping is what we're
  hardening in Phase 4 anyway.
- **Source-level invariant tests for the RAG isolation clauses.** A
  test that runs SQL against a live Postgres would also catch
  regression, but it requires a Postgres-only fixture path the
  conftest doesn't currently provide. The source-grep approach
  catches the regression risk without needing the fixture; Slice 4.4
  will add the canonical `tools/tenant_isolation_test/` runnable that
  exercises the live DB as the operational guard.
- **Append-only CHANGELOG discipline preserved.** Prior entries that
  refer to "Phase 4" meaning the propose-tools work are NOT rewritten;
  this entry explains the drift and PROGRESS.md (live state, not
  history) carries the corrected ordering forward.

### What broke / what we discovered

- **The dev-seed CLI is not idempotent on the shared corpus.** Running
  `seed_dev_knowledge` twice (once for acme, once for beta) added 12
  shared chunks instead of 6. Existing chunk-hash idempotency lives
  in `tools/seed_knowledge` (the real corpus ingester), not in
  `seed_dev_knowledge` (which is a Slice-1 inline-content CLI). Not
  blocking — neither retrieval nor isolation is affected — but
  worth a follow-up. Filed as a future ergonomic improvement.
- **Phase-numbering drift was a real cost.** Three prior CHANGELOG
  entries written this session used "Phase 4" to mean what's actually
  Phase 6. A future contributor reading those entries chronologically
  would have inferred a different roadmap shape. Lesson: when
  finishing a phase, re-read `docs/10-build-roadmap.md` for the next
  phase's actual scope before writing the close-out summary, not just
  the current entry's section heading.

### What's next

- **Phase 4 Slice 4.2** — `bootstrap_tenant` validates connection
  before persisting; `--update` flag for re-bootstrap (treat
  `TenantWazuhConfig` as immutable post-validation per doc 05
  §Tenant misconfiguration).
- **Phase 4 Slice 4.3** — `TenantScopedCache` abstraction (minimal,
  in-memory) + one consumer (agent_name resolution caching) +
  audit-write isolation test.
- **Phase 4 Slice 4.4** — flesh out `tools/tenant_isolation_test` as
  the canonical runnable isolation suite; wire into CI;
  document the two-tenant pattern in ONBOARDING; Phase 4 close-out.

---

## 2026-05-27 — Multi-embedding RRF chaining (ADR 0014)

**Session type:** claude-code (continuation)
**Phase:** Phase 3 close-out — chained-retrieval extension
**Duration:** ~90 min
**Branch / commit:** `main` — starting commit `54e01ae`, this entry's
commit pending.

### What we did

- **Empirical motivation**: ran a full-corpus benchmark
  (`tools/embedding_benchmark/full_corpus_v2_eval.py`) re-embedding the
  live 5173-chunk corpus with `nomic-embed-text-v2-moe` in memory and
  comparing against the existing v1.5 embeddings on a 20-query battery
  of rule-ID + ATT&CK technique lookups with known-correct answers.
  Result: v2-moe vectors-only precision@1 = 35% vs v1.5's 15%
  (2.3× lift) and precision@5 = 50% vs 15% (3.3× lift). But v2-moe
  has a 512-token context limit — 3.5% of the corpus (mostly long
  ATT&CK techniques) gets truncated or fails entirely.
- **Operator framed the goal clearly**: chain v1.5 + v2-moe so they
  complement each other and fill the gap where each individually
  lacks. RRF over diverse rankers is exactly the right primitive.
- **Migration 0006** — `embedding_v2 vector(768)` (nullable) +
  `embedding_v2_model varchar(100)` + HNSW cosine-ops index on the
  new column. Backward-compatible: existing chunks keep working with
  NULL aux columns.
- **Settings** — `EMBEDDING_MODEL_AUX` / `EMBEDDING_PROVIDER_AUX`.
  Empty default preserves Slice-2A behaviour (single-leg vector +
  BM25). When set, the orchestrator builds a second embedder.
- **`make_embedding_provider_aux(settings)`** factory — returns None
  for empty config; constructs the secondary adapter otherwise.
  Shares `_build_provider()` helper with the primary factory.
- **`PgvectorKnowledgeStore`** — accepts `embedder_aux=None` kwarg.
  `upsert()` writes both vectors when configured (with per-chunk
  error tolerance for aux — a v2-moe rejection leaves
  `embedding_v2 IS NULL` for that chunk; primary leg still indexes
  it). `search()` adds a `_vector_aux_candidates()` helper that
  filters on `embedding_v2 IS NOT NULL` so unembedded chunks don't
  pollute or block the aux leg.
- **`search()`** now does 3-way RRF when an aux embedder is wired
  (BM25 + primary vector + secondary vector). Slice-2A behaviour is
  preserved when not — same 2-leg flow as before.
- **`wolf reembed --aux`** — extended to walk rows where
  `embedding_v2_model IS DISTINCT FROM <active aux model>` and
  populate them in batches. Uses an `__unembeddable__` sentinel value
  for chunks the aux model rejects after truncation (1800-char
  default cap), so subsequent runs don't loop on them. Per-chunk
  error tolerance preserved.
- **chat.py** — constructs both embedders via the factories and
  hands the secondary to `PgvectorKnowledgeStore(..., embedder_aux=aux)`.
  Both endpoints (`/chat` and `/chat/stream`) updated symmetrically.
- **Populated `embedding_v2` for the entire corpus**: 5145 / 5173
  chunks (99.5%) successfully embedded with v2-moe; 28 chunks
  (0.5%) marked `__unembeddable__` after truncation (long ATT&CK
  descriptions that even at 1800 chars produce malformed input
  v2-moe rejects). Those 28 stay retrievable via v1.5 + BM25 legs —
  the chained design's complement-each-other promise.
- **Tests** — 2 new in `test_knowledge_store.py`:
  - `test_rrf_fusion_three_legs_chunk_in_all_wins` — a chunk
    ranking in all three legs decisively beats singletons present
    in only one.
  - `test_rrf_fusion_skips_aux_leg_when_no_aux_embedder` — default
    behaviour is preserved when `embedder_aux=None` (the aux helper
    is not even invoked).
  `make check`: **180 passed** (178 prior + 2 new). Lint + mypy
  strict clean.
- **`tools/embedding_benchmark/full_corpus_chained_eval.py`** — runs
  the same 20-query battery against the LIVE store in two modes:
  single-leg (BM25 + v1.5) and chained (BM25 + v1.5 + v2-moe).
- **ADR 0014** captures the design + alternatives + measured impact
  + operator workflow + rollback path.

### Measured impact

20 queries with known-correct answers (rule IDs + ATT&CK technique
IDs) against the live 5173-chunk corpus.

| Mode | precision@1 | precision@5 | p50 latency |
|---|---|---|---|
| Vectors-only v1.5 | 15% (3/20) | 15% (3/20) | (in-memory test) |
| Vectors-only v2-moe | 35% (7/20) | 50% (10/20) | (in-memory test) |
| **BM25 + v1.5** (Slice 2A baseline) | 15% (3/20) | 35% (7/20) | 48 ms |
| **BM25 + v1.5 + v2-moe (ADR 0014)** | **30% (6/20)** | **60% (12/20)** | 159 ms |

Chained mode recovers 5 queries single-leg missed entirely in the
top-5 (Process Injection T1055, Local System T1005, DNS Tunneling
T1071.004, Pass the Hash T1550.002, Boot/Logon Autostart T1547).
Latency goes 48 → 159 ms per search — imperceptible inside the
multi-second LLM generation phase.

### What we decided

- **RRF over a third leg, not score normalization.** Per-leg
  rankings, no cross-leg score comparison — same primitive Slice 2A
  uses for BM25 + vector fusion. Adding a fourth leg later is
  mechanical.
- **Nullable aux column, per-chunk error tolerance.** Chunks the
  aux model can't handle stay retrievable via v1.5 + BM25. The
  design intent is explicitly "v1.5 covers what v2-moe can't" — not
  "everything embeds twice or nothing."
- **Sentinel `__unembeddable__` for chunks even truncation can't
  fix.** Prevents the reembed CLI looping forever on a small set of
  problematic chunks.
- **Empty default for `EMBEDDING_MODEL_AUX`.** Single-leg deployments
  cost nothing; the chained path is opt-in via env. Wolf's "no paid
  dependency" principle isn't touched — both v1.5 and v2-moe are
  Apache 2.0, both run via Ollama.
- **The realistic operational metric is precision@5, not @1.** The
  agent loop retrieves top-K chunks and feeds them to the LLM. RRF
  is structurally better at building a high-recall top-K than at
  picking a single best — exactly what the agent needs.

### What broke / what we discovered

- **First reembed run stalled at 97% coverage** because the CLI's
  initial error path set `embedding_v2_model = NULL` for chunks the
  aux rejected, but the `IS DISTINCT FROM` filter kept picking those
  same NULL rows back up on the next iteration — infinite loop.
  Fix: sentinel value `__unembeddable__` distinct from both NULL and
  the active aux model id. Plus 1800-char truncation cap so most
  long chunks succeed.
- **v2-moe still rejects 28 chunks even at 1800-char input.** Long
  ATT&CK techniques with dense paragraph structure produce
  "unexpected EOF" no matter how we slice the text. Those chunks
  retain `embedding_v2 IS NULL` and `embedding_v2_model =
  '__unembeddable__'`. The chained design absorbs this: v1.5 covers
  the long-context retrieval for them.
- **precision@1 dropped slightly vs vectors-only v2-moe** (35% →
  30%) — RRF dilutes a single-leg dominant ranking when the other
  legs don't agree. This is a known RRF property and the right
  trade because Wolf retrieves top-5, not top-1. precision@5 went
  up by half (35% → 60%) as expected.

### What's next

- **Phase 4 — propose tools + approval gateway.** All Phase 3 work
  now sits at a stable end-state with measured retrieval quality
  improvements documented.
- **Operator install-script update.** Doc 16's install-script spec
  needs the optional aux-embedder step (`ollama pull
  nomic-embed-text-v2-moe`) plus the post-install reembed
  documented. Belongs in the Phase 4 packaging work.
- **Future: 4th RRF leg** (a Wolf-specific fine-tune of one
  embedding model on real analyst queries). Not pressing; the
  3-leg flow at 60% precision@5 is enough to ship Phase 4 against.

---

## 2026-05-27 — Phase 3 follow-ups: judge model, agent_name, reembed, frontend

**Session type:** claude-code (continuation)
**Phase:** Phase 3 close-out — all queued follow-ups
**Duration:** ~120 min
**Branch / commit:** `main` — starting commit `05cb750`, this entry's
commit pending.

### What we did

**Follow-up 1 — stronger grounding judge (ADR 0013):**

- Added three settings to `Settings`:
  - `GROUNDING_JUDGE_MODEL_ID` (empty = use the chat model; backward-
    compat)
  - `GROUNDING_JUDGE_MODEL_PROVIDER` (empty = same as chat)
  - `GROUNDING_JUDGE_API_KEY_REF` (empty = same as chat)
- Refactored `app/agent/model_resolver.py` to factor out a `_build_provider()`
  helper shared by both `get_model_for_tenant()` and the new
  `get_grounding_judge_model()`.
- Threaded `judge_provider` through `chat.py` (both endpoints) into the
  `GroundingValidator`. When the override env vars are empty the helper
  returns the chat provider unchanged.
- Probed three candidates honestly:
  - **qwen3.6:27b** — pulled (17.4 GB) but cannot load on this dev host:
    Ollama: `model requires more system memory (16.1 GiB) than is
    available (11.4 GiB)`. Two VMware VMs (the test agent + the Wazuh
    server) plus Firefox / VS Code consume too much RAM. Deleted the
    model after the failed probe to free disk.
  - **qwen3.5:9b** — pulled (5.6 GB), probe score **0.50** — same JSON
    syntax regression the Qwen 3.5 family showed at 4B in ADR 0009.
    Confirms the 3.5 line on Ollama has a structured-output glue
    issue at every size; gated on the next upstream release.
  - **qwen3:8b** — already pulled, ADR 0010 measured 0.75 (same
    descriptor as qwen3:4b but more parameters; tight-fit at 85%
    GPU / 15% CPU). Realistic local upgrade for this hardware.
- Wrote **ADR 0013** capturing the env-var mechanism, the per-
  candidate findings, and the operator recommendations (qwen3:8b
  for this hw, qwen3.6:27b on workstation-class GPUs with 24+ GiB
  free RAM, hosted Nemotron 120B via OpenRouter for the strongest
  available judge).
- End-to-end retest with `GROUNDING_JUDGE_MODEL_ID=qwen3:8b`:
  - Question: "What SSH brute-force alerts have fired on
    `agent_name linux-test-agent` in the last 30 minutes? Look up
    rule 5712 and tell me what to do."
  - Strategy `guided`, 2 tool calls (`get_rule_definition` +
    `search_alerts`).
  - Verdicts: **supported=2, unsupported=2, unverifiable=1**.
  - **The stronger judge caught a real fabrication**: qwen3:4b
    emitted "Source IP: 192.168.1.100" and "Block the source IP
    (192.168.1.100)" — both wrong; the actual attacking IP was
    192.168.245.1 (the dev host running the brute-force loop).
    Both fabrications received `[unverified]` markers inline.
    This is the validator paying off as designed: it caught a
    confident hallucination the model would otherwise have shipped.

**Follow-up 2 — search_alerts agent_name lookup:**

- Added `agent_name: str | None` field to `SearchAlertsInput`.
- New helper `_resolve_agent_name_to_id()` queries the Server API's
  `/agents?name=` filter and returns the numeric id. When `agent_id`
  is empty and `agent_name` is provided, the tool resolves the name
  before calling the query builder.
- Tool descriptions tightened to clarify `agent_id` expects the
  numeric ID (e.g. `'001'`), not the human-readable name.
- Edge cases: explicit `agent_id` wins over `agent_name` (no
  unnecessary API call); unresolvable name runs an unfiltered query
  (validator catches the resulting under-grounding rather than
  raising); neither set means no agent filter.
- 4 new tests in `test_search_alerts_agent_name.py`.

**Follow-up 3 — wolf reembed CLI:**

- New `app/management/reembed.py`. Walks `knowledge_chunks` where
  `embedding_model != active_provider.model_id`, re-embeds in
  batches, updates only `embedding` + `embedding_model` (content
  + metadata untouched).
- Default mode is REPORT-ONLY; `--apply` required to write. Per-
  tenant scoping via `--tenant-slug` or `--tenant-slug __shared__`
  for the shared corpora. `--limit` for incremental migration.
- Idempotent: re-running after a clean pass finds zero mismatches.
- Smoke-tested in report mode on the live DB (0 mismatches — the
  full corpus was already embedded with the active provider).

**Follow-up 4 — frontend grounding integration:**

- `frontend/lib/types.ts`: `ChatResponseBody` and `ChatExchange`
  gain `grounding_supported / unsupported / unverifiable` fields
  (nullable; null when validator didn't run).
- `frontend/lib/types.ts`: `LoopEventType` adds `grounding.completed`
  (SSE event the backend already emits).
- `frontend/hooks/use-chat-stream.ts`: stores the three grounding
  counts on the completed exchange.
- `frontend/components/markdown.tsx`: new
  `highlightUnverifiedMarkers()` helper walks the rendered React
  tree, splits text nodes on the literal `[unverified]` token, and
  replaces each occurrence with a styled `<span>` (destructive-
  tinted background, warning icon, hover-tooltip). Applied to `p`,
  `li`, `td`, `th`, `blockquote` element renderers — every
  flowing-text location markdown supports.
- `frontend/components/message-thread.tsx`: new `GroundingBadge`
  rendered in the per-exchange metadata strip. Shows
  `grounding N✓ N✗ N?` with a destructive variant when
  unsupported > 0. Hover-tooltip explains what each count means.
- `npm run lint` clean.

### What we decided

- **Don't ship a default that doesn't work for the floor hardware.**
  qwen3.6:27b is the right judge for workstation GPUs but the
  development environment can't run it. The default stays "use the
  chat model" for backward compatibility; operators with capable
  hardware set the override.
- **Mark, don't fail-closed.** When the operator has wired a
  stronger judge AND it flags claims as unsupported, the answer
  reaches the analyst with `[unverified]` markers — never silently
  dropped. The frontend now makes those markers visible.
- **search_alerts unresolvable-name returns empty rather than
  raising.** The validator's "no alerts found" → unsupported claim
  detection catches the under-grounding without a Pydantic-error
  shape the model can't recover from.
- **Reembed defaults to report-only.** Re-embedding 5170 chunks
  takes ~2 minutes; the safety of "show me what would change
  first" outweighs the convenience of one-step apply. `--apply` is
  the explicit opt-in.

### What broke / what we discovered

- **Real RAM ceiling on this dev box.** The two VMware VMs (the
  Wazuh server at .128 and the test agent at .129) consume ~6 GiB
  combined, plus Firefox / VS Code overhead — only 8.1 GiB
  available. qwen3.6:27b at 16.1 GiB doesn't fit. ADR 0013 records
  this so the next operator on this exact setup knows.
- **The Qwen 3.5 family has a persistent OllamaAdapter glue
  problem.** Both qwen3.5:4b (ADR 0009) and qwen3.5:9b score 0.50
  with the same "Not valid JSON" parse error. Not a Wolf bug; the
  3.5 line's chat-template or tool-spec serialisation differs from
  3.x in a way the Ollama JSON path doesn't tolerate. Worth
  re-probing whenever Ollama releases a new qwen3.5 tag.
- **The stronger-judge demo is the most satisfying Phase 3 moment
  so far.** qwen3:4b confidently claimed source IP `192.168.1.100`
  and a "block this IP" instruction — both fabricated. qwen3:8b as
  the judge flagged both. The validator went from "graceful
  degradation when judge fails" to "actively saving the analyst
  from acting on a hallucinated IP."
- **VRAM contention during tests.** The factory test that loads
  BGE-base via sentence-transformers needs ~400 MB VRAM; when
  Ollama has a model loaded, OOM is possible. Easy mitigation:
  `ollama ps` + manual stop before running the full test suite.
  Logged but not codified.

### What's next

- **`wolf` install-script step** that prompts the operator for the
  judge-model preference at first run (qwen3:4b default,
  qwen3:8b recommended if RAM allows, qwen3.6:27b for workstation
  GPUs). Belongs in doc 16 / ADR 0007's install-script spec.
- **Heuristic+LLM hybrid validator** if rich-corpus operation
  shows the LLM judge failing too often. Not pressing.
- **Phase 4** — propose tools + the approval gateway. Phase 3 is
  now closed end-to-end (RAG, hybrid retrieval, grounding
  validator, real corpus, live demo, operator-tunable judge model).

---

## 2026-05-27 — Phase 3 Slice 3: real seed corpora + live end-to-end on new agent

**Session type:** claude-code (continuation)
**Phase:** Phase 3 — Slice 3 of 3 + full-stack live retest
**Duration:** ~60 min
**Branch / commit:** `main` — starting commit `e0e94f4`, this entry's
commit pending.

### What we did

- Operator provisioned a dedicated test agent at `192.168.245.129`
  (`linux-test-agent`, Wazuh agent id 001, status active) — confirmed
  via the Wazuh Server API's `/agents` endpoint. Reachable from the
  dev host; SSH on port 22 (OpenSSH 9.6 on Ubuntu 24.04).
- Built `tools/seed_knowledge/` — the production-grade ingesters:
  - `attack.py` — downloads MITRE/CTI's `enterprise-attack.json`
    (pinned to the master branch; cached under
    `.local/seed_knowledge_cache/`), parses the STIX bundle, filters
    to active `attack-pattern` objects (excludes `revoked` +
    `x_mitre_deprecated`), emits one ChunkInput per technique with
    metadata (`technique`, `title`, `attack_version`,
    `kill_chain_phases`, `is_subtechnique`, `parent_technique`).
    Content lead is the ATT&CK ID for clean FTS keyword hits.
  - `wazuh_rules.py` — downloads the Wazuh release archive
    (pinned to `v4.9.2`), iterates rule XML files under
    `ruleset/rules/`, wraps each file in a synthetic `<root>` before
    `ElementTree.fromstring()` (Wazuh files are top-level `<group>`
    elements — not strictly well-formed XML), emits one ChunkInput
    per `<rule>` with metadata (`rule_id`, `level`, `title`,
    `ruleset_file`, `groups`, `mitre`, `wazuh_version`).
  - `__main__.py` — driver CLI with `--source attack | wazuh_rules
    | all`, `--replace-shared` (deletes existing tenant_id-NULL
    chunks before re-ingesting), `--cache-dir`, `--limit`, and
    SHA-256-of-content idempotency (re-running without
    `--replace-shared` skips chunks already in the DB).
- Idempotency by design: tenant-private chunks (`tenant_id IS NOT
  NULL`) are never touched by the ingester. Operator-local
  customisation (e.g. the ACME SOC runbooks) survives a corpus
  refresh.
- Ran the full clean ingest: `--source all --replace-shared`.
  - Deleted 16 existing shared chunks (the dev-seed corpus from
    Slice 1 + the 5 ATT&CK chunks from the smoke test).
  - 697 ATT&CK techniques parsed from matrix v19.1, all 697 inserted.
  - 4473 Wazuh rules parsed from v4.9.2; 1 file with a
    well-formedness defect (`0910-ms-exchange-proxylogon_rules.xml`)
    logged and skipped (graceful degradation contract).
  - Total runtime 2 min 4 s on the RTX 4050 (embed bottleneck:
    nomic-embed-text via Ollama at ~30 ms/chunk).
  - Final DB state: **5170 shared chunks + 3 tenant-private chunks**.
- Confirmed retrieval quality on the rich corpus by direct store
  smoke-test (bypassing the chat endpoint):
  - "rule 5712 sshd brute force" → Rule 5712 chunk #1 (FTS exact-
    match), Rule 5763 #2, Rule 5714 #3.
  - "T1110 brute force" → Exim brute-force rule #1, T1110 #2,
    Proxmox brute-force rule #3 (interesting cross-source ranking;
    T1110 not #1 but in the top 3).
  - "attacker uses valid credentials to log into another host" →
    T1021.004 SSH #1, T1556 Modify Authentication Process #2, T1078
    Valid Accounts #3 — pure semantic retrieval, all three perfectly
    on-topic.
- 11 new parser tests in `tests/test_seed_knowledge_ingesters.py`:
  ATT&CK STIX parsing (techniques, subtechniques, deprecated filter,
  non-attack-pattern skip, missing-id skip, FTS ID-front content);
  Wazuh rule parsing (multi-rule extraction, content-starts-with-id,
  malformed-file graceful, missing-description skip, zip iteration).
  `make check` clean: **174 passed** (128 prior + 19 knowledge + 16
  validator + 11 ingester). Lint + mypy strict still clean.
- **End-to-end live demo on the new agent**:
  - Triggered 12 SSH brute-force attempts from this host against
    `attacker_user_1` through `attacker_user_12` on
    `192.168.245.129`. All failed (`Permission denied`); 3 dropped
    by SSH's pre-auth connection cap (`kex_exchange_identification:
    read: Connection reset by peer`).
  - Wazuh ingested 10 alerts on agent 001 within 15 s of the burst:
    9× rule 5710 (level 5, sshd non-existent user) + 1× rule 5712
    (level 10, sshd brute force composite). Pattern matches doc 06's
    canonical example and our seeded runbooks exactly.
  - First Wolf chat ("investigate SSH brute-force on
    linux-test-agent in the last 10 minutes") found 0 hits because
    qwen3:4b passed `agent_id="linux-test-agent"` (the name) instead
    of `"001"` (the numeric ID). The model concluded "no alerts
    were found"; **the grounding validator flagged that conclusion
    as `unsupported`** — exactly the right behaviour because
    "search returned 0 hits" is NOT evidence of absence. Final
    answer carried two `[unverified]` markers inline.
  - Second Wolf chat with the agent ID stated explicitly ran the
    full pipeline: 4 steps, 3 tool calls
    (`search_alerts` + `get_rule_definition` + `query_runbook`),
    answer drew on real ATT&CK T1110 content from the freshly-
    ingested STIX bundle (cited specific TrendMicro and Crashoverride
    references that are in MITRE's source corpus). The grounding
    validator's judge LLM returned malformed JSON on this prompt
    (large evidence section); the validator degraded gracefully,
    returned the original answer un-annotated, and surfaced
    `grounding_*` counts as `None`. Both behaviours are the
    documented contract.

### What we decided

- **`tools/seed_knowledge` is the canonical corpus channel.** The
  Slice-1 inline `seed_dev_knowledge.py` survives because it's
  useful for tests + fresh-machine bring-up before the network
  ingest runs, but the dev DB's authoritative material now comes
  from real MITRE + Wazuh sources.
- **Pin both sources, don't follow `master`.** ATT&CK gets bumped
  by changing `ATTACK_URL` (currently `master` for matrix v19.1)
  and clearing the cache; Wazuh ruleset gets bumped by changing
  `WAZUH_VERSION`. Re-embedding the entire corpus is the deliberate
  cost of a version bump — `--replace-shared` makes it explicit.
- **No prose Wazuh docs in this slice.** Scope discipline: XML
  rules + JSON ATT&CK give us realistic corpus volume (~5k chunks)
  without HTML-scraping edge cases. If operators want the user-
  manual prose later, a separate Slice 3.5 can add HTML scraping
  for selected pages.
- **The agent-name vs agent-ID confusion is a tool-side fix**, not
  a validator failure. Adding `agent_name` as a synonym in
  `search_alerts` (lookup against `list_agents`) is the right
  remediation; logged as a Phase-3-follow-up.

### What broke / what we discovered

- **Wazuh rule files aren't valid XML on their own.** They have a
  top-level `<group>` element (not `<rules>` or anything that
  declares itself a root). ElementTree refuses to parse them
  directly. Fix: wrap each file in a synthetic `<root>` before
  parsing. Documented in the ingester.
- **One ruleset file is genuinely malformed**
  (`0910-ms-exchange-proxylogon_rules.xml` at line 57 col 56).
  Parser logs a warning and skips; the other 4473 rules ingest
  cleanly. Likely an upstream Wazuh ruleset issue worth raising
  with them, but out of scope here.
- **ATT&CK STIX bundle structure**: matrix version is on the
  `x-mitre-collection` object, not the technique entries. Parser
  reads it once before iterating; bundle defaults `attack_version`
  to `"unknown"` if the schema changes.
- **The grounding validator catches false-negative claims too.**
  On the agent-name-vs-ID confusion run, the model concluded "no
  alerts were found" off a single 0-hit search — and the validator
  marked both that conclusion claim and the follow-on as
  `unsupported`. This is a Real Result: doc 06's validator design
  catches "we didn't find it so it's not there" reasoning, not just
  fabrication.
- **qwen3:4b's judge JSON is unreliable at high evidence-prompt
  volumes.** On the rich-corpus run the judge's response wasn't
  parseable; validator degraded gracefully. Pushes the stronger-
  judge follow-up (Nemotron via OpenRouter, prompt refinement,
  or heuristic+LLM hybrid) up the priority list.
- **5170 chunks is a real number, not a toy.** Hybrid retrieval +
  the HNSW vector index handle this volume without measurable
  latency change vs the 9-chunk seed. pgvector scales here.

### What's next

- **Stronger grounding judge** (now the top follow-up — Slice 2's
  architecture is sound; the model is the dial).
- **`search_alerts` agent-name lookup** — small, contained fix.
- **`wolf reembed` helper** queued from ADR 0012.
- **Frontend integration of grounding markers** — the chat UI
  doesn't render `[unverified]` or the validation counts specially
  yet.
- **Phase 4 entry** — propose tools + the approval gateway.
  Phase 3 closure ratifies the read-side foundation Phase 4 depends
  on.

---

## 2026-05-27 — Phase 3 Slice 2A + 2B: hybrid retrieval + grounding validator

**Session type:** claude-code (continuation)
**Phase:** Phase 3 — Slice 2 of 3 (both parts)
**Duration:** ~120 min
**Branch / commit:** `main` — starting commit `0daea82`, two commits
land in this session (8f0d544 for Part A, pending for Part B).

### What we did

**Part A — Hybrid retrieval (commit 8f0d544):**

- Migration 0005: added a `content_tsv tsvector` STORED generated
  column on `knowledge_chunks` populated via `to_tsvector('english',
  content)`. Existing rows auto-backfill on the ALTER. GIN index
  `ix_knowledge_chunks_content_tsv` enables fast `@@ tsquery` lookups.
- Declared the column on the SA model as
  `Computed("to_tsvector('english', content)", persisted=True)`
  with `TSVECTOR` type so the hybrid search query can reference it
  via the model. Wolf never writes to this column directly.
- `RetrievedChunk` gained an optional `rrf_score` field (None on
  pure-vector paths; populated on hybrid).
- Replaced `PgvectorKnowledgeStore.search()` with a hybrid
  implementation:
  - `_vector_candidates()` — top-25 by cosine distance via pgvector's
    HNSW index from migration 0004.
  - `_fts_candidates()` — top-25 by `ts_rank_cd`, gated on the `@@`
    predicate so chunks with zero token match are excluded.
  - Reciprocal Rank Fusion (Cormack et al. 2009, k=60): for each
    chunk present in either leg, `score = sum(1 / (60 + rank_in_leg))`.
    Chunks ranked highly in both legs win.
  - Tenant-scoping clause is preserved in both legs (defence in depth).
  - `source_types` + `metadata_filters` apply to both legs via shared
    `_apply_metadata_filters` helper.
- Smoke against the dev corpus showed the expected behaviour:
  - Query "rule 5712" → Rule 5712 chunk ranks #1 (FTS exact-token boost)
  - Conceptual queries → vector-driven ranking dominates
  - Mixed queries → both legs contribute
- 3 new tests in `tests/test_knowledge_store.py` (constants sane,
  `RetrievedChunk` carries `rrf_score`, fusion math correct — chunk
  present in both legs ranks above singletons).

**Part B — Grounding validator (this commit):**

- New `app/grounding/` module:
  - `GroundingValidator` class. LLM-as-judge: extract claims (sentence
    splitter that respects numbered-list markers by requiring a letter
    before the sentence-end punctuation), build evidence (concatenated
    tool results + retrieved chunks with `[TOOL_RESULT N: name]` and
    `[KNOWLEDGE N: source]` tags), one model call producing structured
    JSON verdicts (`supported` / `unsupported` / `unverifiable`),
    splice `[unverified]` inline on unsupported claims.
  - `ClaimVerdict` + `ValidationResult` dataclasses for the structured
    output.
  - Failure modes are non-blocking: judge raises, judge returns
    malformed JSON, codefence-wrapped JSON — all degrade gracefully
    to "validation skipped, original answer returned" per the
    operator's Slice 2 choice (mark-inline, not fail-closed-drop).
- `AgentAnswer` gained three optional fields:
  `grounding_supported / unsupported / unverifiable`. Stay `None`
  when the validator didn't run.
- `AgentLoop._finalize_answer()` helper runs the validator on the
  draft answer before either the `_emit("answer", ...)` event or
  the return. Skips when validator is `None`, answer is empty, or
  there are no citations (no evidence to validate against).
- Hooked at both `AgentAnswer` construction sites in the loop
  (stop_reason="answer" success path AND budget_exhausted path).
- Loop accumulates evidence across steps in two separate lists:
  `all_retrieved_chunks` (from `query_runbook.hits`) and
  `all_tool_results` (everything else) for better provenance in
  the judge's evidence prompt.
- `LoopEventType` gained `grounding.completed`; the SSE stream now
  surfaces validator verdicts to the frontend.
- New audit event type `grounding.validation.completed` records the
  per-loop counts and whether the validator ran.
- `chat.py` constructs the validator from the same `provider` used
  for the agent loop and threads it through `loop.run(...)`. The
  chat response body surfaces the three counts.
- 16 new tests in `tests/test_grounding_validator.py` covering claim
  splitting (simple + numbered lists + empty), evidence formatting,
  happy paths (all supported, mixed with unsupported, marker
  placement), and degradation (no citations, empty answer, judge
  raises, malformed JSON, codefence wrapping, claim-count clamping).
  Annotation logic exercised directly.
- `make check` clean: **162 passed** (146 prior + 16 new). lint +
  mypy strict still clean.

### End-to-end verification on the live Wazuh

1. **Pure RAG question** ("What is the Acme SOC runbook for SSH
   brute-force?"): 1 tool call (`query_runbook`), validator returned
   `supported=1, unsupported=0, unverifiable=1` — the procedural
   summary correctly labeled supported, the "Citations:" trailer
   labeled unverifiable. 93 s.
2. **Mixed-mode embellishment case** (the canonical test from
   Slice 1: "Look up rule 5712 definition + Acme runbook"):
   `get_rule_definition` + `query_runbook` in one loop, validator
   returned `supported=0, unsupported=0, unverifiable=7`. The
   pipeline ran correctly (7 claims extracted, judge called once,
   verdicts surfaced via the API) but qwen3:4b as the judge played
   safe and labeled every claim "unverifiable" instead of flagging
   the specific embellishment as "unsupported". 207 s total.

### What we decided

- **Validator architecture lands as planned, judge-model selection
  is the next dial to turn.** The embellishment-detection gap is
  not an architecture bug; it's a known limitation of LLM-as-judge
  with a 4B model judging its own output. Doc 06's grounding-validator
  design assumes a sufficiently strong judge; we'll evaluate
  alternatives (Nemotron via OpenRouter, prompt refinement, hybrid
  heuristic+LLM fallback) in a follow-up.
- **Mark-inline, not drop**, per the operator's earlier choice. This
  session honoured that posture across all paths: the analyst sees
  the suspect claim with a `[unverified]` marker, never silently
  dropped content. Failure modes (judge errors) also preserve the
  original answer rather than refusing to respond.
- **No tenant- or per-request validator override** for Slice 2. A
  `validator_mode` field on `ChatRequestBody` was offered in the
  Slice 2 planning question and not chosen; current code-level
  default-mark is sufficient. Operator can opt out by removing the
  validator construction in chat.py if needed (one line); a config
  toggle can be added later if multiple operators ask for it.
- **No grounding gate on `[unverified]` claim count**. The validator
  is informative; downstream Phase-4 propose/execute tools may want
  to refuse to propose actions if the answer that motivated them
  has unsupported claims, but that decision belongs in Phase 4 not
  Slice 2.

### What broke / what we discovered

- **Recursive validation is real.** qwen3:4b judging qwen3:4b's
  output is structurally suspect — a model that struggles with
  grounding discipline (ADR 0002) is not the best critic of its own
  grounding. The fact that the validator labeled every claim
  "unverifiable" on the hard case rather than picking a side is the
  model's risk-averse posture under uncertainty. Architecture is
  correct; judge model needs to improve. Logged as Slice 2's main
  follow-up.
- **The numbered-list splitter took two iterations.** First version
  treated `"1."` as a sentence end (matching `[.!?]\s+`), splitting
  `"1. Run list_agents."` into `["1.", "Run list_agents."]`.
  Required a letter before the sentence-end (`[a-zA-Z][.!?]`) to
  avoid digit-as-list-marker false positives. Second iteration
  forgot uppercase letters could appear before the period
  (`"IP."`); fixed with `[a-zA-Z]`. Both iterations caught by the
  unit test.
- **Markdown codefence wrapping is common.** Small models like
  `qwen3:4b` and `granite3.3:8b` sometimes wrap their JSON output in
  triple-backtick fences. The validator strips this before parsing.
  Tested in `test_validate_strips_json_codefence_wrapping`.
- **Async-correctness for sync deps.** The grounding validator does
  one `provider.chat()` call which is already async. No new
  `asyncio.to_thread` needed (unlike the sentence-transformers
  adapter in Slice 1.5).

### What's next

- **Slice 3** — real seed corpora (Wazuh docs + ATT&CK scrapers in
  `tools/seed_knowledge`).
- **Slice 2 follow-up** — evaluate stronger judges (Nemotron 120B
  via OpenRouter, or a heuristic-LLM hybrid) once Slice 3 produces
  enough verdict samples to measure precision/recall meaningfully.
- **`wolf reembed`** helper queued from ADR 0012 still pending.

---

## 2026-05-26 — Phase 3 Slice 1.5: sentence-transformers adapter + ADR 0012

**Session type:** claude-code (continuation)
**Phase:** Phase 3 — Slice 1.5 of 3
**Duration:** ~60 min
**Branch / commit:** `main` — starting commit `8cb3ab9`, final commit
pending this entry.

### What we did

- **Added an optional Python extra `embeddings-local`** in
  `services/orchestrator/pyproject.toml` carrying
  `sentence-transformers>=3.0` + `torch>=2.4`. Default `uv sync`
  is unchanged — the orchestrator's mandatory wheel set stays
  torch-free per ADR 0007's native-packaging constraints.
- **Built `SentenceTransformersEmbeddingAdapter`** in
  `app/knowledge/embeddings.py`. Lazy-imports `sentence_transformers`
  inside the constructor so the module still imports cleanly when
  the optional extra isn't installed (clear `ImportError` with
  install hint at construction time). Detects CUDA, falls back to
  CPU. Wraps `encode()` in `asyncio.to_thread` so it doesn't block
  the event loop. Applies the BGE asymmetric query prefix
  (`"Represent this sentence for searching relevant passages: "`)
  automatically when the model name contains "bge".
- **Added `make_embedding_provider(settings)` factory** that selects
  the adapter from `EMBEDDING_PROVIDER` (default `ollama`) and
  `EMBEDDING_MODEL` env vars. Accepts aliases
  (`sentence-transformers`, `sentence_transformers`, `st`).
- **Threaded the factory through** `services/orchestrator/app/api/chat.py`
  and `services/orchestrator/app/management/seed_dev_knowledge.py` so
  both code paths honour the env-driven selection. No call-site
  hardcodes the Ollama adapter anymore.
- **Wrote `tools/embedding_benchmark/`** — side-by-side benchmark CLI.
  Loads the same 9-chunk dev corpus the seed CLI uses (imports
  `SHARED_CHUNKS` + `runbook_chunks_for` directly so the comparison
  is reproducible). Measures cold-start, per-query latency (3
  trials × 10 queries, median), corpus-embed throughput, and
  qualitative top-5 retrieval for each adapter against the same
  query set. Optional `--json` for machine-readable output.
- **Ran the benchmark** on the RTX 4050 Laptop GPU:
  - Ollama (nomic-embed-text): cold-start 0.07 s (daemon warm),
    p50 30.7 ms, corpus 19 ms/chunk
  - sentence-transformers (BGE-base-en-v1.5): cold-start 10.12 s,
    p50 5.9 ms, corpus 8 ms/chunk
  - Retrieval precision was qualitatively better for BGE on
    entity-specific lookups (e.g. "What is T1078 Valid Accounts?"
    — BGE ranked T1078 #1; Ollama-nomic ranked T1110.001 #1).
    On ambiguous procedural queries both ranked comparably.
    Sample size small; trend suggestive.
- **Wrote ADR 0012** —
  `docs/decisions/0012-embedding-stack-ollama-vs-sentence-transformers.md`.
  Decision: **keep both adapters; default Ollama** (preserves
  ADR 0007's packaging story, matches LLM Ollama pattern, fast
  steady-state startup); **sentence-transformers as opt-in extra**
  for operators with high-throughput ingestion or precision needs.
  Records the empirical numbers verbatim, lays out the
  variable-confound trade explicitly (the chosen comparison
  mixes runtime + model; isolation would have needed same-model
  on both runtimes — the operator chose the cross-stack comparison
  for actionability over rigour).
- **Added 3 new tests** in `tests/test_knowledge_store.py` covering
  the factory contract (default routes to Ollama; unknown provider
  rejected; sentence-transformers aliases accepted). 12 prior
  Slice 1 tests still pass.
- **`make check` clean: 143 passed** (128 prior + 12 Slice 1 + 3
  Slice 1.5). Lint + mypy strict still clean. Benchmark CLI gets
  a file-level `# ruff: noqa: T201, E402` for its intentional CLI
  prints + path-bootstrap import order.
- **Updated `docs/decisions/README.md`** index with ADR 0012.

### What we decided

- **Both adapters are kept, behind the same `EmbeddingProvider`
  Protocol.** Operator switches via `EMBEDDING_PROVIDER` env. The
  protocol absorbs the choice; no other code needs to change.
- **Ollama stays the default** for new installs. The ADR 0007
  packaging argument is load-bearing — torch+transformers add
  ~2 GB to the orchestrator install, which materially hurts the
  `.deb` / `.rpm` channel's appeal. The retrieval-precision edge
  for BGE on micro-benchmark wasn't large enough to overturn this.
- **sentence-transformers is the recommended choice for bulk
  re-embedding** (Slice 3's Wazuh-docs / ATT&CK ingest will run
  thousands of embed calls at once — the 2.4× corpus-throughput
  win matters there). Operator can `EMBEDDING_PROVIDER=
  sentence-transformers` for the duration of the migration,
  then flip back.
- **The benchmark CLI is permanent**, not throwaway. Future
  hardware changes / model swaps can re-run it.
- **No re-embedding helper in this slice.** Flipping
  `EMBEDDING_PROVIDER` without re-embedding the existing corpus
  will silently degrade retrieval (query vectors from BGE searched
  against nomic vectors). A `wolf reembed` CLI is queued as a
  Slice 2 / Slice 3 follow-up; documented as a known gap in ADR
  0012.

### What broke / what we discovered

- **nomic-embed-text via Ollama is NOT L2-normalized.** Raw dot
  products in the benchmark reached +280-290. pgvector's
  `vector_cosine_ops` normalizes internally so retrieval RANKING
  is unaffected, but if anyone ever rewrites Wolf's similarity
  code to use raw dot product, the two adapters would behave very
  differently. Logged in ADR 0012 §"Vector geometry."
- **First-run cold-start asymmetry is misleading.** Ollama's
  reported 0.07 s reflects an already-warm daemon (the model had
  been loaded by Slice 1's seed run earlier in the session). A
  truly cold Ollama would also pay a load cost similar to ST's
  ~10 s. The ADR records this honestly rather than pretending
  Ollama has a structural cold-start advantage.
- **BGE asymmetric retrieval matters.** The first benchmark
  iteration embedded queries WITHOUT the BGE query prefix and
  retrieval quality was visibly worse. Adding the
  `embed_query()` method with the proper prefix lifted the top-1
  precision on entity-specific queries from "comparable to
  nomic" to "noticeably better than nomic." Implementation
  detail documented in the adapter docstring; the benchmark uses
  `embed_query()` when available so future adapters can benefit.

### What's next

- **Phase 3 Slice 2** — hybrid retrieval (BM25 + vector fusion)
  + grounding validator.
- **Phase 3 Slice 3** — real Wazuh-docs / ATT&CK scrapers in
  `tools/seed_knowledge`, plus the `wolf reembed` helper.
- **Validate retrieval precision delta on real corpus.** The
  10-query / 9-chunk micro-benchmark is suggestive. Slice 3's
  thousand-chunk corpus is the right scale to formalize the
  precision claim.

---

## 2026-05-26 — Detour: close Slice 1 end-to-end (Wazuh Server API auth)

**Session type:** claude-code (continuation)
**Phase:** Phase 3 — closure of Slice 1's deferred end-to-end
**Duration:** ~20 min
**Branch / commit:** `main` — starting commit `158b008`, this entry's
commit pending.

### What we did

- **Diagnosed the Server API 401** flagged at Slice 1 close: Wazuh's
  Indexer (OpenSearch security plugin) and Server API (its own RBAC
  database at `/var/ossec/api/configuration/security/rbac.db`)
  maintain **separate user backends**. The `wolf` user (and later
  `admin`) existed only in the Indexer. Direct curl against the Server
  API `/security/user/authenticate` returned `"Invalid credentials"`
  for both. Pure operator-side configuration gap; no Wolf code path
  involved.
- **Operator supplied the Server API admin credentials**
  (`wazuh-wui` / generated). curl confirmed JWT issuance + `/agents`
  + `/rules?rule_ids=5712` all return real data.
- **Re-ran `bootstrap_tenant --tenant-slug acme`** with per-endpoint
  credentials (`admin` for Indexer, `wazuh-wui` for Server API). Idem-
  potent — overwrote the secrets in place; tenant + user bindings
  preserved.
- **Closed the Slice 1 end-to-end gap** with two verifications via
  `/api/v1/chat`:
  - **Pure RAG**: "What is the Acme SOC runbook for SSH brute-force?"
    → strategy `guided`, 2 steps, 1 tool call (`query_runbook`),
    citation present, answer faithfully reproduces all 5 runbook steps
    from the seeded ACME chunk. 60s on the RTX 4050.
  - **Mixed RAG + Server API**: "Look up the actual definition of
    Wazuh rule 5712, then tell me what Acme SOC runbook says…"
    → 2 tool calls (`get_rule_definition` + `query_runbook`), both
    citations attached, 71s. Confirms the same loop can fuse live
    state with retrieved knowledge per doc 06 §"How 'complete
    knowledge' actually gets delivered."

### What we decided

- **No Wolf code changes** — the Slice 1 implementation is unchanged
  by this detour. The failure was operator-side credentials only.
- **Keep the per-endpoint credential pattern** in the dev tenant
  (Indexer admin + Server API admin can be different users). Already
  supported by `bootstrap_tenant` — `--opensearch-username` and
  `--server-api-username` are independent flags.
- **Acknowledge the synthesis-fidelity hiccup** seen in the mixed-mode
  answer: the model wove a fragment of the rule's `ignore=60s`
  parameter into the runbook section ("Block the source IP for 60
  seconds (per `ignore` parameter)") that is NOT in the seeded
  runbook chunk. Retrieval is correct (both citations present);
  synthesis embellishes. This is exactly the grounding-discipline
  failure mode ADRs 0002 / 0010 / 0011 documented for the qwen
  family, and exactly what Phase 3 Slice 2's grounding validator is
  designed to catch. The fabrication evidence reinforces the
  validator's design rationale.

### What broke / what we discovered

- **Wazuh's Indexer/Server-API user-store split** is a real
  deployment gotcha worth surfacing in ONBOARDING. The
  `credentials/wazuh-credentials.txt` template originally listed one
  user as covering both; operators should be told explicitly that
  these are two separate credentials. Logged as a follow-up doc fix.
- **qwen3:4b's synthesis embellishment** when mixing two tool results
  (rule definition + runbook) is observable now that both paths
  work. Quantifying this on a small benchmark set would be a useful
  Slice 2 input for the grounding-validator's reject threshold.

### What's next

- **Phase 3 Slice 1.5** — sentence-transformers `EmbeddingProvider`
  adapter + comparison ADR.
- **Phase 3 Slice 2** — hybrid retrieval + grounding validator
  (motivating evidence from this session's synthesis embellishment).
- **ONBOARDING doc fix** — explicit note that Wazuh Indexer and
  Server API have separate user databases; the operator may need
  two different credentials.

---

## 2026-05-24 — Phase 3 Slice 1: vertical RAG skeleton

**Session type:** claude-code (same session as Granite probe / new-machine handoff)
**Phase:** Phase 3 — Knowledge & RAG (Slice 1 of 3)
**Duration:** ~75 min
**Branch / commit:** `main` — starting commit `f977a83`, final commit
pending this entry.

### What we did

- **Designed Phase 3 as three slices** (vertical skeleton → second
  embedding adapter + comparison → real scrapers + hybrid retrieval +
  grounding validator) to land the architecture-proving path first
  before scaling content or adding ranker complexity.
- **Added `pgvector>=0.3`** to `services/orchestrator/pyproject.toml`
  for the SQLAlchemy `Vector` column type.
- **Pulled `nomic-embed-text`** via Ollama (768-dim, 274 MB, ~1 s warm
  embed on the RTX 4050). Symmetric with the existing Ollama LLM
  pattern — no torch / sentence-transformers wheels added to the
  orchestrator's install set (per ADR 0007 packaging constraints).
- **New `services/orchestrator/app/knowledge/` module:**
  - `models.py` — `KnowledgeChunk` SA model with `Vector(768)`
    embedding + `JSONB` chunk_metadata + `embedding_model` stamp for
    the doc-06 re-embedding trigger. `EMBEDDING_DIMENSION = 768`
    locked into the schema.
  - `embeddings.py` — `EmbeddingProvider` Protocol +
    `OllamaEmbeddingAdapter` (sequential per-text calls to
    `/api/embeddings`; fine at Slice-1 scale, batching deferred).
  - `store.py` — `KnowledgeStore` Protocol +
    `PgvectorKnowledgeStore`. Tenant-scoping enforced at the SQL
    clause: `WHERE tenant_id IS NULL OR tenant_id = $req_tenant`.
    `SHARED_SOURCE_TYPES` / `TENANT_SOURCE_TYPES` validation at
    upsert: shared corpora forbid a tenant_id; private corpora
    require one.
- **Alembic migration 0004** — `knowledge_chunks` table + composite
  `(tenant_id, source_type)` btree index + HNSW
  `vector_cosine_ops` index on `embedding`. `CREATE EXTENSION IF NOT
  EXISTS vector` is idempotent for fresh databases. Applied cleanly
  against the dev DB.
- **`query_runbook` tool** (`app/tools/knowledge.py`) — read-tier,
  metadata filters as first-class Pydantic args per doc 06
  (`source_types`, `rule_id`, `technique`, `limit`). Raises a clear
  `RuntimeError` if `ToolExecContext.knowledge_store` is unset
  rather than failing silently. Registered as the 10th read tool.
- **Plumbed knowledge_store** through `ToolExecContext` (new optional
  field, typed `Any` to avoid an import cycle) → `dispatch_tool_call`
  (new kw param) → `AgentLoop.run` (new kw param) → both the JSON and
  SSE chat endpoints in `app/api/chat.py` (build adapter + store from
  per-request DB session + Ollama base URL).
- **`seed_dev_knowledge` management CLI** — loads the Slice-1 inline
  corpus: 6 shared chunks (Wazuh rules 5710/5712 + active-response;
  ATT&CK T1110 / T1110.001 / T1078) and 3 tenant-private chunks per
  tenant (SSH brute-force runbook, T1110 triage runbook, past
  incident write-up). Fails loud if `DATABASE_URL` is unset (matches
  the lesson learned from ONBOARDING §3.7 alembic drift earlier this
  session). JSON output to stdout for scripting; errors to stderr.
- **Ran the migration + seed against the dev DB.** Confirmed table
  schema and indexes (HNSW + composite btree); seeded 9 chunks for
  tenant `acme` (6 shared with `tenant_id=NULL` + 3 private with
  `tenant_id=acme.id`).
- **12 new pytest tests** in `tests/test_knowledge_store.py`:
  validation rules on `ChunkInput` (shared corpora must have null
  tenant_id; private corpora require one; unknown source_type
  rejected; empty content rejected), `QueryRunbookInput` constraints
  (non-empty query; 1..20 limit clamp; minimal-args default), tool
  surface (raises when store not configured; passes filters through
  to the store correctly).
- **Conftest fix** — under SQLite (the local-dev default), skip the
  `knowledge_chunks` table during `Base.metadata.create_all` because
  `pgvector.Vector` + `JSONB` don't render on SQLite. Phase-3 paths
  are Postgres-only by design; tests stub the store.
- **`make check` clean: 140 passed** (128 prior + 12 new). lint +
  mypy strict still clean.
- **Direct RAG verification** — bypassed the chat endpoint and
  exercised the store directly: query
  *"how does Acme respond to SSH brute-force?"* returned 5 hits with
  cosine distances 0.317–0.415, top hit being the shared ATT&CK
  T1110 chunk, followed by the ACME SOC private runbook chunk. The
  SQL log shows the expected `WHERE tenant_id IS NULL OR tenant_id =
  $acme.id ORDER BY distance LIMIT 5` clause — tenant scoping
  enforced at the query layer.

### What we decided

- **Three-slice Phase 3 plan, not one big landing.** Slice 1 ships
  the vertical (proven). Slice 1.5 adds sentence-transformers as a
  second `EmbeddingProvider` adapter and writes a decision ADR on
  keep-both vs pick-one (per operator's explicit request).
  Slice 2 brings hybrid retrieval + grounding validator. Slice 3
  ships the real Wazuh-docs / ATT&CK scrapers in
  `tools/seed_knowledge`.
- **Ollama-hosted embedding (nomic-embed-text) as Slice 1's primary**
  — keeps the orchestrator wheel set lean for ADR 0007 native
  packaging, symmetric with the LLM Ollama pattern, model lifecycle
  managed by Ollama. Sentence-transformers adapter to land in Slice
  1.5 with a head-to-head benchmark.
- **HNSW for the embedding index** — pgvector's modern default,
  incremental inserts, log-ish query time. IVFFlat reachable later
  via a one-statement index swap if MSSP-scale memory pressure
  appears.
- **Inline 9-chunk seed for Slice 1, not a real scrape.** Smallest
  artifact that proves the vertical; real scrapers come in Slice 3.
- **Tenant scoping enforced inside the store**, not at the tool
  layer. The dispatcher's `sanitize_tenant_id_from_args` already
  strips any model-supplied tenant_id; the store's SQL clause is
  the load-bearing second line of defense per doc 05.
- **The chat-endpoint end-to-end test was blocked by a separate
  Wazuh Server API 401** (the `wolf` user works for the Indexer but
  apparently not the Server API in this deployment) — the model
  routed the test question to `get_rule_definition` rather than
  `query_runbook`. Decided NOT to fix that in Slice 1 because it's
  an operator-side credentials issue, not a Slice-1 scope item.
  The direct-RAG verification stands in as the Slice-1 closure
  signal.

### What broke / what we discovered

- **The conftest's SQLite path needed a knowledge_chunks skip.**
  `Base.metadata.create_all` under SQLite blew up on
  `pgvector.Vector` + `postgresql.JSONB` — both Postgres-only. Fixed
  by filtering the create_all tables list. Phase-3 tests that need a
  real Postgres roundtrip will get a separate fixture in Slice 1.5
  or 2.
- **qwen3:4b's tool-routing pick on a knowledge question.** Asked
  *"What does Wazuh rule 5712 do?"* — the model chose
  `get_rule_definition` (Wazuh Server API) over `query_runbook`
  (RAG), which is arguably correct (live rule definition is more
  authoritative than docs) but blocked the end-to-end test on the
  Server API 401. Worth noting: the agent loop's strategy doesn't
  currently bias toward RAG for product-knowledge questions. The
  Slice-2 grounding validator + prompt-shaping work is where this
  routing bias can be tuned.
- **nomic-embed-text returns vectors with a startling distribution**
  — values like `-3.91` in the first dimension. Not normalized to
  unit length out-of-box. Cosine distance still works (pgvector
  normalizes internally for `vector_cosine_ops`), but worth noting
  if we ever swap to a raw-dot-product comparison.

### What's next

- **Phase 3 Slice 1.5** — sentence-transformers `EmbeddingProvider`
  adapter + head-to-head benchmark + decision ADR.
- **Phase 3 Slice 2** — hybrid retrieval (BM25 + vector fusion) +
  grounding validator.
- **Investigate the Wazuh Server API 401** (operator-side
  credentials gap surfaced during Slice 1 end-to-end).
- **Doc-drift fixes accumulated from this session** still pending:
  ONBOARDING §3.7 alembic env-load, §11 `GET /me` route nit, test
  suite + Postgres asyncpg loop-scope issue.

---

## 2026-05-24 — Opportunistic probe: IBM Granite 3.3 8B (ADR 0011)

**Session type:** claude-code (same session as the new-machine handoff entry below)
**Phase:** Phase 2 closed; pre-Phase-3 setup
**Duration:** ~20 min
**Branch / commit:** `main` — starting commit `600740d`, final commit
pending this entry.

### What we did

- Operator asked which fully-free open-source agentic models were
  realistic challengers to qwen3:4b on the new GPU hardware, with
  the license filter relaxed. Triage surfaced IBM Granite 3.3 8B as
  the most interesting candidate (Apache 2.0, marketed by IBM for
  agentic tool use, dedicated tools-trained variant in the family).
- **Pulled `granite3.3:8b`** (~4.9 GB on disk). Loads at PROCESSOR=
  **88% GPU / 12% CPU** at default 4096 ctx — slightly less CPU
  spillover than qwen3:8b's 85%/15% but the same tight-fit class.
  VRAM 5053 MB of 6141 MB.
- **Ran the probe** — score **0.25**. PASS `tool_call_formatting`
  (IBM's agentic positioning works at the format level); FAIL the
  other three: `json_schema_adherence` (response shape mismatch),
  `multi_step_reasoning` (invalid JSON — same failure shape as
  qwen3.5:4b in ADR 0009), and `grounding_discipline` (fabrication —
  same weakness as qwen3:4b/qwen3:8b). Measured descriptor:
  `basic` / `full` / `unreliable` / 3 / `pipeline`.
- **License-verified Apache 2.0** via the Ollama page
  (https://ollama.com/library/granite3.3).
- **Wrote ADR 0011** marking the probe explicitly opportunistic per
  ADR 0006's "wider matrix" alternatives section. KNOWN_MODELS entry
  added with an inline comment flagging it as **opportunistic
  registration** — *not* part of the four-family supported matrix.
  Operators selecting it via env override get documented pipeline
  behavior.
- Updated `docs/decisions/README.md` index. `docs/15-supported-model-matrix.md`
  is **unchanged** — Granite stays out of the bounded matrix
  deliberately, preserving ADR 0006's narrow commitment.

### What we decided

- **Granite 3.3 8B is NOT a default-flip candidate.** Despite being
  2× qwen3:4b's parameter count and IBM's explicit agentic
  positioning, it regresses on three of four probe tasks on this
  hardware. `DEFAULT_MODEL_ID` stays `qwen3:4b`.
- **Granite stays in `KNOWN_MODELS` as opportunistic registration**
  (ADR 0005/Nemotron precedent) — the registry documents what Wolf
  knows about, not what it recommends. Operators get an honest
  measurement to base their own choice on.
- **No expansion of the four-family matrix in doc 15.** ADR 0006's
  narrowness is deliberate; adding a fifth family on one probe
  result would erode the design.
- **A future agent-loop smoke test of Granite under `guided`
  strategy is the right follow-up** if/when the "marketing says
  agents, probe says pipeline" question becomes load-bearing.
  Granite's `native_tool_calling: full` is real and Wolf's typed
  dispatcher might let it perform better at runtime than the
  static descriptor predicts. Deferred; not in scope for this
  drop-in probe.

### What broke / what we discovered

- **"Purpose-built for agents" doesn't automatically equal
  Wolf-loop fit.** Granite's tool-call format is correct (its
  agentic claim is real at the protocol level), but Wolf's
  structured-output fallback expects a specific `answer` /
  `tool` envelope shape that Granite doesn't reliably produce.
  Useful data point for evaluating any future vendor claim of
  "agentic" — the probe is the truth, not the marketing.
- **Same fabrication weakness as Qwen family** on the no-tools
  grounding-discipline test. Phase 3's grounding validator is the
  cross-model mitigation; this probe is the second independent
  confirmation that the validator is the right design.

### What's next

- Phase 3 (RAG + grounding validator) per `docs/06` and `docs/10` —
  unchanged from the prior session entry. Granite probe complete;
  no further model exploration needed before Phase 3.

---

## 2026-05-24 — New-machine handoff: GPU dev laptop, qwen3:8b + qwen3.5:4b probes

**Session type:** claude-code (new conversation, **new dev machine** — RTX 4050 Laptop GPU)
**Phase:** Phase 2 closed; pre-Phase-3 setup completed
**Duration:** ~75 min
**Branch / commit:** `main` — starting commit `a890a5b`, final session commit
pending this entry.

### What we did

- **Resumed from a clean clone** on the new GPU-equipped laptop following
  `prompts/HANDOFF-NEW-MACHINE.md`. Operator had pre-staged Python 3.13.13,
  uv 0.11.16, Node 24.16.0, Ollama 0.24.0 (with qwen3:4b, qwen3.5:4b,
  gemma3:4b, llama3.2:3b already pulled), Docker 29.5.2, and system
  Postgres 17.10 + pgvector. NVIDIA RTX 4050 Laptop GPU detected (6 GB
  VRAM, driver 595.71.05, CUDA 13.2).
- **Found `credentials/` drop** at repo root containing real Wazuh
  credentials (user `wolf`, password, indexer URL `https://192.168.245.128:9200`,
  Server API URL `https://192.168.245.128:55000`) plus the local Postgres
  password. Was untracked but **not gitignored**; added `credentials/`
  to `.gitignore` immediately to prevent accidental commit.
- **Setup from clean clone** per ONBOARDING.md §3: `uv sync --all-packages`,
  `npm install` in frontend, generated `SECRET_KEY` + `SECRETS_FILE_KEY`,
  wrote `.env` (mode 0600) with `DEFAULT_MODEL_ID=qwen3:4b`, ran
  `alembic upgrade head` (3 migrations clean), bootstrapped tenant `acme`
  with the real Wazuh URLs.
- **Verified end-to-end against real Wazuh** at `192.168.245.128`:
  curl-driven login → chat → tool call (`count_alerts_by_severity`) →
  grounded answer ("325 alerts in 24h, 143 medium + 182 low") in **20.8s**
  (vs ~76s cold on the previous CPU-only VM — clean GPU win). Strategy:
  `guided`. Model: `qwen3:4b`.
- **`make check`: 128 passed, lint + mypy strict clean.** Same baseline
  as the previous VM, on the new hardware.
- **Confirmed Ollama GPU offload** via `ollama ps` for all four pre-pulled
  models: qwen3:4b (3.5 GB, 100% GPU), qwen3.5:4b (5.9 GB, 100% GPU —
  surprisingly large for a 4B; the 256K-ctx capability inflates KV cache
  reservation), gemma3:4b (4.3 GB, 100% GPU), llama3.2:3b (2.8 GB, 100% GPU).
- **Pulled qwen3:8b** (~5.2 GB on disk). Loads at PROCESSOR=**85% GPU /
  15% CPU** at default 4096 ctx — the brief's "tight fit" prediction
  was exactly right. VRAM use 4985 MB of 6141 MB.
- **Ran three model probes** via `uv run python -m tools.model_probe`:
  - **qwen3:4b GPU re-probe** — score 0.75, descriptor identical to
    ADR 0002's CPU measurement. Confirms the probe is hardware-agnostic
    at the capability tier; provides the baseline for the qwen3.5:4b
    cross-comparison.
  - **qwen3.5:4b GPU probe** — score **0.50** (regression). FAIL on
    `tool_call_formatting` and `json_schema_adherence` (model emitted
    invalid JSON across all 3 structured-output retry attempts); PASS
    on `multi_step_reasoning` and `grounding_discipline`. Measured
    descriptor: `basic` / `none` / `unreliable` / 4 / `pipeline`.
  - **qwen3:8b GPU probe** — score 0.75. Identical descriptor to
    qwen3:4b at the static fields (`mid` / `full` / `schema_enforced` /
    8 / `guided`). Two amendments to the existing `KNOWN_MODELS`
    estimate: `structured_output` upgraded `prompt_coaxed` →
    `schema_enforced`; `max_safe_autonomous_steps` tightened 10 → 8.
- **License-verified qwen3.5:4b as Apache 2.0** via Qwen 3.5 release
  notes (open-weight tiers 0.8B–397B-A17B). Ollama page didn't state
  it directly. Cleared the ADR 0006 prerequisite for `license_class`
  in the `KNOWN_MODELS` entry.
- **Wrote two ADRs and amended `KNOWN_MODELS`:**
  - ADR 0009 — qwen3.5:4b GPU probe + cross-comparison vs qwen3:4b.
    Records the regression honestly; decides NOT to flip
    `DEFAULT_MODEL_ID` (handoff brief's condition: only flip if 3.5
    matches/beats 3; it does not). Adds `KNOWN_MODELS["qwen3.5:4b"]`
    with the measured `basic`/`pipeline`/Apache-2.0 descriptor.
  - ADR 0010 — qwen3:8b GPU probe (tight VRAM fit, 85% GPU /
    15% CPU). Records same-descriptor result as qwen3:4b; decides
    NOT to flip default (no measured-capability win + worse latency
    under VRAM pressure). Amends `KNOWN_MODELS["qwen3:8b"]` to match
    probe (structured_output upgrade, max_steps tighten).
- Updated `docs/decisions/README.md` index (rows for 0009 and 0010),
  `docs/15-supported-model-matrix.md` Implementation-status table
  (Qwen 3 8B status flipped, Qwen 3.5 row added, qwen3.5:4b re-probe
  added as gap #4), and `docs/PROGRESS.md` §3 (dev environment now
  GPU-equipped) + §4 (next steps) + §8 (ADR count 8 → 10).
- Re-ran `make check` after the `KNOWN_MODELS` edits: **128 passed**,
  lint + mypy strict still clean.

### What we decided

- **qwen3.5:4b is supported but not recommended** (ADR 0009). Stays in
  `KNOWN_MODELS` per ADR 0006's family-commitment principle; operators
  who select it via env override get documented `pipeline` behavior.
  No default flip. License verified Apache 2.0.
- **qwen3:8b is officially supported on Profile B tight-end** (ADR 0010).
  Operators with more VRAM (12+ GB) may prefer it; on this 6 GB GPU
  it offers no measured capability win and has CPU-spillover latency
  cost, so `qwen3:4b` remains the dev default.
- **`DEFAULT_MODEL_ID` stays `qwen3:4b`.** Both new probes failed to
  produce a default-flip candidate; the ADR 0004 pattern is not
  triggered.
- **Skipped optional probes:** gemma3:4b GPU re-probe (already CPU-probed
  ADR 0003; capability descriptor would not change), llama3.2:3b GPU
  re-probe (same reasoning — ADR 0001 capability is hardware-agnostic).
  Per handoff brief these were explicitly optional.

### What broke / what we discovered

- **`uv run alembic upgrade head` fails without sourcing `.env` first.**
  configparser's `BasicInterpolation` can't resolve `%(DATABASE_URL)s`
  unless the variable is in the process env at alembic-config-load time
  (before `env.py` runs its `set_main_option` override). ONBOARDING.md
  §3.7 doesn't mention this. Worth a doc fix: prepend
  `set -a && source ../../.env && set +a &&` to the alembic command,
  or move the env load earlier in §3.6.
- **`GET /me` 404s.** ONBOARDING.md §11 references `GET /me` for the
  authenticated user lookup; that route doesn't exist (tried both
  `/me` and `/api/v1/me`). The `/api/v1/auth/login` POST works and
  returns the user payload, so it's not blocking — but the doc claim
  is wrong. Worth a quick grep + correction in the next doc sweep.
- **Test suite + Postgres has a latent event-loop scoping bug.**
  Running `make check` with `DATABASE_URL` exported to system Postgres
  (which is what my initial `set -a && source .env && set +a` did)
  triggered 32 pytest errors with `RuntimeError: ... attached to a
  different loop` in asyncpg. The conftest defaults to SQLite for
  local dev — passing 128/128 without `DATABASE_URL` set. CI presumably
  handles Postgres correctly somehow; worth understanding before
  Phase 3 adds pgvector tests that may need the real DB locally.
- **qwen3.5:4b's `native_tool_calling = none` failure mode is
  structurally different from gemma3:4b's.** Gemma earns `none` because
  Ollama returns HTTP 400 on any `tools=[...]` request — model is
  structurally untrained. qwen3.5:4b: Ollama accepts the request and
  the model returns invalid JSON. Smells like a chat-template/glue
  issue in Ollama's qwen3.5 release, not necessarily a model limit.
  ADR 0009 records the descriptor at face value but flags a re-probe
  as the right follow-up.
- **qwen3.5:4b VRAM (5.9 GB at 4096 ctx)** is dramatically larger than
  qwen3:4b's (3.5 GB) despite similar disk size — the 256K-ctx
  capability inflates KV-cache reservation up-front. On this 6 GB
  GPU it fits at default but won't tolerate much context increase.

### What's next

- **Phase 3 (RAG + grounding validator)** per `docs/06` and `docs/10`
  Phase 3 block. Now unblocked — no probes left on this hardware.
  Grounding validator is the designed mitigation for both ADR 0002's
  qwen3:4b grounding fail and ADR 0010's qwen3:8b grounding fail.
- **Doc sweep** for the three drift points discovered (ONBOARDING §3.7
  alembic env load, §11 `GET /me` 404, test-suite Postgres scoping).
  Small; can fold into the start of the next session.
- **Optional follow-up:** qwen3.5:4b re-probe after the next Ollama
  qwen3.5 release.
- **Still blocked on workstation-GPU hardware (24+ GB VRAM):** GLM 5.1
  ~32B probe, Gemma 3 12B/27B probes, Qwen 3 14B/32B probes. Per ADR
  0006 these remain expected probe ADRs but are not blocking Phase 3.

---

## 2026-05-23 — Supported-model commitment (ADR 0006 + doc 15) + ONBOARDING.md

**Session type:** claude-code (new conversation, same dev environment)
**Phase:** Phase 2 closed; pre-Phase-3 setup
**Duration:** ~90 min (discussion + writing)
**Branch / commit:** `main` — uncommitted at time of this entry; will
be committed as part of the same set of commits that adds this entry.

### What we did

- Walked the project owner through the locally-hostable agentic-LLM
  landscape across four hardware tiers, with targeted side-discussions
  of GLM 5.1 and Kimi K2 (the latter ruled out for the matrix because
  even sparse it does not fit any non-multi-GPU local profile).
- Captured the owner's product direction as a load-bearing commitment:
  Wolf must natively support **four** model families locally in
  development — Qwen 3 (4B/8B/14B/32B), Llama 3 (3.x/4 line), Gemma 3
  (4B/12B/27B), GLM 5.1 ~32B dense.  Production posture is user-choice
  (operators pick one or multiple, including hosted APIs).
- Created `docs/15-supported-model-matrix.md` — the living directive
  doc.  Defines the four families with sizes and licenses, the
  six-item "natively support" checklist (KNOWN_MODELS entry + live
  probe + ADR + agent-loop test + strategy assignment + smoke
  coverage + doc 14 entry), the dev quality bar (efficient / robust /
  stable / reliable) with specifics, the production user-choice
  posture, and the current implementation gaps ordered by priority.
- Created `docs/decisions/0006-supported-model-families-commitment.md`
  — the point-in-time ADR with full reasoning, five alternatives
  considered (single-default rejected, wider matrix rejected, drop
  Llama rejected, include Kimi K2 rejected, defer-until-Phase-3
  rejected), and six consequences including the four expected probe
  ADRs.
- Updated `docs/decisions/README.md` index table with ADR 0006 row.
- Added auto-memory entry `supported_model_matrix.md` + one-line
  pointer in `~/.claude/.../memory/MEMORY.md` so future Claude Code
  sessions on other machines pick up the commitment without needing
  to find doc 15 first.
- Wrote `ONBOARDING.md` at repo root — 11-section comprehensive
  onboarding doc for a new contributor (human or AI) on a different
  machine: 60-second orientation, mandatory reading order with three
  tiers, system requirements, first-time setup from a clean clone
  (12 numbered steps), verification (tests / lint / smoke / probe),
  common operational tasks, seven real gotchas with fixes, the
  session-continuity protocol, file-location reference table, and a
  troubleshooting matrix.
- Updated this CHANGELOG and `docs/PROGRESS.md` accordingly.

### What we decided

- Four-family native-support commitment (ADR 0006).  Llama stays in
  the matrix even though it's not Wolf's *recommended* default per
  doc 14 — "supported" and "recommended" are distinct concerns.
- GLM 5.1 anchored at ~32B (dense) rather than the smaller 9B.  The
  project owner is arranging GPU hardware that can run the 32B class,
  so the matrix targets the right tier.
- ADR 0006 alongside doc 15 (rather than only one of them).  doc 15
  is the living matrix; ADR 0006 is the frozen decision record.  Six
  months from now "why these four?" is answered in ADR 0006; "what's
  the current state?" is answered in doc 15.  Both are needed.
- `ONBOARDING.md` at repo root (not `docs/16-onboarding.md`).
  Discoverability after `git clone` matters more than fitting the
  numbered docs/ scheme.  The doc points heavily into docs/ for
  detail.

### What broke / what we discovered

- `pnpm-workspace.yaml` and `services/frontend/` are stale — the
  real Next.js app lives at `/frontend/` at the repo root.  Flagged
  as Gotcha #2 in `ONBOARDING.md` but not fixed in this session;
  cleanup commit deferred.
- Repo `main` is 25 commits ahead of `origin/main` as of the start
  of this session.  All 25 are legitimate Phase 2 work from earlier
  sessions that was never pushed.  The push at the end of this
  session will publish all of them at once.

### What's next

- Push everything (this session's commits + the 25 unpushed Phase 2
  commits) to `origin/main` so the GitHub remote becomes the
  canonical state.
- Hand off to the new GPU dev machine (when it arrives) with the
  session-handoff prompt produced at the end of this session.
- Once on the GPU machine: pull the four families at the larger
  sizes, run probes, write the four expected probe ADRs (one per
  family / size that needs measurement).
- In parallel or after: begin Phase 3 (RAG + grounding validator)
  per `docs/06` and `docs/10`.

### Follow-up commits later in the same session

This entry was written before the following cleanup work; recording
here so the changelog matches the git log.

- `8da5389` — removed stale `pnpm-workspace.yaml` and empty
  `services/frontend/` directory (the deferred cleanup mentioned
  above).  Updated `ONBOARDING.md` accordingly: dropped Gotcha #2,
  renumbered #3-#7 → #2-#6, fixed three inline cross-references and
  the §0 repo-layout block.
- `7917fc5` — fixed factually wrong `bootstrap_tenant` flag names in
  `ONBOARDING.md` §3.9/§3.10 (real flags are `--admin-email`,
  `--admin-password`, `--opensearch-url`, `--opensearch-username`,
  `--opensearch-password`, `--server-api-url`, `--server-api-username`,
  `--server-api-password`, `--verify-tls`/`--no-verify-tls` — not the
  `--user-*` / `--wazuh-*` names previously documented).  Also
  corrected the structural misstatement that `bootstrap_tenant`
  supports a two-step "create tenant first, wire Wazuh later" flow —
  the CLI requires all Wazuh fields up front.  Merged §3.9 + §3.10
  into a single accurate step with a "no Wazuh yet" placeholder
  pattern; renumbered §3.11/§3.12 → §3.10/§3.11.  Clarified in §5
  that the CLI is fully idempotent and re-running it with the same
  `--tenant-slug` is the supported update / credential-rotation path
  (no dedicated update CLI needed).
- `<earlier in session>` — saved the new-machine handoff prompt as
  `prompts/HANDOFF-NEW-MACHINE.md` (was previously only inline in
  chat); appended this follow-up note to the CHANGELOG entry.
- `<later in session>` — committed ADR 0007 + `docs/16-distribution-and-packaging.md`
  + auto-memory entry + small pointers in `docs/09` (Container/build/CI
  section), `docs/decisions/README.md` (index row), `ONBOARDING.md`
  (Tier 2 reading order).  ADR 0007 records the decision to deliver
  Wolf natively (non-container) via `.deb`/`.rpm` system packages +
  systemd units, fronted by a one-line install script that handles
  prerequisite-repo setup (GitLab-style hybrid: Tailscale / Caddy /
  k3s / Docker also use this pattern).  Three alternatives weighed:
  GitLab-style omnibus (Option B, rejected as too expensive
  engineering for the marginal gain), Snap/Flatpak (Option C,
  rejected due to confinement friction with local sockets and
  secrets), and pure Option A without script wrapper (rejected as
  too much friction with three third-party repos to add manually).
  doc 16 specifies the package set, file layout (FHS-conformant),
  `wolf` CLI surface, supported distro matrix, security posture,
  and implementation work-breakdown (~3-4 weeks of focused work
  when the slot arrives).  Implementation deliberately queued for
  post-Phase 4 to avoid repackaging churn before the deployable
  surface stabilizes; current code must continue to honor
  constraints in doc 16 §"How current code should accommodate this
  commitment" (env-driven config, no hard-coded container paths,
  management CLIs remain usable as plain `python -m ...`, frontend
  on Next.js `output: 'standalone'`).
- `<this commit>` — committed ADR 0008 + cross-document repositioning
  to reflect "native primary, Docker supplementary."  Follow-up to
  ADR 0007's "peer" framing after the project owner clarified that
  native is where polish and operator-facing investment go; Docker
  remains baseline-supported (Dockerfiles, compose, Makefile targets
  stay; `make up` keeps passing) for operators who want to build
  their own container images (typically for k8s).  Concrete
  operational change: dev environment switches from Docker Postgres
  to system Postgres 17 + pgvector (apt-installed, systemd-managed),
  matching the production install path operators will use via the
  forthcoming install script.  Files touched: `docs/decisions/0008-...md`
  (new ADR), `docs/decisions/0007-...md` (amendment footer noting
  the positioning change), `docs/decisions/README.md` (index row +
  0007 status annotation), `docs/16-distribution-and-packaging.md`
  (new "Development against this channel" section), `docs/09-tech-stack-and-repo-layout.md`
  (§"Container, build, CI" repositioned), `ONBOARDING.md`
  (§2 reclassifies Docker from mandatory to optional + adds
  PostgreSQL line, §3.4 rewritten to lead with system Postgres
  install steps + keep Docker as alternative, §5 reboot section
  notes systemd auto-start), `Makefile` (comment block clarifying
  which targets serve native dev vs container channel),
  `docker-compose.yml` (top-of-file comment marking it the
  container-channel deployment stack), `docs/PROGRESS.md`
  (§3 dev posture, §8 ADR count).  Auto-memory entry
  `native_distribution_commitment.md` updated to reflect "native
  primary" instead of "peer."
- `<this commit>` — added Qwen 3.5 to the new-machine probe plan.
  Qwen 3.5 released on Ollama ~late May 2026 (~2 days before this
  commit per the library page); falls under ADR 0006's "Qwen 3
  family" commitment as a minor revision (3.x).  Sizes available:
  0.8B, 2B, 4B, 9B, 27B, 35B, 122B (plus MLX + cloud variants).
  On the RTX 4050 Laptop's 6 GB VRAM, qwen3.5:4b (3.4 GB on disk,
  ~3.5 GB VRAM at Q4) fits comfortably — the 9B (was 8B in Qwen 3,
  grown) doesn't.  Key new things vs Qwen 3: 256K context window
  (vs 128K — relevant for Phase 3 RAG), multimodal text+image on
  most variants (Wolf doesn't use this today).  Two things NOT
  confirmed from the Ollama library page: native tool calling
  (Qwen 3 had it, Qwen 3.5 almost certainly does, but probe will
  verify) and license class (Qwen 3 was Apache 2.0, but Qwen 3.5
  page doesn't state — verify before adding to KNOWN_MODELS with
  `license_class`).  Files touched: `prompts/HANDOFF-NEW-MACHINE.md`
  (Step C pull list extended to five models adding qwen3.5:4b with
  caveats; Step D probe expectations updated to three new probes
  including the qwen3:4b vs qwen3.5:4b cross-comparison and the
  potential follow-up default-flip ADR if qwen3.5:4b wins), and
  `docs/PROGRESS.md` §4 (next steps narrative updated to surface
  qwen3.5:4b as the most interesting near-term probe).  No code
  changes; the model abstraction layer already handles new family
  variants via the standard KNOWN_MODELS + probe + ADR workflow.

---

## 2026-05-22 — Switch dev default from llama3.2 to qwen3:4b

**Session type:** claude-code (continuation, same dev environment)
**Phase:** Phase 2 — Read path
**Duration:** ~30 min
**Branch / commit:** `main` — ADR 0004 `e092e21`, config flip
`ca495df`, KNOWN_MODELS amendment `14cc727`, final session commit
pending this entry.

### What we did

- Wrote `docs/decisions/0004-model-switch-llama3.2-to-qwen3-4b.md`
  weighing the three earlier probe ADRs (0001/0002/0003).  Decision:
  flip the dev default to qwen3:4b on probe-evidence + license
  grounds; document that qwen3's grounding-discipline probe failure
  raises Phase 3 grounding-validator priority but does not block the
  switch (the agent loop's tool-gated path bounds the fabrication
  risk).
- Updated `docs/decisions/README.md` index with ADR 0004.
- Changed `DEFAULT_MODEL_ID` from `llama3.2` to `qwen3:4b` in
  `services/orchestrator/app/config.py` as a standalone one-line
  commit referencing ADR 0004 (per doc 14's playbook).
- Restarted orchestrator with the new default and ran a curl-driven
  chat verification against the user's real Wazuh on `192.168.76.129`.
- **Verification exposed a real issue**: chat worked but ran in
  `pipeline` strategy with no tools — the static
  `KNOWN_MODELS["qwen3:4b"]` entry (added in commit `e9cc316`) was
  the conservative initial estimate (basic / pipeline) and shadowed
  the probe-measured capability (mid / guided) at runtime.
- Amended `KNOWN_MODELS["qwen3:4b"]` to match ADR 0002's measured
  capability (mid / guided / full / schema_enforced / 8 steps) in
  commit `14cc727`.
- Re-restarted orchestrator and re-verified end-to-end: now runs in
  `guided` strategy, calls `count_alerts_by_severity` once, returns
  a grounded cited answer with concrete numbers ("15 alerts total,
  all low severity").

### What we decided

- **`qwen3:4b` becomes the dev default.**  ADR 0004 is the canonical
  rationale; future contributors should read it before considering
  another switch.  Llama family stays in `KNOWN_MODELS` for operator
  opt-in via env override.
- **The qwen3:4b grounding-failure data point is not disqualifying**
  — it's a Phase 3 priority signal, not a Phase 2 blocker.
- **The remaining two `KNOWN_MODELS` amendments** (`llama3.2`,
  `gemma3:4b`) stay deferred — neither is the current default, so the
  static-vs-measured drift doesn't affect runtime behaviour today.
  They'll move in a single sweep when convenient.

### What broke / what we discovered

- **Static `KNOWN_MODELS` entries can silently override probe-measured
  capability at runtime.**  The conservative `qwen3:4b` defaults from
  Task 4 of the previous session shadowed ADR 0002's measurements
  because strategy selection reads the static descriptor, not the ADR
  prose.  This is by design (static entries are the source of truth
  the orchestrator boots from) but it means a probe ADR without a
  matching static amendment doesn't actually change runtime behaviour
  — a footgun worth keeping in mind for future probe → switch flows.
- First inference on qwen3:4b after model swap took ~76s (cold
  load); second inference ~169s including a single tool call.  The
  CPU-only ceiling, not a regression.

### What's next

- Wire the 4 mock-only read tools to real Wazuh
  (`get_event_timeline`, `get_agent_alert_history`, `get_agent_detail`,
  `get_rule_definition`).
- Verify Phase 2 exit criterion against a frontier API model — blocked
  on an operator-supplied API key.
- Batch-amend the remaining `KNOWN_MODELS` entries for `llama3.2`
  (structured_output downgrade per ADR 0001) and `gemma3:4b`
  (native_tool_calling downgrade per ADR 0003).
- Begin Phase 3 (RAG + grounding validator) — the qwen3:4b
  grounding-discipline result is the direct motivating evidence.

---

## 2026-05-22 — Phase 2 exit criterion: frontier-API verification

**Session type:** claude-code (continuous session)
**Phase:** Phase 2 — close-out
**Duration:** ~45 min
**Branch / commit:** `main` — see commit below this entry's date

### What we did

- Added `app/management/set_secret.py` — small CLI that reads a value
  from stdin (no shell history exposure) and stashes it in the
  configured secrets backend.  Smoke-tested with a throwaway value
  (round-trip verified, secret never echoed).
- Stashed the operator's OpenRouter API key under
  `model.openrouter.api_key` in `.local/secrets.enc`.
- Added two `KNOWN_MODELS` entries for OpenRouter-hosted open models:
  `deepseek/deepseek-v4-flash:free` (kept for operators who fund the
  account, since DeepSeek's `:free` route gates on credit deposit) and
  `nvidia/nemotron-3-super-120b-a12b:free` (truly free, NVIDIA Open
  Model License — restricted, fine for verification not default).
- Ran the Phase 2 frontier-API verification end-to-end against the
  operator's real Wazuh using Nemotron 120B.  Result: `frontier`
  strategy, one tool call to `count_alerts_by_severity`, grounded
  cited answer in 17 seconds.  Captured verbatim in ADR 0005.
- Restored the steady-state config (DEFAULT_MODEL_ID stays `qwen3:4b`
  in config.py; the verification was env-only).
- Updated PROGRESS.md: Phase 2 exit-criteria bullet flipped from `[ ]`
  to `[x]`; Section 1 marked Phase 2 closed; Section 4 reordered with
  Phase 3 (RAG + grounding validator) as the next step.

### What we decided

- Use `nvidia/nemotron-3-super-120b-a12b:free` rather than a
  DeepSeek-family model for the actual verification because DeepSeek's
  free routes on OpenRouter all gate on credit deposit (HTTP 402 with
  zero-credit accounts).  Nemotron is the strongest of the no-deposit
  free options that genuinely worked.
- Accept the license caveat: Nemotron uses the NVIDIA Open Model
  License (restricted by doc 14's filter), so it is the
  verification-path model, NOT the recommended-default model.  Doc 14
  isolation holds: dev default stays Apache (qwen3:4b).
- Keep both new `KNOWN_MODELS` entries permanently — the
  DeepSeek-flash one as the canonical slug for operators who do top
  up OpenRouter, the Nemotron one as the verified no-deposit path.

### What broke / what we discovered

- **`OPENAI_BASE_URL` must NOT include `/v1`**: OpenAIAdapter posts
  to `{base_url}/v1/chat/completions`.  Setting the env to
  `https://openrouter.ai/api/v1` produced `.../api/v1/v1/chat/...`
  and 404'd.  Correct: `https://openrouter.ai/api`.  Documented
  inline on the OpenRouter entries.
- **The two-`app/`-packages collision struck again.**  Same root
  cause as ADR 0001's probe CLI bug — gateway's `app/` wins the path
  race over orchestrator's when uvicorn is launched from project
  root.  Workaround (`cd services/orchestrator` first) is documented
  in PROGRESS §3 and now in ADR 0005's "issues surfaced" section.
- **OpenRouter `:free` suffix is not a binding promise.**  Three of
  the five candidate `:free` routes we tried returned errors because
  their upstream providers meter independently of OpenRouter's free
  classification; account needed credits even for "free" routes.
  Documented in ADR 0005.

### What's next

- Phase 3 — RAG + grounding validator per docs/06.  Read that doc
  plus the Phase 3 block of docs/10-build-roadmap.md, then plan the
  slice.  qwen3:4b's grounding-discipline failure (ADR 0002) is the
  direct motivation for the grounding validator.

---

## 2026-05-22 — Amend `KNOWN_MODELS` for `llama3.2` and `gemma3:4b` per probe ADRs

**Session type:** claude-code (continuous session)
**Phase:** Phase 2 — close-out cleanup
**Duration:** ~5 min
**Branch / commit:** `main` — see commit below this entry's date

### What we did

- Aligned `KNOWN_MODELS["llama3.2"]` with ADR 0001's measurements:
  `native_tool_calling` upgraded `partial` → `full`;
  `structured_output` downgraded `prompt_coaxed` → `unreliable`.
  Reasoning tier and strategy were already correct.
- Aligned `KNOWN_MODELS["gemma3:4b"]` with ADR 0003's measurements:
  `native_tool_calling` downgraded `partial` → `none` (Gemma 3 4B has
  no native tool calling — Ollama returns HTTP 400 on any chat with
  `tools`); `structured_output` upgraded `prompt_coaxed` →
  `schema_enforced`; `max_safe_autonomous_steps` tightened 5 → 3.
- Added inline comments on each amended entry citing the ADR that
  grounded the change.
- Updated PROGRESS §4 to drop the completed cleanup item.

### What we decided

- Cosmetic cleanup; neither model is the current default
  (`qwen3:4b` holds that since commit `ca495df`).  But aligning
  static estimates with measured truth keeps `KNOWN_MODELS` honest
  for any operator who reads it as documentation.

### What broke / what we discovered

- Nothing.  128 backend tests still pass; ruff + mypy strict clean.
  No code branches on the amended fields (they inform strategy
  selection but not behaviour at the strategy level for these two
  models — `gemma3:4b` was already `pipeline` and `llama3.2` was
  already `guided`).

### What's next

- Frontier-API exit-criterion verification (blocked on operator key).
- Phase 3 entry — RAG + grounding validator per docs/06.

---

## 2026-05-22 — Verify all 9 read tools against real Wazuh; add `--all-tools` smoke mode

**Session type:** claude-code (continuous follow-on session)
**Phase:** Phase 2 — Read path (close-out)
**Duration:** ~30 min
**Branch / commit:** `main` — see commit below this entry's date

### What we did

- Exercised the four previously-mock-only read tools against the
  operator's real Wazuh deployment by calling each tool's `run()`
  directly through a synthesized `ToolExecContext`:
  `get_event_timeline`, `get_agent_alert_history`, `get_agent_detail`,
  `get_rule_definition`.  **All four succeeded first try** — no
  field-shape mismatches between the unit-test mocks and the real
  Server-API / OpenSearch responses.
- Extended `app/management/smoke_wazuh.py` with a `--all-tools` mode
  that exercises every registered read tool against the live
  deployment (calls `run()` through a ToolExecContext, bypassing the
  dispatcher's session requirement but going through full Pydantic
  input/output validation and the real HTTP layer).  Usage:
  `uv run python -m app.management.smoke_wazuh --tenant-slug acme \
   --all-tools --agent-id 000 --rule-id 5402`.
- Re-verified all 9 tools end-to-end against the live Wazuh:
  list_agents (1), get_agent_detail (1), get_cluster_health,
  get_rule_definition (1), search_alerts (5), aggregate_alerts (3),
  count_alerts_by_severity (23 total), get_event_timeline (5),
  get_agent_alert_history (5).  **9/9 ✓.**
- Updated `docs/PROGRESS.md` Section 2 to reflect the new
  live-verified status (all 🟡 read-tool entries flipped to ✅), and
  Section 4 to drop the now-completed wiring step.

### What we decided

- No bugs found, no fixes needed.  The unit-test mocks were written
  with care and matched real shapes accurately enough that the live
  exercise passed without code changes.
- Kept the existing `smoke_test()` (clients-only mode) as the default
  for quick connectivity checks; `--all-tools` is opt-in for the
  fuller verification.

### What broke / what we discovered

- Nothing broke.  The discovery is non-news but worth recording:
  Wazuh's Server API and OpenSearch response shapes for `/agents`,
  `/rules`, and alert documents are stable enough that mock-driven
  unit tests stay accurate against a real deployment.

### What's next

- Frontier-API exit-criterion verification (blocked on operator API key).
- Batch-amend the static `KNOWN_MODELS` entries for `llama3.2` and
  `gemma3:4b` per ADRs 0001 and 0003 (cosmetic — neither is the
  current default).
- Begin Phase 3 (RAG + grounding validator) per docs/06.

---

## 2026-05-22 — Switch dev default model `llama3.2` → `qwen3:4b`

**Session type:** claude-code (continuous session)
**Phase:** Phase 2
**Duration:** ~30 min
**Branch / commit:** `main` — `e092e21` (ADR 0004), `ca495df`
(config flip), `14cc727` (KNOWN_MODELS amendment), `4324bce`
(PROGRESS/CHANGELOG update for switch)

### What we did

- Wrote ADR 0004 weighing the three probe results
  (`docs/decisions/0004-model-switch-llama3.2-to-qwen3-4b.md`).
- Flipped `DEFAULT_MODEL_ID` from `llama3.2` to `qwen3:4b` in
  `services/orchestrator/app/config.py` as a standalone commit.
- Verification surfaced that the static `KNOWN_MODELS["qwen3:4b"]`
  entry (deliberately-conservative Task 4 estimate: basic/pipeline)
  shadowed the probe-measured capability (mid/guided) at runtime —
  qwen3:4b was running in pipeline strategy.  Amended the static
  entry to match measurement per ADR 0002.
- Re-verified end-to-end: chat against real Wazuh, qwen3:4b in
  `guided` mode, one tool call to `count_alerts_by_severity`,
  grounded cited answer ("15 alerts total, all low severity").

### What we decided

- Land the three changes as three separate commits (ADR, config flip,
  static-entry amendment) so each is independently revertable.
- Keep `llama3.2` in `KNOWN_MODELS` — operators who want it just set
  `DEFAULT_MODEL_ID=llama3.2` in `.env`.

### What broke / what we discovered

- The static `KNOWN_MODELS["qwen3:4b"]` from Task 4 silently overrode
  the probe-measured capability, causing the model to run in the
  wrong strategy after the flip.  Verification caught it.  Lesson:
  any time we add a new model to `KNOWN_MODELS` from an estimate, we
  must amend it as soon as the probe runs.
- Earlier in the session, a single mid-conversation `loop_error`
  surfaced as "Model call failed:" with empty detail (the Ollama
  adapter's swallowed exception).  Added diagnostic capture in
  commit `e09b4e5` (logs exception type + traceback to audit data)
  so the next occurrence is debuggable.

### What's next

- Wire the 4 mock-only read tools to real Wazuh (now done — see entry
  above).
- Frontier-API exit-criterion verification (still pending key).
- Phase 3 entry.

---

## 2026-05-22 — Add model recommendations, session continuity tracking, and run the first capability probe

**Session type:** claude-code (executing user's planning brief at
`prompts/CLAUDE-CODE-SESSION-PROMPT.md`)
**Phase:** Phase 2 — Read path
**Duration:** in progress
**Branch / commit:** `main` — Commit 1 `c05cdce` (planning bundle),
Commit 2 `b093761` (session-additions docs), Commit 3 `e9cc316`
(Tasks 4+5 code + probe sys.path fix), final session commit pending
this entry.

### What we did

- **Committed the previously-untracked planning bundle.**
  Commit 1 (`c05cdce`): `docs/00-13` (excluding doc 11) + `README.md`.
  Commit 2 (`b093761`): updated `docs/11-claude-code-instructions.md`
  (session-continuity protocol), new `docs/14-model-recommendations.md`,
  new `docs/PROGRESS.md`, new `docs/CHANGELOG.md`.
- **Moved `PROGRESS.md` and `CHANGELOG.md` from the repo root into
  `docs/`** to match the references in docs 11 and 14.
- **Relaxed the start-of-session reading rule** in
  `docs/11-claude-code-instructions.md`: re-reading PROGRESS.md +
  CHANGELOG.md every turn is required only for a brand-new session, a
  different machine/environment, or a different Claude model version.
  The end-of-session update + final commit remain mandatory regardless.
- **Populated `docs/PROGRESS.md`** with the real current state of Wolf
  (Phase 2 status, what's built and verified, configuration, what's
  next, active decisions, 128-test coverage).
- **Initialized `docs/CHANGELOG.md`** (this entry).
- [TASK 4] Extended `KNOWN_MODELS` in
  `services/orchestrator/app/models/interface.py` with four new entries:
  `qwen3:4b`, `gemma3:4b`, `qwen3:8b`, `glm-5.1`. **Did not** change
  `DEFAULT_MODEL_PROVIDER` or `DEFAULT_MODEL_ID` — both remain
  `ollama` / `llama3.2`.
- [TASK 5] Added `license_class` to `CapabilityDescriptor` in
  `packages/schema/wolf_schema/capability.py`. Populated every existing
  `KNOWN_MODELS` entry: Llama family → `restricted`, Claude/GPT →
  `proprietary`, Qwen/Gemma/Mistral → `apache-2.0`, GLM/DeepSeek →
  `mit`. Non-breaking informational field; no runtime code branches on
  it.
- [TASK 6] Ran the capability probe against live Ollama on this hardware:
  `uv run python -m tools.model_probe --provider ollama --model llama3.2`.
  Required a one-line `sys.path` bootstrap in `tools/model_probe/__main__.py`
  to resolve a two-`app/`-packages collision between
  `services/gateway/app/` and `services/orchestrator/app/` that uv editable
  installs had been silently shadowing (gateway won the ambiguous name).
  Probe result: score 0.68, 3/4 tasks pass; measured `mid` / `guided` —
  matches the static `KNOWN_MODELS` estimate at the strategy tier.  Full
  ADR at `docs/decisions/0001-model-probe-llama3.2-baseline.md`.
- [TASK 7] Pulled `qwen3:4b` and `gemma3:4b` and probed both.
  - `qwen3:4b`: score **0.75** (3/4 pass).  PASS: tool-call formatting,
    JSON-schema adherence, multi-step reasoning.  FAIL:
    grounding-discipline (fabricated specific data when given no tools).
    Measured `mid` / `guided` / `schema_enforced` — every field as good
    as or better than `llama3.2`, except grounding.  ADR
    `docs/decisions/0002-model-probe-qwen3-4b.md`.
  - `gemma3:4b`: score **0.25** (1/4 pass).  Two task failures were
    HTTP 400 from Ollama because Gemma 3 4B has **no native tool
    calling** — the runtime rejects requests that include a `tools`
    parameter.  Measured `basic` / `pipeline`.  Ruled out as a default
    candidate.  ADR `docs/decisions/0003-model-probe-gemma3-4b.md`.
- [TASK 8] Created `docs/decisions/README.md` (ADR definition, naming
  convention `0NNN-short-kebab-title.md`, file template, live index of
  the three new ADRs).

### What we decided

- **Default model stays `llama3.2` for now.** Doc 14 recommends switching
  to an Apache-licensed model (Qwen 3 4B or Gemma 3 4B) before Wolf has
  external users, but the switch is gated on probe data and a follow-up
  ADR. This session adds the candidate entries to `KNOWN_MODELS` so the
  options exist; the switch itself is a separate decision.
- **`license_class` is informational, not enforcement.** It surfaces the
  Llama vs Apache/MIT distinction in the UI eventually, but no code path
  blocks a model on its license. Operator choice always.
- **PROGRESS.md and CHANGELOG.md live in `docs/`, not at repo root.**
  Resolved by moving the files; doc 11 and doc 14 keep their existing
  references.
- **Start-of-session reading is conditional** (brand-new session / new
  environment / different model only). End-of-session update is always
  mandatory. Updated doc 11 to reflect this.

### What broke / what we discovered

- **Two-`app/`-packages collision blocked the probe CLI.** Both
  `services/gateway/app/` and `services/orchestrator/app/` exist as
  Python packages literally named `app`.  uv's editable installs put
  both on `sys.path` (gateway entry first), so bare `import app`
  resolved to the gateway and `app.models.ollama` failed with
  `ModuleNotFoundError`.  Pytest never hit this because its path setup
  happens to land orchestrator first.  Fixed locally to the probe CLI;
  the deeper "rename one of them" surgery is logged as deferred work.
- **`llama3.2`'s static `KNOWN_MODELS` entry was directionally right
  but two fields were off:** `native_tool_calling` was estimated
  `partial` and measured `full` (upgrade); `structured_output` was
  estimated `prompt_coaxed` and measured `unreliable` (downgrade —
  free-form JSON adherence failed mid-document).  Recommended strategy
  matches.
- **`qwen3:4b`'s static entry was conservative across the board.**
  Measured stronger on every dimension except grounding-discipline,
  where it failed cleanly (fabricated when given no tools).  In Wolf's
  tool-gated agent loop that risk is contained but raises Phase 3
  grounding-validator priority.
- **`gemma3:4b` has no native tool calling.** Ollama returns HTTP 400
  on any chat request that includes `tools`.  This is the model
  family's structural limitation, not a transient bug.  Confirms doc 14
  that gemma is a viable summariser at best, not an agent driver.

### What's next

- Write `docs/decisions/0004-model-switch-llama3.2-to-qwen3-4b.md`
  weighing the three probe results.  qwen3:4b is the recommendation
  for the *recommended-for-shipping* default per doc 14; the question
  the ADR settles is whether dev should switch now or wait for the
  Phase 3 grounding validator.
- After the switch ADR lands, change `DEFAULT_MODEL_ID` in
  `services/orchestrator/app/config.py` in a **separate commit** that
  references the ADR (per doc 14's environment-change playbook).
- Wire the four remaining read tools to real Wazuh
  (`get_event_timeline`, `get_agent_alert_history`, `get_agent_detail`,
  `get_rule_definition`).
- Verify Phase 2 exit criterion against a frontier API model in addition
  to the local-Ollama path that already passes.
- Batch-amend the static `KNOWN_MODELS` entries for `llama3.2`,
  `qwen3:4b`, and `gemma3:4b` to reflect measured capability.
