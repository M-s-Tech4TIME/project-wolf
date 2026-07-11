# Wolf — An Agentic AI Platform for Wazuh

> **Wolf** — pack-aware, hunts in coordination, recognizes threats early.

This repository contains the **complete planning and specification bundle** for an
open-source, model-agnostic, agentic AI platform built to help security analysts,
detection engineers, and MSSPs operate Wazuh deployments with greater speed and
precision.

This bundle is written to be **fed directly to Claude Code** (or any capable coding
agent) as the authoritative source of truth for building the system. Every document
here is intended to be read, referenced, and implemented against.

> **Project status (2026-07-12):** active development is **paused**; everything
> through Phase 6-f is shipped and CI-green. Resuming? Start at
> [`docs/HANDOVER.md`](docs/HANDOVER.md) (state, queue, rules), then
> [`ONBOARDING.md`](ONBOARDING.md) (environment setup). Claude Code sessions:
> paste [`docs/CLAUDE-RESUME-PROMPT.md`](docs/CLAUDE-RESUME-PROMPT.md).

---

## What this project is

An **agentic AI layer that sits beside a Wazuh deployment** — never inside it — and
helps humans **detect, investigate, respond to, report on, and document** security
events. It connects to the Wazuh Indexer (OpenSearch) for reading and to the Wazuh
Server API for fleet introspection and (human-approved) active response.

It is **not** a replacement for an analyst. It is a force multiplier with hard
structural guarantees: it can read and reason freely, it can *propose* actions, but
it can never alter or delete a log, change a configuration, or execute a
state-changing action without an authenticated human approval.

## Core principles (non-negotiable)

1. **Separate platform.** Wolf runs as its own service. A failure in Wolf
   must never degrade detection or take down Wazuh.
2. **Read freely, change never (without a human).** The AI can read and analyze
   without limit. It can never mutate logs or configuration. State-changing actions
   are *proposed* by the AI and *approved* by a human before execution.
3. **Structural safety, not prompt safety.** Guarantees are enforced by
   architecture — credentials, tool schemas, dispatch logic — never by instructions
   in a prompt that an attacker could override.
4. **Model-agnostic and open-source.** Any LLM — Claude, GPT, Gemini, DeepSeek, or a
   local model via Ollama — can power the platform. No vendor lock-in. No mandatory
   paid subscription. The whole project is open-source.
5. **Precision lives in the architecture.** Strict tools, schema validation,
   evidence grounding, and human approval make the *platform* reliable regardless of
   which model is plugged in. The model's quality changes *how much autonomy* it is
   given, not whether the platform is trustworthy.
6. **Multi-organization native.** Built for MSSPs from day one. A single-org deployment is
   simply "one organization." There is no unorganizationed code path.
7. **Everything is audited.** Every model call, tool call, proposal, approval, and
   execution is recorded immutably and is organization-scoped.

## Who it serves

SOC analysts (triage and response), detection/security engineers (rule and decoder
work), MSSPs (many isolated clients), and internal single-org security teams. The
same codebase serves all of them; tenancy is the dial.

## How to read this bundle

Read the documents in order. They build on each other.

| # | Document | What it covers |
|---|----------|----------------|
| 00 | [`docs/00-vision-and-scope.md`](docs/00-vision-and-scope.md) | The motive, the problem, what is in and out of scope |
| 01 | [`docs/01-architecture.md`](docs/01-architecture.md) | System architecture, components, data flow, trust tiers |
| 02 | [`docs/02-model-abstraction.md`](docs/02-model-abstraction.md) | The model-agnostic layer; how "any model" works honestly |
| 03 | [`docs/03-tool-catalog-and-capability-tiers.md`](docs/03-tool-catalog-and-capability-tiers.md) | Every tool, the read/propose/execute model, enforcement |
| 04 | [`docs/04-approval-gateway.md`](docs/04-approval-gateway.md) | The proposal lifecycle, approvals, active response |
| 05 | [`docs/05-multi-organization.md`](docs/05-multi-organization.md) | Organization isolation, the enforcement points, edge cases |
| 06 | [`docs/06-knowledge-and-rag.md`](docs/06-knowledge-and-rag.md) | Live state vs stable knowledge; the RAG design |
| 07 | [`docs/07-security-and-threat-model.md`](docs/07-security-and-threat-model.md) | Threat model, prompt injection, hardening |
| 08 | [`docs/08-reporting-and-orchestration.md`](docs/08-reporting-and-orchestration.md) | Reports, documentation, case management |
| 09 | [`docs/09-tech-stack-and-repo-layout.md`](docs/09-tech-stack-and-repo-layout.md) | Suggested stack, repository structure |
| 10 | [`docs/10-build-roadmap.md`](docs/10-build-roadmap.md) | Phased build plan, what to build first and last |
| 11 | [`docs/11-claude-code-instructions.md`](docs/11-claude-code-instructions.md) | Direct working instructions for the coding agent |
| 12 | [`docs/12-glossary.md`](docs/12-glossary.md) | Terminology reference |
| 13 | [`docs/13-system-requirements.md`](docs/13-system-requirements.md) | Developer machine + four runtime hardware profiles |

## The one honest caveat

"Identical precision on a free local model and on a frontier model" is **not**
literally achievable — model capability is real and differs. This bundle does not
pretend otherwise. Instead it specifies a platform that stays **robust and precise
regardless of model**, by putting the guarantees in the architecture and *adapting
the agent's strategy to the model's capability tier*. A strong model is given more
autonomy; a weaker one is given smaller, more scaffolded tasks. The floor of
reliability is constant. The ceiling of autonomy scales with the model. See
[`docs/02-model-abstraction.md`](docs/02-model-abstraction.md) for the full honest
treatment.
