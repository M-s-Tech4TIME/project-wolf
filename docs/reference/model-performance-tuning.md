# Model performance & hardware tuning

**Purpose.** Wolf runs its chat model and its grounding judge on a local model
server (Ollama by default). How *fast* and how *accurately* Wolf responds is a
three-way trade-off — **speed ↔ quality ↔ context capacity** — bounded by your
**GPU VRAM**. This guide documents every lever that moves that trade-off, what
each costs, **when to use which**, and how to apply *and reverse* each one. All
levers are reversible; none require a code change.

> **The one rule to internalise:** Wolf's chat prompt (system prompt + the full
> 14-tool catalog with JSON schemas) is **~7,200 tokens** *before* any
> conversation history or tool results. The context window (`num_ctx`) must
> comfortably exceed that or Ollama silently truncates the tool definitions and
> the model answers *"I have no such tool"* with zero tool calls. So the floor
> for `num_ctx` is **not** negotiable below ~8K; the tuning is about how much
> headroom you buy and what it costs in VRAM/speed. (See ADR 0026, and the
> `ollama-num-ctx-tool-truncation` finding.)

---

## Where each lever lives

Two config surfaces — know which is which:

| Lever | Set in | Applies to |
|-------|--------|-----------|
| `OLLAMA_NUM_CTX` | **Wolf `.env`** (`config.py: ollama_num_ctx`) | context window for chat + judge |
| `DEFAULT_MODEL_ID` / `GROUNDING_JUDGE_MODEL_ID` | **Wolf `.env`** | which model serves chat vs judge (posture) |
| `GROUNDING_MODE` | **Wolf `.env`** | *when* the judge runs vs the answer stream (ADR 0026) |
| `OLLAMA_FLASH_ATTENTION` | **Ollama service** (systemd drop-in) | enables KV-cache quantization; small speed/mem win |
| `OLLAMA_KV_CACHE_TYPE` | **Ollama service** | KV-cache precision: `f16` / `q8_0` / `q4_0` |
| `OLLAMA_NUM_PARALLEL` | **Ollama service** | how many model calls run **truly** concurrently |

Wolf-side knobs take effect on a `wolf-server` restart. Ollama-side knobs take
effect on an `ollama` service restart (they are **global** to that Ollama host —
they affect every model it serves). Wolf reconnects to Ollama automatically; no
`wolf-server` restart is needed for an Ollama-side change.

---

## The levers

### 1. `OLLAMA_NUM_CTX` — context window (Wolf `.env`)

The number of tokens the model holds "on the desk" at once. Ollama's built-in
default is **4096** — too small for Wolf's tool catalog (it truncates → dropped
tools). Larger `num_ctx` = more headroom for multi-step tool loops **but** a
larger KV-cache (more VRAM), which on a small GPU spills the model to CPU and
slows generation.

- **Floor:** ~8192 (fits the ~7.2K tool prompt with thin headroom).
- **Comfortable:** 12288–16384 (safe for multi-step loops with large tool
  results).
- **Trade-off:** every +4K of context enlarges the KV-cache; on a VRAM-tight GPU
  that is a direct, measurable generation slowdown (see the benchmark below).
- **Reverse:** set it back and restart `wolf-server`.

### 2. KV-cache quantization — `OLLAMA_FLASH_ATTENTION` + `OLLAMA_KV_CACHE_TYPE` (Ollama service)

Stores the attention KV-cache at lower precision so a large `num_ctx` costs less
VRAM — the way to **keep** a big context *and* fit on a smaller GPU. Requires
Flash Attention.

| `OLLAMA_KV_CACHE_TYPE` | KV-cache memory | Quality impact | Use when |
|------------------------|-----------------|----------------|----------|
| `f16` *(default)* | 100% (baseline) | none | you have VRAM to spare (enterprise) |
| `q8_0` | ~50% | **negligible** (near-lossless) | VRAM-tight but you want full quality — the safe pick |
| `q4_0` | ~25% | measurable (reasoning / tool-args / long context) | only if `q8_0` still doesn't fit |

- **Trade-off:** a tiny per-step quant/dequant compute cost (more than offset when
  it stops CPU offload); `q4_0` additionally trades real accuracy — risky for the
  tool-calling + grounding-judge workload, so prefer `q8_0`.
- **Scope caveat:** global to the Ollama host — affects chat *and* judge (and any
  other model on that Ollama). Embeddings are unaffected (no generative KV-cache).
- **Apply / reverse:** see [Applying Ollama-side knobs](#applying--reversing-ollama-side-knobs).

### 3. Model posture — `DEFAULT_MODEL_ID` / `GROUNDING_JUDGE_MODEL_ID` (Wolf `.env`)

Which model serves chat vs the judge (ADR 0024).

- **Unified** (`DEFAULT_MODEL_ID=qwen3:8b`, judge `qwen3:8b`) — best answer
  quality; one model stays loaded (no chat↔judge reload). Heavier on VRAM.
- **Split** (`DEFAULT_MODEL_ID=qwen3:4b`, `GROUNDING_JUDGE_MODEL_ID=qwen3:8b`) —
  a **much faster, lighter chat model** (4b fits fully on a 6 GB GPU even at 16K
  context) while the stronger 8b still judges. **Trade-off:** slightly weaker chat
  answers; and if VRAM can't hold both, Ollama reloads between chat and judge
  (a few seconds per turn) — mitigated by `deferred` grounding.
- **Reverse:** flip the IDs, restart `wolf-server`.

### 4. `OLLAMA_NUM_PARALLEL` — real concurrency (Ollama service)

How many model calls Ollama serves **simultaneously** (continuous batching).
Wolf's application layer already runs every request's chat + grounding as
independent concurrent tasks (see ADR 0026 addendum — grounding is *never*
queued); `OLLAMA_NUM_PARALLEL` is the *infrastructure* governor of how many of
those actually execute at once.

- **Unset / 1** — calls serialise at the model server (fine for a single-GPU dev
  box; more than 1 would thrash VRAM there).
- **≥ 2** — genuine parallel chat + grounding across users/orgs/threads (the MSSP
  target); each concurrent slot needs its own KV-cache → **plan VRAM
  accordingly**. This is also what makes `GROUNDING_MODE=incremental` a real
  wall-clock win.
- **Reverse:** unset it, restart `ollama`.

### 5. `GROUNDING_MODE` — when the judge runs (Wolf `.env`)

`blocking` / `deferred` / `incremental` — fully documented in
[ADR 0026](../decisions/0026-grounding-execution-modes.md). Not a hardware lever
per se, but `deferred` (the live default) hides judge latency by settling the
answer first, which makes a constrained GPU *feel* far faster.

---

## Measured benchmark (dev reference)

`qwen3:8b`, single **6 GB RTX 4050 Laptop GPU**, `KV_CACHE_TYPE=f16`, two runs
(reproducible ±0.2 tok/s). This is *the* data behind the guidance above:

| `num_ctx` | f16 (default) | **q8_0 KV-cache** | Fits tool catalog? |
|-----------|---------------|-------------------|--------------------|
| 4096 | 19.3 tok/s (26%/74% CPU/GPU) | 20.2 tok/s (25%/75%) | ❌ truncates tools |
| 8192 | 17.2 tok/s (32%/68%) | 18.8 tok/s (28%/72%) | ✅ tight headroom |
| 12288 | 15.6 tok/s (38%/62%) | 17.4 tok/s (32%/68%) | ✅ safe |
| 16384 | 14.4 tok/s (45%/55%) | **16.4 tok/s (36%/64%)** | ✅ generous |

The q8_0 column (measured 2026-07-02, same host, `OLLAMA_FLASH_ATTENTION=1` +
`OLLAMA_KV_CACHE_TYPE=q8_0`) shows the recovery in practice: at the full 16K
context the model shrinks 7.8 → 6.7 GB and generation improves **+14%** — and
every context size gets faster (flash attention helps across the board). The
residual CPU offload at 16K is the model *weights* themselves (an 8B model
doesn't fully fit in 6 GB even at 4096 — note the 25% floor), so on this card
q8_0 + 16384 is the practical optimum without trading accuracy (`q4_0`) or
model size (split posture).

Reproduce it yourself:

```bash
for CTX in 4096 8192 12288 16384; do
  curl -s http://localhost:11434/api/chat -d '{"model":"qwen3:8b","messages":[],"keep_alive":0}' >/dev/null; sleep 2
  R=$(curl -s http://localhost:11434/api/chat -d "{\"model\":\"qwen3:8b\",\"messages\":[{\"role\":\"user\",\"content\":\"Write two sentences about network security.\"}],\"stream\":false,\"options\":{\"num_ctx\":$CTX}}")
  EC=$(echo "$R" | python3 -c "import sys,json;print(json.load(sys.stdin).get('eval_count',0))")
  ED=$(echo "$R" | python3 -c "import sys,json;print(json.load(sys.stdin).get('eval_duration',1))")
  printf "num_ctx=%-6s %5.1f tok/s  %s\n" "$CTX" "$(python3 -c "print($EC/($ED/1e9))")" "$(ollama ps | awk 'NR==2{print $4,$5}')"
done
```

---

## Scenario recipes — "when to use what"

### A. Constrained dev GPU (~6–8 GB, e.g. RTX 4050 Laptop) — our dev box

Goal: fast, smooth streaming while keeping tools working. In priority order:

1. **KV-cache quantization `q8_0`** + keep `OLLAMA_NUM_CTX=16384` — full context
   correctness, ~half the KV-cache VRAM, most of the speed back, negligible
   quality cost. **Recommended first move.**
2. If step 1 still spills to CPU: drop to `OLLAMA_KV_CACHE_TYPE=q4_0`, **or** keep
   `q8_0` and lower `OLLAMA_NUM_CTX` to 12288.
3. If you want the *fastest* chat and can accept slightly weaker answers: **split
   posture** — `DEFAULT_MODEL_ID=qwen3:4b` (fits fully on-GPU even at 16K),
   judge stays `qwen3:8b`.
4. Keep `GROUNDING_MODE=deferred` (hides judge latency) and `OLLAMA_NUM_PARALLEL`
   unset (a 6 GB card can't hold two concurrent 8b contexts).

### B. Mid-range GPU (10–16 GB)

- `OLLAMA_NUM_CTX=16384`, `KV_CACHE_TYPE=f16` (or `q8_0` to free room for
  concurrency) — full quality, model fits on-GPU.
- `OLLAMA_NUM_PARALLEL=2` for some real concurrency; watch VRAM headroom.
- Unified `qwen3:8b` posture; `deferred` or try `incremental`.

### C. Enterprise / on-prem (24 GB+ single or multi-GPU) — the design target

- `OLLAMA_NUM_CTX=16384`+ (or higher), `KV_CACHE_TYPE=f16` — **no quantization
  needed; zero quality trade-off.**
- `OLLAMA_NUM_PARALLEL=4–8+` (and/or multiple Ollama replicas / a hosted
  OpenAI-compatible endpoint) → true parallel chat + grounding for many
  users/orgs at once (the MSSP goal — ADR 0026 addendum). Provision VRAM per
  concurrent slot.
- Unified `qwen3:8b` (or a larger model); `incremental` grounding becomes a real
  wall-clock win.

### D. CPU-only / no GPU

- Expect slow generation regardless. Use the **split posture** (4b chat), keep
  `num_ctx` at the ~8192 floor, `q8_0` KV-cache, `deferred` grounding. Functional,
  not fast — a stopgap, not a deployment target.

---

## Applying / reversing Ollama-side knobs

`OLLAMA_FLASH_ATTENTION`, `OLLAMA_KV_CACHE_TYPE`, and `OLLAMA_NUM_PARALLEL` are
set on the **Ollama systemd service** via a drop-in (adds env; does not edit the
unit file). Requires `sudo`.

**Apply** (example: `q8_0` KV-cache + 2-way parallel):

```bash
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo tee /etc/systemd/system/ollama.service.d/wolf-tuning.conf > /dev/null <<'EOF'
[Service]
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
# Environment="OLLAMA_NUM_PARALLEL=2"   # uncomment on a GPU with VRAM to spare
EOF
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

**Verify** the model's CPU/GPU split and loaded context:

```bash
ollama ps          # PROCESSOR column: e.g. "100% GPU" (good) vs "45%/55% CPU/GPU"
nvidia-smi --query-gpu=memory.total,memory.used,memory.free --format=csv
```

**Reverse** (back to Ollama defaults — full `f16`, no forced parallelism):

```bash
sudo rm /etc/systemd/system/ollama.service.d/wolf-tuning.conf
sudo systemctl daemon-reload && sudo systemctl restart ollama
```

---

## Scaling up — what happens on bigger hardware / bigger models

When the deployment moves to a stronger GPU (≥12 GB) and/or a bigger model
(14B/32B-class, 256K-context-capable), **the knobs stay exactly the same — only
the values change, and their *direction reverses***: on scarce VRAM the settings
exist to *squeeze in*; on abundant VRAM the same settings *spend* the headroom on
quality and concurrency. Plus one registry step (below) so Wolf drives the new
model at its full capability.

### The arithmetic that governs everything

```
VRAM needed = model weights + KV-cache (num_ctx × per-token cost × parallel slots)
```

- **Weights (quantized, Q4-class):** 8B ≈ 5.2 GB · 14B ≈ 9 GB · 32B ≈ **20 GB**.
  So **12 GB VRAM is 14B-class territory** — a 32B model does *not* fit on 12 GB
  (it would spill ~50% to CPU and stream *slower* than a fully-resident 8B).
  32B wants a **24 GB** card (RTX 3090/4090-class).
- **KV-cache** scales with context length *and* model size (~144 KB/token f16
  on 8B; ~256 KB/token on 32B-class). Reality check on "256K context":

  | Context | 8B KV-cache (f16) | 32B KV-cache (f16) |
  |---------|-------------------|---------------------|
  | 16K | ~2.3 GB | ~4 GB |
  | 32K | ~4.6 GB | ~8 GB |
  | 256K | ~37 GB | **~64 GB** |

  A model *supporting* 256K ≠ *running* it at 256K — filling 256K on a 32B
  model costs ~64 GB of KV-cache alone (multi-GPU/server-class). **Set
  `OLLAMA_NUM_CTX` to what Wolf's workload needs, not the model's maximum**:
  the tool prompt is ~7.2K, so 16K–32K stays the practical sweet spot even on
  huge hardware; headroom above that buys longer conversations and bigger tool
  results, not better answers.

### How each setting translates

| Setting | 6 GB / 8B (today) | 12 GB / 14B-class | 24 GB+ / 32B |
|---------|-------------------|-------------------|--------------|
| `OLLAMA_NUM_CTX` | 16384 | 16384–32768 | 32768+ if wanted; same ~8K floor |
| `OLLAMA_KV_CACHE_TYPE` | `q8_0` (VRAM rescue) | `f16` — delete the drop-in, zero quality trade (or keep `q8_0` to buy concurrency) | `f16`; `q8_0` only to buy *capacity* (more slots / longer ctx) |
| `OLLAMA_NUM_PARALLEL` | unset | 2 — real concurrency begins | 4–8+ — the MSSP posture; each slot costs one KV-cache |
| Model posture | unified 8b | unified 14b, or 4b+8b co-resident (split with zero swap) | unified 32B chat *and* judge |
| `GROUNDING_MODE` | `deferred` | `deferred` / `incremental` | `incremental` becomes a genuine wall-clock win |

### The one step that isn't a config value: register the model's capability

Wolf drives each model per its **capability registry** entry
(`models/interface.py` `KNOWN_MODELS` — step budget, strategy, tool-calling
trust). An **unknown** `DEFAULT_MODEL_ID` deliberately falls back to a
*conservative* descriptor (`native_tool_calling=none`, 3 steps, pipeline
strategy) — it works, but you'd be driving the big model in first gear. On
upgrade day, run the **capability probe** and register the new model's measured
profile (the established pattern — ADR 0002/0009/0010/0011; a 32B-class model
plausibly earns `reasoning_tier=strong`, a larger step budget, and the
`frontier` strategy → deeper autonomous investigations, not just faster
tokens). The full model-choice procedure lives in
`docs/14-model-recommendations.md` §Environment-change playbook.

### Upgrade-day checklist (suggested)

1. Probe + register the new model in `KNOWN_MODELS` (doc 14's playbook).
2. `DEFAULT_MODEL_ID` / `GROUNDING_JUDGE_MODEL_ID` → the new model.
3. `OLLAMA_NUM_CTX` → 16384–32768 per the table above.
4. Remove the KV-quant drop-in (back to `f16`) unless spending it on capacity.
5. Set `OLLAMA_NUM_PARALLEL` per VRAM-after-weights ÷ per-slot KV-cache.
6. Re-run the benchmark in this doc; consider `GROUNDING_MODE=incremental`.

Nothing in Wolf's *architecture* changes — that is by design (model
abstraction ADR 0030; app-layer concurrency unbounded, ADR 0026 addendum).
Post-6.10 these become Wolf config-plane entries (GUI/CLI/file), no rituals.

## Embedding model (knowledge retrieval)

The retrieval embedder is a separate lever from chat/judge, and since
ADR 0033 (2026-07-11) it is FULLY configurable — model, provider, **column
dimension**, MRL truncation, task prefixes (document + query), context
window, and input char cap, each independently for the primary and the
optional aux embedder. The pgvector columns follow `EMBEDDING_DIMENSION` /
`EMBEDDING_DIMENSION_AUX`; two operator tools keep everything consistent:

```bash
cd services/server && set -a && source ../../.env && set +a
# After a WIDTH change — re-types the columns, re-embeds, rebuilds HNSW
# (report-only without --apply; resumable after a crash):
uv run python -m wolf_server.management.embedding_schema --apply
# After a MODEL change (stamp-mismatch detection, idempotent):
uv run python -m wolf_server.management.reembed --apply           # + --aux
# After a GEOMETRY-only change (new prefix / MRL width / num_ctx —
# model id unchanged):
uv run python -m wolf_server.management.reembed --apply --force   # + --aux
```

**Recipe A — nomic combo** (the default shape: `nomic-embed-text` primary +
`nomic-embed-text-v2-moe` aux, 768-dim, HNSW-indexed). Both nomic models
train with task prefixes — set `EMBEDDING_DOCUMENT_PREFIX="search_document: "`
and `EMBEDDING_QUERY_PREFIX="search_query: "` (+ the `_AUX` twins) for best
retrieval, then `reembed --apply --force` (+ `--aux --force`). v2-moe's
512-token window is guarded by the 1800-char aux cap (default).

**Recipe B — qwen3-embedding** (MRL-trained; native 4096-dim, context 40960,
instruction-aware — probed live):

```bash
EMBEDDING_MODEL=qwen3-embedding:latest
EMBEDDING_DIMENSION=768               # or 1024 / 2000 / 4096 — see below
EMBEDDING_REQUEST_DIMENSIONS=768      # server-side MRL truncate+renormalize
EMBEDDING_NUM_CTX=40960
EMBEDDING_QUERY_PREFIX="Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: "
```

Dimension guidance: **768/1024** = MRL sweet spot, HNSW stays (recommended);
**2000** = pgvector's max HNSW-indexable width; **4096** = full native
fidelity but NO ANN index (HNSW caps at 2000 dims) — search runs exact:
perfect recall, linear cost, fine at ~5K chunks, revisit at 100K+. Any
width other than the live one requires `embedding_schema --apply`
(maintenance window: vector legs are empty until the re-embed finishes;
BM25 keeps answering).

Or keep the nomic primary and run qwen as the ADR 0014 third RRF leg
(`EMBEDDING_MODEL_AUX` + `_AUX` twins, `EMBEDDING_DIMENSION_AUX` may differ
from the primary width; backfill with `reembed --aux --apply`) — no primary
re-embed.

**VRAM trade-off**: `qwen3-embedding:latest` is the 8B build (~4.7 GB). On a
6 GB card it cannot sit resident next to `qwen3:8b` chat (~5.2 GB) — Ollama
swaps models per request, so every chat→search→chat cycle pays a model
reload (seconds). Small-GPU guidance: stay on the nomic combo (274 MB +
957 MB), or pull a smaller MRL variant (`qwen3-embedding:0.6b` ≈ 639 MB)
with the same knobs. On ≥12 GB both stay resident and the swap cost
disappears.

## Quick decision guide

- **Streaming feels slow + you're VRAM-tight** → KV-cache `q8_0` first; then
  lower `num_ctx` or split posture.
- **"No such tool" / 0 tool calls** → `num_ctx` too low; raise it (never below the
  ~8K floor).
- **Want many users grounded in parallel (MSSP)** → `OLLAMA_NUM_PARALLEL≥2` +
  matching VRAM (or replicas); Wolf never queues grounding itself.
- **Answers feel weak** → unified `qwen3:8b` posture + `f16` KV-cache (avoid
  `q4_0`).
- **Enterprise hardware** → change nothing for VRAM's sake; scale
  `OLLAMA_NUM_PARALLEL` + VRAM for throughput.

## Related

- ADR 0024 — model posture (unified vs split), the first runtime-perf setting.
- ADR 0026 (+ 2026-07-01 addendum) — grounding execution modes + the
  per-request-parallel concurrency model.
- ADR 0031 — provider failover (hosted primary → local Ollama).
- `docs/13-system-requirements.md`, `docs/14-model-recommendations.md`,
  `docs/15-supported-model-matrix.md`.
- Memory: `ollama-num-ctx-tool-truncation`, `grounding-concurrency-model`.
