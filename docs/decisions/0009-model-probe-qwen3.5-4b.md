# 0009 — Capability probe for `qwen3.5:4b` on RTX 4050 Laptop GPU

**Date:** 2026-05-24
**Status:** accepted
**Decider:** claude-code (executing the new-machine handoff brief at
`prompts/HANDOFF-NEW-MACHINE.md`)
**Related:** [ADR 0002](0002-model-probe-qwen3-4b.md) (the qwen3:4b probe
this one is compared against), [ADR 0004](0004-model-switch-llama3.2-to-qwen3-4b.md)
(the default-flip whose pattern this ADR explicitly *does not* trigger),
[ADR 0006](0006-supported-model-families-commitment.md) (the Qwen 3
family commitment under which Qwen 3.5 is a minor revision),
`docs/14-model-recommendations.md`,
`docs/15-supported-model-matrix.md`,
`services/orchestrator/app/models/interface.py` (KNOWN_MODELS).

## Context

Qwen 3.5 released on Ollama on or around 2026-05-22 (~2 days before this
probe). Per ADR 0006, the Qwen 3 family is one of the four families
Wolf must natively support — Qwen 3.5 falls under that umbrella as a
minor revision (3.x). The new-machine handoff brief flagged qwen3.5:4b
as the most interesting near-term probe: same parameter class as the
current default (qwen3:4b), brand-new release, potential default-flip
candidate **if** it matches or beats ADR 0002's results.

This is also the first opportunity to compare qwen3 and qwen3.5 on
**equal hardware footing**. ADR 0002 ran qwen3:4b on CPU only; this
machine has an NVIDIA RTX 4050 Laptop GPU (6 GB VRAM), so the
comparison reported below uses GPU-resident inference for both models.

## License verification

ADR 0006 requires license verification before any `KNOWN_MODELS` entry.
The Ollama model page does not state the license directly. Verified via
the Qwen 3.5 release announcement and ModelScope: the open-weight tiers
(0.8B through 397B-A17B) are released under **Apache 2.0** with no MAU
limit, no acceptable-use restrictions, and no attribution gates.
qwen3.5:4b is in that tier. License classification: `apache-2.0`.

## Hardware and software at probe time

- Host: Linux laptop, Ubuntu 24.04.4 LTS, Python 3.13.13, `uv` workspace
- GPU: NVIDIA GeForce RTX 4050 Laptop GPU (6 GB VRAM, driver 595.71.05,
  CUDA 13.2)
- Ollama 0.24.0
- `qwen3.5:4b` (~3.4 GB on disk; 5.9 GB resident VRAM at default 4096
  context window — much larger than qwen3:4b's 3.5 GB; the 256K context
  capability appears to inflate the per-token KV cache budget Ollama
  reserves up-front)
- Inference: PROCESSOR=100% GPU (per `ollama ps`); model fits at default
  context but only just — pushing context closer to the model's
  256K maximum would overflow this hardware.

## Probe output (verbatim)

```
Model:     qwen3.5:4b  (ollama)
Timestamp: 2026-05-24T00:26:12.379192+00:00
Score:     0.50  (2/4 tasks passed)

Task results:
  [FAIL] tool_call_formatting           score=0.00  chat() raised: Model failed structured-output fallback after 3 attempts: Not valid JSON: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)
  [FAIL] json_schema_adherence          score=0.00  Response is not valid JSON: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)
  [PASS] multi_step_reasoning           score=1.00  Correctly called lookup_host first
  [PASS] grounding_discipline           score=1.00  Model correctly refused to fabricate specific data

Measured capability:
  reasoning_tier:          basic
  native_tool_calling:     none
  structured_output:       unreliable
  max_safe_auto_steps:     4
  recommended_strategy:    pipeline
```

## qwen3:4b vs qwen3.5:4b on equal GPU footing

Both probes ran on the same RTX 4050 Laptop GPU, minutes apart, with
identical Wolf-side configuration. The qwen3:4b run is a GPU re-probe
of ADR 0002 (originally CPU); the descriptor it produces is **identical
to ADR 0002's**, confirming that the probe measures model quality and
not hardware speed.

| Probe task | qwen3:4b score | qwen3.5:4b score | Winner |
|---|---|---|---|
| `tool_call_formatting` | 1.00 PASS | 0.00 FAIL | **qwen3:4b** ⬆ |
| `json_schema_adherence` | 1.00 PASS | 0.00 FAIL | **qwen3:4b** ⬆ |
| `multi_step_reasoning` | 1.00 PASS | 1.00 PASS | tie |
| `grounding_discipline` | 0.00 FAIL | 1.00 PASS | **qwen3.5:4b** ⬆ |
| **Overall score** | **0.75** | 0.50 | qwen3:4b |
| **Reasoning tier** | mid | basic | qwen3:4b ⬆ |
| **Native tool calling** | full | none | qwen3:4b ⬆ |
| **Structured output** | schema_enforced | unreliable | qwen3:4b ⬆ |
| **Recommended strategy** | guided | **pipeline** | qwen3:4b ⬆ |
| **VRAM at 4096 ctx** | 3.5 GB | 5.9 GB | qwen3:4b ⬆ |

qwen3.5:4b regresses on three of four probe dimensions, including
both formatting tasks that are directly load-bearing for Wolf's tool
dispatch path. The single dimension it wins on — grounding discipline
— is exactly the dimension qwen3:4b is known to be weak on (the
designed mitigation lives in Phase 3's grounding validator), but it
does not offset the regressions.

The `native_tool_calling = none` measurement deserves a footnote.
The probe's failure mode for qwen3.5:4b is structurally **different**
from the failure that earned gemma3:4b the same classification in
[ADR 0003](0003-model-probe-gemma3-4b.md):

- gemma3:4b: Ollama returns HTTP 400 on any chat that includes
  `tools=[...]` — the model is structurally untrained for tools.
- qwen3.5:4b: Ollama accepts the request; the model **returns
  syntactically invalid JSON** (parse error at "line 1 column 2"),
  which the structured-output fallback then can't recover. Smells
  more like a chat-template / prompt-formatting issue in the
  Ollama qwen3.5 release than a hard model-level limitation.

Either way the operational consequence is the same — the probe could
not extract a usable tool call — so the descriptor stands. A follow-up
probe after Ollama bumps the qwen3.5 release (or the model card lands
with a definitive answer on tool-calling support) is worth doing.

## Decision

- **Add `KNOWN_MODELS["qwen3.5:4b"]`** matching the measured probe
  (`basic` / `none` / `unreliable` / 4 / `pipeline`, license
  `apache-2.0`). Provider stays `ollama`. Context window field uses
  the model's nominal 256K capability; the probe does not measure
  context capacity, only behavior.
- **`DEFAULT_MODEL_ID` stays `qwen3:4b`.** The handoff brief explicitly
  contemplated a default-flip ADR following the ADR 0004 pattern
  *only if* qwen3.5:4b matched or beat qwen3:4b's ADR 0002 results.
  It does not; it regresses on the most operationally important
  dimensions. No flip ADR is written.
- **qwen3.5:4b remains a supported model** under the ADR 0006
  commitment (it's a Qwen 3.x variant; the matrix doesn't drop a
  model on capability alone). It's available to operators via
  `DEFAULT_MODEL_ID=qwen3.5:4b` env override; they get the
  documented basic-tier behavior with pipeline strategy.

## Alternatives considered

- **Mark qwen3.5:4b unsupported because of the regression** —
  rejected. ADR 0006 commits Wolf to Qwen 3.x family support; capability
  regression in a single release does not justify dropping the variant
  from `KNOWN_MODELS`. Operators choosing it accept the documented
  limitations; Wolf's job is to surface them honestly.
- **Re-probe qwen3.5:4b multiple times before recording the descriptor**
  — rejected for this ADR. The failure was deterministic across the
  probe's three structured-output retry attempts; flakiness is unlikely.
  A re-probe after the next Ollama qwen3.5 release is the right
  follow-up, not blocking this ADR.
- **Flag qwen3.5:4b as broken-on-Ollama in a code comment instead of
  treating it as a real capability measurement** — rejected. The
  measurement IS the truth from Wolf's perspective: at the moment of
  the probe, on this Ollama version, qwen3.5:4b cannot complete
  tool-formatted requests. Whether the cause is the model, the chat
  template, or Ollama is irrelevant to the descriptor's contract.

## Consequences

- `KNOWN_MODELS["qwen3.5:4b"]` lands as part of this commit with an
  inline comment citing this ADR.
- `docs/15-supported-model-matrix.md` Implementation-status table
  flips Qwen 3 row from "4B (probed, ADR 0002)" to also mention
  ADR 0009 / qwen3.5:4b under the same family.
- No code path changes. The static descriptor drives strategy
  selection; `pipeline` is what an operator who flips
  `DEFAULT_MODEL_ID=qwen3.5:4b` will get out-of-box.
- A follow-up probe of qwen3.5:4b is queued, gated on either: (a) a
  newer Ollama release that bumps qwen3.5's chat-template/tool
  glue, or (b) the official qwen3.5 model card landing with a
  definitive tool-support statement. If a re-probe reverses the
  formatting failures, a new ADR will supersede this one.
- The four-family commitment from ADR 0006 is unaffected — Qwen 3.x
  family coverage stays at "supported."
