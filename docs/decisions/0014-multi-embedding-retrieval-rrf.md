# 0014 — Multi-embedding retrieval via RRF (chained v1.5 + v2-moe)

**Date:** 2026-05-27
**Status:** accepted
**Decider:** human (project owner) with claude-code drafting
**Related:** `docs/06-knowledge-and-rag.md` §Hybrid retrieval,
[ADR 0007](0007-native-distribution-via-system-packages-and-install-script.md)
(packaging constraints on torch wheels — preserved here by keeping
v2-moe an Ollama-hosted optional model, not a new Python dep),
[ADR 0012](0012-embedding-stack-ollama-vs-sentence-transformers.md)
(embedding-adapter selection; this ADR builds on its "keep both behind a
protocol" pattern), Slice 2A
(`feat(phase3-slice2a): hybrid retrieval — BM25 + vector fusion via RRF`,
the two-leg foundation this extends), commit `8f0d544`.

## Context

Two empirical findings from late Phase 3 motivated this:

1. **v2-moe wins precision@1 35% vs v1.5's 15%** on a 20-query battery of
   entity-specific ATT&CK + rule-ID lookups against the live 5173-chunk
   corpus. Measured by re-embedding the full corpus with v2-moe in memory
   and comparing top-K under each model independently (see
   `tools/embedding_benchmark/full_corpus_v2_eval.py`).
2. **v2-moe has a 512-token context limit** while v1.5 has 8K. ~2.4% of
   the live corpus (123 chunks, all long ATT&CK descriptions) gets
   silently truncated when v2-moe embeds them; another ~0.5% fails
   entirely (Ollama returns "unexpected EOF" even after truncation).

Three options were considered:

- **Flip the default to v2-moe.** Pays the precision win but loses
  long-context fidelity on the truncated chunks. The 0.5% that error
  out become unretrievable.
- **Stay on v1.5.** Misses the precision win on entity lookups (the
  most common kind of analyst query).
- **Chain both via RRF** (chosen). Adds a second vector column on
  `knowledge_chunks`, runs three RRF legs (BM25 + v1.5 + v2-moe), and
  fuses them so each leg compensates for the others' weaknesses.

The operator framed this as "complement each other to fill the gap
where both used to lack." That's exactly what RRF over diverse rankers
delivers structurally.

## Decision

**Add an OPTIONAL secondary embedding column + a third RRF leg.** When
`EMBEDDING_MODEL_AUX` is empty (default), the orchestrator behaves
exactly as it did before — single-leg vector + BM25, unchanged. When
set, the store loads the corresponding aux embedder, populates
`embedding_v2` at upsert time, and `search()` fuses three legs:

```
                       ┌── BM25 leg (Postgres FTS, exact tokens)        ──┐
query → search()       ├── Primary-vector leg (embedding, long context) ──┼ RRF fusion → top-K
                       └── Aux-vector leg (embedding_v2, entity precision)─┘
                       (last leg only when aux embedder configured)
```

RRF formula unchanged from Slice 2A: `score = sum(1 / (k + rank_in_leg))`
with `k=60`, summed across whichever legs produced a candidate. A chunk
ranked well in any leg gets boosted; a chunk ranked well in multiple
legs wins decisively. RRF does not care about absolute scores — only
per-leg rankings — so different-distribution vector spaces are
compatible without any cross-leg normalization.

### Concrete shape

- **Migration 0006** adds `embedding_v2 vector(768)` (nullable) plus
  `embedding_v2_model varchar(100)` plus an HNSW cosine-ops index on
  `embedding_v2`. Backward-compatible: existing chunks keep working
  with NULL aux columns.
- **`KnowledgeChunk` model** declares both new columns.
- **`Settings` adds `embedding_model_aux` + `embedding_provider_aux`.**
  Empty default keeps Slice-2A behaviour. Operator sets
  `EMBEDDING_MODEL_AUX=nomic-embed-text-v2-moe` (or any other 768-dim
  model) to enable chaining.
- **`make_embedding_provider_aux(settings)`** factory returns `None`
  when the env var is empty; constructs the second adapter otherwise.
- **`PgvectorKnowledgeStore` accepts `embedder_aux=None`** kwarg.
  `upsert()` writes both vectors when configured; per-chunk error
  tolerance for aux (e.g. v2-moe rejecting a too-long input leaves
  `embedding_v2 IS NULL` for that chunk). `search()` adds a
  `_vector_aux_candidates()` helper that joins on `embedding_v2 IS NOT
  NULL` so unembedded chunks don't contribute to (or pollute) the aux
  leg, but still appear via the v1.5 and BM25 legs.
- **`wolf reembed` CLI gains `--aux`** which walks rows where
  `embedding_v2_model IS DISTINCT FROM <active aux model>` and
  populates the column. Uses a sentinel `__unembeddable__` value for
  chunks the aux model rejects after truncation, so subsequent runs
  don't retry them in a loop. Truncates inputs at 1800 chars by
  default (configurable via `AUX_CHAR_LIMIT` constant) to match
  v2-moe's 512-token window with safety margin.

### Operator workflow

```bash
# .env additions
EMBEDDING_MODEL_AUX=nomic-embed-text-v2-moe

# Populate the new column on the existing corpus (one-time, ~7 min)
ollama pull nomic-embed-text-v2-moe
cd services/orchestrator
set -a && source ../../.env && set +a
uv run python -m app.management.reembed --aux --apply

# Restart orchestrator — queries now use 3-way RRF.
```

The migration is fully reversible: empty the env var, restart, and
search() drops back to the 2-leg flow. The aux column stays in the
schema but is unused. To fully revert, run alembic downgrade 0005.

## Measured impact

Live corpus (5173 chunks: 697 ATT&CK techniques + 4473 Wazuh rules +
3 ACME-private). Twenty-query battery, mixed rule-ID + technique-ID
ground truth.

| Mode | precision@1 | precision@5 | p50 latency |
|---|---|---|---|
| Vectors-only v1.5 | 15% (3/20) | 15% (3/20) | (in-memory test) |
| Vectors-only v2-moe | 35% (7/20) | 50% (10/20) | (in-memory test) |
| **BM25 + v1.5** (Slice 2A baseline) | 15% (3/20) | 35% (7/20) | **48 ms** |
| **BM25 + v1.5 + v2-moe (this ADR)** | **30% (6/20)** | **60% (12/20)** | **159 ms** |

The chained-mode precision@5 of 60% is **1.7× the Slice 2A baseline**.
Five queries that single-leg missed entirely in the top-5 are now
correctly retrieved (Process Injection T1055, Local System T1005, DNS
Tunneling T1071.004, Pass the Hash T1550.002, Boot/Logon Autostart
T1547). Latency goes 48 → 159 ms per search — the extra ~110 ms is
imperceptible inside the multi-second LLM generation step that follows.

precision@1 sits between the two single-vector modes (15% < 30% < 35%).
This is expected: RRF dilutes a chunk's score in cases where it's a
strong winner in only ONE leg. For Wolf's actual use case the agent
retrieves top-K chunks (K=5) and feeds them all to the LLM — so
precision@5 is the load-bearing metric, and chained mode wins there
decisively.

### Truncation observed in practice

- 5145 / 5173 chunks (99.5%) successfully embedded with v2-moe.
- 28 chunks (0.5%) marked `__unembeddable__` after truncation — long
  ATT&CK descriptions even at 1800 chars produce malformed input
  v2-moe rejects with "unexpected EOF." These chunks remain
  retrievable via the v1.5 + BM25 legs; they just don't contribute to
  the aux leg's rankings. Per ADR 0014 §"Complement each other,"
  this is the design intent: v1.5 covers what v2-moe can't.

## Alternatives considered

- **Flip the default to v2-moe; drop v1.5.** Rejected. The 3.5% of
  chunks v2-moe can't fully embed (truncated or rejected) would lose
  fidelity or disappear from retrieval. Net retrieval quality on a
  diverse query set would likely be worse, not better. Empirically:
  v2-moe alone got 50% precision@5; chained gets 60%.
- **Per-chunk-fallback at embed time** (use v2-moe for short chunks,
  v1.5 for long ones, store one vector). Rejected because the
  embedding spaces are different — a query embedded by v2-moe can't
  meaningfully cosine-compare against a vector from v1.5. RRF across
  parallel legs is the only correct way to combine different
  embedding models.
- **Score normalization + weighted sum** instead of RRF. Rejected as
  more complex than necessary. RRF is parameter-free (one constant,
  k=60), distribution-agnostic (per-leg rankings, not scores), and
  established in the literature (Cormack et al. 2009). The 2A path
  already uses it; extending to three legs is mechanical.
- **Make `embedding_v2` non-nullable + reject chunks the aux can't
  embed.** Rejected. Some chunks are genuinely too long for v2-moe
  but valuable to retrieve via v1.5. Forcing the aux to embed
  everything would force us to either drop those chunks (loss of
  data) or truncate aggressively (degrading the v1.5 leg as well).
  Per-chunk NULL aux is the right design — coverage stays at 100%,
  precision improves where the aux works.
- **Skip a separate column; re-use `embedding` after re-embed with
  v2-moe.** Rejected for the same long-chunk-loss reason: v1.5's
  full-fidelity coverage of the 3.5% of long chunks would be lost,
  net retrieval would regress.

## Consequences

- **Wolf's RAG now supports N-leg RRF in principle.** The store's
  `search()` calls a list of leg-producing helpers and unions them;
  adding a fourth leg (e.g. a domain-fine-tuned model later) is one
  more helper method.
- **The `embedding_v2` column is nullable** and stays NULL when the
  operator doesn't configure an aux embedder. Single-leg deployments
  cost nothing — same wire surface, same query plan.
- **Storage doubles for chunks with aux embeddings:** 768 floats × 4
  bytes × 5145 chunks ≈ 15 MB additional. Negligible.
- **Per-query latency triples** (48 → 159 ms) in chained mode. Still
  well under chat-time perceptibility but worth noting if Wolf is
  ever wrapped in a synchronous-low-latency context (which it isn't
  today).
- **The `embedding_v2_model` sentinel value `__unembeddable__`** marks
  chunks that the aux model can't handle. This is a new convention
  worth documenting in any future operator-facing tooling around the
  knowledge layer.
- **Schema is forward-compatible.** Migration 0006 is backward-
  compatible (nullable column). Migration 0007 could downgrade the
  aux column if ever needed; migrating to a different secondary
  model dimension would require a new column (`embedding_v3` etc.)
  since pgvector columns are width-locked.
- **No frontend change required.** The chained path returns the same
  `RetrievedChunk` shape; the grounding validator + UI care about
  citations, not which leg surfaced them.
- **No new tests beyond unit-level for the RRF math.** End-to-end
  retest via `tools/embedding_benchmark/full_corpus_chained_eval.py`
  is the empirical guard; running it after any future leg-addition
  is the operator's check.

## Rollback path

Reversible without data loss:

1. Empty `EMBEDDING_MODEL_AUX` in `.env` and restart orchestrator.
   Single-leg behaviour resumes immediately; aux column data is
   ignored.
2. Optionally `alembic downgrade 0005` to drop the columns +
   index. The `embedding` column + Slice 2A two-leg behaviour
   remain functional.

Re-running `wolf reembed --aux --apply` after a model change re-populates
the column from scratch. The sentinel handling keeps long-running
re-embeds bounded.
