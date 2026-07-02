# 14 — Model Recommendations

This document recommends which models Wolf should support, default to, and test
against. It is written as of **May 2026** with awareness of the project's
**current development state** (Phase 2, the model abstraction layer in
`services/orchestrator/app/models/` already built and operational).

It is intended to *complement* what is already running, not to override it.

## TL;DR — the direct answers

**What is the best agentic model for Wolf right now?**

- **On CPU-only hardware (your current dev environment):** **Qwen 3 4B**
  (Apache 2.0). Best agentic model that runs comfortably on 16 GB RAM
  without a GPU. Most reliable tool-calling in its size class.
- **On modest GPU hardware (6-8 GB VRAM):** **Qwen 3 8B** (Apache 2.0).
  The expected default for "first real deployment."
- **On strong GPU hardware (24 GB+ VRAM):** **GLM-5.1** (MIT). The
  strongest open agentic model that currently exists. Designed for
  long-horizon, multi-tool-call investigations — exactly Wolf's workload.
- **For operators who choose hosted APIs:** **Claude (Opus 4.7)** is the
  strongest agentic model in the closed-source space; alternatives are
  GPT-4o and Gemini 2.5 Pro. Wolf supports all three.

**What is currently running in your dev environment?**

Llama 3.2 3B. Keep using it for development — it works on your hardware. But
it should not be Wolf's *recommended* default for shipping (license issue;
see "License filter" below). Replace with Qwen 3 4B before external users.

**What changes if hardware improves?**

See "Environment-change playbook" later in this doc. The short version: any
hardware change triggers a re-probe and possibly a default-model switch. The
playbook is mechanical — same five steps every time.

## License filter — applied first

Wolf's core principle says fully open-source, no restrictions. The model layer
must hold the same line. Filtering the May 2026 landscape by **truly open
licenses (Apache 2.0 / MIT)**:

| Model family | License | Verdict |
|---|---|---|
| Qwen 3.x (Alibaba) | Apache 2.0 | ✅ Recommended |
| Gemma 3.x (Google) | Apache 2.0 | ✅ Recommended |
| GLM-5.1 (Zhipu) | MIT | ✅ Recommended |
| DeepSeek V4 (DeepSeek) | MIT | ✅ Recommended |
| Kimi K2.6 (Moonshot) | MIT | ✅ Recommended |
| Mistral Small 4 | Apache 2.0 | ✅ Recommended |
| **Llama 3.x / 4 (Meta)** | **Llama Community License** | ⚠️ **Not Wolf's default** |
| Hosted APIs (Claude, GPT, Gemini) | proprietary | ✅ Supported as a *choice* — never required |

### Why Llama is not Wolf's default

The Llama Community License carries a 700M monthly-active-user cap and naming
requirements. For most Wolf operators this is non-binding in practice, but the
license is not OSI-open. It would not pass scrutiny from an MSSP's legal
review, and shipping Wolf with a Llama default contradicts the project's own
stated principles.

**For your current development work, llama3.2:latest is fine** — it's a
sensible local-testing model on CPU-only hardware. The only change is to
update Wolf's *recommended* default (what new operators see in docs and config
examples) to an Apache-licensed model of similar size before Wolf has external
users.

## Hardware-matched recommendations

These map to the four runtime profiles in `13-system-requirements.md`.

| Profile | Hardware | Recommended model | Wolf strategy | Notes |
|---|---|---|---|---|
| **A — CPU-only / constrained** | 8 cores, 16-32 GB RAM, no GPU | **Qwen 3 4B** *or* **Gemma 3 4B** | `basic` | The "your current dev box" tier. Apache 2.0. Replaces Llama 3.2 as the recommended default. |
| **B — Modest GPU** | 8-12 cores, 32 GB RAM, 6-8 GB VRAM | **Qwen 3 8B** | `mid` | The expected "first real deployment" tier. Strong tool-calling, broad ecosystem support. |
| **C — Strong GPU / serious self-host** | 12+ cores, 64 GB RAM, 24 GB VRAM | **Qwen 3.6 27B** or **GLM-5.1** (via inference API) | `mid` to `frontier` | MSSP, detection-engineering-heavy use. |
| **D — Hosted API** | None — provider runs it | **Claude / GPT / Gemini** (operator's choice) | `frontier` | For operators who choose to pay. Wolf supports them; never requires them. |

## What this changes in your current code (small)

The abstraction layer in `services/orchestrator/app/models/` already does the
right thing. The static `KNOWN_MODELS` dict in `interface.py` needs two small
updates:

1. **Add entries for the recommended Apache-licensed models.** At minimum:
   `qwen3:4b`, `gemma3:4b`, `qwen3:8b`, plus `glm-5.1` for the premium tier.
   Each entry needs the capability descriptor fields (`context_window`,
   `native_tool_calling`, `reasoning_tier`, `recommended_strategy`).
2. **Keep `llama3.2` as a known model** for backward compatibility and
   testing, but flag it in the descriptor (e.g. add a `license_class` field
   marking it as restricted) so any UI showing model choices can surface the
   distinction.

Default selection (`DEFAULT_MODEL_PROVIDER` / `DEFAULT_MODEL_ID` in
`config.py`) does **not** need to change for your dev work right now. Change
it when you're ready to switch to an Apache-licensed model — `qwen3:4b` is the
natural successor to your current `llama3.2:latest`, similar footprint on the
same hardware.

## Run the capability probe before committing to any default

You built `tools/model_probe/` in Phase 1 but haven't run it against your live
Ollama yet. **Do this next**, on your current host:

```bash
uv run python -m tools.model_probe --provider ollama --model llama3.2
uv run python -m tools.model_probe --provider ollama --model qwen3:4b
uv run python -m tools.model_probe --provider ollama --model gemma3:4b
```

Three reasons this matters now:

1. **It tells you what your specific quantization of each model can
   actually do.** The static `KNOWN_MODELS` entries are estimates. The probe
   measures.
2. **It may revise the recommended strategy.** A model that the static
   registry calls `mid` might benchmark as `basic` on your CPU-only setup, or
   vice versa. The orchestrator should pick a strategy based on measured
   capability.
3. **It gives you an empirical comparison** between your current default
   (Llama 3.2) and the proposed replacements, so the switch is grounded in
   data rather than license argument alone.

Capture the probe output in `docs/decisions/` as an ADR — that's the right
place for "we chose model X because the probe said Y."

## CI matrix — proving model-agnosticism

To make the "any model works" promise true rather than aspirational, CI must
test against multiple providers regularly. Recommended matrix once the model
choice settles:

| CI job | Provider | Model | Purpose |
|---|---|---|---|
| `test-local` | Ollama | `qwen3:4b` (or chosen Apache default) | Proves CPU-only deployment works. Required to pass on every PR. |
| `test-mid` | Ollama | `qwen3:8b` | Proves the mid-tier strategy. Run nightly. |
| `test-hosted` | Anthropic | `claude-opus-4-7` | Proves the frontier-tier strategy and the Anthropic adapter. Run nightly. |
| `test-openai` | OpenAI | `gpt-4o` (or current) | Proves the OpenAI adapter. Run nightly. |

The `test-local` job is the hard gate. If a PR breaks it, the merge is
blocked — that's how Wolf keeps the local-model path real instead of theater.

## Environment-change playbook — what to do when hardware changes

The recommended model **must** track the hardware. A hardcoded default model
fails the moment the deployment moves from a CPU-only laptop to a GPU server.
This section is the operator's checklist for every realistic environment
change.

The principle: **never assume; always probe.** When hardware changes, run
the capability probe against candidate models on the new hardware before
changing any defaults.

> This playbook covers the **model choice**. The companion **settings** side —
> how `OLLAMA_NUM_CTX` / KV-cache quantization / `OLLAMA_NUM_PARALLEL` / posture
> translate across hardware tiers (the VRAM arithmetic, the 256K-context reality
> check, and the upgrade-day checklist) — lives in
> `docs/reference/model-performance-tuning.md` §Scaling up.

### Trigger events that require re-evaluating the model choice

Any one of these means it's time to re-run the probe and reconsider the
default:

1. **A GPU is added to the host** (or removed).
2. **VRAM increases or decreases** (GPU swap, multi-GPU setup, smaller card).
3. **System RAM changes materially** (e.g. 16 GB → 64 GB).
4. **The deployment moves to different infrastructure** (laptop → server,
   single-host → cluster, on-prem → cloud).
5. **A new model release** in the recommended families (Qwen 4, GLM-6, etc.).
6. **A organization onboards with different capability needs** (e.g. an MSSP adds
   a customer who needs deeper investigation autonomy).

### What to do when each one happens

| Hardware change | Likely new tier | Candidate models to test | What to do |
|---|---|---|---|
| **Add a 6-8 GB GPU** (e.g. RTX 3060/4060) | Profile A → B | `qwen3:8b`, `mistral:7b` | Pull each, run probe, switch default to the winner. Expect `mid` strategy. |
| **Add a 12-16 GB GPU** (e.g. RTX 4070/4080) | Profile B (strong) | `qwen3:14b`, `gemma3:12b` | Test mid-size models. Expect solid `mid` strategy. |
| **Add a 24 GB GPU** (e.g. RTX 4090/5090) | Profile C | `qwen3.6:27b`, `gemma3:27b`, `glm-5.1:flash` | Test 27B-class models locally. `frontier` strategy becomes viable for some. |
| **Multi-GPU cluster** (48 GB+ aggregate) | Profile C (heavy) | `glm-5.1` (full), `deepseek-v4:flash`, `kimi-k2.6` | The premium open models become deployable locally. |
| **System RAM increases** (CPU-only) | Profile A (upper) | `qwen3:8b` Q4_K_M, `gemma3:12b` Q4_K_M | More RAM allows larger CPU-inference models, though speed remains the constraint. |
| **Move to inference API** (no local GPU needed) | Profile C/D | `glm-5.1` via Together/Fireworks/AceCloud | Switch to a hosted open-model API. No local hardware burden. |
| **Move to hosted closed API** (operator chose to pay) | Profile D | `claude-opus-4-7`, `gpt-4o`, `gemini-2.5-pro` | Best autonomous agent behavior; trade-off is data egress. |

### The mechanical steps on any environment change

This is the playbook. Run it every time:

```bash
# 1. Pull candidate models for the new hardware tier
ollama pull qwen3:4b      # always — baseline
ollama pull qwen3:8b      # if GPU >= 6 GB VRAM
ollama pull qwen3.6:27b   # if GPU >= 24 GB VRAM
ollama pull glm-5.1:flash # if you can run it (large)

# 2. Run the capability probe against each
uv run python -m tools.model_probe --provider ollama --model qwen3:4b
uv run python -m tools.model_probe --provider ollama --model qwen3:8b
# ... repeat for each candidate

# 3. Compare measured capability descriptors
# The probe outputs reasoning_tier, native_tool_calling reliability,
# tokens-per-second, and grounding-discipline score. The winner is the
# one with the strongest tier AND acceptable latency on the hardware.

# 4. Write an ADR documenting the decision
# docs/decisions/0NNN-model-switch-<from>-to-<to>.md
# Include: the trigger, probe results side by side, decision, and rollback path.

# 5. Update DEFAULT_MODEL_ID in services/orchestrator/app/config.py
# Single line change. Commit separately from any other work.

# 6. Re-run the orchestrator integration tests
# Confirm the new model passes the agent-loop test against real Wazuh.

# 7. Update the per-organization default if there are organizations whose model is currently set
# to the old default. Organizations who chose a specific model are untouched.
```

### What CI must enforce regardless of environment

These rules don't change as hardware changes — they enforce the
model-agnostic promise:

- **The local-model CI job (`test-local`) always runs against an Apache or MIT
  model** that fits on the smallest supported profile. As of May 2026, that
  is **Qwen 3 4B**. Update it only when a better Apache/MIT model in the
  4B class arrives.
- **No PR may merge if `test-local` fails.** This is the hard guarantee that
  Wolf works on free, local hardware. Even when production is running on
  hosted Claude, the CPU-local path must keep working.
- **Hosted-model CI jobs are informational, not blocking.** A failure in
  `test-hosted-claude` should warn but not block — it usually means an API
  outage, not a Wolf bug.

### A note on quantization

Every recommended local model assumes **Q4_K_M quantization** unless stated
otherwise. This is the default in Ollama and the sweet spot of size/quality
for most models. If you have spare VRAM or RAM, Q5_K_M or Q6_K give modest
quality gains. Q8_0 is overkill for agentic use; the gain over Q5/Q6 is
minimal and rarely worth the doubled memory.

When you add a GPU, you don't need to re-download models — Ollama uses the
GPU automatically if available. Just confirm with `ollama ps` after a query
that the model is showing GPU offload rather than 100% CPU.

## How "best model" evolves — the maintenance commitment

The open-model landscape resets every quarter. Six months ago the answer was
Llama 3.1 70B; today it's Qwen 3 / GLM-5.1; six months from now it will be
something not yet released.

To keep this document honest:

- **Re-evaluate the recommended default every quarter.** Run the probe against
  the current generation of Apache/MIT models. If something new dominates
  measurably, update the default.
- **Record each evaluation as an ADR in `docs/decisions/`.** Don't lose the
  reasoning trail.
- **Never bet the platform on a specific model.** The whole point of the
  abstraction layer in `services/orchestrator/app/models/` is that the model
  is a swappable component. The recommendations in this document are
  defaults, not architecture.

## The honest summary

You picked Llama 3.2 for development. It was the right call for that
hardware. The only thing to change is the *recommended default for shipping*,
because Llama's license fails Wolf's own "no restrictions" criterion. Replace
it with an Apache-licensed model in the same size class (Qwen 3 4B or
Gemma 3 4B), run the probe to confirm capability on your hardware, and ship.

Everything else in this document is forward-looking: as new models arrive and
as operators deploy on better hardware, the abstraction layer absorbs them.
The platform's safety guarantees do not depend on which model is plugged in.
