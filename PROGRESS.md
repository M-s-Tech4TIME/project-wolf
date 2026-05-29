# Wolf — Live Progress

> **What is this?** The live, non-chronological view of where Wolf is *right now*.
> CHANGELOG.md has the time-ordered history; this file is the snapshot any
> human or new Claude session should read first to know what to do next.
>
> **Update rules:** rewrite freely as state changes. Keep it short and current.
> If you find yourself describing what *happened*, that belongs in CHANGELOG.md
> instead.

---

## Session anchor (read this first)

If you are a new Claude session picking this up: the single sentence
that orients you is **"Slice 5.0c-c (theme palette) shipped at `3c070c3`;
next is Slice 5.0c-d — progressive answer rendering via
`/api/v1/chat/stream`."**

The four-colour palette (Platinum / Dusk Blue / Steel Blue / Icy Blue)
lives in [`frontend/app/globals.css`](frontend/app/globals.css) and is
documented in the cross-session memory entry `wolf-color-palette.md`.
The polish backlog (sidebar search, message fading + "Show more",
hover-on-message actions, new-chat greeting screen, full icon-rail
mode) lands as **Slice 5.0c-f**, sequenced **after 5.0c-e and before
Phase 5.4** (decided 2026-05-29). Polish sits on top of streaming +
activity feed so hover-on-message understands a streaming vs settled
message; one focused web-test pass instead of three.

Standing operating rules: per-slice web-test workflow (memory:
`per-slice-web-test-checkpoints.md`); never push to `origin/main`
without an explicit ask; restart procedure lives in
[`docs/restart.md`](docs/restart.md) — use it, don't re-derive it.

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
| **5.0c-a** | Verdict rename + 4 chips — `supported`→🟢 *Verified*, `uncertain`→🟡 *Uncertain* (amber), `unsupported`→🔴 *Not Verified*, `unverifiable`→🟡 *Non-factual* (muted). Backend now emits all four marker tokens; frontend renders four distinct chip styles. | ✅ shipped + self-validated | pending commit |
| **5.0c-b** | UI layout: persistent + resizable + text-wrapping Evidence panel · collapsible Conversations sidebar · avatar moved to sidebar footer · Settings gear in header (placeholder for User Settings + Wolf Configuration) · native overflow-y-auto chat scroll · /auth/me now returns real email + display_name · per-exchange meta row · code-block Copy with HTTPS+HTTP fallback · disclaimer + scroll-to-bottom button | ✅ shipped + user-verified | `28dc96d` |
| **5.0c-c** | Theme/colour palette — Platinum `#e7ecef`, Dusk Blue `#274c77`, Steel Blue `#6096ba`, Icy Blue `#a3cef1` (user-chosen 2026-05-29, supersedes earlier `wolf-color-palette-outlook.png`). Applied across light + dark CSS variables, markdown, buttons, animations, surfaces; grounding chips keep their semantic colours. See [[wolf-color-palette]]. | ✅ shipped | `3c070c3` |
| **5.0c-d** | Progressive answer rendering — Claude-style token-by-token reveal via `/api/v1/chat/stream`. See [[progressive-response-and-live-activity]]. | ⏳ not started | — |
| **5.0c-e** | Live activity feed during steps — narrate what Wolf is actually doing right now (searching Wazuh, asking the model, judging, drafting). See [[progressive-response-and-live-activity]]. | ⏳ not started | — |
| **5.0c-f** | Polish backlog (decided 2026-05-29 to sit after 5.0c-e): sidebar search · message fading + "Show more" on long user messages · hover-on-message actions (date / retry / edit / copy) · new-chat greeting screen with quick-action cards · full icon-rail mode when the sidebar is collapsed. Lands after streaming + activity so polish sits on top of them, not under. | ⏳ not started | — |
| **5.0d** | Color/theme to match `wolf-color-palette-outlook.png` (Wazuh dark-navy + blue) | ⏳ not started | — |

### After 5.0a–d

1. **Phase 5.4 — Native HTTPS + `wolf-cert` CLI** (decided 2026-05-29):
   slot immediately after the 5.0c UI work, before Phase 5 RBAC.
   Generates a Wolf Root CA + leaf certs for the orchestrator and the
   frontend dev server with **100-year validity** (RFC 5280 forbids
   truly unlimited; 100 years is the established practical-infinity
   pattern). A dedicated `wolf-cert` CLI owns init / add-host / renew /
   status / export-ca / revoke. Built with the `cryptography` library
   already in the dependency footprint. Unlocks every secure-context
   API (clipboard, web crypto, service workers, push) — removes the
   reason the 5.0c-b code-block Copy needed an execCommand fallback.
   See [[native-https-and-wolf-cert]] for design intent.
2. **Phase 5 — Organizations + RBAC** (decided 2026-05-28): superuser
   (default at install) creates orgs + assigns users/roles; org admins
   manage their own org; regular users only chat. Scope reserved for a
   superuser cross-org chat access path (designed-in but off by default).
   The DB already has `users.is_superuser` as scaffolding.
2. **Phase 5.5 — Knowledge management UI** (proposed 2026-05-28):
   admin-facing web page where org admins author / edit / delete their
   tenant's runbooks and past-incident write-ups via an MDXEditor-based
   markdown editor; superusers can also edit the shared global corpora
   (`wazuh_doc`, `attack`). Auto-chunks + re-embeds on save against the
   existing `knowledge_chunks` table. Structured tags (`rule_id`,
   `technique`, `action_type`) surface in `chunk_metadata` for the next
   two phases. See [[runbook-authoring-and-actionable-runbooks]].
3. **Grounding-enrichment via more tools** (proposed 2026-05-28). Two
   tracks, both real:
   - **Continuously**: every new tool added in any future phase is
     evaluated for *evidence value to the judge* alongside its main
     purpose. The grounding judge looks at the evidence dictionary
     regardless of which tool produced it — so any well-cited new tool
     raises the Verified ratio automatically.
   - **Dedicated phase**: a focused chunk of time prioritising tools
     specifically by how much evidence value they add. Candidate list:
     `get_agent_details`, `lookup_ip_reputation`, `get_attack_technique`
     (MITRE), `get_cve_details`, `quote_runbook` (exact-passage
     retrieval with line numbers — pairs with Phase 5.5's structured
     tags), expanded `get_rule_definition` coverage. Each tenant-scoped
     via the existing patterns; external feeds need an API-key plumbing
     pattern that respects the secrets backend.
4. **Phase 6 — Propose tools + approval gateway** (originally planned;
   now tightly coupled to Phase 5.5): runbook steps tagged with
   `action_type` become first-class **proposed** actions with provenance
   back to the runbook line that prescribed them. The analyst sees
   *"This action comes from page X of your `[ACME SOC] SSH brute-force
   runbook`, line 4"* before approving. Hard safety rule: Wolf never
   auto-executes; runbook → propose → human approve → orchestrator
   executes. CI already enforces no `execute_*` tools in the
   orchestrator today, and this phase preserves that.
5. Then the originally-planned cases / reporting work.

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
- Frontend dev URL: `http://<LAN-IP>:3000` (discover with `hostname -I`);
  three files pin the IP — see [`docs/restart.md`](docs/restart.md) under
  "Test credentials" for which to update when the IP changes.
  Orchestrator: `:8000`.

### Restart runbook

See [`docs/restart.md`](docs/restart.md) — the exact procedure to bring
Wolf back to a known-clean state. Use it whenever a backend change
needs to land, before a manual web-test, or anytime things feel odd.

### Per-slice workflow (current standing rule)

1. Implement (unit tests + lint + mypy + frontend tsc/eslint clean).
2. **Fresh-state reset:** `pkill uvicorn`, `ollama stop <chat>`, `ollama stop <judge>`, confirm GPU memory ≤ ~100 MiB.
3. Relaunch orchestrator.
4. **Claude self-validates** by hitting `/api/v1/auth/login` then `/api/v1/chat` with representative prompts; checks `/tmp/orchestrator.log` for errors.
5. Reset again.
6. Hand over to user with exact prompts + expected outcomes + honest caveats.
7. User manually verifies in the web UI; only then move on.
