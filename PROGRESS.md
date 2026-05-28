# Wolf — Live Progress

> **What is this?** The live, non-chronological view of where Wolf is *right now*.
> CHANGELOG.md has the time-ordered history; this file is the snapshot any
> human or new Claude session should read first to know what to do next.
>
> **Update rules:** rewrite freely as state changes. Keep it short and current.
> If you find yourself describing what *happened*, that belongs in CHANGELOG.md
> instead.

---

## Current focus

**Phase 5 prep — stabilization slices (5.0a → 5.0d) before Phase 5 proper.**

The trigger was a manual web-test session that surfaced 10 bugs / UX issues.
We agreed to fix everything before starting Phase 5 (Organizations + RBAC),
which now becomes its own dedicated phase *after* stabilization, not before.

### Slice status

| Slice | Scope | Status | Commit |
|---|---|---|---|
| **5.0a** | search_alerts free_text fix · 30d→365d time guardrail with grace · aggregation exemption · search_after cursor pagination · time parser supports months/years | ✅ shipped + web-verified | `755e786` |
| **5.0b** | Grounding 4-verdict taxonomy (yellow `[unverified]` caution vs red `[unsupported]`) · fabrication hardening via failed-tool negative evidence · empty-answer synthesis fallback · per-slice fresh-reset + self-validation workflow | ✅ implemented + Claude-side validated; **awaiting user web-test** | pending |
| **5.0c** | UI overhaul: persistent + resizable + text-wrapping Evidence panel · collapsible Conversations sidebar · fixed message input · chat vertical scroll · user-avatar dropdown replacing the session-id chip | ⏳ not started | — |
| **5.0d** | Color/theme to match `wolf-color-palette-outlook.png` (Wazuh dark-navy + blue) | ⏳ not started | — |

### After 5.0a–d

**Phase 5 — Organizations + RBAC** (new dedicated phase, decided 2026-05-28):
superuser (default at install) creates orgs + assigns users/roles; org admins
manage their own org; regular users only chat. Scope reserved for a
superuser cross-org chat access path (designed-in but off by default). The
DB already has `users.is_superuser` as scaffolding.

Then the originally-planned cases / reporting / propose-tools+approval work.

---

## What a new session needs to know

- **Roadmap & architecture:** see [`ONBOARDING.md`](ONBOARDING.md), the [`docs/`](docs/) folder, and the ADR set in [`docs/decisions/`](docs/decisions/).
- **Decisions log:** every architecture-or-default change has an ADR. The most recent: **0014** (multi-embedding RRF), **0015** (grounding yellow/red + judge model on a constrained GPU — Slice 5.0b).
- **History:** [`docs/CHANGELOG.md`](docs/CHANGELOG.md) — newest entries at the top, every session adds one.
- **Per-Claude memory** (cross-session preferences, not in the repo): under `~/.claude/projects/-home-alsechemist-Codespace-project-wolf/memory/` with `MEMORY.md` as the index. Current load-bearing entry: `per-slice-web-test-checkpoints.md` — defines the reset → Claude self-validation → reset → manual user test cycle.

### Hardware facts to remember

- GPU: 6 GB (5.64 GiB usable).
- Chat model: `qwen3:4b` (~3.5 GB) — fits with headroom.
- Grounding judge: `qwen3:8b` (~5 GB) — does **not** fit alongside chat;
  Ollama swaps them on every grounding call. User explicitly chose to keep
  8b for judge quality and accept the swap latency (see ADR 0015). First
  answer after idle is slow (~2–3 min cold load); steady state is faster.
- Two-tenant dev setup: `acme` (`admin@example.com` / `wolf_admin_dev_password`)
  and `beta` (`beta-admin@example.com` / `beta_admin_dev_password`).
- Wazuh deployment: `192.168.245.128` (Indexer 9200 / Server API 55000).
  Credentials in `credentials/` (gitignored).
- Frontend dev URL: `http://192.168.68.108:3000`. Orchestrator: `:8000`.

### Per-slice workflow (current standing rule)

1. Implement (unit tests + lint + mypy + frontend tsc/eslint clean).
2. **Fresh-state reset:** `pkill uvicorn`, `ollama stop <chat>`, `ollama stop <judge>`, confirm GPU memory ≤ ~100 MiB.
3. Relaunch orchestrator.
4. **Claude self-validates** by hitting `/api/v1/auth/login` then `/api/v1/chat` with representative prompts; checks `/tmp/orchestrator.log` for errors.
5. Reset again.
6. Hand over to user with exact prompts + expected outcomes + honest caveats.
7. User manually verifies in the web UI; only then move on.
