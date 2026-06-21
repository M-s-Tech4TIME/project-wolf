# 0024 — Model posture: keep the split (qwen3:4b chat / qwen3:8b judge) as default; make it configurable in Phase 6.10

**Date:** 2026-06-18
**Status:** accepted — **active posture revisited 2026-06-19 (see addendum): unified-8b is now the live default**

> **Addendum (2026-06-19) — active posture flipped to unified-8b.** This ADR
> chose the split as default on a *latency* basis. Phase 6 web-testing surfaced
> what latency couldn't: `qwen3:4b` is **unreliable on the agentic propose flow**
> — it called the wrong tools, dropped the `propose_active_response` call, and
> emitted nonsense final answers. The operator's priority is **quality /
> reliability over speed** (speed is a hardware concern), so `DEFAULT_MODEL_ID`
> is now `qwen3:8b` (chat) with the judge already 8b → **unified-8b**. The split
> is **not removed** — it remains selectable via the same `DEFAULT_MODEL_ID` /
> `GROUNDING_JUDGE_MODEL_ID` env knobs (revert chat to `qwen3:4b`), and the
> Phase 6.10 GUI toggle still lands. Bonus: unified-8b means chat and judge are
> the same model → **no 4b↔8b swap**, so one model stays resident. `num_ctx` is
> already 8192 (aligned). The split stays the documented speed-optimised option.
**Decider:** human (project owner), with claude-code drafting + the live A/B measurement below
**Related:** [ADR 0010](0010-model-probe-qwen3-8b.md) (qwen3:8b probe — no measured
capability win over 4b), [ADR 0013](0013-grounding-judge-separate-model.md) (judge as a
separately-configured model), [ADR 0015](0015-grounding-yellow-vs-red-and-judge-on-constrained-gpu.md)
(kept the 8b judge on the 6 GB GPU despite swap; named the revisit triggers this ADR exercises),
[ADR 0014](0014-multi-embedding-retrieval-rrf.md) (the dual-embedding decision this ADR re-confirms),
[ADR 0019](0019-web-first-configurability.md) (every knob gets a GUI surface — the home of the
configurable toggle), `docs/15-supported-model-matrix.md`, Phase 6.10 (config-settings system).

## Context

Before opening Phase 6 (wolf-gateway), the operator interrogated Wolf's
four-model runtime posture and asked whether it's the right shape:

- **Chat / judge:** `qwen3:4b` (chat stream) + `qwen3:8b` (grounding judge),
  the *split* established by ADR 0013 + ADR 0015.
- **Embeddings:** `nomic-embed-text` + `nomic-embed-text-v2-moe`, the RRF dual
  established by ADR 0014.

The operator's hypothesis was that **unifying on `qwen3:8b` for both chat and
judge would be *faster*** — eliminating the per-grounded-turn model swap that
ADR 0015 documented (the "2 min 44 s self-validation after idle"), and letting a
single stronger model serve everything. The operator also asked whether
`nomic-embed-text` alone is self-sufficient (drop the aux embedder).

Per Wolf's "measure first, then land the change as an ADR-backed slice"
discipline, we measured before deciding. The decision below is **what the data
showed**, which overturned the speed hypothesis.

## Measurement

Live, on the actual dev host — **NVIDIA RTX 4050 Laptop, 6 GB VRAM, 23 GiB RAM**
(unchanged since ADR 0010/0015) — via the Ollama API timing metrics
(`load_duration`, `prompt_eval_duration`, `eval_duration`). Warm OS page cache
(steady-state active-session conditions). 200-token generations; the judge leg
fed a representative ~5 KB / 4,311-token evidence prompt at `num_ctx=8192`
(matching `model_resolver.get_grounding_judge_model`'s real setting).

**Per-model speed (warm):**

| Model | Role | Token rate | Cold load (warm cache) |
|---|---|---|---|
| `qwen3:4b` | chat | **61.8 tok/s** | ~2.6 s |
| `qwen3:8b` | chat (default ctx) | **18.0 tok/s** (3.4× slower) | ~1.3 s |
| `qwen3:8b` | judge (ctx 8192) | **11.8 tok/s** | ~1.3 s |

**Full grounded turn (chat → judge), warm:**

| Posture | Chat leg | Judge leg | **Turn total** | Swap |
|---|---|---|---|---|
| **Split** (4b chat / 8b judge) — status quo | 5.7 s | 23.6 s | **29.3 s** | judge.load 1.8 s + next-turn 4b reload 2.8 s |
| **Unified 8b**, naive (chat default ctx / judge 8192) | 12.9 s | 22.6 s | **35.5 s** | judge.load 1.7 s — a `num_ctx`-change reload of the *same* model |
| **Unified 8b**, ctx-aligned (both at 8192) | 14.0 s | 21.0 s | **35.0 s** | judge.load 0.1 s (truly one resident instance) |

Raw metrics archived at measurement time; method reproducible via a small
Ollama-API harness.

### What the data shows

1. **The swap is cheap, not the villain.** When the model file is warm in RAM,
   the 4b↔8b swap costs **~1.8–2.8 s** — far less than the penalty of running
   *chat* on 8b (5.7 s → 12.9 s). The split loses ~2.5 s to swapping; unifying
   on 8b loses ~7 s of chat speed. **The split is ~6 s faster per warm grounded
   turn**, and streams the chat answer **3.4× faster** (61.8 vs 18.0 tok/s) —
   which the analyst sees token-by-token.

2. **Posture does not touch grounding cost.** The judge leg is ~22 s = **3.9 s**
   prompt-eval (4,311 tokens of evidence) + **~17 s** generating 200 tokens at
   11.8 tok/s. The judge is `qwen3:8b` in *every* posture, so this is identical
   across them. The real grounding-latency levers are the judge's **output
   length**, the **evidence window**, and **keeping it warm** — orthogonal to
   the chat/judge model choice.

3. **ADR 0015's "2 min 44 s" was the cold-page-cache edge case** — first answer
   after long idle / RAM pressure (the two VMs) evicting the 5.2 GB file from
   page cache, forcing a full disk re-read + fragmented VRAM + retries. In warm
   steady state the swap is ~2 s. Unifying on 8b does **not** fix the cold *first*
   turn either (it still cold-loads 8b for the chat leg); it only helps on turns
   2+.

4. **`num_ctx` keys the loaded instance.** Even with the same model id, the naive
   unified path (chat at default ctx, judge at 8192) pays a ~1.7 s *reload* each
   leg because Ollama treats a different `num_ctx` as a different resident
   instance. Only the ctx-aligned unified path keeps one instance warm.

5. **Per the probe (ADR 0010), qwen3:8b shows no *measured* capability win over
   qwen3:4b** (both 0.75; both pass tool-call/JSON/multi-step; both FAIL
   `grounding_discipline`). 8b's real-world answer-quality lift is real but
   unmeasured — so "unify on 8b" is a *quality/simplicity* choice, not a
   criteria-driven or speed-driven one.

## Decision

1. **Embeddings — keep both, unchanged.** ADR 0014's measured data stands:
   `nomic-embed-text` alone gets precision@5 35%; the dual RRF (v1 + v2-moe) gets
   **60%**, and v2-moe alone silently truncates ~3.5% of long chunks (so it
   isn't self-sufficient either). The two genuinely complement (v1 long-context,
   v2-moe entity precision). Cost is trivial (+15 MB, +110 ms, ~1.2 GB VRAM for
   both small embedders — not the VRAM constraint). **No change.**

2. **Chat / judge — keep the split (`qwen3:4b` chat / `qwen3:8b` judge) as the
   shipped default.** It is measurably faster on this hardware *and* preserves
   the independent judge ADR 0013 exists to provide (8b grading 4b's output, not
   a model grading itself). **No `.env` change — the split is already live**
   (`DEFAULT_MODEL_ID=qwen3:4b`, `GROUNDING_JUDGE_MODEL_ID=qwen3:8b`). This ADR
   makes the existing posture *evidence-backed* rather than assumed.

3. **Make the posture a first-class, user-selectable setting in Phase 6.10.**
   Both postures stay valid choices for different operators/hardware; the
   mechanism already exists as the `DEFAULT_MODEL_ID` + `GROUNDING_JUDGE_MODEL_ID`
   env knobs. Phase 6.10 (config-settings system, ADR 0019) promotes them to a
   DB-source-of-truth, Superuser-only, audited **"Model posture"** setting with a
   GUI radio/toggle (the same shape as the Wazuh single-vs-distributed selector).
   It becomes a concrete 6.10 consumer alongside the same-network-gate toggle.

## Alternatives considered

- **Unify on `qwen3:8b` for both (the operator's original hypothesis).**
  Rejected *as the default*, kept *as a selectable option*. Strongest chat
  answer quality + one resident model (idle-resilient on turns 2+, simplest), but
  the data shows it is ~6 s slower per warm grounded turn, streams chat 3.4×
  slower, and gives up judge independence (8b grading 8b). Legitimate for an
  operator who values answer quality / simplicity over stream speed, or on cold-
  /idle-heavy workloads — which is exactly why it becomes a *configurable* choice
  rather than being dropped. If chosen, **align `num_ctx`** (run chat at the
  judge's ctx) so there's one warm instance, not a per-leg reload.
- **Unify on `qwen3:4b` for both.** Rejected. Fastest, no swap, but the judge
  grades its own family's output — the recursive-validation trap ADR 0013 was
  built to escape (the model whose grounding discipline the validator exists to
  compensate for would be grading that discipline). Worst judge quality; against
  the honesty mission.
- **Drop the aux embedder (`nomic-embed-text` only).** Rejected — see decision 1;
  ADR 0014's data is decisive.
- **Chase the grounding-latency levers now instead of touching posture.**
  Deferred, not rejected — they're *orthogonal* to posture (decision-2 doesn't
  preclude them). Capping the judge's output tokens, shrinking the evidence
  window, and keeping the judge warm would cut grounding time in *any* posture.
  Tracked as a future optimization; out of scope for this documentation slice.

## Consequences

- **No runtime change in this slice.** The running configuration already matches
  the decision; this ADR + the doc updates record *why*.
- **Phase 6.10 gains a concrete consumer:** the "Model posture" setting (split
  vs unified-8b), Superuser-only + audited + synced across env ⇄ CLI ⇄ GUI.
- **Grounding-latency levers are now an explicit, tracked follow-up** (judge
  output cap / evidence window / keep-warm) — independent of posture, the
  actual path to faster grounding.
- **Revisit trigger (carried from ADR 0015):** a GPU with **≥10 GB usable VRAM**
  changes the calculus — `qwen3:4b` + `qwen3:8b` + both embedders all stay
  resident simultaneously, so the split has *zero* swap and is strictly best
  (fast chat **and** independent judge), while unified-8b's per-token chat
  penalty remains. Re-measure and update the default when such hardware is the
  target.
- **Reversible by construction:** the posture is two env knobs (and, post-6.10, a
  GUI toggle); flipping costs seconds.
