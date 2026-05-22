# 0001 — Capability probe baseline for `llama3.2` on CPU-only dev VM

**Date:** 2026-05-22
**Status:** accepted
**Decider:** claude-code (executing the planning brief at
`prompts/CLAUDE-CODE-SESSION-PROMPT.md`)
**Related:** `docs/14-model-recommendations.md`,
`services/orchestrator/app/models/interface.py` (KNOWN_MODELS),
`tools/model_probe/`

## Context

`llama3.2:latest` (3B, Q4_K_M) is Wolf's running dev default
(`DEFAULT_MODEL_PROVIDER=ollama`, `DEFAULT_MODEL_ID=llama3.2` in
`services/orchestrator/app/config.py`). The static `KNOWN_MODELS` entry
for it was an estimate; the capability probe in `tools/model_probe/` had
never been run live on this hardware. Doc 14 explicitly asks for a probe
baseline before any default-model switch decision.

## Hardware and software at probe time

- VM at `192.168.76.128`
- ~16 GB RAM, **CPU-only** inference (Ollama reports no GPU detected)
- Ubuntu 24.04.4 LTS
- Python 3.13.13, `uv` workspace
- Ollama 0.24.0, model `llama3.2:latest` (digest
  `a80c4f17acd5…`, 2.0 GB on disk, Q4_K_M, Llama family — 3.2 B params)
- Probe code: `tools/model_probe/probe.py` + `tools/model_probe/tasks.py`
  (built in Phase 1, never previously run live)

## Bug surfaced and fixed before the probe could run

The CLI invocation in the probe's own docstring
(`uv run python -m tools.model_probe ...`) failed at import:

```
ModuleNotFoundError: No module named 'app.models'
  File "tools/model_probe/__main__.py", line 78
    from app.models.ollama import OllamaAdapter
```

Root cause: **both** `services/orchestrator/app/` and `services/gateway/app/`
expose a package literally called `app`. uv's editable installs put both on
`sys.path` (gateway first). Bare `import app` resolves to the gateway's
`app/` (which has no `models/` submodule) and shadows the orchestrator's.

Pytest never hit this because its own path setup happens to land
orchestrator first. The bare CLI path was never exercised — PROGRESS.md
already noted "capability probe built but never run against live Ollama
on this hardware."

Fix landed in `tools/model_probe/__main__.py`: a small `sys.path` bootstrap
at module load time that **unconditionally** puts the orchestrator dir at
position 0, beating the gateway's `app/` regardless of editable-install
ordering. Bootstrap is local to the probe CLI and does not affect any
other process or test.

The deeper "two `app/` packages" architecture choice (each service uses
`app/` as its internal namespace) is out of scope for this ADR. The
bootstrap is correct as long as cross-service imports remain a special
case rather than the norm — which they should.

## Probe output (verbatim)

```
Model:     llama3.2  (ollama)
Timestamp: 2026-05-22T15:28:37.903869+00:00
Score:     0.68  (3/4 tasks passed)

Task results:
  [PASS] tool_call_formatting           score=1.00  Correct tool name emitted
  [FAIL] json_schema_adherence          score=0.00  Response is not valid JSON: Expecting ',' delimiter: line 28 column 25 (char 455)
  [PASS] multi_step_reasoning           score=1.00  Correctly called lookup_host first
  [PASS] grounding_discipline           score=0.70  Model did not fabricate but refusal was ambiguous

Measured capability:
  reasoning_tier:          mid
  native_tool_calling:     full
  structured_output:       unreliable
  max_safe_auto_steps:     8
  recommended_strategy:    guided
```

## Measured vs. static — side by side

| Field | Static `KNOWN_MODELS["llama3.2"]` | Measured (this probe) | Delta |
|---|---|---|---|
| `reasoning_tier` | `mid` | `mid` | match ✓ |
| `native_tool_calling` | `partial` | `full` | **upgrade** ⬆ |
| `structured_output` | `prompt_coaxed` | `unreliable` | **downgrade** ⬇ |
| `max_safe_autonomous_steps` | 8 | 8 | match ✓ |
| `recommended_strategy` | `guided` | `guided` | match ✓ |

The two deltas tell the same story from different angles. `llama3.2` will
emit a syntactically correct tool call when asked (hence `full`
tool-calling) but cannot reliably emit a parsable JSON response under
free-form schema constraint (the JSON-schema adherence test failed at
column 25 of line 28 — a delimiter slip mid-document). For Wolf this
means: lean on the typed tool dispatch path (which validates with
Pydantic and rejects malformed calls cleanly), not on the
structured-output fallback's free-form JSON path. The dedicated
`count_alerts_by_severity` tool added earlier this Phase is exactly the
right shape for this model class.

The `recommended_strategy=guided` verdict matches the static estimate,
which means **no strategy-selection change is needed**. The grader hit
the "overall ≥ 0.65 and multi-step passed" branch and called `mid` /
`guided`.

## Decision

- **No change to `DEFAULT_MODEL_ID`.** It stays `llama3.2`. The measured
  capability landed at the same strategy tier the orchestrator was
  already running it under.
- **No change to the static `KNOWN_MODELS["llama3.2"]` entry yet.**
  The deltas are real but small; both compensate at the strategy level.
  When the qwen3:4b and gemma3:4b probes complete (Task 7) the team can
  compare side-by-side and decide whether to amend the static estimates
  in a single sweep.
- **Future severity-style questions should keep routing to dedicated
  tools** (`count_alerts_by_severity`) rather than the structured-output
  fallback — the JSON parse failure here is direct evidence the fallback
  is fragile on this model.

## Alternatives considered

- **Re-grade as `basic` / `pipeline`** based on the JSON failure alone —
  rejected. Three of four probe tasks pass cleanly, including the two
  load-bearing ones for agentic use (multi-step tool calling and
  grounding). Downgrading to `basic` would needlessly choke the model
  out of using tools the dispatcher already validates.
- **Flip `DEFAULT_MODEL_ID` to `qwen3:4b` immediately** — rejected.
  Doc 14's playbook says re-measure before flipping. The qwen3:4b probe
  hasn't run yet on this hardware; the decision must be probe-grounded,
  not just license-grounded.

## Consequences

- Wolf continues to run on `llama3.2` for development with no orchestrator
  config change.
- The `structured_output=unreliable` measurement is a flag to keep an eye
  on if a Phase 3 feature ever depends on free-form JSON from the model.
- This ADR plus a future `0NNN-model-probe-qwen3-4b.md` (and/or
  gemma3:4b) form the evidence base for the eventual
  `0NNN-model-switch-llama3.2-to-<x>.md` decision.
- Rollback is trivial: revert any future `DEFAULT_MODEL_ID` change; this
  ADR records what `llama3.2`'s measured capability was on this exact
  hardware on this date.
