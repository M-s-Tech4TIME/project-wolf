---
name: project-wrap-up-2026-07-12
description: "Development PAUSED 2026-07-12 (operator's subscription ending). Everything through Phase 6-f + ADR 0033 + PG18 shipped and CI-green. Resume via docs/HANDOVER.md + docs/CLAUDE-RESUME-PROMPT.md."
metadata:
  type: project
---

**Development paused 2026-07-12** — the operator's subscription was ending; they asked for a
full wrap-up so the project can be resumed cold (by them + Claude Code) at an unknown later date.

**State at pause:** everything through Phase 6-f (ADR 0032) + ADR 0033 (configurable embedding
stack, no-cap 4096) + the PostgreSQL 18 replacement is SHIPPED and CI-green; nothing
half-finished. Gates re-proven fresh at wrap-up: 963 backend tests / 0 skips, mypy --strict
(117 files), ruff clean.

**The wrap-up shipped:**
- `docs/HANDOVER.md` — the master snapshot: state, resume queue, standing-rules digest, ADR map,
  credentials guide, environment-rebuild pointer, graphify regeneration. **Read it first on resume.**
- `docs/CLAUDE-RESUME-PROMPT.md` — paste into a fresh Claude Code session: orient → verify
  environment → prove gates → resume the queue.
- `credentials.example/` (tracked) — CHANGE_ME templates of the four credential files
  (wazuh / postgresql / openrouter / wolf); the real `credentials/` stays gitignored (public
  repo — real credentials NOT committed despite operator dispensation; templates were the
  operator-offered alternative and the real files remain on the dev machine).
- ONBOARDING.md stays the single canonical dev-setup guide (no duplicate doc); patched with the
  embedding-model pulls + a HANDOVER pointer.

**Resume queue (order, from HANDOVER §3):** (1) 6-f.4 operator web-test — virustotal upsert
end-to-end (wazuh-wui credential + qwen3:8b chat model); (2) [[model-switch-nemotron-after-slices]]
(operator-gated); (3) Phase 6.9 → 6.7 → 6.8; (4) Phase 6.10 ([[config-settings-system-phase]]);
(5) Phases 6.11/6.12; (6) Phase 6.13 ([[grounding-enrichment-tools-future-phase]]); then Phase 7+.

**Why:** the operator explicitly wanted a "developer taking a break" handover — everything
documented so the returning developer (Claude Code included) knows where to start.

**How to apply:** on the first session after resume, follow `docs/CLAUDE-RESUME-PROMPT.md`
verbatim; treat HANDOVER §3 as the queue and HANDOVER §4 as the rules digest; all standing-rule
memories in this directory remain in force.
