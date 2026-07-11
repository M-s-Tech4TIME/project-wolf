---
name: embedding-stack-adr-0033
description: "ADR 0033 (2026-07-11): embedding stack FULLY configurable per-embedder (column dims, MRL, doc+query prefixes, num_ctx, char caps); pgvector schema follows settings via embedding_schema tool; reembed --force for geometry changes; HNSW caps at 2000 dims"
metadata:
  type: project
---

The embedding stack is FULLY configurable per ADR 0033 (operator directive:
switching embedding models must switch EVERYTHING, including the DB
vectors). Every knob is per-embedder (primary + `_AUX` twins):
model/provider, **column width** `EMBEDDING_DIMENSION(_AUX)` (independent —
4096 primary next to 768 aux is legal; RRF fuses ranks so mixed widths are
sound), MRL `EMBEDDING_REQUEST_DIMENSIONS`, `EMBEDDING_QUERY_PREFIX` +
`EMBEDDING_DOCUMENT_PREFIX` (truncation happens BEFORE prefixing at the
adapter), `EMBEDDING_NUM_CTX` (Ollama options.num_ctx), and
`EMBEDDING_CHAR_LIMIT` (aux default 1800 = the old v2-moe hardcode, now
adapter-level so upsert/seed/re-embed truncate identically).

**Key mechanics:**
- ORM widths come from the NARROW `get_embedding_dimensions()` loader in
  config.py — NEVER full `Settings` at models-import time (its SECRET_KEY
  placeholder guard breaks secretless contexts like CI alembic-check; this
  exact failure happened on the first push, run 29156482490).
- `wolf_server.management.embedding_schema [--apply]` reconciles the live
  DB: drop HNSW → retype `vector(N)` USING NULL → reset stamps → batched
  re-embed → SET NOT NULL only when clean → rebuild HNSW. RESUMABLE (plan
  derives from live catalog); aux sentinel `__unembeddable__` rows are
  steady state, excluded from planning.
- `reembed --apply --force` (keyset-paginated) for GEOMETRY changes that
  don't change model_id (prefixes / MRL width / num_ctx).
- pgvector HNSW caps at **2000 dims** (halfvec 4000, unused): qwen native
  4096 = storable + exact-scan searchable, NO ANN index — fine ~5K chunks.
- Two first-class recipes in .env.example + tuning guide: **nomic combo**
  (nomic-embed-text + v2-moe, official `search_document: `/`search_query: `
  prefixes — LIVE on the dev box since 2026-07-11, corpus force-re-embedded)
  and **qwen3-embedding** (768/1024/2000/4096 width guidance; 8B ~4.7 GB
  won't sit beside qwen3:8b chat on a 6 GB GPU).
- Live-probed facts: qwen3-embedding native 4096/ctx 40960; v2-moe 768/ctx
  512, MRL (honors dimensions=256); Ollama `/api/embed` honors `dimensions`
  + `options.num_ctx`; legacy `/api/embeddings` does neither.
Related: [[postgres-18-baseline]], [[grounding-enrichment-tools-future-phase]].
