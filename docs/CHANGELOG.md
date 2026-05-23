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
