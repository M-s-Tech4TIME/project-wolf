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
