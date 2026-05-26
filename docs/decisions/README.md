# Architectural Decision Records (ADRs)

This directory holds the **architectural decision records** for Wolf — short,
date-stamped write-ups of decisions that shape the system's structure,
defaults, or constraints.

## What an ADR is

An ADR captures a single decision and the reasoning behind it: what we
chose, what we rejected, why, and what we'd do differently if the trade-off
changed. ADRs are append-only. We never rewrite a past ADR — if a decision
is later reversed, the new ADR references the old one as superseded.

## When to write one

Write an ADR whenever a decision:

- Changes a project default (default model, default storage backend,
  default port, etc.).
- Locks in a structural choice the whole codebase will depend on.
- Records the result of a measurement that future decisions will rely on
  (e.g. capability-probe results, benchmark numbers).
- Documents a deliberate deviation from the planning bundle (`docs/00-13`).

Routine fixes, small refactors, and one-off task completions belong in
`docs/CHANGELOG.md`, not here.

## Naming convention

```
0NNN-short-kebab-title.md
```

- `0NNN` is a four-digit zero-padded sequence number starting at `0001`.
  Increment by one for each new ADR; never re-use numbers.
- `short-kebab-title` is a lowercase, hyphen-separated phrase that gives
  a reader the gist without opening the file.

Examples:

- `0001-model-probe-llama3.2-baseline.md`
- `0042-storage-postgres-over-sqlite.md`
- `0103-model-switch-llama3.2-to-qwen3-4b.md` (would reference 0001)

## File template

```markdown
# 0NNN — <title>

**Date:** YYYY-MM-DD
**Status:** proposed | accepted | superseded by 0NNN | deprecated
**Decider:** human / claude-code / mixed
**Related:** links to other ADRs, doc sections, or commits

## Context
What problem are we solving? What changed in the environment that made
this decision necessary now?

## Decision
The choice we're making, stated in one or two sentences.

## Alternatives considered
- **<Alt A>** — why not.
- **<Alt B>** — why not.

## Consequences
What becomes easier / harder as a result. Any follow-up work this
implies. Any rollback path.
```

## Index of ADRs in this directory

| # | Title | Status |
|---|---|---|
| 0001 | `model-probe-llama3.2-baseline.md` — first live probe of llama3.2 on the dev VM (CPU-only) | accepted |
| 0002 | `model-probe-qwen3-4b.md` — qwen3:4b probe; recommended Apache-licensed candidate | accepted |
| 0003 | `model-probe-gemma3-4b.md` — gemma3:4b probe; ruled out (no native tool calling) | accepted |
| 0004 | `model-switch-llama3.2-to-qwen3-4b.md` — dev default flipped to qwen3:4b | accepted |
| 0005 | `phase2-exit-criterion-frontier-verification.md` — frontier-API exit criterion met (Nemotron 120B via OpenRouter) | accepted |
| 0006 | `supported-model-families-commitment.md` — Wolf commits to native local support for four families (Qwen 3, Llama 3, Gemma 3, GLM 5.1 ~32B) | accepted |
| 0007 | `native-distribution-via-system-packages-and-install-script.md` — native delivery channel will be `.deb`/`.rpm` + systemd, fronted by an install script (GitLab-style hybrid) | accepted (positioning amended by 0008) |
| 0008 | `native-primary-docker-supplementary.md` — native delivery is primary; Docker is baseline-supported (not promoted); dev environment uses system Postgres | accepted |
| 0009 | `model-probe-qwen3.5-4b.md` — qwen3.5:4b GPU probe; regression vs qwen3:4b on tool calling; supported but not recommended; NO default flip | accepted |
| 0010 | `model-probe-qwen3-8b.md` — qwen3:8b GPU probe (tight fit 85% GPU/15% CPU); same measured capability as qwen3:4b; KNOWN_MODELS amended | accepted |
| 0011 | `model-probe-granite3.3-8b.md` — opportunistic probe of IBM Granite 3.3 8B (Apache 2.0); 0.25 score; native tool calling works but structured-output fails Wolf's schema; outside ADR 0006 matrix | accepted |
| 0012 | `embedding-stack-ollama-vs-sentence-transformers.md` — keep both adapters; default Ollama (lean wheels, ADR 0007); sentence-transformers as opt-in extra `embeddings-local` for throughput / precision workloads | accepted |
| 0013 | `grounding-judge-separate-model.md` — env-driven `GROUNDING_JUDGE_MODEL_ID` lets the operator route the validator to a stronger judge (qwen3:8b locally, hosted Nemotron via OpenRouter). qwen3.6:27b doesn't fit this dev box's RAM; qwen3.5:9b regresses; qwen3:8b is the realistic local upgrade. | accepted |

_Update this table whenever you add a new ADR._
