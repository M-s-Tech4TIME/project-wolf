# Wolf — Development Changelog

> **This is the append-only history of the Wolf project.** Every Claude Code
> session, every meaningful human change, every decision — appended here as
> the work happens.
>
> **Rules for this file:**
>
> - Append only. Never delete or rewrite past entries.
> - Newest entries at the top. Reverse chronological.
> - Every session adds at least one entry, even if "no code changes — just
>   investigation."
> - Be specific. "Updated config" is useless; "Set DEFAULT_MODEL_ID=qwen3:4b
>   in services/orchestrator/app/config.py after probe results showed
>   reasoning_tier=basic on this hardware" is useful.
> - For decisions that change architecture or defaults, also write a full ADR
>   in `docs/decisions/` and reference its filename here.
>
> For *current* project state, see `PROGRESS.md` (live, updated, not
> chronological).

---

## Entry template

Copy this block and fill in at the start of each session entry:

```
## YYYY-MM-DD — [Session brief title]

**Session type:** [claude-code / human / mixed]
**Phase:** [from roadmap]
**Duration:** [approx — for capacity tracking]
**Branch / commit:** [git ref where work ended]

### What we did
- [bullet — concrete action]
- [bullet — concrete action]

### What we decided
- [decision, with reason; link to ADR if applicable]

### What broke / what we discovered
- [unexpected issue, finding, surprise]

### What's next
- [next-action item — should match PROGRESS.md "What's next"]
```

---

## 2026-05-23 — Supported-model commitment (ADR 0006 + doc 15) + ONBOARDING.md

**Session type:** claude-code (new conversation, same dev environment)
**Phase:** Phase 2 closed; pre-Phase-3 setup
**Duration:** ~90 min (discussion + writing)
**Branch / commit:** `main` — uncommitted at time of this entry; will
be committed as part of the same set of commits that adds this entry.

### What we did

- Walked the project owner through the locally-hostable agentic-LLM
  landscape across four hardware tiers, with targeted side-discussions
  of GLM 5.1 and Kimi K2 (the latter ruled out for the matrix because
  even sparse it does not fit any non-multi-GPU local profile).
- Captured the owner's product direction as a load-bearing commitment:
  Wolf must natively support **four** model families locally in
  development — Qwen 3 (4B/8B/14B/32B), Llama 3 (3.x/4 line), Gemma 3
  (4B/12B/27B), GLM 5.1 ~32B dense.  Production posture is user-choice
  (operators pick one or multiple, including hosted APIs).
- Created `docs/15-supported-model-matrix.md` — the living directive
  doc.  Defines the four families with sizes and licenses, the
  six-item "natively support" checklist (KNOWN_MODELS entry + live
  probe + ADR + agent-loop test + strategy assignment + smoke
  coverage + doc 14 entry), the dev quality bar (efficient / robust /
  stable / reliable) with specifics, the production user-choice
  posture, and the current implementation gaps ordered by priority.
- Created `docs/decisions/0006-supported-model-families-commitment.md`
  — the point-in-time ADR with full reasoning, five alternatives
  considered (single-default rejected, wider matrix rejected, drop
  Llama rejected, include Kimi K2 rejected, defer-until-Phase-3
  rejected), and six consequences including the four expected probe
  ADRs.
- Updated `docs/decisions/README.md` index table with ADR 0006 row.
- Added auto-memory entry `supported_model_matrix.md` + one-line
  pointer in `~/.claude/.../memory/MEMORY.md` so future Claude Code
  sessions on other machines pick up the commitment without needing
  to find doc 15 first.
- Wrote `ONBOARDING.md` at repo root — 11-section comprehensive
  onboarding doc for a new contributor (human or AI) on a different
  machine: 60-second orientation, mandatory reading order with three
  tiers, system requirements, first-time setup from a clean clone
  (12 numbered steps), verification (tests / lint / smoke / probe),
  common operational tasks, seven real gotchas with fixes, the
  session-continuity protocol, file-location reference table, and a
  troubleshooting matrix.
- Updated this CHANGELOG and `docs/PROGRESS.md` accordingly.

### What we decided

- Four-family native-support commitment (ADR 0006).  Llama stays in
  the matrix even though it's not Wolf's *recommended* default per
  doc 14 — "supported" and "recommended" are distinct concerns.
- GLM 5.1 anchored at ~32B (dense) rather than the smaller 9B.  The
  project owner is arranging GPU hardware that can run the 32B class,
  so the matrix targets the right tier.
- ADR 0006 alongside doc 15 (rather than only one of them).  doc 15
  is the living matrix; ADR 0006 is the frozen decision record.  Six
  months from now "why these four?" is answered in ADR 0006; "what's
  the current state?" is answered in doc 15.  Both are needed.
- `ONBOARDING.md` at repo root (not `docs/16-onboarding.md`).
  Discoverability after `git clone` matters more than fitting the
  numbered docs/ scheme.  The doc points heavily into docs/ for
  detail.

### What broke / what we discovered

- `pnpm-workspace.yaml` and `services/frontend/` are stale — the
  real Next.js app lives at `/frontend/` at the repo root.  Flagged
  as Gotcha #2 in `ONBOARDING.md` but not fixed in this session;
  cleanup commit deferred.
- Repo `main` is 25 commits ahead of `origin/main` as of the start
  of this session.  All 25 are legitimate Phase 2 work from earlier
  sessions that was never pushed.  The push at the end of this
  session will publish all of them at once.

### What's next

- Push everything (this session's commits + the 25 unpushed Phase 2
  commits) to `origin/main` so the GitHub remote becomes the
  canonical state.
- Hand off to the new GPU dev machine (when it arrives) with the
  session-handoff prompt produced at the end of this session.
- Once on the GPU machine: pull the four families at the larger
  sizes, run probes, write the four expected probe ADRs (one per
  family / size that needs measurement).
- In parallel or after: begin Phase 3 (RAG + grounding validator)
  per `docs/06` and `docs/10`.

### Follow-up commits later in the same session

This entry was written before the following cleanup work; recording
here so the changelog matches the git log.

- `8da5389` — removed stale `pnpm-workspace.yaml` and empty
  `services/frontend/` directory (the deferred cleanup mentioned
  above).  Updated `ONBOARDING.md` accordingly: dropped Gotcha #2,
  renumbered #3-#7 → #2-#6, fixed three inline cross-references and
  the §0 repo-layout block.
- `7917fc5` — fixed factually wrong `bootstrap_tenant` flag names in
  `ONBOARDING.md` §3.9/§3.10 (real flags are `--admin-email`,
  `--admin-password`, `--opensearch-url`, `--opensearch-username`,
  `--opensearch-password`, `--server-api-url`, `--server-api-username`,
  `--server-api-password`, `--verify-tls`/`--no-verify-tls` — not the
  `--user-*` / `--wazuh-*` names previously documented).  Also
  corrected the structural misstatement that `bootstrap_tenant`
  supports a two-step "create tenant first, wire Wazuh later" flow —
  the CLI requires all Wazuh fields up front.  Merged §3.9 + §3.10
  into a single accurate step with a "no Wazuh yet" placeholder
  pattern; renumbered §3.11/§3.12 → §3.10/§3.11.  Clarified in §5
  that the CLI is fully idempotent and re-running it with the same
  `--tenant-slug` is the supported update / credential-rotation path
  (no dedicated update CLI needed).
- `<earlier in session>` — saved the new-machine handoff prompt as
  `prompts/HANDOFF-NEW-MACHINE.md` (was previously only inline in
  chat); appended this follow-up note to the CHANGELOG entry.
- `<this commit>` — committed ADR 0007 + `docs/16-distribution-and-packaging.md`
  + auto-memory entry + small pointers in `docs/09` (Container/build/CI
  section), `docs/decisions/README.md` (index row), `ONBOARDING.md`
  (Tier 2 reading order).  ADR 0007 records the decision to deliver
  Wolf natively (non-container) via `.deb`/`.rpm` system packages +
  systemd units, fronted by a one-line install script that handles
  prerequisite-repo setup (GitLab-style hybrid: Tailscale / Caddy /
  k3s / Docker also use this pattern).  Three alternatives weighed:
  GitLab-style omnibus (Option B, rejected as too expensive
  engineering for the marginal gain), Snap/Flatpak (Option C,
  rejected due to confinement friction with local sockets and
  secrets), and pure Option A without script wrapper (rejected as
  too much friction with three third-party repos to add manually).
  doc 16 specifies the package set, file layout (FHS-conformant),
  `wolf` CLI surface, supported distro matrix, security posture,
  and implementation work-breakdown (~3-4 weeks of focused work
  when the slot arrives).  Implementation deliberately queued for
  post-Phase 4 to avoid repackaging churn before the deployable
  surface stabilizes; current code must continue to honor
  constraints in doc 16 §"How current code should accommodate this
  commitment" (env-driven config, no hard-coded container paths,
  management CLIs remain usable as plain `python -m ...`, frontend
  on Next.js `output: 'standalone'`).

---

## 2026-05-22 — Switch dev default from llama3.2 to qwen3:4b

**Session type:** claude-code (continuation, same dev environment)
**Phase:** Phase 2 — Read path
**Duration:** ~30 min
**Branch / commit:** `main` — ADR 0004 `e092e21`, config flip
`ca495df`, KNOWN_MODELS amendment `14cc727`, final session commit
pending this entry.

### What we did

- Wrote `docs/decisions/0004-model-switch-llama3.2-to-qwen3-4b.md`
  weighing the three earlier probe ADRs (0001/0002/0003).  Decision:
  flip the dev default to qwen3:4b on probe-evidence + license
  grounds; document that qwen3's grounding-discipline probe failure
  raises Phase 3 grounding-validator priority but does not block the
  switch (the agent loop's tool-gated path bounds the fabrication
  risk).
- Updated `docs/decisions/README.md` index with ADR 0004.
- Changed `DEFAULT_MODEL_ID` from `llama3.2` to `qwen3:4b` in
  `services/orchestrator/app/config.py` as a standalone one-line
  commit referencing ADR 0004 (per doc 14's playbook).
- Restarted orchestrator with the new default and ran a curl-driven
  chat verification against the user's real Wazuh on `192.168.76.129`.
- **Verification exposed a real issue**: chat worked but ran in
  `pipeline` strategy with no tools — the static
  `KNOWN_MODELS["qwen3:4b"]` entry (added in commit `e9cc316`) was
  the conservative initial estimate (basic / pipeline) and shadowed
  the probe-measured capability (mid / guided) at runtime.
- Amended `KNOWN_MODELS["qwen3:4b"]` to match ADR 0002's measured
  capability (mid / guided / full / schema_enforced / 8 steps) in
  commit `14cc727`.
- Re-restarted orchestrator and re-verified end-to-end: now runs in
  `guided` strategy, calls `count_alerts_by_severity` once, returns
  a grounded cited answer with concrete numbers ("15 alerts total,
  all low severity").

### What we decided

- **`qwen3:4b` becomes the dev default.**  ADR 0004 is the canonical
  rationale; future contributors should read it before considering
  another switch.  Llama family stays in `KNOWN_MODELS` for operator
  opt-in via env override.
- **The qwen3:4b grounding-failure data point is not disqualifying**
  — it's a Phase 3 priority signal, not a Phase 2 blocker.
- **The remaining two `KNOWN_MODELS` amendments** (`llama3.2`,
  `gemma3:4b`) stay deferred — neither is the current default, so the
  static-vs-measured drift doesn't affect runtime behaviour today.
  They'll move in a single sweep when convenient.

### What broke / what we discovered

- **Static `KNOWN_MODELS` entries can silently override probe-measured
  capability at runtime.**  The conservative `qwen3:4b` defaults from
  Task 4 of the previous session shadowed ADR 0002's measurements
  because strategy selection reads the static descriptor, not the ADR
  prose.  This is by design (static entries are the source of truth
  the orchestrator boots from) but it means a probe ADR without a
  matching static amendment doesn't actually change runtime behaviour
  — a footgun worth keeping in mind for future probe → switch flows.
- First inference on qwen3:4b after model swap took ~76s (cold
  load); second inference ~169s including a single tool call.  The
  CPU-only ceiling, not a regression.

### What's next

- Wire the 4 mock-only read tools to real Wazuh
  (`get_event_timeline`, `get_agent_alert_history`, `get_agent_detail`,
  `get_rule_definition`).
- Verify Phase 2 exit criterion against a frontier API model — blocked
  on an operator-supplied API key.
- Batch-amend the remaining `KNOWN_MODELS` entries for `llama3.2`
  (structured_output downgrade per ADR 0001) and `gemma3:4b`
  (native_tool_calling downgrade per ADR 0003).
- Begin Phase 3 (RAG + grounding validator) — the qwen3:4b
  grounding-discipline result is the direct motivating evidence.

---

## 2026-05-22 — Phase 2 exit criterion: frontier-API verification

**Session type:** claude-code (continuous session)
**Phase:** Phase 2 — close-out
**Duration:** ~45 min
**Branch / commit:** `main` — see commit below this entry's date

### What we did

- Added `app/management/set_secret.py` — small CLI that reads a value
  from stdin (no shell history exposure) and stashes it in the
  configured secrets backend.  Smoke-tested with a throwaway value
  (round-trip verified, secret never echoed).
- Stashed the operator's OpenRouter API key under
  `model.openrouter.api_key` in `.local/secrets.enc`.
- Added two `KNOWN_MODELS` entries for OpenRouter-hosted open models:
  `deepseek/deepseek-v4-flash:free` (kept for operators who fund the
  account, since DeepSeek's `:free` route gates on credit deposit) and
  `nvidia/nemotron-3-super-120b-a12b:free` (truly free, NVIDIA Open
  Model License — restricted, fine for verification not default).
- Ran the Phase 2 frontier-API verification end-to-end against the
  operator's real Wazuh using Nemotron 120B.  Result: `frontier`
  strategy, one tool call to `count_alerts_by_severity`, grounded
  cited answer in 17 seconds.  Captured verbatim in ADR 0005.
- Restored the steady-state config (DEFAULT_MODEL_ID stays `qwen3:4b`
  in config.py; the verification was env-only).
- Updated PROGRESS.md: Phase 2 exit-criteria bullet flipped from `[ ]`
  to `[x]`; Section 1 marked Phase 2 closed; Section 4 reordered with
  Phase 3 (RAG + grounding validator) as the next step.

### What we decided

- Use `nvidia/nemotron-3-super-120b-a12b:free` rather than a
  DeepSeek-family model for the actual verification because DeepSeek's
  free routes on OpenRouter all gate on credit deposit (HTTP 402 with
  zero-credit accounts).  Nemotron is the strongest of the no-deposit
  free options that genuinely worked.
- Accept the license caveat: Nemotron uses the NVIDIA Open Model
  License (restricted by doc 14's filter), so it is the
  verification-path model, NOT the recommended-default model.  Doc 14
  isolation holds: dev default stays Apache (qwen3:4b).
- Keep both new `KNOWN_MODELS` entries permanently — the
  DeepSeek-flash one as the canonical slug for operators who do top
  up OpenRouter, the Nemotron one as the verified no-deposit path.

### What broke / what we discovered

- **`OPENAI_BASE_URL` must NOT include `/v1`**: OpenAIAdapter posts
  to `{base_url}/v1/chat/completions`.  Setting the env to
  `https://openrouter.ai/api/v1` produced `.../api/v1/v1/chat/...`
  and 404'd.  Correct: `https://openrouter.ai/api`.  Documented
  inline on the OpenRouter entries.
- **The two-`app/`-packages collision struck again.**  Same root
  cause as ADR 0001's probe CLI bug — gateway's `app/` wins the path
  race over orchestrator's when uvicorn is launched from project
  root.  Workaround (`cd services/orchestrator` first) is documented
  in PROGRESS §3 and now in ADR 0005's "issues surfaced" section.
- **OpenRouter `:free` suffix is not a binding promise.**  Three of
  the five candidate `:free` routes we tried returned errors because
  their upstream providers meter independently of OpenRouter's free
  classification; account needed credits even for "free" routes.
  Documented in ADR 0005.

### What's next

- Phase 3 — RAG + grounding validator per docs/06.  Read that doc
  plus the Phase 3 block of docs/10-build-roadmap.md, then plan the
  slice.  qwen3:4b's grounding-discipline failure (ADR 0002) is the
  direct motivation for the grounding validator.

---

## 2026-05-22 — Amend `KNOWN_MODELS` for `llama3.2` and `gemma3:4b` per probe ADRs

**Session type:** claude-code (continuous session)
**Phase:** Phase 2 — close-out cleanup
**Duration:** ~5 min
**Branch / commit:** `main` — see commit below this entry's date

### What we did

- Aligned `KNOWN_MODELS["llama3.2"]` with ADR 0001's measurements:
  `native_tool_calling` upgraded `partial` → `full`;
  `structured_output` downgraded `prompt_coaxed` → `unreliable`.
  Reasoning tier and strategy were already correct.
- Aligned `KNOWN_MODELS["gemma3:4b"]` with ADR 0003's measurements:
  `native_tool_calling` downgraded `partial` → `none` (Gemma 3 4B has
  no native tool calling — Ollama returns HTTP 400 on any chat with
  `tools`); `structured_output` upgraded `prompt_coaxed` →
  `schema_enforced`; `max_safe_autonomous_steps` tightened 5 → 3.
- Added inline comments on each amended entry citing the ADR that
  grounded the change.
- Updated PROGRESS §4 to drop the completed cleanup item.

### What we decided

- Cosmetic cleanup; neither model is the current default
  (`qwen3:4b` holds that since commit `ca495df`).  But aligning
  static estimates with measured truth keeps `KNOWN_MODELS` honest
  for any operator who reads it as documentation.

### What broke / what we discovered

- Nothing.  128 backend tests still pass; ruff + mypy strict clean.
  No code branches on the amended fields (they inform strategy
  selection but not behaviour at the strategy level for these two
  models — `gemma3:4b` was already `pipeline` and `llama3.2` was
  already `guided`).

### What's next

- Frontier-API exit-criterion verification (blocked on operator key).
- Phase 3 entry — RAG + grounding validator per docs/06.

---

## 2026-05-22 — Verify all 9 read tools against real Wazuh; add `--all-tools` smoke mode

**Session type:** claude-code (continuous follow-on session)
**Phase:** Phase 2 — Read path (close-out)
**Duration:** ~30 min
**Branch / commit:** `main` — see commit below this entry's date

### What we did

- Exercised the four previously-mock-only read tools against the
  operator's real Wazuh deployment by calling each tool's `run()`
  directly through a synthesized `ToolExecContext`:
  `get_event_timeline`, `get_agent_alert_history`, `get_agent_detail`,
  `get_rule_definition`.  **All four succeeded first try** — no
  field-shape mismatches between the unit-test mocks and the real
  Server-API / OpenSearch responses.
- Extended `app/management/smoke_wazuh.py` with a `--all-tools` mode
  that exercises every registered read tool against the live
  deployment (calls `run()` through a ToolExecContext, bypassing the
  dispatcher's session requirement but going through full Pydantic
  input/output validation and the real HTTP layer).  Usage:
  `uv run python -m app.management.smoke_wazuh --tenant-slug acme \
   --all-tools --agent-id 000 --rule-id 5402`.
- Re-verified all 9 tools end-to-end against the live Wazuh:
  list_agents (1), get_agent_detail (1), get_cluster_health,
  get_rule_definition (1), search_alerts (5), aggregate_alerts (3),
  count_alerts_by_severity (23 total), get_event_timeline (5),
  get_agent_alert_history (5).  **9/9 ✓.**
- Updated `docs/PROGRESS.md` Section 2 to reflect the new
  live-verified status (all 🟡 read-tool entries flipped to ✅), and
  Section 4 to drop the now-completed wiring step.

### What we decided

- No bugs found, no fixes needed.  The unit-test mocks were written
  with care and matched real shapes accurately enough that the live
  exercise passed without code changes.
- Kept the existing `smoke_test()` (clients-only mode) as the default
  for quick connectivity checks; `--all-tools` is opt-in for the
  fuller verification.

### What broke / what we discovered

- Nothing broke.  The discovery is non-news but worth recording:
  Wazuh's Server API and OpenSearch response shapes for `/agents`,
  `/rules`, and alert documents are stable enough that mock-driven
  unit tests stay accurate against a real deployment.

### What's next

- Frontier-API exit-criterion verification (blocked on operator API key).
- Batch-amend the static `KNOWN_MODELS` entries for `llama3.2` and
  `gemma3:4b` per ADRs 0001 and 0003 (cosmetic — neither is the
  current default).
- Begin Phase 3 (RAG + grounding validator) per docs/06.

---

## 2026-05-22 — Switch dev default model `llama3.2` → `qwen3:4b`

**Session type:** claude-code (continuous session)
**Phase:** Phase 2
**Duration:** ~30 min
**Branch / commit:** `main` — `e092e21` (ADR 0004), `ca495df`
(config flip), `14cc727` (KNOWN_MODELS amendment), `4324bce`
(PROGRESS/CHANGELOG update for switch)

### What we did

- Wrote ADR 0004 weighing the three probe results
  (`docs/decisions/0004-model-switch-llama3.2-to-qwen3-4b.md`).
- Flipped `DEFAULT_MODEL_ID` from `llama3.2` to `qwen3:4b` in
  `services/orchestrator/app/config.py` as a standalone commit.
- Verification surfaced that the static `KNOWN_MODELS["qwen3:4b"]`
  entry (deliberately-conservative Task 4 estimate: basic/pipeline)
  shadowed the probe-measured capability (mid/guided) at runtime —
  qwen3:4b was running in pipeline strategy.  Amended the static
  entry to match measurement per ADR 0002.
- Re-verified end-to-end: chat against real Wazuh, qwen3:4b in
  `guided` mode, one tool call to `count_alerts_by_severity`,
  grounded cited answer ("15 alerts total, all low severity").

### What we decided

- Land the three changes as three separate commits (ADR, config flip,
  static-entry amendment) so each is independently revertable.
- Keep `llama3.2` in `KNOWN_MODELS` — operators who want it just set
  `DEFAULT_MODEL_ID=llama3.2` in `.env`.

### What broke / what we discovered

- The static `KNOWN_MODELS["qwen3:4b"]` from Task 4 silently overrode
  the probe-measured capability, causing the model to run in the
  wrong strategy after the flip.  Verification caught it.  Lesson:
  any time we add a new model to `KNOWN_MODELS` from an estimate, we
  must amend it as soon as the probe runs.
- Earlier in the session, a single mid-conversation `loop_error`
  surfaced as "Model call failed:" with empty detail (the Ollama
  adapter's swallowed exception).  Added diagnostic capture in
  commit `e09b4e5` (logs exception type + traceback to audit data)
  so the next occurrence is debuggable.

### What's next

- Wire the 4 mock-only read tools to real Wazuh (now done — see entry
  above).
- Frontier-API exit-criterion verification (still pending key).
- Phase 3 entry.

---

## 2026-05-22 — Add model recommendations, session continuity tracking, and run the first capability probe

**Session type:** claude-code (executing user's planning brief at
`prompts/CLAUDE-CODE-SESSION-PROMPT.md`)
**Phase:** Phase 2 — Read path
**Duration:** in progress
**Branch / commit:** `main` — Commit 1 `c05cdce` (planning bundle),
Commit 2 `b093761` (session-additions docs), Commit 3 `e9cc316`
(Tasks 4+5 code + probe sys.path fix), final session commit pending
this entry.

### What we did

- **Committed the previously-untracked planning bundle.**
  Commit 1 (`c05cdce`): `docs/00-13` (excluding doc 11) + `README.md`.
  Commit 2 (`b093761`): updated `docs/11-claude-code-instructions.md`
  (session-continuity protocol), new `docs/14-model-recommendations.md`,
  new `docs/PROGRESS.md`, new `docs/CHANGELOG.md`.
- **Moved `PROGRESS.md` and `CHANGELOG.md` from the repo root into
  `docs/`** to match the references in docs 11 and 14.
- **Relaxed the start-of-session reading rule** in
  `docs/11-claude-code-instructions.md`: re-reading PROGRESS.md +
  CHANGELOG.md every turn is required only for a brand-new session, a
  different machine/environment, or a different Claude model version.
  The end-of-session update + final commit remain mandatory regardless.
- **Populated `docs/PROGRESS.md`** with the real current state of Wolf
  (Phase 2 status, what's built and verified, configuration, what's
  next, active decisions, 128-test coverage).
- **Initialized `docs/CHANGELOG.md`** (this entry).
- [TASK 4] Extended `KNOWN_MODELS` in
  `services/orchestrator/app/models/interface.py` with four new entries:
  `qwen3:4b`, `gemma3:4b`, `qwen3:8b`, `glm-5.1`. **Did not** change
  `DEFAULT_MODEL_PROVIDER` or `DEFAULT_MODEL_ID` — both remain
  `ollama` / `llama3.2`.
- [TASK 5] Added `license_class` to `CapabilityDescriptor` in
  `packages/schema/wolf_schema/capability.py`. Populated every existing
  `KNOWN_MODELS` entry: Llama family → `restricted`, Claude/GPT →
  `proprietary`, Qwen/Gemma/Mistral → `apache-2.0`, GLM/DeepSeek →
  `mit`. Non-breaking informational field; no runtime code branches on
  it.
- [TASK 6] Ran the capability probe against live Ollama on this hardware:
  `uv run python -m tools.model_probe --provider ollama --model llama3.2`.
  Required a one-line `sys.path` bootstrap in `tools/model_probe/__main__.py`
  to resolve a two-`app/`-packages collision between
  `services/gateway/app/` and `services/orchestrator/app/` that uv editable
  installs had been silently shadowing (gateway won the ambiguous name).
  Probe result: score 0.68, 3/4 tasks pass; measured `mid` / `guided` —
  matches the static `KNOWN_MODELS` estimate at the strategy tier.  Full
  ADR at `docs/decisions/0001-model-probe-llama3.2-baseline.md`.
- [TASK 7] Pulled `qwen3:4b` and `gemma3:4b` and probed both.
  - `qwen3:4b`: score **0.75** (3/4 pass).  PASS: tool-call formatting,
    JSON-schema adherence, multi-step reasoning.  FAIL:
    grounding-discipline (fabricated specific data when given no tools).
    Measured `mid` / `guided` / `schema_enforced` — every field as good
    as or better than `llama3.2`, except grounding.  ADR
    `docs/decisions/0002-model-probe-qwen3-4b.md`.
  - `gemma3:4b`: score **0.25** (1/4 pass).  Two task failures were
    HTTP 400 from Ollama because Gemma 3 4B has **no native tool
    calling** — the runtime rejects requests that include a `tools`
    parameter.  Measured `basic` / `pipeline`.  Ruled out as a default
    candidate.  ADR `docs/decisions/0003-model-probe-gemma3-4b.md`.
- [TASK 8] Created `docs/decisions/README.md` (ADR definition, naming
  convention `0NNN-short-kebab-title.md`, file template, live index of
  the three new ADRs).

### What we decided

- **Default model stays `llama3.2` for now.** Doc 14 recommends switching
  to an Apache-licensed model (Qwen 3 4B or Gemma 3 4B) before Wolf has
  external users, but the switch is gated on probe data and a follow-up
  ADR. This session adds the candidate entries to `KNOWN_MODELS` so the
  options exist; the switch itself is a separate decision.
- **`license_class` is informational, not enforcement.** It surfaces the
  Llama vs Apache/MIT distinction in the UI eventually, but no code path
  blocks a model on its license. Operator choice always.
- **PROGRESS.md and CHANGELOG.md live in `docs/`, not at repo root.**
  Resolved by moving the files; doc 11 and doc 14 keep their existing
  references.
- **Start-of-session reading is conditional** (brand-new session / new
  environment / different model only). End-of-session update is always
  mandatory. Updated doc 11 to reflect this.

### What broke / what we discovered

- **Two-`app/`-packages collision blocked the probe CLI.** Both
  `services/gateway/app/` and `services/orchestrator/app/` exist as
  Python packages literally named `app`.  uv's editable installs put
  both on `sys.path` (gateway entry first), so bare `import app`
  resolved to the gateway and `app.models.ollama` failed with
  `ModuleNotFoundError`.  Pytest never hit this because its path setup
  happens to land orchestrator first.  Fixed locally to the probe CLI;
  the deeper "rename one of them" surgery is logged as deferred work.
- **`llama3.2`'s static `KNOWN_MODELS` entry was directionally right
  but two fields were off:** `native_tool_calling` was estimated
  `partial` and measured `full` (upgrade); `structured_output` was
  estimated `prompt_coaxed` and measured `unreliable` (downgrade —
  free-form JSON adherence failed mid-document).  Recommended strategy
  matches.
- **`qwen3:4b`'s static entry was conservative across the board.**
  Measured stronger on every dimension except grounding-discipline,
  where it failed cleanly (fabricated when given no tools).  In Wolf's
  tool-gated agent loop that risk is contained but raises Phase 3
  grounding-validator priority.
- **`gemma3:4b` has no native tool calling.** Ollama returns HTTP 400
  on any chat request that includes `tools`.  This is the model
  family's structural limitation, not a transient bug.  Confirms doc 14
  that gemma is a viable summariser at best, not an agent driver.

### What's next

- Write `docs/decisions/0004-model-switch-llama3.2-to-qwen3-4b.md`
  weighing the three probe results.  qwen3:4b is the recommendation
  for the *recommended-for-shipping* default per doc 14; the question
  the ADR settles is whether dev should switch now or wait for the
  Phase 3 grounding validator.
- After the switch ADR lands, change `DEFAULT_MODEL_ID` in
  `services/orchestrator/app/config.py` in a **separate commit** that
  references the ADR (per doc 14's environment-change playbook).
- Wire the four remaining read tools to real Wazuh
  (`get_event_timeline`, `get_agent_alert_history`, `get_agent_detail`,
  `get_rule_definition`).
- Verify Phase 2 exit criterion against a frontier API model in addition
  to the local-Ollama path that already passes.
- Batch-amend the static `KNOWN_MODELS` entries for `llama3.2`,
  `qwen3:4b`, and `gemma3:4b` to reflect measured capability.
