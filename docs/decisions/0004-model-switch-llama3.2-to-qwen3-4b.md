# 0004 — Switch dev default model from `llama3.2` to `qwen3:4b`

**Date:** 2026-05-22
**Status:** accepted
**Decider:** claude-code with explicit human direction
**Related:** ADR 0001 (`llama3.2` baseline), ADR 0002 (`qwen3:4b`),
ADR 0003 (`gemma3:4b`), `docs/14-model-recommendations.md` §"License
filter" and §"Environment-change playbook",
`services/orchestrator/app/config.py` (`DEFAULT_MODEL_ID`).

## Context

`llama3.2:latest` has been Wolf's running dev default since the model
abstraction layer landed in Phase 1.  It works.  Two unrelated pressures
ask whether it should stay the default:

1. **License.** Doc 14 §"License filter" says Wolf must hold the
   open-source line at the model layer the same way it does in code.
   Llama's Community License has a 700M monthly-active-user cap and
   naming requirements — it is not OSI-open and would not pass an
   MSSP's legal review.  Doc 14's verdict: *"shipping Wolf with a Llama
   default contradicts the project's own stated principles."*
2. **Measured capability.** ADRs 0001/0002/0003 ran the live probe on
   `llama3.2`, `qwen3:4b`, and `gemma3:4b` on the same CPU-only dev
   hardware (~16 GB RAM, no GPU).  Probe data is now in hand.

This ADR decides what the **dev default** becomes — i.e. what
`DEFAULT_MODEL_ID` is in `services/orchestrator/app/config.py` going
forward.  The recommended-default-for-shipping question doc 14 raises
is the same answer; this ADR settles both.

## The probe evidence (recap)

Same hardware, same probe code, same window:

| Probe task | `llama3.2` | `qwen3:4b` | `gemma3:4b` |
|---|---|---|---|
| `tool_call_formatting` | 1.00 PASS | 1.00 PASS | 0.00 FAIL (HTTP 400) |
| `json_schema_adherence` | 0.00 FAIL | 1.00 PASS | 1.00 PASS |
| `multi_step_reasoning` | 1.00 PASS | 1.00 PASS | 0.00 FAIL (HTTP 400) |
| `grounding_discipline` | 0.70 PASS | 0.00 FAIL | 0.00 FAIL |
| **Overall score** | 0.68 | **0.75** | 0.25 |
| **Reasoning tier** | mid | mid | basic |
| **Recommended strategy** | guided | guided | pipeline |
| **License** | restricted (Llama Community) | **apache-2.0** | apache-2.0 |

Headline reading: **`qwen3:4b` wins overall** (higher score, Apache
license) and matches `llama3.2` at the strategy tier.  `gemma3:4b` is
out — Gemma 3 4B doesn't have native tool calling (ADR 0003).

## The trade-off worth being explicit about

`qwen3:4b` failed the **grounding-discipline** probe (0.00 vs
`llama3.2`'s 0.70).  The failure shape: given a factual question with
no tools available, qwen3:4b fabricated a specific IP address /
numeric count rather than refusing.  `llama3.2`, in the same setup,
declined cleanly.

This is real but contained:

- **In Wolf's tool-gated agent loop (`guided` strategy, what both
  models use)**, the model can almost always call a tool — and every
  tool result flows through Pydantic-validated dispatch with the
  citation attached.  Fabrication risk in the answer is bounded by
  what the tool actually returned.
- **In `pipeline` strategy (no tools exposed)**, fabrication is the
  real danger.  Neither model is in pipeline mode under the new
  default — both are `guided`.  So this failure shape doesn't trigger
  in normal use.
- **Phase 3's grounding validator** (`docs/06`) is designed exactly to
  catch this: every factual claim in a final answer must trace to a
  real tool result.  Adopting qwen3:4b raises Phase 3's priority
  without weakening Phase 2's guarantees.

The grounding-discipline failure is a reason to **build the validator
sooner**, not a reason to keep an MAU-capped model as the default.

## Decision

**Flip `DEFAULT_MODEL_ID` from `llama3.2` to `qwen3:4b`** in
`services/orchestrator/app/config.py`.  Land that as a one-line
commit that references this ADR.  No other config changes.

`llama3.2` stays in `KNOWN_MODELS` as a documented option — operators
who specifically want it (or who already have it pulled locally) can
set `DEFAULT_MODEL_ID=llama3.2` in their `.env` and the dispatcher will
pick it up unchanged.

Per-tenant model configuration is a separate, later concern (a
`TenantModelConfig` table mirroring `TenantWazuhConfig`).  Until that
exists, the process-level default is what every tenant gets.

## Alternatives considered

- **Stay on `llama3.2` until Phase 3's grounding validator is in.**
  Rejected.  The validator helps both models equally and Phase 3 is
  not next week.  The license posture matters now if anyone external
  pulls the repo and tries to ship.  The probe data also shows the
  switch is net-positive at the strategy level.
- **Wait for `qwen3:8b` to be probable on a GPU.**  Rejected as the
  blocker for the *dev* default — no GPU on the current host.  The
  `qwen3:8b` entry in `KNOWN_MODELS` is the Profile B target for the
  first real GPU-equipped deployment, but it doesn't gate Profile A.
- **Run the structured-output fallback path more aggressively now that
  we know JSON is solid.**  Out of scope for this ADR.  The dispatcher's
  typed path is the right primary; the fallback is the safety net.

## Consequences

- New chat sessions on a re-started orchestrator load `qwen3:4b`
  instead of `llama3.2`.  Apache 2.0 license; no MAU cap.
- The Phase 2 chat path (Wazuh tool calls, citations, multi-turn,
  count_alerts_by_severity) keeps working — the strategy tier is the
  same.  Verification: a manual chat against the user's real Wazuh
  through the Next.js UI after restart.
- **The grounding validator (Phase 3) becomes more important.**  Note
  it in PROGRESS.md as a tier-up of Phase 3 priority.
- Rollback: revert the single commit that changes `DEFAULT_MODEL_ID`
  and restart the orchestrator.  No data migration involved.
- Operators who want `llama3.2` keep it via env override.
- The static `KNOWN_MODELS["qwen3:4b"]` entry remains the static
  estimate for now — amending it to match measured capability (per
  ADR 0002) is a separate cleanup deferred to the same batch as the
  llama3.2 / gemma3:4b amendments (see PROGRESS §4).
