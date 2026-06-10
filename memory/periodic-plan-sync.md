---
name: periodic-plan-sync
description: STANDING RULE (2026-06-04) — periodically audit progress vs plans/architecture/goals across every project doc; surface drift proactively
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

STANDING RULE (2026-06-04): periodically check, map, and relate Wolf's progress + advancements against the project's plans, architecture, goals, and every document that needs to be documented and tracked.

**Why:** the user added this rule right after I surfaced significant drift between `docs/10-build-roadmap.md` and reality during the Phase 5.8 → 5.9 transition. The roadmap predated the Phase 5 sub-tree, the component rename, mTLS, wolf-database, the three smoke targets, and ADR 0016 — none of which existed in the doc. The user wants doc drift caught **while it accumulates**, not as a big sweep at the end of the build.

**How to apply:**
- Between major phase transitions (e.g., closing Phase 5.8 before opening 5.9), audit at minimum: `docs/10-build-roadmap.md`, `docs/01-architecture.md`, relevant ADRs in `docs/decisions/`, and `docs/PROGRESS.md` for drift vs actual shipped work.
- When closing a slice, check whether the slice's outcomes invalidate or supersede paragraphs in any planning doc; flag it proactively.
- For each significant new architectural decision (mTLS, edge component, fully-independent systemd units, etc.), confirm there's an actual ADR or addendum.
- Surface findings WITHOUT waiting for the user to ask. The Phase 5.8 audit only happened because the user prompted it; the cost of catching drift earlier is lower than the cost of a big late-stage sweep.

Related: [[multi-slice-batch-handover]] for slice-set hygiene; [[no-unaddressed-errors]] for "don't let drift accumulate silently"; [[integrity-across-the-stack]] for the every-change-preserves-integrity baseline this rule extends.
