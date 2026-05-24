# 15 — Supported Model Matrix

This document is a **product commitment**, not a recommendation. It states
which model families Wolf must natively support, what "natively support"
concretely requires, and the quality bar all supported models must meet.

The decision to commit to these four families is recorded in
[ADR 0006](decisions/0006-supported-model-families-commitment.md). This
file maintains the living state; ADR 0006 preserves the reasoning.

It complements (does not replace):

- `docs/02-model-abstraction.md` — the abstraction layer itself.
- `docs/14-model-recommendations.md` — which model to *recommend* per
  hardware tier and why.
- `docs/decisions/` — point-in-time ADRs for specific model probes and
  default-flip decisions.

If you are a future Claude Code session, a contributor on a different
machine, or a human reviewer: **read this file before adding, removing,
or changing model support.** The list and the quality bar below are
directives from the project owner.

## The four families Wolf must support natively in development

| Family | Sizes Wolf must support | License | Provider |
|---|---|---|---|
| **Qwen 3** | 4B, 8B, 14B, 32B | Apache 2.0 | Ollama (local) |
| **Llama 3** (3.x / 4 line) | 3B, 8B, up to the largest size dev hardware can run | Llama Community License | Ollama (local) |
| **Gemma 3** | 4B, 12B, 27B | Gemma license | Ollama (local) |
| **GLM 5.1** | ~32B class (dense) | MIT | Ollama (local) |

"Natively" means **running directly on the dev machine via Ollama (or an
equivalent local runtime), not via a hosted API.** The frontier-API path
remains available (see ADR 0005) but is not what "supported" means here.

### Why these four

- **Qwen 3** — strongest agentic capability per parameter; Apache 2.0;
  the steady-state local default (see ADR 0004).
- **Llama 3** — broadest ecosystem; widely deployed; Wolf must work
  against it even though its license bars it from being Wolf's
  *recommended* shipping default (see `docs/14` §"License filter").
- **Gemma 3** — Google's open release; commonly chosen by operators
  with policies favoring Google-origin software. No native tool
  calling means the agent loop must work via the `pipeline` strategy
  for this family (see ADR 0003).
- **GLM 5.1 ~32B** — strongest MIT-licensed open agentic model in the
  workstation-GPU tier; the natural top-of-line local choice.

### Hardware envelope for development

Dev machines must be able to exercise the matrix up to **workstation-GPU
tier** (Profile C in `docs/13-system-requirements.md`): 24 GB+ VRAM,
enough to run a 32B dense model locally at Q4. The 16 GB CPU-only dev VM
(used through Phase 2) is the *floor*, not the ceiling — it can only
exercise the small end of each family. Phase 3 onward, expect to verify
against the full matrix as workstation hardware becomes available.

## What "natively support" requires — the concrete checklist

For each model in the matrix, Wolf must have **all six** of the following
before that model can be claimed as supported:

1. **`KNOWN_MODELS` entry** in
   `services/orchestrator/app/models/interface.py` with a complete
   `CapabilityDescriptor` (context_window, native_tool_calling,
   reasoning_tier, recommended_strategy, license_class).
2. **A live capability probe** run against the model on representative
   hardware via `tools/model_probe/`, with results recorded as an ADR
   in `docs/decisions/`. The static `KNOWN_MODELS` entry must be
   amended to match the probe's measured capability (see ADR 0004 for
   the pattern).
3. **A passing end-to-end agent-loop test** against the operator's real
   Wazuh (or, in CI, a Wazuh fixture). The test must exercise: tool
   call → tool dispatch → second model call → grounded answer with
   citation.
4. **A documented strategy assignment.** Each model must run cleanly
   under at least one of the three strategies (`frontier`, `guided`,
   `pipeline`) without prompt-engineering workarounds outside the
   strategy itself.
5. **Smoke coverage in `tools/model_probe` and
   `services/orchestrator/app/management/smoke_wazuh.py --all-tools`**
   — the model must complete a multi-tool smoke run end-to-end.
6. **An entry in `docs/14-model-recommendations.md`** under the
   appropriate hardware tier, so operators picking a model on the
   recommended-defaults path see it.

A model in the matrix that fails any of the six is a **gap to close**,
not a model to drop. Drop a family from the matrix only with explicit
project-owner approval.

## The development quality bar

All four families must work to the same standard. Specifically:

### Efficient

- The agent loop must not waste tokens or calls on a per-model basis.
  Per-model prompt scaffolding lives in `services/orchestrator/app/agent/`
  and `services/orchestrator/app/models/`; if a workaround is needed for
  one family, it must be implemented in a way that does not penalize the
  others.
- Strategy selection (`basic` / `guided` / `frontier` / `pipeline`)
  must be driven by measured `CapabilityDescriptor`, not hard-coded
  per-family conditionals.

### Robust

- Tool dispatch must handle each family's tool-call format
  (native-JSON tool calls for Qwen/Llama/GLM; pipeline-style text
  parsing for Gemma) without family-specific branches leaking into
  business logic.
- Pydantic input validators on tool schemas must accept the
  reasonable variations small models emit (lenient `min_level`,
  relative-time parsing, explicit-null stripping — see existing
  validators in `services/orchestrator/app/tools/alerts.py` and
  `dispatcher.py`).

### Stable

- A new release within a supported family (e.g. Qwen 3.6 over Qwen 3)
  must not break Wolf. The contract is the `CapabilityDescriptor`, not
  the model version string. Adding a new size or revision is a
  `KNOWN_MODELS` entry + a probe ADR — not a code change in the agent
  loop.
- Default-model switches across the matrix happen via the playbook in
  `docs/14` §"Environment-change playbook" — never ad-hoc.

### Reliable

- Every supported model must pass the agent-loop test against real
  Wazuh in CI on the `test-local` job (see `docs/14` §"CI matrix").
  A failure on any matrix model blocks the merge — that is how the
  "all four families work" promise stays real.
- Citations must be attached on every tool-grounded answer, for every
  model. If a model can answer correctly but cannot attach citations,
  the grounding validator (Phase 3) must reject the answer regardless
  of which family produced it.

## Production posture — user choice

In production, Wolf does not pick a model for the operator. The operator
picks:

- **From the matrix above** if they want a locally-hosted model and have
  the hardware. Wolf surfaces all four families and their available
  sizes; the operator selects one or more.
- **Multiple models simultaneously** if the operator wants per-task
  routing (e.g. Qwen 3 8B for the chat tier, GLM 5.1 32B for deep
  investigations). The model abstraction layer already supports this;
  the UI exposes it.
- **A hosted API** (Claude, GPT, Gemini, OpenRouter routes) if the
  operator chooses to pay or has frontier-tier needs and no local GPU.
  Wolf supports them; never requires them.

The principle from `docs/00-vision-and-scope.md` holds: **no paid
dependency is ever required.** A clean local deployment using any of the
four families above must always be a viable production path.

## Implementation status — as of 2026-05-24

| Family | Smallest size | Largest size | Status |
|---|---|---|---|
| Qwen 3 | 4B (CPU probe ADR 0002, GPU re-confirmed in ADR 0009/0010) | 32B | 4B + 8B (GPU probe ADR 0010, tight fit) in `KNOWN_MODELS`; 14B/32B entries present but unprobed (require workstation GPU) |
| Qwen 3.5 | 4B (GPU probe ADR 0009; regression — see ADR) | — | 4B in `KNOWN_MODELS` as `basic`/`pipeline` per probe; license Apache 2.0 confirmed |
| Llama 3 | 3B (probed, ADR 0001) | — | 3.2 in `KNOWN_MODELS`; larger sizes pending workstation hardware |
| Gemma 3 | 4B (probed, ADR 0003) | — | 4B in `KNOWN_MODELS`; 12B/27B entries pending |
| GLM 5.1 | — | ~32B (entry, unprobed) | Static `KNOWN_MODELS` entry only — no probe yet, no live verification |

**Known gaps to close, in priority order:**

1. **GLM 5.1 ~32B live probe.** Currently the only family with zero
   measured data. Requires workstation-GPU hardware. Block on
   hardware availability, not on Wolf-side code.
2. **Gemma 3 12B / 27B entries and probes.** Once the
   workstation-GPU tier is available, run the probe and write the
   ADR.
3. **Qwen 3 14B / 32B live probes** on the same workstation
   hardware.
4. **qwen3.5:4b re-probe** after the next Ollama qwen3.5 release or
   the official model-card publication — ADR 0009's `none` /
   `unreliable` measurement may be a chat-template/glue issue rather
   than a model-level limitation. Worth re-checking before assuming
   the regression is permanent.
5. **CI `test-local` job formalization** so every matrix model is
   exercised on every PR, not only the current default.

## Maintenance

This file is a **directive**, not a snapshot. Update it when:

- A family is added or removed from the matrix (owner decision only).
- A new size becomes a first-class supported size within an existing
  family.
- The quality bar changes (almost never; if it does, link the ADR that
  explains why).

Do **not** update this file for routine probe results, model version
bumps, or default flips — those go in `docs/decisions/` and
`docs/CHANGELOG.md` per the protocol in
`docs/11-claude-code-instructions.md`.
