---
name: grounding-execution-modes
description: ADR 0026 — grounding is a configurable execution MODE (blocking/deferred/incremental); env-now via GROUNDING_MODE, Phase 6.10 GUI consumer #3. WEB-TESTED 2026-06-21: live default = deferred; the cited evidence-scope was PULLED (starved the judge) → deferred to grounding-enrichment phase
metadata:
  type: project
---

ADR 0026 (2026-06-21, web-tested same day) — **grounding execution is configurable**,
shipped backend-first env-driven, queued as the **3rd Phase 6.10 Superuser-GUI consumer**
(after the same-network gate + model posture, [[config-settings-system-phase]] /
[[notification-and-realtime-phases]] siblings).

- `GROUNDING_MODE` (`config.py`/`.env`, normalized prop, unknown→`blocking` fallback):
  - **blocking** — judge awaited BEFORE the `answer` SSE event (code default / fallback).
  - **deferred** — **LIVE DEFAULT** (operator's web-test pick). `answer` fires immediately
    (raw + `grounding_pending`); a later `grounding.completed` carries annotated content +
    counts the frontend PATCHES onto the settled node (`branches.updateAssistantGrounding`
    — the tree forbids dup-id re-append).
  - **incremental** — claims judged in CONCURRENT batches (`validator.validate_streaming`,
    `asyncio.as_completed`, offset-mapped merge); each batch emits `grounding.partial`
    (progressive chips). Real concurrency needs `OLLAMA_NUM_PARALLEL>=2`/VRAM; on the
    6 GB GPU it serializes → behaves like deferred (operator confirmed "seemed same"). Kept
    as a selectable option for better hardware.
- **NO evidence-scope knob.** A `GROUNDING_EVIDENCE_SCOPE=cited` trim was built and
  **PULLED** at web-test: "dedupe to last call per tool name" dropped a RICH earlier
  `list_agents` (status=disconnected → 2 hits) for the EMPTY later one (never_connected →
  0 hits) — the model legitimately calls one tool with different args — so the judge was
  starved and flagged true claims Not-Verified. Safe trimming needs PER-CLAIM relevance →
  deferred to the [[grounding-enrichment-tools-future-phase]]. Evidence is always `all`.
- Key code: `agent/loop.py` `_finalize_answer` is mode-aware + OWNS the `answer` emit;
  `agent/events.py` `grounding.partial`; non-stream `POST /chat` is always blocking.
  Frontend pending state = `grounding_pending` on `AssistantMessageNode`/`StreamCompletion`;
  late verdicts applied by a **ref-guarded** chat-shell effect (mirrors the archive
  effect — set-state-in-effect solved at root, NOT an eslint disable).

**Why:** unified-8b (ADR 0024) made answers great but grounding runs after the stream,
so the turn felt slow; the operator wanted async/simultaneous grounding as a *switchable*
option. Built to honor [[no-unaddressed-errors]] (no disable bypass) + measure-then-flip
discipline. cited was pulled because a half-broken knob is worse than none.

**How to apply:** the live default is now `deferred` (`.env` `GROUNDING_MODE=deferred`).
To try others, set `GROUNDING_MODE=blocking|incremental` + restart wolf-server. When
Phase 6.10 lands, promote the selector to the Superuser Settings GUI (audited, synced
env⇄CLI⇄GUI). Honesty invariant: no mode ever DROPS grounding — only *when* verdicts
surface changes; the `grounding.validation.completed` audit event always fires. Do NOT
re-propose a name-keyed evidence-scope trim; the proper fix is per-claim evidence
selection in the grounding-enrichment phase.
