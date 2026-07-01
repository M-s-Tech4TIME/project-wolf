# 0026 — Grounding execution modes: blocking / deferred / incremental (configurable; Phase 6.10 consumer)

**Date:** 2026-06-21
**Status:** accepted — **web-tested same day (see addendum): default → `deferred`; the `cited` evidence-scope was PULLED**

> **Addendum (2026-06-21) — web-test outcomes.** The operator web-tested all
> three modes + the evidence scope on the live cluster:
> - **`blocking`** — as before.
> - **`deferred`** — preferred ("I like it even better than blocking for the UX
>   it gives"). **Adopted as the live default** (`.env` `GROUNDING_MODE=deferred`;
>   the code default in `config.py` stays `blocking` as the conservative no-`.env`
>   fallback). This is the operator's measure-then-flip decision, the same shape
>   as unified-8b becoming default after ADR 0024's measurement.
> - **`incremental`** — "seemed same as deferred." Correct: on the single 6 GB GPU
>   the judge batches serialize behind the shared (cache-warm) evidence prefix, so
>   the progressive `grounding.partial` chips land near-together ≈ deferred. It is
>   wired and verified (the loop emits `grounding.partial` then `grounding.completed`
>   — `test_incremental_mode_emits_partial_then_completed`); it only *diverges* with
>   `OLLAMA_NUM_PARALLEL>=2` / more VRAM. Kept as a selectable option for that hardware.
> - **`GROUNDING_EVIDENCE_SCOPE=cited`** — **PULLED.** It produced "worst possible"
>   verdicts (Not-Verified almost everywhere) because the "dedupe to the last call
>   per tool name" heuristic is unsafe: the model legitimately calls the *same tool
>   with different arguments* (e.g. `list_agents status=disconnected` → 2 hits, then
>   `list_agents status=never_connected` → 0 hits), and the dedup dropped the rich
>   earlier result in favour of the empty later one → the judge was starved of
>   evidence → it flagged true claims as unsupported. Safe evidence trimming needs
>   *per-claim relevance*, not a name-keyed dedup — that belongs to the
>   **grounding-enrichment phase** (the `grounding-enrichment-tools-future-phase`
>   memory: better evidence-collection tools = more Verified verdicts). So the
>   `GROUNDING_EVIDENCE_SCOPE` knob, `_scope_tool_results`, and the `build_evidence`
>   `scope` arg are **removed**; grounding evidence is always `all` (the proven
>   behavior). The "Knob 2" section below is superseded by this addendum.
**Decider:** human (project owner), with claude-code drafting + the live-code analysis below
**Related:** [ADR 0013](0013-grounding-judge-separate-model.md) (judge as a separately-configured
model), [ADR 0015](0015-grounding-yellow-vs-red-and-judge-on-constrained-gpu.md) (8b judge on the
6 GB GPU; named the latency revisit triggers), [ADR 0024](0024-model-posture-split-default-configurable.md)
(model posture — the *first* selectable runtime-perf setting, same env-now / 6.10-GUI shape this ADR
reuses; §"grounding-latency levers" there explicitly deferred the work this ADR now does),
[ADR 0019](0019-web-first-configurability.md) (every knob gets a GUI surface — Phase 6.10),
`docs/06-knowledge-and-rag.md` §Hallucinated grounding, Phase 6.10 (config-settings system).

## Context

After 6-b.3 flipped chat+judge to unified-`qwen3:8b` (ADR 0024 addendum), the operator
observed — correctly — that the *answer quality* is now excellent but the **full turn feels
slower** because grounding runs **after** the token stream finishes:

> "after the chat stream gets completed, the grounding stage initiates, making a full complete
> response from Wolf slower … can it be made simultaneously … ground syncing with the chat
> stream's content and judging it at the same time?"

### How grounding works today (measured against the live code)

1. The chat model streams tokens live — `AgentLoop._chat_or_stream` emits a `model.delta`
   SSE event per content delta (`agent/loop.py`), so the analyst reads the answer as it is
   generated.
2. When the final step produces no tool calls, the loop builds the answer and calls
   `_finalize_answer`, which **awaits** `GroundingValidator.validate` — one judge call
   (`qwen3:8b`) over the answer split into ≤12 sentence-claims + **all** tool-result /
   knowledge evidence — and only **then** emits the single `answer` SSE event carrying the
   *annotated* content (inline `[verified]` / `[unverified]` / `[unsupported]` / `[non-factual]`
   markers) + the four verdict counts.
3. So grounding is strictly **on the critical path**: the turn is not "done" (frontend
   `phase: "running"` → `"done"`) until the judge returns. That post-stream pause is exactly
   what the operator feels. With unified-8b the old 4b↔8b *model swap* is already gone
   (ADR 0024 addendum), so the residual cost is one judge forward pass: prompt-eval over the
   evidence window + generating the verdict JSON.

### Why it isn't free to "just run it during the stream"

The judge and the chat run on the **same model on one GPU**. Ollama serializes requests to a
model unless `OLLAMA_NUM_PARALLEL > 1` (continuous batching), which is **not set** and on the
dev host's 6 GB card (`qwen3:8b` already spills to CPU) would add a second KV-cache + more CPU
offload — likely *slowing* the stream rather than overlapping for free. True token-stream ⇄ judge
concurrency is **hardware-gated** (matching the operator's own "speed depends on hardware"
framing). Additionally the agent loop only *knows* a step is the final answer once it returns
**no tool calls**, so "judge each sentence as it streams" cannot begin until the final step's
stream is essentially complete anyway.

## Decision

Make grounding execution a **configurable runtime mode**, env-driven now and promoted to the
Phase 6.10 Superuser Settings GUI alongside the same-network gate (ADR 0019) and model posture
(ADR 0024). Two orthogonal, composable knobs; **defaults preserve today's exact behavior** (no
regression — the operator flips a knob, web-tests, then changes the default by decision, exactly
as unified-8b became default after measurement).

### Knob 1 — `GROUNDING_MODE`

- **`blocking`** *(default)* — today's behavior. One judge call, awaited; the `answer` event
  carries the annotated content + counts. Strongest guarantee: the analyst never sees an
  un-vetted answer marked "complete."
- **`deferred`** *(recommended)* — the loop emits the `answer` event **immediately** with the
  raw (un-annotated) content, `grounding_pending: true`, and null counts; the message renders
  fully + a "Verifying claims…" indicator; the judge then runs and the loop emits a follow-up
  `grounding.completed` carrying the **annotated content + counts**, which the frontend patches
  onto the already-settled message. **Perceived-latency win**: time-to-readable-answer drops to
  the token stream alone; verdicts arrive a moment later, asynchronously. This is the operator's
  "asynchronous while synchronous" — achieved by **pipelining** (answer-first, verdicts-after),
  not by fighting single-GPU serialization.
- **`incremental`** — like `deferred`, but the claims are judged in **concurrent batches**
  (`asyncio.gather` over claim sub-groups); each batch's verdicts are emitted as a
  `grounding.partial` event the moment it returns, so chips **pop in progressively** and the
  judge phase parallelizes across batches. On `OLLAMA_NUM_PARALLEL ≥ 2` / adequate VRAM the
  batches genuinely overlap (real wall-clock win); on the constrained single-GPU host they
  serialize and `incremental` degrades gracefully to ≈ `deferred` (first chips still appear
  sooner). **Honest caveat documented in `.env` + the matrix doc.**

### Knob 2 — `GROUNDING_EVIDENCE_SCOPE`

- **`all`** *(default)* — feed the judge every tool-result + knowledge chunk (today's behavior).
- **`cited`** — feed the judge only evidence the answer is grounded in: tool results that
  produced a citation, **deduplicated to the last call per tool name**, plus retrieved chunks.
  Real prompt-eval reduction when the model made redundant / superseded tool calls (e.g.
  `list_agents` three times). Conservative by construction: it never drops a *failed*-tool
  signal (negative evidence is always kept) and never drops the last result of a cited tool, so
  it cannot reintroduce the truncation-driven false "unsupported" of Slice 5.0b.1. Composes with
  every mode.

### Invariants preserved across all modes

- **Honesty over speed (blocking remains the strict default).** No mode ever *drops* grounding;
  `deferred`/`incremental` only move *when* the verdicts surface, never *whether*. The audit
  event `grounding.validation.completed` is always written.
- **Validator failure stays non-blocking** (ADR 0013 posture): a failed/empty judge call returns
  the un-annotated answer with `ran: false` — in `deferred`/`incremental` the frontend simply
  clears the "Verifying…" indicator with no chips, never a spinner that hangs.
- **The judge model is unchanged** — posture (ADR 0024) and execution mode (this ADR) are
  orthogonal levers. This ADR is the "grounding-latency levers" follow-up ADR 0024 §Alternatives
  explicitly deferred (judge output / evidence window / *when* it runs).

## Alternatives considered

- **Lower the judge's `num_ctx` / per-source cap to brute-force speed.** Rejected as the primary
  lever — it re-opens the Slice 5.0b.1 truncation bug (rule descriptions falling out of the
  judge's view → false "unsupported"). `cited` scope reduces tokens *safely* instead.
- **A separate `/chat/ground` request after the stream closes.** Cleaner "next-turn-now"
  (the SSE ends at `answer`, unlocking the composer immediately) but needs server-side
  per-loop evidence persistence or shipping raw evidence back from the browser. Deferred:
  for v1 we keep grounding inside the one SSE stream (the message settles + becomes readable on
  `answer`; the SSE stays briefly open to deliver chips). Revisit if "type the next question
  while the previous answer is still being verified, same conversation" becomes a hard ask.
- **True token-stream ⇄ judge concurrency on the current host.** Rejected on this hardware
  (single 6 GB GPU, `qwen3:8b` spills to CPU, `OLLAMA_NUM_PARALLEL` unset → serialized). The
  `incremental` mode is built so it *becomes* real concurrency on capable hardware without any
  further code change — set `OLLAMA_NUM_PARALLEL ≥ 2`.

## Consequences

- **New SSE contract (additive, back-compatible):** `grounding.completed` gains an optional
  `annotated_content` field; a new `grounding.partial` event carries a progressive
  `{annotated_content, supported, uncertain, unsupported, unverifiable}` for `incremental`.
  `blocking` callers see no behavioral change. The non-streaming `POST /chat` endpoint always
  runs `blocking` semantics (it returns one payload) regardless of `GROUNDING_MODE`.
- **Frontend gains an in-place grounding patch path** (`branches.updateAssistantGrounding`):
  late verdicts update a *settled* assistant node by `loop_id` (cannot re-`appendChildOf` — that
  throws on duplicate id by design). `AssistantMessageNode` gains `grounding_pending`.
- **Phase 6.10 gains a third concrete consumer:** the "Grounding mode + evidence scope" setting,
  Superuser-only + audited + synced env ⇄ CLI ⇄ GUI.
- **Reversible by construction:** two env knobs (and, post-6.10, a GUI control); flipping costs a
  restart.
- **Revisit trigger:** a GPU with ≥10 GB usable VRAM (or `OLLAMA_NUM_PARALLEL ≥ 2`) makes
  `incremental` a strict win — re-measure and consider it (or `deferred`) as the shipped default.

## Addendum (2026-07-01) — Concurrency model: per-request parallel grounding, never a queue (MSSP)

**Principle (the "serve everyone like Claude" bar).** Millions of people use Claude
simultaneously and each gets a dedicated experience — no user waits on another user's
message to finish. Wolf holds itself to the same bar: **every response Wolf gives is
grounded independently, concurrently, and in parallel** — across every organisation,
every user, every chat thread, and every message. No analyst's verdict chips ever wait
behind another analyst's (or their own earlier message's) grounding. This is
foundational to the project's MSSP goal, where many users across many organisations
interact with Wolf at once.

**This is already the architecture — verified in the live code (2026-07-01), not
aspirational.** Each chat message is an independent `POST /api/v1/chat/stream` request
that runs in its **own** `asyncio` task, with its **own** grounding judge provider, its
**own** `GroundingValidator`, and its **own** DB session (`api/chat.py`). Grounding runs
inside that per-request task. There is **no process-wide (or per-org / per-user /
per-conversation) lock, semaphore, or queue anywhere on the grounding path** — grep-verified.
So two responses' grounding passes (and a third user's, in another org) are already fully
independent async work: none blocks another. The application layer imposes **no
concurrency ceiling and never queues grounding**.

**Decision: keep it that way — explicitly no grounding queue.** A short-lived proposal on
2026-07-01 to *serialise* grounding into a FIFO queue (global, then per-conversation) was
**considered and rejected before any code was written.** Serialising grounding — making
one analyst wait for another's (or their previous message's) verdicts — is antithetical to
MSSP: it turns a shared judge into a single-lane bottleneck. Wolf will **not** add such a
queue. Every response grounds on its own, immediately, in parallel.

**The app layer is unbounded; the *only* governor of simultaneous execution is
infrastructure.** How many grounding passes physically run at the same instant is set by
the model server, not by Wolf:

- **Ollama** serialises calls to a model unless `OLLAMA_NUM_PARALLEL > 1` (continuous
  batching). It is **unset** on the dev host (single small GPU) by choice — raising it
  there would thrash VRAM (`qwen3:8b` already spills to CPU). That dev limit is a
  *hardware* constraint, **not** a Wolf design constraint.
- Wolf is built for **enterprise/on-prem deployment on the operator's own, more capable
  hardware.** There, the deployer unlocks true simultaneous grounding purely by
  provisioning capacity — **no Wolf code change**:
  - set `OLLAMA_NUM_PARALLEL` high (e.g. 4–8+) so the judge model serves many concurrent
    passes via continuous batching;
  - provision GPU VRAM to match (each concurrent request adds a KV-cache);
  - optionally scale the model server horizontally (multiple Ollama replicas / a hosted
    OpenAI-compatible endpoint behind `OPENAI_BASE_URL`/OpenRouter) — the per-request judge
    provider already fans out cleanly.

**Net:** the concurrency Wolf's users experience scales directly with the hardware they
give it. The application already supports unbounded parallel grounding; the enterprise
operator dials up `OLLAMA_NUM_PARALLEL` + VRAM (or replicas) to realise it. This keeps
single-org ↔ MSSP parity: the same code that serves one analyst serves thousands in
parallel, limited only by the iron.
