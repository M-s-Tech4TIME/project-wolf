# ADR 0033 — Fully configurable embedding stack (dimension-aware schema switching)

**Status:** Accepted (2026-07-11)
**Deciders:** operator + Claude Code session
**Supersedes:** the fixed-768 column contract in ADR 0012 / migration 0004
(both stay valid as the *baseline*; this ADR makes the width a setting).
**Related:** ADR 0012 (embedding runtime), ADR 0014 (multi-embedding RRF),
ADR 0008 addendum (PostgreSQL 18).

## Context

Until now `knowledge_chunks.embedding` / `embedding_v2` were hard-locked at
`vector(768)`: the width lived as a constant in the ORM model, in migrations
0004/0006, and implicitly in every adapter. Models whose native dimension
differs (qwen3-embedding: 4096) could only participate via MRL truncation
*down to* 768. The operator's directive: choosing an embedding model must
switch **everything** it implies — dimensions, context window, task
prefixes, the database columns and indexes, and the stored vectors — as a
supported, guided operation, for both first-class configurations:

- **nomic combo** (the current live setup): `nomic-embed-text` primary +
  `nomic-embed-text-v2-moe` aux (768-dim both; v2-moe: 512-token window,
  MRL-trained, task prefixes `search_document: ` / `search_query: `).
- **qwen3-embedding solo**: native 4096-dim, 40960 context,
  instruction-aware queries, MRL-truncatable to any width.

(All capabilities live-probed via Ollama `/api/show` + `/api/embed`,
2026-07-11.)

## Decision

### 1. Every embedding knob is a per-embedder setting

Primary and aux each carry the FULL set (aux twins suffixed `_AUX`):

| Setting | Meaning | Default |
|---|---|---|
| `EMBEDDING_MODEL` / `_PROVIDER` | model + runtime | nomic-embed-text / ollama |
| `EMBEDDING_DIMENSION` | **primary column width** (ORM + live schema) | 768 |
| `EMBEDDING_DIMENSION_AUX` | aux column width (0 = same as primary) | 0 |
| `EMBEDDING_REQUEST_DIMENSIONS(_AUX)` | MRL truncate+renormalize request | 0 (off) |
| `EMBEDDING_QUERY_PREFIX(_AUX)` | task/instruction prefix, queries only | "" |
| `EMBEDDING_DOCUMENT_PREFIX(_AUX)` | task prefix, passages only | "" |
| `EMBEDDING_NUM_CTX(_AUX)` | Ollama options.num_ctx per embed call | 0 (model default) |
| `EMBEDDING_CHAR_LIMIT(_AUX)` | hard input cap before embedding | 0 / 1800 |

Truncation happens at the **adapter**, before prefixing (the task marker
must survive), so upsert, seeding, and re-embeds behave identically —
previously the 1800-char v2-moe cap lived only in the `reembed` CLI.

### 2. The vector columns follow settings; a tool reconciles the database

- The ORM declares `Vector(EMBEDDING_DIMENSION)` / `Vector(EMBEDDING_DIMENSION_AUX)`
  read at import time. Baseline migrations still create 768 — migrations
  stay frozen history; **the schema tool owns the delta**:

      uv run python -m wolf_server.management.embedding_schema [--apply]

  Per out-of-sync column it: drops the HNSW index → drops NOT NULL
  (primary) → `ALTER … TYPE vector(N) USING NULL` (a width change
  invalidates every stored vector) → resets the model stamps → re-embeds
  everything with the active provider (batched, per-batch commits) →
  restores NOT NULL (only when clean) → rebuilds the index. **Resumable**:
  the plan derives from live catalog state (width, NULLs, constraint,
  index), so a crashed run continues instead of redoing.
- Mismatch between settings and live schema **fails loudly** (Postgres
  "expected N dimensions"; the adapter's dim check names the fix knobs);
  `alembic check` also flags it until the tool runs. Never silent.
- `reembed` gains `--force`: re-embed every row in scope even when the
  model stamp matches — required when the *geometry* changes without the
  model id changing (new prefix, different MRL width, num_ctx unlock).
  Keyset-paginated so rewritten rows aren't revisited.

### 3. HNSW cap stated honestly

pgvector indexes (HNSW/IVFFlat) cap at **2000 dims** on the `vector` type.
Above that (e.g. qwen native 4096) the tool skips ANN indexing and search
runs **exact** — perfect recall, linear cost; fine at Wolf's current corpus
scale (~5K chunks), revisit if a corpus grows into the hundreds of
thousands. MRL mid-points (1024/2000) keep HNSW while retaining most of
the quality; `halfvec` (cap 4000) is deliberately out of scope until a
real corpus needs it.

### 4. Independent primary/aux widths

`embedding` and `embedding_v2` no longer share a width — a 4096 qwen
primary can sit next to a 768 v2-moe aux. Cosine distance per leg is
self-consistent; RRF fuses ranks, not distances, so mixed widths are
sound.

## Consequences

- Switching between the two first-class recipes (or any future model) is:
  edit `.env` → restart wolf-server → `embedding_schema --apply` (only if
  a width changed) → `reembed --apply --force` (+ `--aux`) — documented in
  `.env.example` and `docs/reference/model-performance-tuning.md`.
- Retrieval is degraded during an apply (vector legs empty until
  re-embedded; BM25 keeps answering) — maintenance-window operation.
- The nomic recipes now carry their **official task prefixes** (both
  nomic models train with `search_document: `/`search_query: `); enabling
  them on an existing corpus requires the `--force` re-embed.
- CI is untouched: defaults remain 768 end-to-end, so alembic-check and
  the migration baseline stay green with zero special-casing.

---

## Addendum (2026-07-12): no width cap — binary-quantized HNSW + exact rerank

Operator directive: qwen3-embedding's native 4096 dims must be **fully
utilizable with no restrictions**. §3's "no ANN index above 2000 dims"
posture is superseded:

- Widths above pgvector's 2000-dim plain-HNSW cap now get a
  **binary-quantized HNSW expression index** —
  `CREATE INDEX ... USING hnsw ((binary_quantize(col)::bit(N)) bit_hamming_ops)`
  (bit vectors index to 64,000 dims) — built automatically by the
  `embedding_schema` tool (`ColumnPlan.index_kind = "bq"`; wrong-kind
  indexes are detected via `pg_indexes.indexdef` and rebuilt).
- The store's vector legs switch to a **two-stage query** when the leg's
  embedder declares a width above the cap: stage 1 ranks by Hamming
  distance over the *same* indexed expression, oversampled by
  `EMBEDDING_BQ_OVERSAMPLE` (default 4); stage 2 reranks those candidates
  by **exact cosine over the full-fidelity stored vectors** and keeps the
  usual candidate limit. Quantization only shapes the candidate pool;
  final ordering is always exact — no fidelity loss at any width.
- Verified empirically (2026-07-12): a 4096-dim probe table's EXPLAIN
  shows `Index Scan using ..._hnsw` for the exact SQL shape SQLAlchemy
  emits (`CAST(binary_quantize(...) AS BIT(4096)) <~> ...`).
- Trade-off stated honestly: BQ's Hamming stage is an approximation
  filter; recall is governed by the oversample factor (4 is the
  community sweet spot for normalized high-dim embeddings like qwen's;
  raise it if a very large corpus ever shows recall droop).
