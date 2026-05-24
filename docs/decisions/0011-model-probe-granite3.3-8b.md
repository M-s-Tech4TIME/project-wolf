# 0011 — Capability probe for `granite3.3:8b` on RTX 4050 Laptop GPU (opportunistic)

**Date:** 2026-05-24
**Status:** accepted
**Decider:** claude-code (executing operator request to broaden the
empirical comparison beyond the four-family commitment)
**Related:** [ADR 0006](0006-supported-model-families-commitment.md)
(four-family commitment — Granite is **outside** that matrix; this
probe is the "opportunistic registration" path ADR 0006 explicitly
permits), [ADR 0002](0002-model-probe-qwen3-4b.md) (the current
dev-default baseline this probe is compared against),
`docs/14-model-recommendations.md`, `services/orchestrator/app/models/interface.py`
(KNOWN_MODELS).

## Context

After the Phase-2 close-out and ADR 0009/0010 probes, the operator
asked which fully-free open-source agentic models were realistic
challengers to qwen3:4b on the new RTX 4050 Laptop hardware, with the
license filter relaxed (i.e. the four-family commitment in ADR 0006
explicitly applied). The top recommendation in that triage was IBM's
Granite 3.3 8B — Apache 2.0, marketed by IBM specifically for agentic
tool use, with a dedicated tools-trained variant. The operator chose
to probe it.

Per ADR 0006's "Wider matrix" alternatives section, additional
families beyond the committed four can receive `KNOWN_MODELS` entries
as **opportunistic registrations** — operators choose them at their
own risk without the six-item "natively support" quality bar
(KNOWN_MODELS + live probe + ADR + agent-loop test + strategy
assignment + smoke coverage + doc 14 entry). This ADR is the live
probe; subsequent items in the checklist are deliberately not pursued.

## License verification

Confirmed Apache 2.0 via the Ollama model page
(https://ollama.com/library/granite3.3 — "License: Apache 2.0" with
link to apache.org/licenses/LICENSE-2.0). License classification:
`apache-2.0`.

## Hardware and software at probe time

- Host: Linux laptop, Ubuntu 24.04 LTS, Python 3.13.13, `uv` workspace
- GPU: NVIDIA GeForce RTX 4050 Laptop GPU (6 GB VRAM, driver 595.71.05,
  CUDA 13.2)
- Ollama 0.24.0
- `granite3.3:8b` (~4.9 GB on disk; 6.1 GB resident at default 4096
  context window — tight VRAM fit similar to qwen3:8b)
- Inference: PROCESSOR=**12% CPU / 88% GPU** at default 4096 context
  (per `ollama ps`); VRAM use 5053 MB of 6141 MB total. Slightly less
  CPU spillover than qwen3:8b's 15%/85% — fractionally better fit on
  this hardware, but the same tight-fit class.

## Probe output (verbatim)

```
Model:     granite3.3:8b  (ollama)
Timestamp: 2026-05-24T01:00:05.972221+00:00
Score:     0.25  (1/4 tasks passed)

Task results:
  [PASS] tool_call_formatting           score=1.00  Correct tool name emitted
  [FAIL] json_schema_adherence          score=0.00  chat() raised: Model failed structured-output fallback after 3 attempts: Response must contain "answer" or "tool" key
  [FAIL] multi_step_reasoning           score=0.00  chat() raised: Model failed structured-output fallback after 3 attempts: Not valid JSON: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)
  [FAIL] grounding_discipline           score=0.00  Model appears to have fabricated specific data (IP or count)

Measured capability:
  reasoning_tier:          basic
  native_tool_calling:     full
  structured_output:       unreliable
  max_safe_auto_steps:     3
  recommended_strategy:    pipeline
```

## Reading the result

The score is misleading on its own. Granite 3.3 8B's headline 0.25 is
driven by three failures, but those failures cluster on **the
structured-output fallback path**, not the native tool-calling path:

- `tool_call_formatting` — Wolf sends `tools=[...]` and validates that
  the model emits a properly-named tool call. **PASS.** Confirms IBM's
  agentic-tools positioning works at the format level.
- `json_schema_adherence` — Wolf asks the model to emit free-form JSON
  matching Wolf's `answer`/`tool` envelope. **FAIL** with "Response
  must contain 'answer' or 'tool' key" — Granite produced JSON but
  not the shape Wolf's structured-output schema expects.
- `multi_step_reasoning` — also routes through structured-output
  (the probe's expected response is a JSON object). **FAIL** with the
  same "Not valid JSON" parse error qwen3.5:4b showed in ADR 0009.
- `grounding_discipline` — Granite fabricated specific data when
  asked open-ended without tools. **FAIL.** Same weakness ADR 0002
  flagged for qwen3:4b and ADR 0010 flagged for qwen3:8b.

The descriptor that lands (`basic` / `full` / `unreliable` / 3 /
`pipeline`) is driven by the heuristic that pushes recommended_strategy
down on grounding-discipline + multi-step failures. But the
`native_tool_calling: full` measurement is real — under Wolf's
**guided strategy** (where every step is a typed dispatcher-validated
tool call, and the structured-output fallback is never invoked),
Granite might perform better than the descriptor predicts. This ADR
does not test that hypothesis; the probe's strategy verdict stands as
written.

## qwen3:4b vs granite3.3:8b on equal GPU footing

| Probe task | qwen3:4b score | granite3.3:8b score | Winner |
|---|---|---|---|
| `tool_call_formatting` | 1.00 PASS | 1.00 PASS | tie |
| `json_schema_adherence` | 1.00 PASS | 0.00 FAIL | **qwen3:4b** ⬆ |
| `multi_step_reasoning` | 1.00 PASS | 0.00 FAIL | **qwen3:4b** ⬆ |
| `grounding_discipline` | 0.00 FAIL | 0.00 FAIL | tie (both fabricate) |
| **Overall score** | **0.75** | 0.25 | qwen3:4b |
| **Reasoning tier** | mid | basic | qwen3:4b ⬆ |
| **Native tool calling** | full | full | tie |
| **Structured output** | schema_enforced | unreliable | qwen3:4b ⬆ |
| **Recommended strategy** | guided | pipeline | qwen3:4b ⬆ |
| **VRAM at 4096 ctx** | 3.5 GB (100% GPU) | 5053 MB (88% GPU / 12% CPU) | qwen3:4b (clean fit) |
| **Parameter count** | 4B | 8B | granite (more params, no descriptor lift) |

Granite 3.3 8B is **not a viable challenger to qwen3:4b** on this
hardware as a default-model candidate. Despite being 2× the
parameter count and IBM's explicit agentic positioning, the
Wolf-probe outcome regresses on three of four tasks. The
native-tool-calling parity is the one bright spot — a future
guided-strategy-only smoke test against Wolf's tool dispatcher might
show Granite at parity for the loop-execution path even though it
fails the probe's heuristic. That test is not in this ADR's scope.

## Decision

- **Add `KNOWN_MODELS["granite3.3:8b"]`** at the probe-measured tier
  (`basic` / `full` / `unreliable` / 3 / `pipeline`, license
  `apache-2.0`). Provider `ollama`. Mark with an inline comment as
  **opportunistic registration per ADR 0006** — *not* part of the
  four-family supported matrix. Operators who select it via env
  override get the documented pipeline behavior.
- **`DEFAULT_MODEL_ID` stays `qwen3:4b`.** Granite 3.3 8B does not
  meet the bar.
- **No additions to `docs/15-supported-model-matrix.md`.** The
  matrix is bounded by ADR 0006's four-family commitment; Granite
  stays out of that table to preserve the deliberate narrowness
  ADR 0006 argues for.
- **Granite stays a "documented option" in `KNOWN_MODELS`** the same
  way DeepSeek-flash and Nemotron do (ADR 0005's pattern). The
  KNOWN_MODELS entry surfaces it for operators who specifically
  want to try it; Wolf does not promote it.

## Alternatives considered

- **Exclude Granite from `KNOWN_MODELS` entirely because the score
  is low.** Rejected. The `KNOWN_MODELS` registry is honest
  documentation of what Wolf knows about, not a recommendation
  list. Excluding low-scoring models would force operators who
  want to experiment to manually patch the registry — that hides
  the measured truth rather than surfacing it.
- **Promote Granite into the four-family matrix on the strength of
  the tool-call PASS plus IBM's agentic positioning.** Rejected.
  ADR 0006's matrix is bounded by *measured Wolf-loop fit*, not
  vendor positioning. A future Granite probe — perhaps on a
  workstation GPU at the 8B class with a less-stringent
  structured-output expectation, or a re-probe against the
  tools-trained variant if IBM publishes a distinct one — could
  reverse this, but the current probe data does not support
  matrix inclusion.
- **Re-probe with the agent-loop smoke instead of the static
  probe to test the guided-strategy hypothesis.** Deferred, not
  rejected. The smoke harness (`smoke_wazuh --all-tools` plus a
  driven chat session) is the right test, but it requires
  flipping `DEFAULT_MODEL_ID=granite3.3:8b` temporarily and
  driving a real Wazuh interaction. Out of scope for this
  drop-in probe; a follow-up if/when the question becomes
  load-bearing.

## Consequences

- `KNOWN_MODELS["granite3.3:8b"]` lands in the same commit with an
  inline comment citing this ADR and explicitly marking it
  opportunistic.
- `docs/15-supported-model-matrix.md` is **unchanged** — Granite is
  not in the four-family matrix and this ADR does not propose
  expanding it.
- `docs/decisions/README.md` gains a row for ADR 0011 (opportunistic
  probe).
- Operators on this hardware have one more datapoint: a marketed-for-
  agents 8B model that does not, on Wolf's probe, outperform the
  4B Qwen default. The "purpose-built for agents" claim does not
  automatically translate to Wolf-loop fit.
- No code path change. The static descriptor drives strategy
  selection; `pipeline` is what an operator who sets
  `DEFAULT_MODEL_ID=granite3.3:8b` will get.
- The Granite family does **not** receive a probe-cadence commitment.
  If IBM ships Granite 3.4 or a tools-trained variant, a fresh
  probe is welcome but not mandatory.
