# 0015 — Grounding yellow vs red + keeping the 8b judge on a constrained GPU

**Date:** 2026-05-28
**Status:** accepted
**Decider:** human (project owner) with claude-code drafting
**Related:** [ADR 0013](0013-grounding-judge-separate-model.md) (the original
single-marker, separate-judge design this ADR refines), [ADR 0010](0010-model-probe-qwen3-8b.md)
(qwen3:8b probe), `docs/06-knowledge-and-rag.md` (validator design),
Slice 5.0b's commit (pending) which ships this change.

## Context

Slice 5.0b followed a web-test session where the user flagged two
problems with grounding as it stood after Slice 2B + ADR 0013:

1. **One marker for two situations.** Every claim the validator could not
   directly back from evidence was tagged red `[unverified]` — including
   plausible general-knowledge prose and reasonable inferences. The user's
   own observation, looking at a correct answer covered in red badges: *"although
   he showed unverified, but wolf is actually right."* Over-flagging erodes
   trust in the marker.
2. **Fabrication-on-tool-failure slipped through.** When a tool errored at
   schema validation (so `Citation` was never added), the answer had no
   citations → `_finalize_answer` early-returned without validating →
   fabricated specifics (the "12 critical, 45 high, 187 medium, 1,234 low —
   total 1,478" answer for a query whose tool actually errored) reached the
   user with no marker at all.

Separately, the same session revealed a hardware constraint with operational
consequences:

3. **`qwen3:8b` (the judge from ADR 0013) cannot coexist with `qwen3:4b`
   (chat) on this 6 GB GPU.** Ollama evicts and reloads them on every
   grounding call. The first answer after an idle period took 2 minutes
   44 seconds in self-validation, and a previous turn timed out at 300 s on
   the chat-model cold reload — surfaced as `(empty)` then `ReadTimeout` →
   `loop_error` to the user.

## Decisions

### (A) 4-verdict grounding taxonomy, two inline markers

| Verdict | Meaning | Marker | Render |
|---|---|---|---|
| `supported` | A specific evidence segment clearly backs the claim. | none | — |
| `unverifiable` | No factual content to check (preamble / opinion / instruction). | none | — |
| `uncertain` | Plausible general statement, best-practice, or inference; evidence neither confirms nor contradicts. | `[unverified]` | 🟡 yellow chip (`Info` icon) |
| `unsupported` | Contradicts the evidence, *or* states a specific fact (count / ID / name / timestamp) that should have come from evidence but is absent. Fabrications land here. | `[unsupported]` | 🔴 red chip (`AlertTriangle`) |

Hard rule in the judge prompt: **any specific number / count / ID / name /
timestamp not present in the evidence is `unsupported`, never `uncertain`.**
This is the safety property — fabricated specifics must never get the
softer yellow treatment.

The `GroundingBadge` follows a severity ladder: red > amber > green. Inline
markers and the badge both expose the four counts to the analyst.

### (B) Failed tool calls become explicit negative evidence

The agent loop now accumulates `all_tool_failures` and passes it to the
validator. `build_evidence` emits a `[TOOL_FAILED i: name]` section per
failure with the directive *"any specific fact that would have come from
it is UNSUPPORTED."* `_finalize_answer` no longer early-returns when
there are no successful citations *but* there were tool failures — exactly
the case where the model tends to fabricate to fill the gap.

### (C) Keep `qwen3:8b` judge on the 6 GB GPU; mitigate via empty-answer fallback

Three options were on the table:

| Option | Outcome | Decision |
|---|---|---|
| Switch judge to `qwen3:4b` (reuse chat) | No swap, no thrash, fast. Loses 8b classification quality. | Rejected |
| **Keep `qwen3:8b` judge** | Best judge quality. Each grounding call swaps models on this GPU → slow first-answer-after-idle, occasional empty completions. | **Accepted** |
| Disable grounding | Fastest. Loses every honesty signal Slice 5.0b just added. | Rejected |

The user explicitly weighed grounding quality > latency on this hardware,
and committed to a fresh-state reset (`pkill uvicorn` + `ollama stop`
each model) before every test cycle so the GPU starts predictable rather
than fragmented. To stop the *worst* symptom (a blank "(empty)" answer
bubble after the model returns empty content post-tool), the loop now
re-prompts once without tools (`_synthesize_final`); if even that comes
back empty, an honest fallback message is shown.

**When this should be revisited:** any of (a) a GPU upgrade that fits 4b + 8b
together (≥ 10 GB usable for ample headroom), (b) a smaller capable judge
that fits alongside 4b in 6 GB (e.g. a future ~2 B parameter Qwen tuned
for fact-checking), or (c) the user re-evaluating the latency trade-off
in operational use. The mechanism is a single env var
(`GROUNDING_JUDGE_MODEL_ID`) — empty falls back to chat, so the switch
is reversible in seconds.

## Consequences

- Trust in the inline markers improves: red means "real problem," yellow
  means "be careful." This was the explicit user request.
- Fabrication on tool failure is no longer invisible — the very class of
  failure that caught us in 5.0a (the parser regression) is now flagged.
- First-answer latency on this hardware remains 2–3 minutes after an idle
  period because of the qwen3:4b ↔ qwen3:8b swap. Subsequent turns in
  the same session are faster.
- The 4-verdict + tool-failure design adds load to the judge prompt;
  qwen3:8b handles it; smaller judges may need prompt simplification if
  ever swapped in.
