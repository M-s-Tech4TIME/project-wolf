# 0013 — Grounding judge as a separately-configured model

**Date:** 2026-05-27
**Status:** accepted
**Decider:** human (project owner) with claude-code drafting
**Related:** `docs/06-knowledge-and-rag.md` (validator design),
[ADR 0005](0005-phase2-exit-criterion-frontier-verification.md) (hosted-API
provider plumbing), [ADR 0006](0006-supported-model-families-commitment.md)
(four-family local commitment), [ADR 0009](0009-model-probe-qwen3.5-4b.md)
(qwen3.5:4b regression), [ADR 0010](0010-model-probe-qwen3-8b.md)
(qwen3:8b on RTX 4050 Laptop), Phase 3 Slice 2B's grounding-validator
commit (`e0e94f4`) which shipped the architecture, Phase 3 Slice 3's
end-to-end retest which surfaced the recursive-validation problem
documented here.

## Context

Slice 2B shipped the grounding validator wired to the same `ModelProvider`
the agent loop used for chat (qwen3:4b). The Slice 3 end-to-end retest
on the new test agent produced two observations that motivated this ADR:

1. **The validator does flag false-negative claims** ("no alerts were
   found" off a single 0-hit search → marked `unsupported`). That part
   of the design works.
2. **On richer evidence prompts the judge returns malformed JSON** and
   the validator degrades gracefully (counts surfaced as `None`). qwen3:4b
   judging its own draft is a recursive-validation setup: the model whose
   grounding discipline the validator exists to compensate for (ADR 0002)
   is also the model being asked to grade that discipline.

The structural fix is "use a different/stronger model for the judge than
for the chat." This ADR establishes the env-driven mechanism for that and
captures the model-selection findings honestly.

## What we tried

| Candidate | Outcome | Notes |
|---|---|---|
| **qwen3.6:27b** | **Cannot load on this dev machine** | Ollama: `model requires more system memory (16.1 GiB) than is available (11.4 GiB)`. The host has 23 GiB RAM total but two VMware VMs (the Wazuh server and the test agent) consume ~6 GiB combined, plus Firefox / VS Code / Postgres / orchestrator overhead. No swap-budget remediation; the model literally won't load. |
| **qwen3.5:9b** | **0.50** (regression vs qwen3:4b's 0.75) | Same Qwen 3.5 family JSON-syntax bug ADR 0009 documented for qwen3.5:4b. Not size-specific; the 3.5 line on Ollama has a structured-output glue issue at every size we've tried. Re-probe gated on the next Ollama qwen3.5 release. |
| **qwen3:8b** | 0.75 (same as qwen3:4b) | Per ADR 0010. Tight-fit on the 6 GB GPU (~85% GPU / 15% CPU at default 4096 ctx). The descriptor doesn't improve over qwen3:4b at the probe-task level; the additional parameters are likely useful on harder real-world judge prompts even though the probe can't measure it. |
| **nvidia/nemotron-3-super-120b-a12b:free via OpenRouter** | Not re-probed; ADR 0005's hosted path is wired | The strongest grounding judge available to Wolf today. Requires an OpenRouter API key (operator-supplied). Not always-free in principle — the route is free at the time of writing but free tiers churn. Useful as the deliberate "stronger judge when operator can supply a key" path; not the default. |

## Decision

**Add three settings that let the operator configure the grounding judge
independently from the chat model:**

- `GROUNDING_JUDGE_MODEL_ID` — model id for the judge. Empty = use the
  chat model (current behaviour, backward-compat default).
- `GROUNDING_JUDGE_MODEL_PROVIDER` — provider (`ollama`, `openai`,
  `anthropic`). Empty = same as `DEFAULT_MODEL_PROVIDER`.
- `GROUNDING_JUDGE_API_KEY_REF` — secret backend key holding the API
  token (only meaningful for the `openai`/`anthropic` provider paths).

**Code shape**: a new `get_grounding_judge_model()` helper alongside the
existing `get_model_for_tenant()` in `app/agent/model_resolver.py`.
`chat.py` builds the judge provider once per request via that helper
and hands it to `GroundingValidator(judge_provider)`. When the
override env vars are empty, the helper returns the chat provider
unchanged — single-model deployments work without change.

**Operator recommendation for this dev machine (RTX 4050 Laptop, 23 GiB
RAM, two VMs running)**: leave the override empty for development; the
existing qwen3:4b judge is acceptable when it works and degrades safely
when it doesn't. For a "stronger judge" experience on this exact
hardware, the realistic upgrade is `GROUNDING_JUDGE_MODEL_ID=qwen3:8b`
(probed in ADR 0010, no upgrade installation required, expect ~5-10 s
of additional latency per chat answer). For production / workstation-
GPU hardware, `GROUNDING_JUDGE_MODEL_ID=qwen3.6:27b` becomes viable
once the host has the RAM (24+ GiB free); for the most demanding
deployments the hosted-Nemotron path via OpenRouter
(`GROUNDING_JUDGE_MODEL_PROVIDER=openai`,
`GROUNDING_JUDGE_API_KEY_REF=model.openrouter.api_key`,
`OPENAI_BASE_URL=https://openrouter.ai/api`,
`GROUNDING_JUDGE_MODEL_ID=nvidia/nemotron-3-super-120b-a12b:free`)
is the strongest available judge today.

## Alternatives considered

- **Make qwen3.6:27b the bundled default and document the RAM
  requirement.** Rejected for *this development environment* — the
  hardware can't run it, so Wolf would ship a default that doesn't
  work for the most common dev setup. ADR 0006's "Wolf must run on
  Profile B tight-end hardware (6 GB VRAM, ~24 GiB RAM)" commitment
  is load-bearing. Operators with workstation-class hardware will
  set the override; the default has to work for the floor.
- **Use a heuristic-only validator (token overlap) instead of LLM-as-
  judge.** Rejected. The canonical Slice 2B failure mode (the
  "block for 60 seconds per the ignore parameter" embellishment)
  shares tokens with the evidence but combines them in a way the
  evidence doesn't support. Heuristics miss this; semantic judges
  catch it. The LLM-as-judge design from doc 06 stands.
- **Add a heuristic-overlap fallback on top of the LLM judge.**
  Reasonable; deferred. Worth doing once we've measured how often
  the LLM judge fails vs how often the heuristic catches a real
  miss. Today the failure rate of the LLM judge is well-bounded
  (graceful degrade returns the original answer) so the additional
  code complexity isn't justified yet.
- **Bundle a smaller dedicated judge model (e.g. a 1B classifier
  fine-tuned for claim verification).** Rejected for this slice.
  Maintaining a fine-tuned judge is a different kind of project;
  not the Wolf scope today.
- **Always run the validator through OpenRouter Nemotron.** Rejected
  as the default. The "no paid dependency ever required" principle
  from `docs/00` extends to hosted-API tiers that could change
  pricing or rate limits. Wolf's default must run with zero
  internet egress.

## Consequences

- **`config.py` gains three settings**; defaults preserve current
  behaviour. Existing operators see no change unless they set the
  override.
- **`model_resolver.py` gains `_build_provider()` helper and
  `get_grounding_judge_model()`** alongside the existing chat-model
  resolver. Both reuse the same provider-construction code path.
- **`chat.py` builds the judge provider once per request** and hands
  it to `GroundingValidator(judge_provider)`. No other code touches
  the validator's model selection.
- **Empirical landscape recorded**: qwen3:8b is the realistic
  local-judge upgrade on this hardware. qwen3.6:27b is the right
  pick on a host with the RAM. Hosted Nemotron is the strongest
  judge available; not free in perpetuity.
- **Frontend now surfaces the validator verdict** (counts as a
  badge, `[unverified]` markers inline) — operators can SEE when
  the judge marked claims unsupported. Previously the data flowed
  through the API but the UI didn't render it.
- **No new tests required for this ADR** beyond what's already
  exercised by `test_grounding_validator.py`. The factory branch
  is a one-line conditional on `grounding_judge_model_id`.
- **Rollback path**: this commitment is reversible. The settings
  default to "use chat model" — emptying the override returns to
  Slice 2B behaviour. Removing the helper function and reverting
  chat.py is a small surgical edit.

## Follow-ups queued but not blocking this ADR

- **Heuristic+LLM hybrid validator** if rich-corpus operation shows
  the LLM judge failing too often.
- **Re-probe qwen3.5 family** after the next Ollama qwen3.5
  release; if the structured-output bug is fixed, qwen3.5:9b
  becomes the recommended local judge.
- **Bundle install-script step** that prompts the operator for a
  judge-model preference at first run (`qwen3:4b` default,
  `qwen3:8b` recommended if RAM allows, `qwen3.6:27b` for
  workstation-GPU). Belongs in doc 16's install-script spec.
