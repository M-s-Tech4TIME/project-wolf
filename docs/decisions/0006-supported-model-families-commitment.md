# 0006 — Supported model families commitment

**Date:** 2026-05-23
**Status:** accepted
**Decider:** human (project owner) with claude-code drafting
**Related:** `docs/15-supported-model-matrix.md` (the living matrix this
ADR establishes), `docs/14-model-recommendations.md` (recommended
defaults — a distinct concern), `docs/02-model-abstraction.md` (the
abstraction layer that makes multi-family support possible), ADR 0001
(Llama 3.2 probe), ADR 0002 (Qwen 3 4B probe), ADR 0003 (Gemma 3 4B
probe), ADR 0004 (Llama → Qwen default flip), ADR 0005 (Phase 2 exit
criterion — frontier-API verification).

## Context

Phase 2 is closed (ADR 0005). Before moving into Phase 3 (RAG +
grounding validator), the project owner reviewed the open-model
landscape — generic survey of locally-hostable agentic LLMs across
four hardware tiers, plus targeted discussion of GLM 5.1 and the Kimi
family.

Two product questions surfaced from that review:

1. **Should Wolf bet on a single default model and recommend operators
   use it, or should the platform commit to supporting multiple
   families as first-class citizens?**
2. **If multiple, which families specifically — and on what hardware
   posture?**

These questions matter now (not later) because:

- Phase 3 introduces the grounding validator, which is the first
  component whose correctness depends sharply on the model's
  instruction-following discipline. If Wolf is going to behave the
  same across N model families, the validator and its tests must be
  designed against that promise from the start, not retrofitted.
- The `CapabilityDescriptor` abstraction (built in Phase 1, used since)
  was designed precisely to make the platform model-agnostic. A
  commitment to multi-family support is the contract that abstraction
  is meant to honor. Without an explicit commitment, the abstraction
  risks drift into "Qwen + fallbacks" rather than "first-class N
  families."
- Llama 3 — widely deployed but license-restricted — sits in an
  awkward spot. Doc 14 §"License filter" already says Llama is not
  Wolf's recommended *default*; this ADR clarifies whether Wolf must
  still *support* it. The two are distinct, and the codebase needs the
  distinction made explicit.

## Decision

**Wolf commits to natively supporting four model families as
first-class local-host citizens in development:**

| Family | Sizes | License | Provider |
|---|---|---|---|
| Qwen 3 | 4B, 8B, 14B, 32B | Apache 2.0 | Ollama (local) |
| Llama 3 (3.x / 4 line) | 3B, 8B, larger sizes as dev hardware permits | Llama Community License | Ollama (local) |
| Gemma 3 | 4B, 12B, 27B | Gemma license | Ollama (local) |
| GLM 5.1 | ~32B dense | MIT | Ollama (local) |

"Natively" means running locally via Ollama (or equivalent local
runtime) on dev hardware — not via a hosted API. The hosted-API path
(established in ADR 0005) remains supported but is a separate concern.

**Production posture:** Wolf does not pick a model for the operator.
Operators select from the matrix above (one or multiple) based on their
hardware, or choose a hosted API. The platform's "no paid dependency is
ever required" principle (`docs/00`) is preserved by the matrix above
being fully usable on free, local infrastructure.

**Operationalization:** the living matrix, the six-item "natively
support" checklist (KNOWN_MODELS entry + live probe + ADR + agent-loop
test + strategy assignment + smoke coverage + doc 14 entry), the
development quality bar (efficient / robust / stable / reliable), and
the current implementation gaps are documented in
`docs/15-supported-model-matrix.md`. This ADR establishes the
commitment; doc 15 maintains the state.

## Alternatives considered

- **Single default model (Qwen 3 only), recommend operators use it.**
  Rejected. The `CapabilityDescriptor` abstraction was built precisely
  to keep the platform model-agnostic; collapsing to one family would
  let that abstraction rot. It would also concentrate risk: a single
  upstream license change, training-data controversy, or capability
  regression in one quarterly release would destabilize the whole
  platform. Multi-family support is structurally cheaper now (when the
  agent loop is small) than later.

- **Wider matrix — also include DeepSeek V4, Mistral Small, Kimi K2,
  Phi 4.** Rejected as a *commitment*, accepted as *opportunistic
  registration*. The commitment list is intentionally narrow because
  every family carries an ongoing maintenance cost: a probe ADR per
  release, an agent-loop test, doc upkeep, CI matrix slot. Four
  families is a load the project can carry from Phase 3 onward;
  six-to-eight is not. Additional families can still receive
  `KNOWN_MODELS` entries (as Wolf already has for DeepSeek and
  Nemotron via ADR 0005), but they are not first-class — operators
  choose them at their own risk, without the full quality bar
  applied.

- **Drop Llama 3 from the matrix because its license fails the
  "recommended default" criterion in doc 14.** Rejected. "Recommended
  default" and "supported" are distinct concerns. Llama is the most
  widely deployed open-weights family on the planet; an operator who
  arrives at Wolf already running Ollama almost certainly has a Llama
  model pulled. Failing to support Llama would create a friction
  point with no offsetting benefit, since Wolf's existing license
  posture (`license_class` on the descriptor) already lets the UI
  warn operators about Llama's restrictions if needed.

- **Include Kimi K2 in the matrix.** Rejected. Kimi K2 is purpose-built
  for agentic use and would arguably be the best technical fit for
  Wolf's loop, but it is not realistically local-hostable on any
  hardware tier short of multi-GPU server (1T MoE, ~250 GB weights
  even at Q4). The matrix is for *native* local support; Kimi K2
  belongs on the hosted-API path alongside Nemotron (ADR 0005).

- **Defer the commitment until after Phase 3, decide once the
  grounding validator's per-family behavior is understood.** Rejected.
  Phase 3 design decisions (validator strictness, retrieval-grounded
  citation format, prompt scaffolding) need to be made *against* a
  known multi-family commitment, not in a vacuum. Deferring would
  recreate the same problem after building a non-trivial slice of
  Phase 3.

## Consequences

- **Doc 15 becomes a directive document** that future Claude Code
  sessions and contributors must consult before adding, removing, or
  changing model support. The auto-memory has been updated with a
  pointer.

- **Four probe ADRs are now expected** as workstation-GPU hardware
  becomes available, in priority order: GLM 5.1 ~32B (zero measured
  data today), Gemma 3 12B/27B, Qwen 3 14B/32B, Llama at the largest
  size dev hardware can run. Each follows the ADR 0001/0002/0003
  pattern. These are not blocked on Wolf-side code — they are blocked
  on hardware.

- **Phase 3 design is constrained by this commitment.** The grounding
  validator and any retrieval-augmented prompt scaffolding must be
  designed to work across all four families' tool-call formats
  (native-JSON for Qwen / Llama / GLM; pipeline-text-parsing for
  Gemma — see ADR 0003). Family-specific branches in business logic
  are explicitly out of bounds; per-family behavior lives only in
  `services/orchestrator/app/models/` and the strategy implementations.

- **CI gains a matrix-formalization task.** Today the `test-local` job
  runs against a single chosen default. The commitment implies the
  matrix CI design described in `docs/14` §"CI matrix" must be
  formalized — at minimum every supported family must have a smoke
  job exercising the agent loop, with the smallest size of each
  family treated as the blocking gate. Scheduling this is a Phase 3
  task; the design is doc 14's, the formalization is owed.

- **The maintenance cadence increases modestly.** Each quarterly
  re-evaluation (per doc 14 §"How 'best model' evolves") now spans
  four families instead of one. This is the deliberate cost of the
  commitment.

- **Rollback path.** This commitment is reversible. A future ADR could
  remove a family from the matrix (e.g. if Meta's Llama license
  becomes incompatible with even "supported but not recommended"
  posture, or if a family's capability degrades across a generation).
  The mechanical change is small: remove from doc 15's matrix, remove
  the `KNOWN_MODELS` entries, remove the CI smoke job. The new ADR
  must cite this one as superseded.

- **No code changes are required by this ADR.** The platform already
  has the abstractions (`CapabilityDescriptor`, `ModelProvider`
  protocol, strategy selector) that make the commitment honorable.
  This ADR makes the *intent* explicit; subsequent probe ADRs will
  exercise it.
