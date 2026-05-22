# 0003 — Capability probe for `gemma3:4b` on CPU-only dev VM

**Date:** 2026-05-22
**Status:** accepted
**Decider:** claude-code (executing the planning brief at
`prompts/CLAUDE-CODE-SESSION-PROMPT.md`)
**Related:** ADR 0001 (`llama3.2` baseline), ADR 0002 (`qwen3:4b`),
`docs/14-model-recommendations.md`,
`services/orchestrator/app/models/interface.py` (KNOWN_MODELS)

## Context

Doc 14 lists `gemma3:4b` alongside `qwen3:4b` as a Profile A
(CPU-only / 16-32 GB RAM) Apache-licensed candidate. This ADR records
the live probe result on the same hardware as ADRs 0001 and 0002 to
complete the comparison.

## Hardware and software at probe time

Identical to ADR 0001 and 0002:

- VM at `192.168.76.128`, ~16 GB RAM, CPU-only Ollama inference
- Ubuntu 24.04.4 LTS, Python 3.13.13
- Ollama 0.24.0
- `gemma3:4b` (Q4_K_M, Apache 2.0 license)

## Probe output (verbatim)

```
Model:     gemma3:4b  (ollama)
Timestamp: 2026-05-22T15:43:15.750337+00:00
Score:     0.25  (1/4 tasks passed)

Task results:
  [FAIL] tool_call_formatting           score=0.00  chat() raised: Client error '400 Bad Request' for url 'http://localhost:11434/api/chat'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/400
  [PASS] json_schema_adherence          score=1.00  JSON valid and schema-conformant
  [FAIL] multi_step_reasoning           score=0.00  chat() raised: Client error '400 Bad Request' for url 'http://localhost:11434/api/chat'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/400
  [FAIL] grounding_discipline           score=0.00  Model appears to have fabricated specific data (IP or count)

Measured capability:
  reasoning_tier:          basic
  native_tool_calling:     none
  structured_output:       schema_enforced
  max_safe_auto_steps:     3
  recommended_strategy:    pipeline
```

## What the 400s mean

Two of the four probe tasks send `tools: [...]` to Ollama's `/api/chat`.
Ollama returned HTTP 400 for both. That is not a transient bug — it is
the **Gemma 3 family's structural limitation**: Gemma 3 4B is trained
without native tool-calling support, so the Ollama runtime rejects any
request that includes a tools parameter.

The probe correctly interpreted this:

- `native_tool_calling: none` (the model can't accept tool definitions)
- `recommended_strategy: pipeline` (no tools → no agent loop; only the
  deterministic-pipeline strategy applies)

The one task that did pass (`json_schema_adherence`) confirms gemma3:4b
*can* emit valid structured JSON — it just can't be driven by Wolf's
tool-calling dispatcher.

## Measured vs. static — side by side

| Field | Static `KNOWN_MODELS["gemma3:4b"]` | Measured (this probe) | Delta |
|---|---|---|---|
| `reasoning_tier` | `basic` | `basic` | match ✓ |
| `native_tool_calling` | `partial` | `none` | **downgrade** ⬇ |
| `structured_output` | `prompt_coaxed` | `schema_enforced` | upgrade ⬆ |
| `max_safe_autonomous_steps` | 5 | 3 | downgrade |
| `recommended_strategy` | `pipeline` | `pipeline` | match ✓ |

The static estimate over-promised on `native_tool_calling=partial`.
Reality: zero tool calling. The strategy-tier verdict was correct.

## Three-way comparison on this hardware

| Model | Overall | Tool-calling | JSON | Multi-step | Grounding | Strategy | License |
|---|---|---|---|---|---|---|---|
| `llama3.2` | 0.68 | PASS | FAIL | PASS | PASS | guided | restricted |
| `qwen3:4b` | **0.75** | PASS | PASS | PASS | FAIL | guided | **apache-2.0** |
| `gemma3:4b` | 0.25 | FAIL (400) | PASS | FAIL (400) | FAIL | pipeline | apache-2.0 |

`gemma3:4b` is **not a viable substitute for `llama3.2`** in Wolf's
agent loop. The whole architecture leans on tool calling; a model that
cannot accept tools at all is forced into pipeline mode, which today
exposes no tools and reduces the agent to a single summarisation pass.
Useful at the edges — e.g. as a cheap summariser inside a deterministic
pre-fetch pipeline once those exist — but not as Wolf's default.

`qwen3:4b` is the clear Apache-licensed candidate.

## Decision

- **Static `KNOWN_MODELS["gemma3:4b"]` should be amended** to
  `native_tool_calling=none`, `max_safe_autonomous_steps=3`,
  `structured_output=schema_enforced`. Reasoning tier and strategy
  already match. Deferred to the same batch as the qwen3:4b amendment
  noted in ADR 0002.
- **Remove `gemma3:4b` from consideration as a default-model
  candidate** until either (a) the Gemma family adds native tool
  calling, or (b) Wolf grows a real pipeline-strategy implementation
  that can drive it. Until then it lives in `KNOWN_MODELS` as a
  documented option for operators who specifically want it for
  summarisation slots.
- **Do not flip `DEFAULT_MODEL_ID` in this session.** The follow-up
  switch decision (a separate `0NNN-model-switch-llama3.2-to-qwen3-4b.md`
  ADR) is now well-evidenced by ADRs 0001/0002/0003 but is itself a
  separate commit per doc 14's playbook.

## Alternatives considered

- **Try `gemma3:4b` with the structured-output fallback** instead of
  native tool-calling — possible but rejected for now. Wolf's fallback
  exists exactly for this case, but the agent loop's branching on
  `native_tool_calling` would need additional work to route gemma-class
  models through it cleanly. Out of scope for this session.
- **Re-probe with `--no-tools`** to isolate JSON / reasoning quality
  with no tools in the request — interesting but not needed: doc 14
  explicitly says the recommendation between qwen3 and gemma3 in
  Profile A is a coin flip until probed, and the probe answered the
  question. No need to re-grade.

## Consequences

- The 4B-class Apache-licensed pick on this hardware is `qwen3:4b`,
  not `gemma3:4b`.
- A future `0NNN-model-switch-llama3.2-to-qwen3-4b.md` ADR has the
  evidence base it needs (ADRs 0001, 0002, 0003) to make the call.
- Wolf does not lose anything by keeping `gemma3:4b` in `KNOWN_MODELS`
  — it remains an option for operators who pair it with structured-
  output workflows where its 1.00 JSON score is the relevant signal.
