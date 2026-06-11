# 02 — Model Abstraction Layer

This document addresses the most ambitious requirement of the project: **the user
chooses their own model, the platform is fully open-source, there are no limits, and
it works robustly with free local models or paid frontier models alike.**

This is achievable — with one honest qualification stated up front.

## The honest qualification

"Identical precision from a free 7B local model and from a frontier model" is **not
literally possible.** Model capability is real and it differs. A small local model
is genuinely worse at long-horizon, multi-step security reasoning than Claude, GPT,
or Gemini. No abstraction layer can erase that gap.

What **is** achievable, and what this project commits to:

> The platform stays **robust, safe, and precise regardless of which model is
> plugged in.** The model's capability changes *how much autonomy the agent is
> given* — not whether the platform can be trusted. The floor of reliability is
> constant. The ceiling of autonomy scales with the model.

The reason this works: **precision is engineered into the architecture, not bought
from the model.** Strict tool schemas, output validation, evidence grounding,
deterministic scaffolding, and mandatory human approval make the system trustworthy.
A weaker model simply does smaller, more constrained steps with more scaffolding
around it; a stronger model is trusted with larger, more autonomous steps. Either
way the answers are grounded and the actions are gated.

This is the design that delivers the user's real intent — *no vendor lock-in, no
mandatory spend, robust output either way* — without promising something false.

## The model provider interface

The orchestrator never calls a model SDK directly. It calls one internal interface,
and a **provider adapter** implements that interface for each backend.

Required adapters at minimum:

- **Anthropic** (Claude)
- **OpenAI** (GPT)
- **Google** (Gemini)
- **DeepSeek**
- **Ollama** (local models — Llama, Mistral, Qwen, DeepSeek-distill, etc.)
- **OpenAI-compatible generic** — a catch-all for any endpoint exposing the OpenAI
  API shape (vLLM, LM Studio, LocalAI, OpenRouter, and others)

The interface must normalize, at minimum:

- **Chat completion** with a system prompt and message history.
- **Tool / function calling** — the single most important normalization. Providers
  differ in tool-call format. The adapter converts the platform's canonical tool
  schema to the provider's format and converts tool-call responses back to canonical
  form. For models with weak or no native tool-calling, the adapter implements a
  **structured-output fallback** (see below).
- **Streaming** of tokens.
- **Token accounting** and context-window limits.
- **Structured JSON output** with schema enforcement where the provider supports it,
  and a parse-validate-retry loop where it does not.
- **Error and rate-limit handling**, normalized to a common error taxonomy.

A new provider is added by writing one adapter. Nothing else in the system changes.

## The capability descriptor

Every configured model carries a **capability descriptor** — a small declared
profile the orchestrator reads to decide how to drive the agent. Fields:

- `context_window` — usable token budget.
- `native_tool_calling` — full / partial / none.
- `reasoning_tier` — `frontier` / `strong` / `mid` / `basic`.
- `structured_output` — schema-enforced / prompt-coaxed / unreliable.
- `max_safe_autonomous_steps` — how many tool-calling iterations to allow before
  forcing a checkpoint.
- `recommended_strategy` — see strategies below.

The descriptor can be **shipped as a default per known model** and **overridden** by
the operator. It is also **measured**: the platform ships a self-test suite (see
"Capability probing") that empirically grades a newly configured model.

## Capability tiers and matched strategies

The orchestrator runs one of several **agent strategies**, selected by the model's
`reasoning_tier`. All strategies produce grounded, gated output — they differ only
in how much they lean on the model versus deterministic scaffolding.

### Frontier / strong tier (e.g. Claude, GPT, Gemini, large DeepSeek)

**Strategy: autonomous multi-step agent.** The model is given the full tool catalog
and runs a multi-step plan-act-observe loop with a generous step budget. It composes
its own investigation. The orchestrator still validates every tool call and grounds
every answer, but the model drives.

### Mid tier (e.g. a capable ~30-70B local model)

**Strategy: guided agent with checkpoints.** Shorter step budgets. The orchestrator
decomposes complex requests into named sub-tasks and runs the model against one
sub-task at a time. More frequent validation. Tool selection may be narrowed to a
relevant subset per sub-task to reduce the chance of a wrong call.

### Basic tier (e.g. a small ~7-13B local model)

**Strategy: deterministic pipeline with model-in-the-slots.** The platform does the
orchestration with **deterministic code**, and calls the model only for the specific
sub-steps it is reliably good at: classifying an alert, summarizing a fixed set of
retrieved events, drafting prose from structured data, extracting entities. The
"agentic" planning is done by code, not the model. Tool calls are issued by the
pipeline, not chosen freely by the model. The model fills slots; it does not steer.

This is the key insight for the "free model" requirement: **a weak model inside a
strong deterministic pipeline produces robust results.** The pipeline guarantees the
queries are correct, the evidence is real, and the action is gated. The model only
contributes language understanding and generation in tightly bounded slots.

### Why every tier stays safe and precise

Regardless of tier:

- Tool schemas are strict; malformed calls are rejected, not guessed at.
- Every factual claim must cite a real tool result or retrieved chunk; ungrounded
  claims are caught by the grounding validator (see `06` and `07`).
- Every state-changing action goes through the same approval gateway.
- The capability tier changes the *autonomy*, never the *guarantees*.

## Structured-output fallback (for weak tool-calling)

Many local models have unreliable native tool-calling. The generic adapter handles
this with a fallback:

1. The system prompt instructs the model to respond **only** with a JSON object
   matching a given schema (tool name + arguments, or a final answer).
2. The adapter strips any code fences or preamble and parses the JSON.
3. If parsing or schema validation fails, the adapter retries with the validation
   error fed back, up to a small bounded number of attempts.
4. If it still fails, the step fails cleanly and is surfaced — never guessed.

This makes even models without function-calling usable, at the cost of some
robustness, which the mid/basic strategies already account for.

## Capability probing (the self-test suite)

The platform ships a **model self-test**: a fixed battery of representative tasks
(tool-call formatting, JSON schema adherence, a small multi-step reasoning task, a
grounding-discipline check). When an operator configures a new model, they run the
probe. It outputs a measured capability descriptor and a recommended strategy. This
turns "which strategy for this model" from guesswork into measurement, and it lets
operators honestly see what their chosen model can and cannot do **before** relying
on it.

## Per-organization model choice

Model configuration is **per organization**. An MSSP can run Client A on a local Ollama
model (for data-residency reasons) and Client B on Claude (for maximum capability),
simultaneously, on the same platform. The data-residency implications of this choice
are covered in `07-security-and-threat-model.md` — it is not merely a quality knob,
it is a data-governance decision.

## Cost, limits, and the "without spending a penny" goal

- The platform itself imposes **no limits** — no seat caps, no usage metering, no
  paywalled features. It is open-source and free.
- A team can run **entirely free**: a local model via the bundled Ollama runtime,
  self-hosted everything. No external paid dependency is required for any feature.
- If a team *chooses* a paid API model, that cost is between them and the provider;
  the platform neither adds nor brokers cost.
- The platform should expose **token-usage visibility** per organization so operators
  running paid models can see their spend — transparency, not metering.

## What the operator must understand (surface this in the product)

The platform should be transparent with operators about the trade-off, rather than
hiding it. On model configuration, show the measured capability descriptor and a
plain-language summary: e.g. "This model scored *basic*. The platform will run the
deterministic pipeline strategy: investigations will be reliable but less
autonomous, and complex multi-step correlation may need to be broken into steps. For
maximum autonomy, configure a stronger model." Honesty here is a feature — it lets a
team make an informed choice and trust the results they get.
