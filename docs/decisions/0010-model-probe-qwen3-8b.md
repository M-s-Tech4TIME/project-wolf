# 0010 — Capability probe for `qwen3:8b` on RTX 4050 Laptop GPU

**Date:** 2026-05-24
**Status:** accepted
**Decider:** claude-code (executing the new-machine handoff brief at
`prompts/HANDOFF-NEW-MACHINE.md`)
**Related:** [ADR 0002](0002-model-probe-qwen3-4b.md) (the qwen3:4b
probe this size-up is compared against), [ADR 0006](0006-supported-model-families-commitment.md)
(Qwen 3 sizes 4B/8B/14B/32B are all in the matrix), `docs/14-model-recommendations.md`,
`docs/15-supported-model-matrix.md`, `services/orchestrator/app/models/interface.py`
(KNOWN_MODELS — the static qwen3:8b entry this ADR amends).

## Context

ADR 0006 commits Wolf to natively supporting Qwen 3 at sizes 4B, 8B,
14B, and 32B. The 4B was probed in ADR 0002 (CPU); the larger sizes
were blocked on GPU hardware. The new dev laptop (NVIDIA RTX 4050
Laptop, 6 GB VRAM) is a Profile B tight-end machine per `docs/13`; it
can run qwen3:8b at all but the upper end of context where VRAM
pressure forces partial CPU offload.

The handoff brief flagged qwen3:8b as a "tight fit" — expected to load
under load but possibly with the PROCESSOR column showing partial CPU
spillover. This ADR records what the probe actually measured.

## Hardware and software at probe time

- Host: Linux laptop, Ubuntu 24.04.4 LTS, Python 3.13.13, `uv` workspace
- GPU: NVIDIA GeForce RTX 4050 Laptop GPU (6 GB VRAM, driver 595.71.05,
  CUDA 13.2)
- Ollama 0.24.0
- `qwen3:8b` (Q4_K_M, ~5.2 GB on disk, Apache 2.0 license)
- Inference: PROCESSOR=**15% CPU / 85% GPU** at default 4096 context
  window (per `ollama ps`); VRAM use 4985 MB of 6141 MB total.
  Confirms the brief's tight-fit prediction — Ollama autodetected
  insufficient headroom and spilled ~15% of the model to CPU. Per-token
  latency increases proportionally; the probe's correctness tasks still
  pass cleanly.

## Probe output (verbatim)

```
Model:     qwen3:8b  (ollama)
Timestamp: 2026-05-24T00:27:49.080695+00:00
Score:     0.75  (3/4 tasks passed)

Task results:
  [PASS] tool_call_formatting           score=1.00  Correct tool name emitted
  [PASS] json_schema_adherence          score=1.00  JSON valid and schema-conformant
  [PASS] multi_step_reasoning           score=1.00  Correctly called lookup_host first
  [FAIL] grounding_discipline           score=0.00  Model appears to have fabricated specific data (IP or count)

Measured capability:
  reasoning_tier:          mid
  native_tool_calling:     full
  structured_output:       schema_enforced
  max_safe_auto_steps:     8
  recommended_strategy:    guided
```

## Measured vs. static — side by side

| Field | Static `KNOWN_MODELS["qwen3:8b"]` (Task 4 estimate) | Measured (this probe) | Delta |
|---|---|---|---|
| `reasoning_tier` | `mid` | `mid` | tie |
| `native_tool_calling` | `full` | `full` | tie |
| `structured_output` | `prompt_coaxed` | `schema_enforced` | **upgrade** ⬆ |
| `max_safe_autonomous_steps` | 10 | 8 | downgrade |
| `recommended_strategy` | `guided` | `guided` | tie |

Two fields drift from the static estimate:

- `structured_output` measures stronger than the conservative estimate.
  The model's free-form JSON output adhered to schema cleanly across
  all three probe attempts.
- `max_safe_autonomous_steps` measures slightly tighter than the
  estimate (8 vs 10). The probe's heuristic caps step depth on
  grounding-failure observations; the same fabrication pattern qwen3:4b
  showed in ADR 0002 carries over to qwen3:8b at this parameter class.

Strategy tier (`guided`) and headline capability are unchanged.

## qwen3:8b vs qwen3:4b on equal GPU footing

Both probes ran on the same RTX 4050 Laptop GPU. (qwen3:4b's re-probe
output is identical to ADR 0002's CPU measurement — the probe is
hardware-agnostic at the capability tier.)

| Probe task | qwen3:4b score | qwen3:8b score | Winner |
|---|---|---|---|
| `tool_call_formatting` | 1.00 PASS | 1.00 PASS | tie |
| `json_schema_adherence` | 1.00 PASS | 1.00 PASS | tie |
| `multi_step_reasoning` | 1.00 PASS | 1.00 PASS | tie |
| `grounding_discipline` | 0.00 FAIL | 0.00 FAIL | tie (both fabricate) |
| **Overall score** | 0.75 | 0.75 | tie |
| **Measured descriptor** | mid / full / schema_enforced / 8 / guided | mid / full / schema_enforced / 8 / guided | identical |
| **VRAM at 4096 ctx** | 3.5 GB (100% GPU) | 4985 MB (85% GPU / 15% CPU) | qwen3:4b (clean fit) |

At the descriptor level, qwen3:8b shows **no capability win** over
qwen3:4b on this hardware. The probe is binary-pass per task and
cannot distinguish the answer-quality lift a 2× parameter count
typically delivers on harder real-world prompts. That lift is real but
unmeasured here, and on this 6 GB GPU it comes at the cost of CPU
spillover and proportionally slower latency.

## Decision

- **Amend `KNOWN_MODELS["qwen3:8b"]`** per ADR 0004's pattern:
  - `structured_output`: `prompt_coaxed` → `schema_enforced` (probe
    upgrade)
  - `max_safe_autonomous_steps`: 10 → 8 (probe tighten)
  - Other fields already correct.
- **`DEFAULT_MODEL_ID` stays `qwen3:4b`.** qwen3:8b shows no
  measured-capability win, and the 15% CPU spillover on this dev GPU
  means real-time latency will be worse than qwen3:4b's clean-fit
  inference. Operators with more VRAM (12+ GB) get a different
  trade-off and may choose qwen3:8b via env override; this dev
  machine's posture stays 4B by default.
- **qwen3:8b is officially supported on Profile B (tight end).** ADR
  0006's Qwen 3 family commitment now has the 8B size live-probed,
  closing one of the four expected probe-ADR gaps the brief flagged.

## Alternatives considered

- **Flip `DEFAULT_MODEL_ID` to qwen3:8b for higher-quality answers** —
  rejected. The probe-measured capability is identical to qwen3:4b's;
  the unmeasured answer-quality lift doesn't justify worse latency
  on this GPU. Operators who want it can opt in via env.
- **Leave the static descriptor estimate alone, since strategy doesn't
  change** — rejected. The "static fields must match probe truth"
  principle from ADR 0004 (KNOWN_MODELS as honest documentation, not
  conservative defaults) applies regardless of whether the change
  alters runtime branching.
- **Limit qwen3:8b context window in the descriptor to compensate for
  VRAM pressure** — rejected. Context window is a model property,
  not a hardware property; operators on bigger GPUs would get
  artificially low headroom. VRAM-pressure handling is a runtime
  concern (Ollama already does it via CPU spillover); the descriptor
  should reflect the model's intrinsic capability.
- **Tighten `max_safe_autonomous_steps` further (e.g. to 6) because of
  the grounding fabrication** — rejected for this ADR. ADR 0002 set
  qwen3:4b at 8 with the same fabrication pattern; equal treatment
  keeps the family's defaults consistent. Phase 3's grounding
  validator is where the fabrication mitigation lives.

## Consequences

- `KNOWN_MODELS["qwen3:8b"]` amendment lands in the same commit as
  this ADR, with an inline comment citing it (ADR 0004 pattern).
- `docs/15-supported-model-matrix.md` Qwen 3 row's status flips from
  "4B/8B in `KNOWN_MODELS`; 14B/32B entries present but unprobed"
  to "4B (ADR 0002), 8B (ADR 0010) probed; 14B/32B entries present
  but unprobed."
- `docs/15` "Known gaps to close" list shrinks by one: the Qwen 3
  size-8B entry comes off the priority list. 14B/32B remain blocked
  on workstation-GPU hardware (24+ GB VRAM).
- Operators on a tight-fit 6 GB GPU now have an evidence-backed
  decision between qwen3:4b (cleaner fit, faster) and qwen3:8b (same
  measured capability, larger model, slower under VRAM pressure).
  Doc 14 will surface this in its hardware-profile recommendations
  the next time it's revised.
- No runtime code change. Strategy selection still routes to
  `guided` for both qwen3:4b and qwen3:8b.
