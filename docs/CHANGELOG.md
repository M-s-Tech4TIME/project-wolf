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
