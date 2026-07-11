# 0012 — Embedding stack: Ollama-hosted nomic-embed-text vs in-process sentence-transformers BGE-base

**Date:** 2026-05-26
**Status:** accepted
**Decider:** claude-code (executing operator request from Phase 3 Slice 1
planning) with human review
**Related:** [docs/06-knowledge-and-rag.md](../06-knowledge-and-rag.md)
(knowledge layer design), [ADR 0007](0007-native-distribution-via-system-packages-and-install-script.md)
(packaging constraints that motivated the default choice),
[ADR 0008](0008-native-primary-docker-supplementary.md)
(positioning of the native channel),
`services/orchestrator/app/knowledge/embeddings.py`
(the protocol + both adapters this ADR records the decision for),
`tools/embedding_benchmark/` (the harness that produced the measurements).

## Context

Phase 3 Slice 1 ([commit 158b008](../../services/orchestrator/app/knowledge/))
shipped the vertical RAG path with a single embedding adapter
(`OllamaEmbeddingAdapter`, model `nomic-embed-text`). At Slice 1 planning
the project owner explicitly asked for a second adapter
(sentence-transformers) plus a head-to-head comparison and a decision
ADR on whether to keep both or pick one.

Slice 1.5's deliverable, then, is empirical: install the alternative
runtime as an **optional** Python extra (so ADR 0007's lean-default-wheels
contract stays intact), build the adapter behind the same
`EmbeddingProvider` protocol, run a side-by-side benchmark on this dev
machine against the Phase 3 Slice 1 seeded corpus, and decide.

Two distinct variables were collapsed into a single A/B test:
- **Runtime**: Ollama HTTP daemon vs in-process Python+torch
- **Model**: `nomic-embed-text` (Ollama-published, 768-dim) vs
  `BAAI/bge-base-en-v1.5` (HuggingFace, 768-dim, MIT)

The decision to collapse them — chosen explicitly by the operator —
is honest about what it sacrifices (we cannot isolate runtime vs model
contributions to any observed delta) and what it gains: a real-world
"which whole stack should I run" answer rather than a more rigorous
but less actionable single-axis comparison.

## Measurements

Hardware: NVIDIA RTX 4050 Laptop GPU (6 GB VRAM, driver 595.71.05, CUDA 13.2).
Software: Python 3.13.13, sentence-transformers 5.x, torch 2.12, Ollama 0.24.0.
Corpus: 9 chunks (the Slice 1 dev seed — 6 shared Wazuh/ATT&CK + 3 ACME
runbook/incident). Queries: 10 representative knowledge questions, 3
trials each, median per query.

### Latency and throughput

| Metric | Ollama (nomic-embed-text) | sentence-transformers (BGE-base-en-v1.5) | Winner |
|---|---|---|---|
| Cold-start (adapter ctor → first embed) | **0.07 s** | 10.12 s | Ollama (model pre-loaded by daemon) |
| Query latency mean | 30.5 ms | 6.0 ms | **ST** (5×) |
| Query latency p50 | 30.7 ms | 5.9 ms | **ST** (5×) |
| Query latency p95 | 33.4 ms | 6.8 ms | **ST** (5×) |
| Corpus embed throughput | 19 ms/chunk | 8 ms/chunk | **ST** (2.4×) |

The Ollama cold-start measurement reflects an **already-warm daemon**
(the model was resident from the Slice 1 seed run earlier in the same
session). On a cold daemon Ollama also pays a model-load cost (we'd
estimate roughly comparable to ST's 10 s). Treat 0.07 s as the steady-
state startup; treat 10 s as ST's per-orchestrator-restart startup.

ST's per-query win is real and consistent: in-process avoids the HTTP
round-trip to Ollama and lets PyTorch keep the model resident in GPU
memory.

### Retrieval quality (qualitative)

Same 9-chunk corpus, same 10 queries, top-5 per adapter. Spot checks
on queries where the corpus contains an exactly-matching chunk:

| Query | Ollama top-1 | ST/BGE top-1 |
|---|---|---|
| "What is Password Guessing in MITRE ATT&CK?" | ACME Brute-force triage (off) | **T1110.001 Password Guessing** (correct) |
| "What is T1078 Valid Accounts?" | T1110.001 Password Guessing (wrong) | **T1078 Valid Accounts** (correct) |
| "Past incident with bastion host and SSH attack" | INC-2026-0042 ACME sweep (correct) | INC-2026-0042 ACME sweep (correct) |
| "When should I block an IP at the perimeter?" | ACME Brute-force triage | **ACME SSH brute-force response** (more direct) |
| "What does Wazuh rule 5712 do?" | Rule 5712 chunk (correct) | Rule 5712 chunk (correct) |

BGE-base wins on **entity-specific lookup queries** (does this technique
ID, does this CVE-style identifier, exist? — and the corpus has a chunk
literally about it). On ambiguous procedural queries both rank in the
right neighbourhood. Sample size is small (10 queries, 9 chunks); the
trend is suggestive, not proof. A larger benchmark over Slice 3's real
corpus is the right place to formalize the precision delta.

### Vector geometry

- BGE-base output is L2-normalized to unit length (the adapter applies
  `normalize_embeddings=True`). Dot product = cosine similarity ∈ [-1, 1].
- nomic-embed-text via Ollama is NOT unit-normalized. Raw dot product
  in the benchmark reached +280–290. Cosine RANKING via pgvector's
  `vector_cosine_ops` index is unaffected (the index normalizes
  internally), but if Wolf code ever switched to a raw-dot-product
  similarity measure, the two adapters would behave very differently.
  Documented for the next reader.

### Operator-facing footprint

| Concern | Ollama path | sentence-transformers path |
|---|---|---|
| New Python wheels in the orchestrator install set | None | torch + sentence-transformers + transformers + tokenizers (~2 GB) |
| ADR 0007 native-packaging story | clean — `wolf-orchestrator.deb` stays small | painful — torch CUDA wheels are huge and per-platform |
| Embedding-model lifecycle | managed by Ollama daemon (idle-evicts; same as the LLM) | always resident in the orchestrator process |
| Swapping the embedding model | `ollama pull <new>` + `EMBEDDING_MODEL=...` env | HuggingFace download on next restart + env var |
| GPU sharing | Ollama mediates between LLM + embedding model load/unload | ST holds VRAM continuously; the LLM and embedder both compete for GPU |
| Available model catalog | Ollama embedding library (smaller, curated) | HuggingFace (every embedding model ever published) |
| Fine-tuning ecosystem (hypothetical, not Phase 3) | harder | easier |

## Decision

**Keep both adapters. Default to Ollama-hosted; expose
sentence-transformers as opt-in via the `embeddings-local` optional
extra and `EMBEDDING_PROVIDER=sentence-transformers` env override.**

Concretely:

- `app.knowledge.embeddings.OllamaEmbeddingAdapter` and
  `SentenceTransformersEmbeddingAdapter` both implement the same
  `EmbeddingProvider` Protocol.
- `make_embedding_provider(settings)` constructs the right one from
  `EMBEDDING_PROVIDER` + `EMBEDDING_MODEL` (config.py).
- sentence-transformers requires `uv sync --extra embeddings-local`
  to install torch + transformers — the default install stays lean
  per ADR 0007.
- `EMBEDDING_PROVIDER=ollama` is the default in `Settings`; the
  default install set has zero torch wheels.

### Why default Ollama

- **ADR 0007 packaging constraint is load-bearing for the native
  delivery channel.** Adding torch to the default wheel set makes
  the `.deb` / `.rpm` story dramatically harder and the install
  larger by ~2 GB. Slice 1.5 must not undo ADR 0007.
- **Symmetry with the LLM stack.** Operators already manage Ollama
  for the chat model; managing one more Ollama model is the same
  pattern, not a new pattern.
- **Steady-state startup is faster** (0.07 s vs 10 s) when the
  daemon is already running. For the dev hot-reload loop and for
  production where restarts happen rarely, that matters more than
  per-query latency.
- **The per-query latency gap (30 ms → 6 ms) is small in absolute
  terms.** RAG retrieval is bottlenecked by pgvector's HNSW scan +
  the model-generation step, not by the 24-ms embedding-call
  delta. A 24-ms saving on a 60-second chat answer is not material.

### Why keep ST as opt-in

- **Retrieval precision on entity lookups looked better for BGE.**
  Not conclusive on 10 queries, but worth keeping the option open
  for operators who run a knowledge-heavy workload and want to
  benchmark on their own corpus.
- **In-process throughput** (2.4× for corpus embed) matters at
  ingestion time — Slice 3's Wazuh-docs / ATT&CK scrape will embed
  thousands of chunks at once. An operator running a large
  re-embedding migration can flip `EMBEDDING_PROVIDER=
  sentence-transformers` for the duration, then flip back.
- **Future fine-tuning** on Wolf-specific terminology is structurally
  easier via the HuggingFace path. Not Phase 3 scope, but the door
  stays open.

### Why a single env var is enough

`EMBEDDING_PROVIDER` selects the adapter; `EMBEDDING_MODEL` selects
the model name within that provider. No new wire surface, no new
config file, no per-tenant variant (intentionally — at this phase
the embedding choice is a deployment-wide decision; per-tenant
embedding would require parallel `embedding_model` columns or
parallel chunk stores, which is over-engineering for Phase 3).

### A model swap is NOT free

`KnowledgeChunk.embedding_model` is stamped on every row. Flipping
`EMBEDDING_PROVIDER` from `ollama` to `sentence-transformers`
without re-embedding the existing corpus will silently degrade
retrieval — the query is embedded by BGE, the stored vectors come
from nomic. Mitigation: a future `wolf reembed` CLI (or a Slice 3
migration helper) that re-embeds all rows whose `embedding_model`
differs from the active provider's. Out of Slice 1.5 scope;
documented here so the gap is recorded.

## Alternatives considered

- **Pick sentence-transformers as the default.** Rejected on ADR
  0007 packaging grounds. The retrieval-precision edge isn't large
  enough to outweigh ~2 GB of mandatory torch wheels in the
  default install.
- **Pick Ollama as the only adapter, drop ST entirely.** Rejected
  per the operator's explicit Slice 1 request. The ST adapter also
  has real future utility (high-throughput bulk re-embedding, easier
  fine-tuning) that's worth keeping reachable.
- **Build both as full first-class peers (ST in default install).**
  Rejected — fights ADR 0007. Optional extras are the right
  mechanism for "supported but not always installed."
- **Use fastembed (ONNX runtime) instead of sentence-transformers
  for the in-process path.** Reasonable; not investigated this
  slice. The ~150 MB ONNX install vs ~2 GB torch install would
  weaken the "torch is too heavy" argument. A future ADR could
  swap the ST adapter for a fastembed one if anyone is motivated
  to do that work. The protocol stays the same.
- **Compare same-model on both runtimes (nomic-embed-text via
  Ollama AND via HuggingFace) to isolate runtime from model.**
  Originally recommended; the operator chose the cross-stack
  comparison instead. Documented above as the explicit trade.

## Consequences

- **`pyproject.toml` gains `[project.optional-dependencies]
  embeddings-local`** with `sentence-transformers>=3.0` and
  `torch>=2.4`. Default `uv sync` is unchanged.
- **`Settings` gains three fields**: `embedding_provider`,
  `embedding_model`, `embedding_dimension`. All default to the
  Ollama+nomic path. The dimension is enforced by the adapter on
  embed and matches `knowledge_chunks.embedding`'s 768-dim column.
- **`chat.py` and `seed_dev_knowledge.py` route through
  `make_embedding_provider(settings)`** instead of constructing
  the adapter directly. Operator can flip via env without code change.
- **`tools/embedding_benchmark/`** is a permanent (not throwaway)
  CLI for re-running this comparison whenever the question is
  raised on different hardware or a different corpus.
- **No data migration in this commit.** Slice 1's seeded chunks
  remain embedded by nomic. If you change the default later, you
  must also re-embed; a `wolf reembed` helper is queued as a
  follow-up.
- **The retrieval-precision observation needs validation at
  Slice 3 scale.** 10-query micro-benchmark is suggestive; a real
  evaluation belongs on the real Wazuh-docs + ATT&CK corpus once
  Slice 3 lands.
- **Rollback.** This commitment is reversible. Either adapter can
  be removed in a future ADR by deleting the class + the env
  branch in `make_embedding_provider`. The protocol absorbs the
  change.

## Addendum (2026-07-11) — MRL dimensions + instruction-aware queries (qwen3-embedding wiring)

The operator pulled `qwen3-embedding:latest` (the 8B build) and asked for it
as a configurable option. Capability check before wiring (`ollama show` +
live probes):

- **Native dimension 4096** — 5.3× the fixed 768-dim pgvector column; but the
  Qwen3-Embedding family is **MRL-trained** (Matryoshka), officially
  supporting user-defined output dimensions with truncate+renormalize.
- **Context 40960 tokens** — no chunk-length failure mode (contrast the aux
  v2-moe's 512-token window that forced the best-effort NULL-v2 path).
- **Instruction-aware asymmetric retrieval** — queries want an instruct
  prefix; documents embed raw (~1–5% retrieval quality loss without it per
  the model card).
- **Probed live**: Ollama's modern `/api/embed` honours `dimensions: 768`
  (returns 768-dim L2-normalized vectors) and batched `input`; the legacy
  single-input `/api/embeddings` Wolf used until now always returns the
  native 4096 and cannot truncate.

Decision — wire the CAPABILITY, not the model:

1. **`OllamaEmbeddingAdapter` moves to `/api/embed`** (the docstring's
   "once Ollama exposes one" future arrived): batched input with a 32-input
   sub-batch cap (bounds single-request latency on slow embedders),
   injectable transport for hermetic tests.
2. **`EMBEDDING_REQUEST_DIMENSIONS[_AUX]`** (default 0 = never sent): opt-in
   server-side MRL truncation. Explicitly opt-in because blind truncation of
   a NON-MRL model corrupts the geometry — the dimension-mismatch refusal
   names the knob for the MRL case. sentence-transformers path maps it to
   the library's own `truncate_dim`.
3. **`EMBEDDING_QUERY_PREFIX[_AUX]`** (default "") + `embed_query` on the
   provider protocol: queries prefixed, passages raw. The store's `search()`
   now routes queries through `embed_query` — which also ACTIVATES the ST
   adapter's existing BGE prefix that was previously dead code (search()
   always called plain `embed`).
4. Primary and aux each carry their own knobs (a native-768 nomic primary
   next to an MRL qwen3-embedding aux is a valid 3-leg config).

Cosine distance everywhere (HNSW `vector_cosine_ops`) makes normalization
moot for ranking; Ollama returns unit vectors anyway (probed). The
`embedding_model` per-chunk stamp + `management/reembed.py` (`--apply`,
`--aux`) remain the planned-re-embed contract — flipping the primary model
without a re-embed still degrades exactly as this ADR always warned.

Defaults unchanged (nomic-embed-text, no knobs). Recipes + the 6 GB-GPU VRAM
trade-off (the 8B embedder cannot sit resident next to qwen3:8b chat) are in
`.env.example` and `docs/reference/model-performance-tuning.md`.
