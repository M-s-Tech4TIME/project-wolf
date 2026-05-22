# 0002 — Capability probe for `qwen3:4b` on CPU-only dev VM

**Date:** 2026-05-22
**Status:** accepted
**Decider:** claude-code (executing the planning brief at
`prompts/CLAUDE-CODE-SESSION-PROMPT.md`)
**Related:** ADR 0001 (`llama3.2` baseline),
`docs/14-model-recommendations.md`,
`services/orchestrator/app/models/interface.py` (KNOWN_MODELS)

## Context

Doc 14 names `qwen3:4b` as the recommended Apache-licensed replacement
for `llama3.2` once the probe confirms it works on the same hardware.
This ADR captures the live probe result against the dev VM, to be
compared with ADR 0001 (`llama3.2` baseline) when we revisit the default
model choice.

## Hardware and software at probe time

Identical to ADR 0001:

- VM at `192.168.76.128`, ~16 GB RAM, CPU-only Ollama inference
- Ubuntu 24.04.4 LTS, Python 3.13.13, `uv` workspace
- Ollama 0.24.0
- `qwen3:4b` (Q4_K_M, ~2.5 GB on disk, Apache 2.0 license)

## Probe output (verbatim)

```
Model:     qwen3:4b  (ollama)
Timestamp: 2026-05-22T15:41:13.782348+00:00
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

| Field | Static `KNOWN_MODELS["qwen3:4b"]` | Measured (this probe) | Delta |
|---|---|---|---|
| `reasoning_tier` | `basic` | `mid` | **upgrade** ⬆ |
| `native_tool_calling` | `partial` | `full` | **upgrade** ⬆ |
| `structured_output` | `prompt_coaxed` | `schema_enforced` | **upgrade** ⬆ |
| `max_safe_autonomous_steps` | 5 | 8 | upgrade |
| `recommended_strategy` | `pipeline` | `guided` | **upgrade** ⬆ |

Three of four headline fields measured stronger than the static estimate
from doc 14. The estimate was deliberately conservative; the actual model
performs better than the table's "Profile A — basic" reading suggested.

## qwen3:4b vs llama3.2 — head-to-head on this hardware

Both use identical Wolf-side configuration; both ran on the same VM
within minutes of each other.

| Probe task | llama3.2 score | qwen3:4b score | Winner |
|---|---|---|---|
| `tool_call_formatting` | 1.00 PASS | 1.00 PASS | tie |
| `json_schema_adherence` | 0.00 FAIL | 1.00 PASS | **qwen3:4b** ⬆ |
| `multi_step_reasoning` | 1.00 PASS | 1.00 PASS | tie |
| `grounding_discipline` | 0.70 PASS | 0.00 FAIL | **llama3.2** ⬆ |
| **Overall score** | 0.68 | **0.75** | qwen3:4b |
| **Reasoning tier** | mid | mid | tie |
| **Recommended strategy** | guided | guided | tie |
| **Structured output** | unreliable | **schema_enforced** | qwen3:4b ⬆ |

The two models land at the same strategy tier (`guided`). qwen3:4b wins
on JSON adherence — which is exactly the weakness the llama3.2 probe
flagged. qwen3:4b loses on grounding discipline — under the
"no-tools-given" pipeline test it fabricated specific data. llama3.2
held the line and didn't invent.

For Wolf this trade-off matters: the orchestrator's dispatch path
validates every tool call with Pydantic, so the model's JSON
reliability is meaningful when the model uses the structured-output
fallback. Grounding discipline matters more in the **answer** the model
produces from real tool results — and the grounding validator (Phase 3)
is the designed line of defense there, regardless of which 4B-class
model is in use.

## Decision

- **Static `KNOWN_MODELS["qwen3:4b"]` should be updated** at the next
  appropriate moment to reflect measured capability (`mid` /
  `schema_enforced` / `guided` / 8 max steps). Deferred to the same
  batch update as ADR 0003 (gemma3:4b) so all three are revised
  together.
- **No `DEFAULT_MODEL_ID` switch yet.** With gemma3:4b probe still
  outstanding, the comparison is incomplete. A standalone
  `0NNN-model-switch-llama3.2-to-<x>.md` ADR will follow once all three
  4B-class probes are on the table.

## Alternatives considered

- **Mark qwen3:4b as the new default immediately on score alone** —
  rejected. 0.75 vs 0.68 is a real gap but the grounding-discipline
  fail is a non-trivial regression against Wolf's own design principle.
  Decision deserves the gemma3:4b data point too.
- **Treat the grounding-discipline failure as disqualifying** —
  rejected. The failure mode is "fabricates when given no tools", which
  is exactly the pipeline-mode scenario the grounding validator is
  designed to catch. In guided mode (with the dispatcher validating
  tool calls), this failure shape is contained.

## Consequences

- `qwen3:4b` is empirically viable on this CPU-only hardware at the
  guided-strategy tier.
- The grounding-discipline result is a data point that Phase 3's
  grounding validator becomes more important (not less) if we adopt a
  Qwen-class model as the default.
- Combined with ADR 0001, this ADR forms two-thirds of the comparison
  table that informs the eventual switch decision. ADR 0003 (gemma3:4b)
  completes it.
