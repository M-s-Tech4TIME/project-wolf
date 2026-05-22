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

## YYYY-MM-DD — [first real entry goes here]

[Your team fills in the first session entry. Below is a worked example for
reference; delete it once you have a real first entry.]

---

## Example entry (delete me after first real entry)

## 2026-05-22 — Add doc 14 model recommendations and progress tracking

**Session type:** mixed (human planning + claude-code execution)
**Phase:** Phase 2 — Read path
**Duration:** ~1 hour
**Branch / commit:** main @ abc1234

### What we did
- Added `docs/14-model-recommendations.md` with hardware-tiered model picks
  and the environment-change playbook.
- Added `docs/PROGRESS.md` and `docs/CHANGELOG.md` for cross-session
  continuity.
- Added a standing instruction in `docs/11-claude-code-instructions.md` that
  every session must read PROGRESS.md first and update it last.
- Committed previously-untracked `docs/` files in two commits.

### What we decided
- Keep `llama3.2` as the running dev default but document Qwen 3 4B as the
  recommended Apache-licensed replacement once the probe confirms viability.
  See `docs/decisions/0001-model-default-policy.md`.
- Two-file tracking (PROGRESS + CHANGELOG) rather than one file — the live
  state needs to stay small enough to fit in a session's context.

### What broke / what we discovered
- Capability probe has never been run against the actual Ollama. Should be
  the next task.

### What's next
- Run `uv run python -m tools.model_probe --provider ollama --model llama3.2`
  and capture the output.
- Pull `qwen3:4b` and probe it for comparison.
- Wire the remaining 4 read tools (get_event_timeline, get_agent_alert_history,
  get_agent_detail, get_rule_definition) to real Wazuh.
