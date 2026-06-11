---
name: multi-slice-batch-handover
description: "2026-05-30 active policy for the 5.0c-i → 5.0c-j → 5.0c-k → 5.0c-l block: hand over when a coherent feedback set is satisfied, not strictly per-slice. Claude uses judgement on what to bundle. Bundles planned: (i+j) UI polish, (k+l) stream lifecycle + branching."
metadata:
  node_type: memory
  type: feedback
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

**Active rule for the 5.0c-i → 5.0c-l block (revised 2026-05-30).**

The user has now landed on a feedback-set-based handover policy, not
per-slice. Their exact words: *"its better to hand me over when
everything is supposed to be working as per feedback and as per
instructed, rather than per slice, cause some feedback or improvements
might be dependent on the other slices or the upcoming slices. But if
you think that any feedback that has been fully served then you can
hand it over to me for a test."*

**How to apply:**
  - Run the full integrity gate (backend suite + cross-organization gate +
    ruff + mypy strict on CI-scoped + frontend tsc + eslint + restart +
    Claude self-validate) at the end of every slice. Non-negotiable.
  - Commit per slice. The git history stays granular even when
    handovers are bundled.
  - At handover time, leave the orchestrator DOWN (clean GPU/RAM for
    the user to relaunch) per [[per-slice-web-test-checkpoints]] —
    and tell the user explicitly in the handover note that's why
    they'll see a NetworkError until they restart. (Twice now they've
    pinged me about it; the runbook is in [docs/restart.md] but the
    handover should call it out.)
  - Bundle handovers when slices share a test surface or depend on
    each other. The current plan:
      Bundle 1: 5.0c-i (rename + greeting-fade tweak) + 5.0c-j (chats
                history pane) — small UI wins, no shared state.
      Bundle 2: 5.0c-k (stop button + concurrent streams) + 5.0c-l
                (thread branching) — both touch the stream lifecycle;
                branching is much harder to test without stop and
                concurrent already in place.
  - Use judgement: if one slice within a bundle finishes clearly
    self-contained and well-tested, hand it over early with a caveat
    rather than rigidly waiting for the bundle.

**Standing rule unchanged.** The per-slice procedure (fresh-reset →
Claude self-validate → fresh-reset → user web-test) still applies for
every slice OUTSIDE this block. See [[per-slice-web-test-checkpoints]].

**Earlier history kept for context:**
  - The initial attempt at this policy (2026-05-30 morning) was
    "batch everything 5.0c-g → 5.0c-k into one big handover." User
    reversed mid-batch back to per-slice. This second revision
    (afternoon) settles on the per-bundle compromise above.

**Decisions still in force from the same feedback exchange:**
  - **Retry nudge** wording: keep the "preserve well-supported claims,
    consider what was missed" framing in [prompts.py:RETRY_NUDGE]; do
    not weaken to a temperature bump.
  - **Branch storage** (5.0c-l): client-side only until Phase 5 RBAC
    brings persistent conversations.

**New decisions from this exchange (2026-05-30 afternoon):**
  - **Concurrent streams** are wanted, NOT a blocker as MVP claimed.
    User explicitly called out the single-stream constraint as "a
    usability blocker" in image 1. Multi-stream manager required.
  - **Stop button** mid-stream is wanted; Wolf's response box should
    show "Response interrupted by the user" when aborted.
  - **Edit/Retry semantics** (5.0c-l): Retry must regenerate in place
    with branch navigation, NOT submit at the bottom as a new turn.
    Edit happens **inline inside the message bubble** (not in the
    composer) with Save/Cancel + a disclaimer "Editing this message
    will create a new conversation branch." Retry chip on **every**
    user and Wolf message, not just the latest — the "latest only"
    limit from 5.0c-f / 5.0c-g is a 5.0c-l deliverable to widen.
  - **Greeting fade** (5.0c-i): current 280ms is too fast; target
    ~1500ms total with a smoother transition INTO the chat view (the
    in-flight UserBubble + StreamingView should fade in too, not just
    appear).
