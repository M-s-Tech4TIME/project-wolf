# Wolf — Development Progress

> **This is the live state of the Wolf project.** Read this file at the start
> of every Claude Code session, before doing anything else. Update it at the
> end of every session.
>
> For history of what changed when, see `CHANGELOG.md` (append-only).

**Last updated:** 2026-06-28 by claude-code (**6-d.4 — /actions GUI for reversal + timed blocks (CODE-COMPLETES 6-d).** The browser surface for 6-d (ADR 0028); frontend-only (chat surfacing rode 6-d.2's prompt #4). `lib/types.ts` `ActionProposal` gained `reverses_proposal_id` / `reversal_proposal_id` / `auto_unblock_at` (mirrors the 6-d.2 `ProposalOut`). `app/actions/page.tsx`: a **Reversal / Auto-reversal** sky chip on undo proposals; an **Undoes block #…** field (notes physical removal via wolf-pack); timed blocks show **Duration** ("1h — auto-reverses on expiry"), an executed still-in-effect timed block shows **· auto-reverses <when>**, and a block whose reversal is authorised shows **· reversal authorised**; `resultDetail` surfaces the reversal's honest outcome ("Reversal authorised + recorded — physical removal pending wolf-pack"); the **approve dialog is reversal-aware** — for an undo it says Wolf *authorises + records* the reversal and the host removal runs via wolf-pack (NOT "a real change on your fleet"), so the reviewer is never misled. GATE: dashboard `tsc` + `eslint` clean (dev hot-reloads the page — no build/restart for FE); NO backend change; NO migration; NO CI change (FE rides the existing job). **6-d is code-complete (6-d.1→6-d.4); all CI green on main.** REMAINING: the per-slice **web-test checkpoint** — RESTART wolf-server (stale code until restart: it must pick up the new `unblock_ip`/`enable_user` intents + `block_duration` + `list_active_blocks` tool schema + the auto-reversal scheduler) then exercise on the live cluster: (1) block an IP with a reason → /actions; (2) ask to unblock it → Wolf recalls the reason+evidence; (3) re-block an already-blocked IP → dedup context; (4) a timed block ("block for 2m") → auto-reverses + appears in /actions with "timed block expired" context. **No real host AR without operator go-ahead** (reversals are wolf-pack-bound = no host change; a forward block IS real). NEXT (operator's call): the OTHER action classes (`rule_tuning`/`agent_action`/`config_change`, now reversal-aware via the 6-d linkage), or Phase 6.10 config-settings / 6.7-6.9 notifications+SMTP.)

_Prior:_ **6-d.3 — timed auto-reversal scheduler.** The timed-block half of 6-d (ADR 0028): "block X for 1h" now AUTOMATICALLY reverses when the window expires, contextualised in /actions. Wazuh's own `<timeout>` is config-side/fixed → this arbitrary-duration timer is **Wolf-owned**. `gateway/scheduler.py` = a periodic in-process sweep launched from `main.py` `lifespan` (asyncio task, cancelled on shutdown; a sweep error is logged, never kills the loop). Each tick claims due timed blocks (`auto_unblock_at <= now AND reversal_proposal_id IS NULL AND state=succeeded`, `SELECT … FOR UPDATE SKIP LOCKED` — no-op on SQLite, idempotent + race-safe vs a manual unblock) and, per block in its own transaction, creates the **system-initiated** auto-reversal. The auto-reversal is **pre-consented by the timed-block approval** (the approver who authorised "block for 1h" authorised the expiry reversal — the second half of that one time-boxed action): fires WITHOUT a second human approval (no SoD; requested_by/approved_by = `WOLF_SYSTEM_ACTOR` sentinel; audited `action.proposal.auto_reversal.approved`), then runs the same wolf-pack-bound reversal perform/verify (lands `succeeded` = authorised+recorded, `dispatched:false`; the block stays in effect until wolf-pack confirms removal). Carries the recalled original reason + "automatic reversal: timed block expired @ <ts>" context. Config (env-only, future 6.10 consumer): `AUTO_REVERSAL_ENABLED` (default on — cheap no-op when nothing due) + `AUTO_REVERSAL_SWEEP_INTERVAL_SECONDS` (default 60). Added a documented `override_engine` test utility to `database.py` (point a background task's own `db_session()` at the test engine). GATE: ruff + mypy --strict clean; full backend **715 passed / 0 skip** (+ sweep auto-reverses a due block; idempotent; ignores not-due/indefinite; manual unblock pre-empts — all on SQLite = CI's test DB); NO migration; NO CI change (scheduler under already-strict `gateway`; tests auto-collect). Note: the prior **6-d.1/6-d.2 CI runs failed ONLY on a newly-disclosed dep advisory** (joserfc 1.6.5 → CVE-2026-48990), fixed by floor-pinning `joserfc>=1.6.7` (commit `aa920d4`, CI green); not a code defect. NEXT: **6-d.4** — the /actions GUI (reversal linkage + recalled reason/evidence + timed-block "blocked until <ts>" + "reversal authorised — pending wolf-pack" + auto-reversal entries) + chat surfacing + web-test checkpoint.)

_Prior:_ **6-d.2 — reverse-intents + provenance recall + reversal ledger (backend).** The working core of 6-d (ADR 0028): Wolf can now PROPOSE an undo (`unblock_ip` / `enable_user`), recalling WHY the original block was made, plus TIMED blocks that record their auto-reversal time. **Migration 0016** (additive/nullable on `action_proposals`): `reverses_proposal_id` (reversal→block), `auto_unblock_at` (timed block→due time, indexed), `reversal_proposal_id` (block→its authorised reversal); up/down round-trip + `alembic check` clean on Postgres. **Reverse intents** resolve to the SAME platform command as the forward intent (the undo is that command's delete-inverse); `parse_duration` ("30m"/"1h"/"2d"/secs, 60s–30d). **Recall** (`propose_active_response`): an undo calls `find_active_block` → recalls reason+evidence+when, links `reverses_proposal_id`, reverses the EXACT command used, refuses cleanly when no block on record; re-blocking an already-blocked IP surfaces dedup context; `block_duration`→timed block (refused on non-reversible restart); `create_reversal_proposal` stamps the block (no double-reverse). **Reversal execute** (`gateway/reversal.py`, Option A): wolf-pack-bound `perform`/`verify`/`freshness` → reversal lands `succeeded` (authorised+recorded), `dispatched:false`, `reversal_state:authorized_pending_wolf_pack`; the block stays `succeeded` (in effect) until wolf-pack confirms removal — NO fake host success. A succeeded timed forward block stamps `auto_unblock_at`. **`list_active_blocks`** read tool = Wolf's org-scoped dispatch ledger (honest "not a live host check"). Prompt #4 updated (undo + recall + duration + dedup). GATE: ruff + mypy --strict clean; full backend **711 passed / 0 skip** (+reverse-intent/recall/dedup/duration/ledger/reversal-execute/cross-org tests); migration 0016 verified on Postgres; NO CI change (new modules under already-strict `gateway`/`tools`; migration auto-runs in alembic-check). NEXT: **6-d.3** — the timed auto-reversal scheduler (the in-process sweep that fires `auto_unblock_at`, system-initiated + pre-consented by the timed-block approval).)

_Prior:_ **6-d.1 — AR reversal model: ADR 0028 + reversal catalog metadata (design gate).** Opens the **6-d** line (operator-prioritised BEFORE the other action classes): give Wolf a *generic* reversal/undo capability, AR first. Studied **every** AR script — Wazuh `wazuh@v4.14.5` (`src/active-response/{,firewalls/}`) + the local `scriptreference/opnsense-fw` — and traced the manager→agent path through **execd**. **Finding:** every *enforcement* script has an exact delete-inverse (`iptables -D`, `--remove-rich-rule`, remove from `/etc/hosts.deny`, `route del`, `netsh … delete rule`, `passwd -u`, `pfctl -T delete`, `ipfw/npf` delete, opnsense-fw `-T delete`); only `restart-wazuh` is non-reversible. **Decisive constraint:** the **Server API can't dispatch a `delete`** — `framework/wazuh/active_response.py` puts the API `command` into the message's top-level `command`, then the agent's execd (`ExecdRun`) **unconditionally rewrites it to `add`** (`execd.c:276`); `delete` is generated ONLY for execd's timeout-list entry (`execd.c:413`) after the config-side `<timeout>` (per-call `timeout` is a rejected API field). So (a) physical unblock must run on the host → **wolf-pack (Phase 12)** [operator chose **Option A**: authorize+record+recall now, no fake host success]; (b) Wazuh's native timed reversal is config-side/fixed → arbitrary-duration auto-unblock is **Wolf-owned** (6-d.3). THIS slice = the design gate only: **ADR 0028** (full reversal matrix, the execd constraint, the now/wolf-pack split, the provenance-recall + timed-auto-reversal + B1-pre-consent framing, the generic-reversal stance); `ARCommand` gained **`reversible: bool`** (renamed dead `stateful`) + **`reverses_via: str`** (the delete-inverse description, non-empty IFF reversible — test-enforced); reference doc §4b (reversal matrix + why it's wolf-pack-bound); ADR index backfilled 0024–0028. GATE: ruff + mypy --strict clean (`active_response.py`); `test_active_response.py` **+2** (reverses_via-iff-reversible; enforcement-reversible / restart-not) → 28 passed; NO schema/runtime behaviour change yet (catalog + docs only); NO migration; NO CI change (touched module already strict; tests auto-collect). NEXT: **6-d.2** — migration 0016 (reverse linkage + `auto_unblock_at`) + reverse intents (`unblock_ip` / `enable_user`) + provenance recall (block's reason+evidence resurfaced at unblock / re-block) + `list_active_blocks` read tool + wolf-pack-bound reversal `perform`.)

_Prior:_ **6-c.2b — `method` override + OS-unknown failover (SHIPPED, web-tested); CLOSES 6-c.** The second ADR-0027 half + the last 6-c slice. **Optional `method` input** on `propose_active_response` — a specific catalog command instead of the auto-pick (unlocks the stranded commands: host-deny/route-null/win_route-null/ipfw). `resolve_method_command` guards it: command ∈ catalog, **intent-target consistency** (`INTENT_TARGETS` — can't block_ip with a username command), and **unconditional platform-fit** when OS is known (wrong-platform method refused). **OS-unknown user-guided failover:** when `classify_os` returns nothing, an explicit `method` lets Wolf proceed on the requester's ASSERTED platform (platform-fit skipped), annotated; any proposer may use it, human approval the gate (decisions #2/#3). Every proposal records **`method_source`** = auto/override/user_asserted (content-hashed, approver-visible); expected-effect flags overrides + asserted platform. Prompt #4: use `method` only when the user explicitly names a mechanism. WEB-TESTED LIVE: host-deny override on Linux → host-deny (not firewall-drop) ✅; netsh on Linux → refused + model reported it ✅; pf/ipfw override on OPNsense 009 → correctly refused (only opnsense-fw platform-fits) ✅. GATE: ruff + mypy --strict clean; full backend **618 passed / 0 skip**; NO migration; NO CI change; wolf-server restarted fresh. **6-c is now COMPLETE** (intent-driven → BSD/severity/hardening → per-OS selection + OPNsense → method override + failover); ADR 0027 fully implemented. NEXT (per operator): the other action classes (`rule_tuning`/`agent_action`/`config_change`), or 6.10 config-settings / 6.7-6.9 notifications+SMTP. Deferred: model reasoning fine-tune → Phase 7.5 (Central Brain); OPNsense AR-log dashboard decoder → Phase 6.11 (Wolf-assisted Wazuh diagnostics); true host-effect AR verification → Phase 12 (wolf-pack).)

_Prior:_ **6-c.2a — per-BSD-OS AR selection + OPNsense `opnsense-fw` (SHIPPED, real block VERIFIED live).** First ADR-0027 slice; all 4 ADR open questions resolved (macOS→pf; OS-unknown failover open to any proposer; **manager-config presence check DROPPED** — a `<command>` tag proves nothing about the agent; split `OS_BSD` per-OS, version-aware). `active_response.py`: retired `OS_BSD` for `OS_FREEBSD`/`OS_OPENBSD`/`OS_NETBSD`/`OS_OPNSENSE` (OPNsense/pfSense detected AHEAD of generic FreeBSD via the `os.uname` blob); added `opnsense-fw`; `block_ip` per-OS = Linux→firewall-drop, Windows→netsh, **macOS→pf** (was route-null), FreeBSD/OpenBSD→pf, NetBSD→npf, **OPNsense→opnsense-fw**; pf↔ipfw version gate (`_predates_pf`: FreeBSD<5.3 / macOS<10.7→ipfw; best-effort, fail-safe to pf — pf timeline web-verified). Confirmed Wolf NEVER reads `/manager/configuration` in code (only a grounding comment existed) — an AR is just a `PUT /active-response` call. **WIN (web-tested live):** block IP on agent 009 (OPNsense) → Wolf proposed `opnsense-fw` → approved → **the IP landed in the `__wazuh_agent_drop` pf table and was blocked** (firewall Live View "Wazuh agent blocklist"; `pfctl -t __wazuh_agent_drop -T show` shows it). The shipped opnsense-fw does `pfctl -t __wazuh_agent_drop -T add` + `pfctl -k <ip>` (session kill) against the table OPNsense's built-in rule blocks; stock `pf` used a different unreferenced table + no session kill → it dispatched (`pf - add`) but never applied. Resolves both ADR 0027 §4 items + root-causes the earlier no-op; confirms opnsense-fw is THE OPNsense command. **Observability gap (NOT Wolf, NOT blocking):** opnsense-fw's AR run doesn't raise a Wazuh dashboard alert — decoder `ar_log_json` + rule 657 match the standardized `active-response/bin/<cmd>: {json}` line stock C scripts write; opnsense-fw (custom Python) writes free-text → not decoded. Fix = a custom manager decoder+rule (operator-side Wazuh tuning; fits Phase 6.11 Wolf-assisted-Wazuh-diagnostics). GATE: ruff + mypy --strict clean; full backend **610 passed / 0 skip** (per-OS classify, version-gate pf/ipfw, OPNsense→opnsense-fw propose, catalog consistency); NO migration; NO CI change (touched packages already strict); wolf-server restarted fresh to load 6-c.2a. NEXT: **6-c.2b** — optional `method` override + OS-unknown user-guided failover.

_Prior:_ **6-c.1 — BSD active response + dynamic severity + AR-flow hardening (SHIPPED, web-tested).** Web-testing 6-c on the live cluster + two read-only queries (agent OS distribution; manager `?section=command`) + the OPNsense docs drove four changes. **BSD:** agent 009 `opnsense-firewall` reports `os.platform=bsd` (FreeBSD 14.3) and the manager already has `pf`/`ipfw`/`npf` configured → `classify_os` now maps `bsd`/`freebsd`/`openbsd`/`opnsense`/`pfsense`→`OS_BSD` (Darwin still macOS), `pf`/`ipfw`/`npf` catalogued, `block_ip`+BSD→`pf` (pf universal across BSD; ipfw FreeBSD-specific — both present on OPNsense). **Dynamic severity:** replaced the static+backwards `_HIGH_SEVERITY_COMMANDS` (restart=High, block=Low) with per-command catalog **base impact** (block=High, disable=Medium, restart=Low) + context escalation (disable root/admin→High); frontend renders Medium amber, `critical` tier wired. **Validation-error bug:** the dispatcher now renders a guided message (not a raw pydantic `errors()` dump) for EVERY tool, and `propose_active_response.rationale` is now optional (a model omission can't hard-fail the proposal; honest placeholder recorded). **Outcome-reporting bug:** prompt principle #4 now requires the model to report a proposal's outcome (queued→pending approval; rejected→state it + quote the reason) — fixes the silent pivot to a generic agent description. GATE: ruff + mypy --strict clean; full backend suite **0 skip**; dashboard tsc+eslint clean; **web-tested live** (BSD→pf selection ✅, guided refusal relay ✅, severity tiers ✅); wolf-server restarted fresh (18:38) to load the batch before the test. NO migration (severity computed; intent/params in JSONB); NO CI change (touched packages already in the strict set; dashboard edit rides the frontend job). **OPNsense finding:** approved `pf` on 009 DISPATCHED (`pf - add` in the Wazuh AR log) but did NOT land in OPNsense's blocklist — stock pf hits `pfctl` directly while OPNsense manages pf via its own config/alias system → confirms *dispatched ≠ host-applied* + that **agent-side script presence** (not manager config) is decisive; OPNsense's own `opnsense-fw` is the fix. **ADR 0027 (`proposed`):** optional `method` override + manager-config capability verification + OS-unknown user-guided failover + OPNsense→`opnsense-fw` routing; 4 open questions await the operator before any 6-c.2 code. NEXT: commit/push 6-c.1; on operator sign-off of ADR 0027's open questions, build 6-c.2 — where the OPNsense block actually applies via `opnsense-fw` and the stock-pf no-op gets root-caused.

_Prior:_ **6-c — platform-aware, intent-driven AR selection (SHIPPED).** The model used to name the active-response *command* (firewall-drop vs netsh) with the validator refusing a confirmed wrong-platform pick (6-b.1); 6-c moves command selection OFF the model entirely. **`wazuh/active_response.py`** gained the intent layer next to `AR_COMMANDS`: `INTENT_BLOCK_IP`/`INTENT_DISABLE_USER`/`INTENT_RESTART`, the `_INTENT_COMMANDS` selection table (string = OS-agnostic; dict = OS-specific), `AR_INTENTS`, `INTENT_LABELS`, and `resolve_intent_command(intent, os_class) -> IntentResolution` — `block_ip` → firewall-drop (Linux)/netsh (Windows)/route-null (macOS); `disable_user` → disable-account (Linux/macOS); `restart` → restart-wazuh (any). **`tools/propose_active_response.py`**: input field `command` → **`intent`**; `run()` resolves OS (`resolve_agent_os`→`classify_os`) then `resolve_intent_command`, refusing with a guided reason (never queuing) when the OS is unknown for an OS-specific intent OR the intent is unsupported on the OS (`disable_user` on Windows — no default AR); the resolved command + originating `intent` are frozen into the content-hashed proposal; summary/expected-effect name BOTH the intent and the selected command + OS. Downstream (validator backstop, persistence, execution) UNCHANGED. **`agent/prompts.py`**: propose principle #4 now says express the intent, not a command. GATE: ruff + mypy --strict clean (`wazuh`/`tools`/`agent`); full backend suite **603 passed / 0 skip** (catalog-consistency guard + `resolve_intent_command` units + intent-driven propose tests incl. headline block_ip-on-Windows→netsh, OS-unknown refusal, disable_user-on-Windows refusal, OS-agnostic restart). NO migration (intent in JSONB `parameters`); NO CI change (touched packages already strict; the propose-tool execute-token guard still holds). ADR 0025 amended with a 6-c addendum; roadmap marks 6-c ✅; AR reference §5 updated. Refusing an OS-specific intent on unknown OS is intentionally STRICTER than 6-b.1's fail-open validator (ambiguous selection must never reach the queue); method-within-intent (host-deny vs firewall-drop) is a tracked follow-on. NEXT: operator web-test (block IP on a Windows agent auto-selects netsh; restart resolves; needs wolf-server restart to pick up the new tool schema) → commit/push; then the other action classes (`rule_tuning`/`agent_action`/`config_change`).

_Prior:_ **Dependency-security closeout + npm-audit CI gate.** Post-push hygiene after the Phase 6 chunk landed: (1) CI `dep-audit` (pip-audit) flagged a newly-disclosed advisory — `pydantic-settings 2.14.1→2.14.2` (GHSA-4xgf-cpjx-pc3j; symlink traversal in NestedSecretsSettingsSource, not a path Wolf uses) — floor pinned by hand in server+gateway + uv lock (commit `7c124d8`). (2) Operator spotted a failing Dependabot update job → dashboard npm vulns (which pip-audit, Python-only, never sees): `undici 8.4.1→8.5.0` (DIRECT dep for the mTLS proxy fetch in `app/api/[...path]`; 7 advisories / 3 high; the Dependabot security-update job had errored on it) + `hono 4.12.21→4.12.26` (transitive via `@modelcontextprotocol/sdk`; high serve-static path traversal) via an `overrides` entry → `npm audit` 0 vulnerabilities (commit `dadf01b`). (3) **Added an npm-audit CI gate** to the `frontend` job (`npm audit --audit-level=moderate` right after `npm ci`, fails fast) — the npm counterpart to the Python `dep-audit` job; moderate+ now fails the build, low-severity transitive noise allowed, escape hatch = floor pin / `overrides`. Job renamed `Frontend (tsc + eslint + build + audit)`. All Phase 6 work (`710fd87` grounding+6-b bundle, `7c124d8`, `dadf01b`) pushed; CI green on each (npm-audit gate landed `587ff4a`).

_Prior:_ **Grounding modes web-test — default → `deferred`; `cited` evidence-scope PULLED; 2 UI fixes.** Operator web-tested the ADR 0026 modes live + flagged 2 UI issues (screenshot). (1) **deferred** preferred over blocking ("I like it even better") → made the **LIVE default** (`.env` `GROUNDING_MODE=deferred`; `config.py` default stays `blocking` as the no-`.env` fallback). (2) **incremental** "seemed same as deferred" — expected on the single 6 GB GPU (judge batches serialize behind the cache-warm shared evidence prefix → progressive chips land together); it's verified-wired (emits `grounding.partial`→`grounding.completed`, `test_incremental_mode_emits_partial_then_completed`), diverges only with `OLLAMA_NUM_PARALLEL>=2`; KEPT as a selectable option for that hardware. (3) **`GROUNDING_EVIDENCE_SCOPE=cited` PULLED** — produced Not-Verified almost everywhere: the "dedupe to last call per tool name" heuristic dropped a RICH earlier `list_agents` (status=disconnected → 2 hits) in favour of the EMPTY later one (never_connected → 0 hits) → judge STARVED of evidence → flagged true claims unsupported. The fix isn't a better heuristic (the model legitimately calls the same tool with different args) — safe trimming needs PER-CLAIM relevance, which belongs to the **grounding-enrichment phase** ([[grounding-enrichment-tools-future-phase]]). Removed the knob + `_scope_tool_results` + `build_evidence(scope=)` + all plumbing (config/loop/chat) + the 2 cited tests; evidence is always `all` (proven). UI: (a) streaming **caret** was a block sibling after `<Markdown>` → rendered on its OWN line (the "1-line gap" in the response box); now an inline `::after` on the last prose block (`STREAM_CARET` in `message-thread.tsx`) → sits at the END of the current streamed line; (b) composer **paste gap** — real cause (clarified): a ctrl+C selection over the rendered thread carries trailing newlines from block boundaries (the Copy *button* is clean); added an `onPaste` normaliser (strip trailing whitespace + collapse 3+ newline runs; clean pastes → native), plus `rows={2}`→`rows={1}` so short content doesn't sit in a 2-row box. GATE: ADR 0026 addendum; ruff + mypy --strict clean; full backend suite green; dashboard `tsc` + `eslint` clean; NO migration; NO CI change; wolf-server restarted with `GROUNDING_MODE=deferred`. NEXT: operator sign-off → commit/push (this + the grounding-modes slice + pending 6-b…6-b.3) → **6-c** (platform-aware AR selection); robust grounding-evidence tooling tracked for its dedicated phase.

_Prior:_ **Grounding execution modes (ADR 0026) — blocking / deferred / incremental, CONFIGURABLE; backend SHIPPED, default `blocking` (zero regression).** After praising 6-b.3 quality, the operator flagged the post-stream grounding pause and asked for async/simultaneous grounding as a switchable option. Built THREE selectable grounding execution modes + an evidence-scope sub-knob, env-driven now (`GROUNDING_MODE` / `GROUNDING_EVIDENCE_SCOPE`) and queued as the **3rd Phase 6.10 Superuser-GUI consumer** (after the same-network gate + model posture): **blocking** (default — judge awaited BEFORE the `answer` event; today's behavior, unchanged) / **deferred** (the `answer` event fires immediately with raw content + `grounding_pending`; the judge runs after; a follow-up `grounding.completed` carries the annotated content + counts the frontend PATCHES onto the settled message — time-to-readable-answer drops to the token stream alone, chips arrive async) / **incremental** (claims judged in CONCURRENT batches via `validate_streaming`; each batch emits `grounding.partial` so chips pop in progressively; real concurrency on `OLLAMA_NUM_PARALLEL>=2`, degrades gracefully to ~deferred on the single 6 GB GPU behind a cache-warm shared evidence prefix). **#2 evidence scope:** `cited` feeds the judge only citation-bearing tool results deduped to the LAST call per tool (safe prompt-eval cut; never drops a failed-tool negative-evidence signal); `all`=today. Backend: `config.py` knobs (+normalizing props, unknown→safe default); `grounding/validator.py` (`build_evidence(scope=)` dedupe + shared `_prepare`/`_assemble` + `validate_streaming` async-gen with offset-mapped batched merge); `agent/loop.py` `_finalize_answer` now mode-aware + OWNS the `answer` emission (blocking emits AFTER grounding; deferred/incremental emit raw-first then patch); `events.py` + `chat.py` new `grounding.partial` event + settings wiring (non-stream `POST /chat` always blocking + scope). Frontend: `types.ts` (`grounding.partial`, `grounding_pending` on node + completion), `branches.updateAssistantGrounding` (in-place node patch — can't re-append, tree forbids dup ids), `use-conversation-streams` (`groundingPatch` state + partial/completed handlers + pending-flag on `answer`), `chat-shell` **ref-guarded** apply-effect (mirrors the archive effect; set-state-in-effect solved at ROOT, NOT an eslint disable), `message-thread` "verifying…" pill while pending. GATE: ADR 0026; **595 backend / 0 skip** (+ validator cited/streaming/failed-batch + loop blocking/deferred/incremental event-ordering tests); ruff + mypy --strict clean; dashboard `tsc` + `eslint` clean; NO migration; NO CI change (all edits to already-strict modules; frontend rides the existing job). WEB-TEST HAND-OFF: wolf-server restarted with the new code; default `blocking` = unchanged baseline → set `GROUNDING_MODE=deferred` (or `incremental`) in `.env` + restart to exercise the feature (answer settles immediately + "verifying…" pill → chips patch in async). NEXT: operator web-test of the modes → pick + flip the default → commit/push (this slice + pending 6-b…6-b.3) → **6-c** (platform-aware AR selection).

_Prior:_ **6-b.3 — model posture → UNIFIED-8B (configurable) + propose-tool citations.** Web-test round 3: qwen3:4b proved unreliable on the agentic propose flow (grounded agent 003 correctly post-6-b.2, but then called the wrong tool with a missing field + never reached `propose_active_response` → nonsense answer). FIX (operator: quality/reliability over speed, speed is hardware-bound): `.env` `DEFAULT_MODEL_ID` flipped `qwen3:4b→qwen3:8b`; judge already 8b → **chat + judge BOTH qwen3:8b**. Split NOT removed — the `DEFAULT_MODEL_ID`/`GROUNDING_JUDGE_MODEL_ID` env knobs still select it (revert chat to qwen3:4b); Phase 6.10 GUI toggle still lands; `num_ctx` already 8192 (aligned); bonus: same model for chat+judge → NO 4b↔8b swap. ADR 0024 addendum records the revisited active default. SECOND FIX: propose-tool **citations** — `ProposeActiveResponseOutput` gained a `citation` field (set on every path via `make_citation`), so a `propose_active_response` call now shows in the Evidence/Citations panel like read tools (no frontend change — loop+UI already render any tool's citation). ruff+mypy clean; propose suite green (citation asserted); wolf-server restarted 19:16 (verified `default_model_id=qwen3:8b`); no migration; no CI change. NEXT: operator web-test (8b completes propose flow + citation shows) → commit/push 6-b…6-b.3 → 6-c (platform-aware AR selection).

_Prior:_ **6-b.2 — real-agent AR execution CONFIRMED + agent-grounding fix; 6-c QUEUED.** After restarting wolf-server (6-b.1 fix was on disk but the long-running service held pre-fix code — restart is now part of the web-test handoff), operator re-tested: **Approve & execute WORKED + showed in Wazuh's active-response log** = smoke (b) effectively passed on a real agent. Two findings fixed (one root cause): asked to act on agent 003, Wolf proposed on **001** → capability check correctly refused (001 not in the credential's group) → the RBAC engine was right, it was fed the wrong agent. Cause: (1) stale agent **system prompt** (principle #4 still said "you cannot block IPs … explain it would have to be proposed" — never pointed the model at `propose_active_response` or told it to ground the exact agent); (2) `agent_id` schema example was literal `'001'` (small chat model copies it). FIX: rewrote prompt #4 for capability-driven propose-and-approve (use the EXACT agent from request/`list_agents`; never default/guess; pass exact target; ask if ungroundable) + neutralised biasing examples (`agent_id`/`srcip`/tool desc). ruff+mypy clean; propose suite green; wolf-server restarted 18:42 with new prompt+schema; no migration; no CI change. QUEUED **6-c — platform-aware intent-driven AR selection** (operator-requested): model gives intent (`block_ip`/`disable_user`/`restart`)+agent+target, Wolf resolves agent OS + deterministically picks the platform-correct command (firewall-drop↔netsh, route-null↔win_route-null), so "block IP on agent 003 (Windows)" auto-selects `netsh`; platform *safety* already shipped (6-b.1 validator refusal), 6-c adds *smart selection* via a server-side resolver. NEXT: operator re-test (agent-grounding) + commit/push 6-b/6-b.1/6-b.2, then 6-c.

_Prior:_ **6-b.1 — active-response API contract FIXED + AR command catalog (web-test feedback).** Operator web-tested 6-b (checks 1–3 ✅); 2 findings + a directive to master Wazuh AR. **Finding 1:** firewall-drop failed `400 … Invalid field {'custom'}` — the write client sent a body Wazuh 4.14.x rejects. Researched authoritatively (NOT guessed): probed the **live cluster v4.14.3** safely (sentinel field + nonexistent agent → no execution) → `PUT /active-response` accepts ONLY `command`/`arguments`/`alert`; `custom`/`timeout`/`location` REJECTED; command must be **`!`-prefixed** to run now; manager does NOT validate command name; **HTTP 200 even on failure** (`error:1`+`failed_items`). Read the **AR script source on GitHub across v4.14.3 + v4.14.5** (latest 4.x; identical except `netsh.c` internal rule build) → shared helpers give a UNIFORM contract: srcip blockers read `parameters.alert.data.srcip` (validated numeric IPv4/IPv6 by `get_ip_version`), `disable-account` reads `…data.dstuser`, `restart-wazuh` neither; `add`/`delete` reversal is config-side (no per-call timeout). FIX: new `wolf_server/wazuh/active_response.py` = AR command **catalog** (platform/target/reversible) + `build_ar_body` (`!`-prefix, `alert.data.*`, **NO custom**) + `classify_os`/`is_valid_ip` + `interpret_ar_result` (dispatch ≠ host-applied — honest 200-with-failed_items verification); validator now catalog-driven (require valid srcip / non-empty username per command; **lenient** platform check — refuse ONLY a confirmed OS mismatch, never unknown OS per the 6-a.1 no-false-refusal lesson); propose tool gained structured `srcip`/`username` + resolves agent OS + freezes params into the content-hashed proposal; write client + execution `_perform`/`_verify` rewired through the catalog. **Finding 2 (UX):** expired card read "Expires expired" → now "Expired" (red). Frontend card + approve-confirm surface the structured target ("block IP …"). **Reference:** `docs/reference/wazuh-active-response.md` (full source-grounded catalog of EVERY default AR command + the unified contract + correlations); ADR 0025 amended; `wazuh-active-response-contract` reference memory added + mirrored. VERIFIED: corrected body via the REAL write client vs nonexistent agent 99999 (zero execution) → **200 OK** for firewall-drop/disable-account/restart-wazuh (old 400 GONE); **659 backend / 0 skip**; ruff + mypy --strict clean; dashboard `tsc`+`eslint` clean; NO migration. CI audit: no workflow change (new module in `wolf_server/wazuh` already strict; new tests auto-collect; frontend rides existing job; safety-check greps only `tools/`). WEB-TEST: re-test block-IP on an **acme** agent → proposal carries the IP; approving runs the corrected command (real firewall-drop on a real acme agent = smoke (b), use a disposable agent). NEXT: operator web-test sign-off + commit/push.)

_Prior:_ **6-b — action-approval queue GUI SHIPPED (pending operator web-test).** The human-in-the-loop surface for Phase 6: a role-gated `/actions` dashboard page where a reviewer sees Wolf's pending action proposals and approves/rejects them. Backend (6-a/6-a.1) already exposes `/api/v1/organization/action-proposals` (list/get/approve/reject, capability-gated); this slice is the GUI + one backend affordance: `list_proposals` gained **`state=all`** (recent across all lifecycle states, newest-first, cap 200) for the activity history, default stays `pending`; both org-forced-filtered; +3 tests. Frontend: `lib/types.ts` `ActionProposal`/`ProposalState` (mirrors `ProposalOut`); `lib/api.ts` `listActionProposals`/`approveActionProposal`/`rejectActionProposal`; new `lib/capabilities.ts` (`canProposeActions`=analyst+, `canApproveActions`=responder/engineer/admin — UX mirror of `ROLE_CAPABILITIES`, superuser excluded); `app/actions/layout.tsx` (ACTION_PROPOSE guard, else → /chat) + `app/actions/page.tsx` (pending queue as detail CARDS — action/target/rationale/expected-effect/evidence/rollback/severity/proposed-time/**TTL countdown** — Approve/Reject + recent-activity history with verification outcome; Approve = confirm dialog stating it's a REAL fleet change; Approve disabled on one's OWN proposal = separation-of-duties, also server-enforced; analyst = read-only); `chat-header.tsx` "Action approvals" entry (clipboard icon, analyst+). GATE: **641 backend / 0 skip** (CI-parity; +3 list-endpoint tests); ruff + mypy --strict clean; dashboard `tsc` + `eslint` clean; wolf-server restarts clean (propose tool registered); `/actions` route mounted + auth-gated (401 unauth); NO migration. CI audit: no workflow change (frontend rides the existing tsc+eslint+build job; backend change in `api/` already strict; new test auto-collects; safety-check greps only `tools/`). WEB-TEST HAND-OFF: generate a proposal by asking Wolf in chat to propose `firewall-drop` on an **acme** agent (acme RBAC-allowed AR on `agent:group:acme`; beta refused at propose time = negative test) → lands pending in /actions. SAFE now: view / Reject / self-approval(SoD) block / analyst read-only. NOTE: "Approve & execute" on an acme proposal runs a REAL firewall-drop = smoke (b), do deliberately on a disposable agent. FOLLOW-ONS: smoke (b) per go-ahead; pending-count badge / live push → notification+SSE phases (6.7/6.8); other action classes. NEXT per operator: web-test sign-off + commit/push, then other action classes / 6.10 / 6.9.)

_Prior:_ **6-a.1 — group-aware capability gate (live-smoke fix BEFORE 6-b).** Ran the read-only capability-denial smoke (a) against the REAL cluster before building 6-b; it mechanically passed but CAUGHT a correctness gap: the per-org `wolf-acme` credential grants `active-response:command` on **`agent:group:acme`** (the 6.6-f isolation model), NOT `agent:id:*` — so 6-a's id-only `can(AR, "agent:id:<id>")` pre-flight would have FALSELY refused every AR acme was genuinely authorized for (6-b would be dead-on-arrival). FIX: the gate now mirrors Wazuh RBAC's agent resource expansion — allowed on `agent:id:<id>` (or wildcard) OR on `agent:group:<g>` for ANY group the target agent is in, deny-wins across the union (`CredentialCapabilities.can_on_agent`); the agent's groups are resolved FRESH at decision time (`resolve_agent_groups`, read-only, fail-closed to `[]`) in BOTH the propose pre-flight AND execution `_perform`; `WazuhServerApiActionClient.execute_active_response` now takes `agent_groups` + gates via `can_on_agent`. Operator validated by REMOVING `active-response:command` from `wolf-beta` (acme/beta otherwise IDENTICAL): live smoke (read-only, NO PUT ever issued) → acme ALLOW on in-group agent 002 / REFUSE out-of-scope agent 001 (403→fail-closed); beta REFUSE on visible agent 006 (`available_action_classes()` EMPTY — capability ABSENT, not invisibility). GATE: **638 backend / 0 skip** (capability + action-client + propose suites extended with group-scoped allow / cross-group deny / no-capability deny); ruff + mypy --strict clean (no strict-set change — `wazuh`/`gateway`/`tools`/`api` already covered); safety-check greps still clean (no write refs in `tools/`); NO schema change. ADR 0025 amended ("agent resource expansion" subsection); `wolf-unrestricted-full-power` memory updated + mirrored. FOLLOW-ONS unchanged: **6-b** approval-queue GUI next; then the live propose→approve→**EXECUTE** smoke (real state change) per operator go-ahead. NEXT: 6-b GUI per operator.)

_Prior:_ **Phase 6 OPENED — capability-driven action execution (ADR 0025); foundational backend slice 6-a SHIPPED.** Reframe per `wolf-unrestricted-full-power`: Wolf is NOT read-only — it acts within whatever the per-org Wazuh credential's RBAC authorizes; doc 04's safety machinery is PRESERVED, only doc 03 fact #3 (credential physically read-only) is INVERTED. Operator decisions: **A2** execute IN wolf-server via an in-process gateway module (the `services/gateway/` stub stays reserved — NOT a separate service in v1); **B1** every write needs explicit human approval (NO autonomous writes); **C1** ADR + a one-action foundational slice. SHIPPED: ADR 0025; `wazuh/capabilities.py` (RBAC introspection via `/security/users/me/policies`, fail-closed, `can()` / `available_action_classes()`); `wolf_server/gateway/` (`ActionProposal` + migration `0015`; `state_machine` forward-only + gated; `proposals` create + content-hash freeze + computed severity; `validator` structural HARD GATE = resolved target / bounded blast radius / allow-listed command; `approval` = separation-of-duties + `ACTION_APPROVE` + TTL; `execution` = hash-integrity → freshness re-check → bounded write → verification read → audit every transition); bounded `WazuhServerApiActionClient.execute_active_response` (capability-checked BEFORE issuing; read-only `WazuhServerApiClient` kept exactly as-is); `propose_active_response` tool (tier=propose, registered at startup); RBAC `ACTION_PROPOSE` (analyst+) + `ACTION_APPROVE` (responder/engineer/admin), NO `ACTION_EXECUTE` role (execution is system-internal); org-scoped capability-gated API (list/get/approve/reject) with `action_proposals` in the cross-org isolation gate. GATE: **626 backend / 0 skip** (capability introspection, validator, SoD, state machine, freshness, verification, write-guard, cross-org proposal isolation); ruff + mypy --strict clean (`gateway` + `tools` ADDED to the CI strict set); migration `0015` up/down round-trips on Postgres + `alembic check` clean; wolf-server boots clean (propose tool registered + all 4 routes mounted). FOLLOW-ONS (tracked, NOT built): **6-b** approval-queue GUI (the browser web-test checkpoint); other action classes (`rule_tuning`/`agent_action`/`config_change`); severity-tiered authority / four-eyes / crown-jewel (policy hooks, B1 default = approval-for-all); auto-execution (Phase 13). The live propose→approve→**EXECUTE** smoke on the real cluster (a real state change) is deferred to operator go-ahead / 6-b. NEXT: 6-b GUI, other action classes, or 6.10 (config-settings) / 6.9 (SMTP) per operator.)

_Prior:_ **Model posture measured + decided (ADR 0024) — pre-Phase-6 model-stack checkpoint, DOCS-ONLY (no code/CI change).** Operator interrogated Wolf's runtime model posture before opening Phase 6 and hypothesised that unifying on `qwen3:8b` for BOTH chat + grounding would be FASTER (kill the per-grounded-turn 4b↔8b swap). MEASURED FIRST (live on the dev host RTX 4050 6 GB, warm page cache, via Ollama API timing metrics): hypothesis is WRONG on this hardware — `qwen3:4b` chat **61.8 tok/s** vs `qwen3:8b` chat **18.0 tok/s** (3.4× slower); warm grounded turn **SPLIT 29.3 s vs UNIFIED-8b 35.5 s** (split ~6 s FASTER); the swap is only ~1.8–2.8 s warm (NOT the villain) — the 8b-chat slowdown costs more than the swap it would save; the judge leg (~22 s = 3.9 s prompt-eval over 4,311 evidence tokens + ~17 s generating @ 11.8 tok/s) is IDENTICAL in every posture (judge is 8b either way), so posture is NOT the grounding-latency lever (output length / evidence window / keep-warm are); ADR 0015's "2 m 44 s" was the cold-page-cache edge case, not steady state. DECISION (ADR 0024): KEEP THE SPLIT (`qwen3:4b` chat / `qwen3:8b` judge) as the DEFAULT — faster AND preserves the independent judge (ADR 0013, 8b grading 4b not itself); NO runtime change — the split is already live in `.env`, so this makes the posture EVIDENCE-BACKED not assumed. EMBEDDINGS: keep BOTH `nomic-embed-text` + `nomic-embed-text-v2-moe` (ADR 0014 decisive: dual RRF precision@5 60% vs 35% single; v2-moe alone truncates ~3.5% of long chunks — neither is self-sufficient). QUEUED into Phase 6.10: a Superuser-only / audited / synced **"Model posture"** setting (split vs unified-8b) via the existing `DEFAULT_MODEL_ID` + `GROUNDING_JUDGE_MODEL_ID` knobs — a 2nd concrete consumer after the same-network-gate toggle; unified-8b stays a valid SELECTABLE option (max answer-quality / idle-resilient; align `num_ctx` if chosen). Grounding-latency levers tracked as a posture-INDEPENDENT future optimization. NEXT per operator: Phase 6 (wolf-gateway, capability-driven) / 6.10 (config-settings) / 6.9 (SMTP).)

_Prior:_ **6.6-g — vestigial URL-column cleanup + indexer-node fallback (the last 6.6 structural debt).** (a) DROPPED the per-org `opensearch_url`/`server_api_url`/`verify_tls` columns (migration `0014`) — since 6.6-e the resolver reads URLs+TLS from the install ecosystem TOPOLOGY (fresh per query), so they were written-but-never-read; row now holds only cred keys + index pattern + scoping; API + `_upsert_wazuh_config` stop writing them. (b) MODERNISED `bootstrap_organization`: sources URLs/TLS from the topology (requires one to validate, like the API), DROPPED `--opensearch-url`/`--server-api-url`/`--verify-tls` args (the pure `_validate_wazuh_connection` helper + its tests unchanged). (c) INDEXER-NODE FALLBACK-on-failure (ADR 0020 decision 1's resilience half): `_resolve_runtime_endpoints` now SHUFFLES distributed indexer nodes (random primary + ordered fallbacks via new `WazuhConnection.opensearch_fallback_urls`); `WazuhOpenSearchClient.execute` retries the SAME query against the next node on a transport-error/5xx (4xx = credential verdict, NOT retried); single-host has no fallbacks, manager stays master-only. Gate: ruff + mypy --strict (incl. management) + full backend suite green; migration `0014` up/down round-trips on live Postgres + `alembic check` clean. LIVE-VERIFIED on the real 3-node cluster: healthy primary OK; DEAD primary `.99` → logs `node_unreachable` + fails over to a real node → OK; all-dead → `WazuhOpenSearchError`. STILL TRACKED (not this slice): Q4 citation enrichment (surface `agent.labels.group` in `AlertHit` — tool-enrichment phase) + the `read *` leak forward-coverage (DLS on all queried index families — Phase 6.11); index-pattern *discovery* closed-as-infeasible (scoped users can't enumerate indices). NEXT per operator: Phase 6 (wolf-gateway, capability-driven) / 6.10 (config-settings) / 6.9 (SMTP).)

_Prior:_ Phase 6.6 CLOSED — operator web-test sign-off ("all checkpoints working as described and as expected") + a credential-change validation fix + foundational direction captured.** Web-test fed back 4 items: **(Q1, FIXED)** changing a per-org Wazuh username with a blank password silently kept the OLD stored credential (`_resolve_credential` ignored the typed username) → now keep-existing applies ONLY when the username is unchanged; a username change with no password is a 422, blank+unchanged still keeps the stored password; client-side inline check mirrors it (card tracks loaded usernames); +2 tests (22 in the credentials suite); ruff/mypy/tsc/eslint clean; wolf-server restarted. **(Q2)** the index pattern is a TARGET SELECTOR not a restriction (DLS scopes the data) — kept as a default-`wazuh-alerts-*` advanced override; dynamic index-discovery tracked (`wazuh-credential-refinements`). **(Q3)** single-org (non-MSSP) already works (one broad-access credential, no DLS, filter OFF → the scope probe's `unrestricted` path) — captured the standing principle `single-org-mssp-parity` (MSSP-achievable must be single-org-achievable). **(Q4)** `agent.labels.group` is only a query FILTER (opt-in), never silently injected; surfacing it in citations is a tool-enrichment item (`AlertHit` omits it today). **DIRECTION SHIFT (memory `wolf-unrestricted-full-power`):** operator reframed Wolf from read-only to FULLY UNRESTRICTED + empowered — restriction comes from Wazuh's own RBAC (the credential's capabilities), not Wolf limiting itself; reshapes Phase 6 (propose+approval → capability-driven), the read-only client posture, Phase 13; land via ADR when Phase 6 opens. Also added roadmap **Phase 6.11** (Wolf-assisted Wazuh RBAC provisioning + diagnostics, Superuser-only, Wolf's first write-authority over Wazuh — ADR-worthy) + **Phase 6.12** (per-org ↔ Superuser assistance/escalation, deps 6.7/6.8). NEXT per operator: Phase 6 (wolf-gateway, now capability-driven) / 6.10 (config-settings) / 6.9 (SMTP); tracked Wazuh cleanups in `wazuh-credential-refinements` + the vestigial per-org URL columns.)

_Prior:_ 6.6-f SHIPPED — dynamic per-org scoping: drop static org-id filter, add `agent.labels.group` injection, fix probe + scope bugs (ADR 0020). Wiring real per-org Wazuh RBAC (the official "read + manage a group of agents" use case) surfaced three problems, diagnosed LIVE against the cluster (admin + per-org creds): (1) the static `organization_id` indexer-query filter is the wrong tool — Wazuh alerts don't carry it, and the per-org credential's own Wazuh RBAC + index DLS already isolate it dynamically (`wolf-acme`'s role has `wazuh-alerts*` DLS `match agent.labels.group:acme` → sees 36 alerts vs admin 216k); REPLACED with an OPTIONAL, opt-in `inject_group_label_filter` injecting `terms:{agent.labels.group:[...]}` — the REAL field, multi-label OR-combined, DEFAULT OFF (credential is the boundary); `wazuh_agent_groups` → `agent_group_labels`. (2) Probe bug "authenticated (HTTP 403)": `probe_indexer` hit `GET /` (needs `cluster:monitor`, correctly denied a scoped role) → new `probe_indexer_read` tests `_count` on the index pattern → honest "can read N alert(s)" / "denied read"; install-topology probe keeps `GET /`. (3) Scope bug "across 0 group(s)": scope called `/groups` (needs `group:read`, correctly absent) → now reads `GET /security/users/me/policies` (allowed for self) → the credential's TRUE `agent:group:*` RBAC scope (`acme`), NOT the incidental multi-group membership of its agents (`default`/`BIS`); multi-group supported. Files: model + migration `0013`; `probe.py`/`credentials.py`/`config.py`/`resolver.py`/`query_builder.py`/`opensearch.py` (injection + renamed safety re-checks `_assert_group_label_filter_present`/`_assert_group_label_match`); `api/wazuh_credentials.py` (422 when filter on with no labels); `bootstrap_organization.py` (`--inject-group-label-filter` + `--agent-group-label`). Frontend: card relabel ("Agent group label(s)" + "Restrict indexer queries to these label(s)" + scoped-group badges) + types. ADR 0020 addendum. Gate: ruff + mypy --strict + **580 backend / 0 skip / 0 warning** + cross-org isolation gate RE-EXPRESSED against `agent.labels.group` (still meaningful); `tsc`/`eslint` clean; migration `0013` up/down round-trips on live Postgres + `alembic check` clean. **LIVE-VERIFIED** against the real distributed cluster: acme → "can read 36 alert(s)" + "scoped to 1 group(s): acme"; beta → "0 alert(s)" + "scoped to 1 group(s): beta"; injection acme/label=acme → 36 hits all `agent.labels.group=acme` (return-check passed); label=does-not-exist → 0 hits. Phase 6.6 (a/b/b.1/c/d/e/f) functional web-test essentially passed; AWAITING operator sign-off of the new card behavior, then Phase 6.6 CLOSED. NEXT per operator: Phase 6 (wolf-gateway) / 6.10 (config-settings) / 6.9 (SMTP).)

_Prior:_ 6.6-e SHIPPED — runtime per-query topology + credential resolution (ADR 0020). `resolver.get_wazuh_connection` rewired: URLs + TLS from the install ecosystem topology (read fresh per query; distributed → random indexer node + manager master); per-org credentials + index filter + scoping flag from `organization_wazuh_configs`. New `WazuhTopologyMissingError` (404). `tests/test_resolver.py` (4). Per-org URL columns now vestigial (tracked cleanup). 499 backend / 0 skip.

_Prior:_ 6.6-b.1 — distributed topology refinement (operator UI web-test feedback): required indexer-only `cluster_name` → uniform `WazuhNode {url, name?}` (OPTIONAL component-specific names) + MULTIPLE dashboards (`dashboards` list ≥1, each a probe blocker); single-host unchanged; no migration (JSON shape). ADR 0020 addendum. 495 backend / 0 skip.

_Prior:_ 6.6-d SHIPPED — per-org Wazuh Credentials UI + rotation log (ADR 0020). New `components/wazuh-credentials-card.tsx` on each org's Superuser detail page: indexer + Server-API user/password (write-only, blank=keep), index filter, optional agent groups, inject-filter toggle. "Test & save" SOFT-FAIL (saves even on probe failure; renders probe results + scope summary + warnings + verified/not-verified). 409 (no topology) → guided link. New Superuser `GET .../wazuh-credentials/history` (rotation log, never logs creds) + 2 tests. 492 backend / 0 skip.

_Prior:_ 6.6-b SHIPPED — install-level Wazuh Ecosystem UI (ADR 0020), frontend-only. New `app/superuser/wazuh/page.tsx` (Superuser-only, new "Wazuh ecosystem" nav item): Single/Distributed segmented builder (dynamic indexer-node + worker lists), write-only credentials (blank = keep), verify-TLS toggle. "Test & save" → PUT: hard-fail 400 renders the failing-endpoint detail; success renders per-endpoint probe results + worker warnings + "last verified". New `lib/types.ts` topology types + `lib/api.ts` fetchWazuhTopology/saveWazuhTopology. tsc + eslint(0); live route 200.

_Prior:_ 6.6-c SHIPPED — per-org Wazuh credentials backend (ADR 0020). New `wolf_server/wazuh/credentials.py` (`probe_org_credentials` reuses 6.6-a's probes + adds a SCOPE SUMMARY via Server-API /agents+/groups, never raises; `resolve_endpoints_from_topology`). New `wolf_server/api/wazuh_credentials.py`: `GET`/`PUT /api/v1/superuser/organizations/{id}/wazuh-credentials` (Superuser-only). PUT = SOFT-FAIL save (creds persist even when probe fails; validated_at null), 409 if no install topology, audit never logs creds, "omit password ⇒ keep existing". Migration 0012 adds optional `wazuh_agent_groups`. Decisions: TWO credential pairs per org (Indexer+Server-API); per-org row keeps URL columns sourced from topology as a COHERENCE BRIDGE until 6.6-e. 490 backend / 0 skip; alembic 0012 round-trips.

_Prior:_ 6.6-a SHIPPED — install-level Wazuh ecosystem topology, backend only (ADR 0020). Phase 6.6 = Superuser-owned Wazuh component mapping (proceeding ahead of Phase 6/wolf-gateway per operator; 6.6 depends only on shipped 6.5 Superuser+RBAC). New `wolf_server/wazuh/probe.py` (reusable indexer/manager/dashboard probes → `EndpointProbeResult`, never raises), `wolf_server/wazuh/topology.py` (pydantic discriminated union single/distributed), `WazuhEcosystemTopology` ORM + migration 0011 (single-row install-wide; passwords → secrets backend ONLY), `wolf_server/api/wazuh_topology.py` (`GET`/`PUT /api/v1/superuser/wazuh-topology`, Superuser-only, validate-before-persist HARD fail — workers are warnings; audit never logs creds). INERT at runtime until 6.6-e. 476 backend / 0 skip; alembic 0011 round-trips; live 401 through proxy+mTLS.

_Prior:_ 6.5-h.2 SHIPPED — same-network verification gate (ADR 0018 item 9; topology ADR 0023). TLS edge proxy (`scripts/edge-proxy.mjs`, Node stdlib) fronts STOCK Next: terminates TLS, STRIPS client `x-wolf-client-ip`/XFF/x-real-ip + STAMPS real `socket.remoteAddress` as `X-Wolf-Client-IP`, forwards to UNMODIFIED next dev/standalone on a loopback inner port (SSE preserved, WS/HMR spliced); `dev.mjs` rewired + prod shim + debian install + unit comments. wolf-server: `wolf_server/network/local_network.py` (`local_cidrs()` via `ifaddr`+loopback, `client_ip_in_local_network()` fail-closed); `verify-invite` trusts `X-Wolf-Client-IP` ONLY under dashboard mTLS, else real TCP peer; out-of-network → 403 `wrong_network` w/o consuming token. `same_network_gate_enabled` default **FALSE** (MSSP-safe; on-prem opts in via `SAME_NETWORK_GATE_ENABLED=1`). MSSP gap → default flipped ON→OFF; follow-ups Phase 6.10 (Superuser config-settings GUI toggle) + per-org trusted networks. `wolf_server/network` added to CI mypy strict-set.

_Prior:_ 6.5-h SHIPPED — invite-link verification flow (ADR 0018 item 9, SPLIT — same-network gate → 6.5-h.2 above). Admin-created accounts start UNVERIFIED with a single-use 7-day invite token (SHA-256 hash stored); the user pastes the invite link after login to flip to VERIFIED. Migration 0010 (3 User cols + backfill existing → verified). GATE in require_organization_context (chokepoint for org data; /me + verify-invite + logout reachable); 4 User() sites created verified. New auth/invite.py; create_member returns raw token ONCE; regenerate-invite-link (Admin); POST /auth/verify-invite (single-use, 403 missing/expired/mismatch WITHOUT consuming). Frontend /verify paste-link screen + routing guards + Users-page badge/expiry/generate-link + hardened Dialog. Audit isolated from notifications.

_Prior:_ 6.5-f SHIPPED — Superuser-membership consent gate (ADR 0018): request → Admin approve/reject → time-limited UserOrganization grant → revoke or lazy expiry; migration 0009; activity timeline + dismissable transparency banner; Superuser chat-nav gate; MSSP message hygiene. DEFERRED to dedicated phases (ADR 0021): notification system (Phase 6.7) + SSE (Phase 6.8), isolated from audit/logs.

_Prior:_ 6.5-i SHIPPED — input-validation + exception-handling retrofit, operator web-tested. Closes the gap for fields shipped before the 2026-06-15 standing rule. Audit found the backend largely already at the bar; two real gaps closed: (1) auth.py LoginRequest email/password now Field(max_length=320)/Field(max_length=1024) — email stays plain str so "Wolf"/wolf@wolf.local still log in, no min_length so login doesn't probe credential shape, + test_login_rejects_oversized_fields; (2) frontend client mirrors — chat-composer 4000-char cap+counter+send guard, chat-sidebar rename empty/whitespace revert (commitRename), login-form noValidate + app-native inline empty-field messages + borderless error. Sidebar search + history-overlay filter intentionally unconstrained (read-only, no payload) — recorded so the audit is complete. 481 backend + cross-org isolation + mypy --strict green; frontend tsc/eslint(0)/build green; live smoke 422/401. No CI workflow change needed (existing typecheck/test/frontend jobs already cover the touched surfaces). Standing input-validation rule now satisfied for pre-rule fields + applies inline to all new ones. NEXT: 6.5-f (Superuser-membership-grant).)

---

## 1. Where we are right now

**Current phase:** Phase 6.4 COMPLETE. Four closure points
since the last update:

0. **Phase 6.4 — tenant→organization codebase rename SHIPPED**
   (2026-06-11, commits `076febd` + `a7d0aed` + `e382674` +
   `3f000cb`, all 14 CI jobs green at `3f000cb`).
   - Alembic migration 0007 renames every schema object (3 tables,
     5 columns, 3 named uniques, 3 FKs, 7 indexes) via
     Postgres-native `ALTER ... RENAME` — in-place, no rebuild;
     `downgrade()` round-trips. FK constraints renamed by **dynamic
     pg_constraint lookup** because legacy DBs carry Postgres
     auto-names (`user_tenants_user_id_fkey`) while post-2026-06-05
     fresh DBs carry NAMING_CONVENTION names
     (`fk_user_tenants_user_id_users`) — hardcoding either shape
     broke the other (caught by CI, fixed in `3f000cb`).
   - Backend: ~144 Python files swept (~1500 substitutions);
     `wolf_server.tenancy` → `wolf_server.organization`;
     `Tenant`→`Organization`, `UserTenant`→`UserOrganization`,
     `TenantContext`→`OrganizationContext`, etc. 8 files renamed
     via git mv (incl. `bootstrap_organization.py`,
     `tools/cross_organization_isolation/`).
   - Frontend: 8 dashboard files; `tenant-switcher.tsx` →
     `organization-switcher.tsx`.
   - Docs/config: 27 living docs + 10 memory files + Makefile +
     debian/control + ci.yml + .env.example. ADRs + migrations
     0001-0006 intentionally untouched (immutable history).
   - Hygiene along the way: optional-dep test now `importorskip`s
     cleanly + `embeddings-local` extra installed; starlette
     deprecation warning fixed properly by adding `httpx2` test dep
     (no filterwarnings). Final: 397 passed / 0 skipped /
     0 warnings; mypy --strict clean; isolation suite 18 passed.
   - Memory entry `tenant-renamed-to-organization` flipped
     STANDING RULE → COMPLETED.

1. **Batches 1, 2, 3 of docs/17 release-engineering all CLOSED**
   (2026-06-09). 9 of 14 gaps in docs/17 are now closed; only
   gaps 2, 4, 6, 12, 14 remain — all dedicated-release-phase or
   build-now-adjacent. All 14 CI jobs green at HEAD.

2. **Real CVE patched**: starlette 1.0.0 → 1.2.1 caught by the
   new `dep-audit` CI job (Gap 10) before it could reach a
   release. Validated end-to-end that the new gates work.

3. **Four coupled ADRs PROPOSED** (2026-06-10):
   - **ADR 0017 — Wolf Central Brain** **— ACCEPTED 2026-06-11**
     after 4-round operator review. The 17-point operator
     requirements (memory, deep-think, continuous learning, self-
     validation) finalized into 4 subsystems:
     - **Memory** (4 layers: episodic / session / long-term /
       semantic; Postgres-backed; 6-category fact_type enum; 30d
       exponential decay; load-once retrieval; cross-org "My memory"
       UI per ADR 0019; per-fact-type retention; opt-out toggle)
     - **Thinking** (Deep-think 4th strategy; both manual button +
       auto-escalate triggers; soft cost cap with warning)
     - **Self-validation** (Action validator: hard gate, no
       bypass, inline rejection + edit-and-retry; 3 confidence
       states; no cost cap on validator — safety not perf)
     - **Continuous learning** (4 workers W1-W4; per-org; alert-
       pattern cadence configurable default-daily; environment
       fingerprinting auto with scope expanded to Wazuh log
       sources — alerts.json + indexer indices — but log content
       stays in indexer, not replicated to Wolf DB)
     §"Robust answer posture" (point 8 disagreement) ACCEPTED
     as-written: Wolf delivers "always useful + never unexplained
     'I don't know'" but rejects "never says uncertain" to avoid
     hallucination during incident response. 5 new phases (7.5,
     8.5, 9.5, 11.5, Phase 12 rename) added. wolf-hunt / wolf-den /
     wolf-pack names reserved for future ADRs ~0021/0022/0023.
   - **ADR 0018 — Bootstrap Superuser + Per-Org RBAC + Login UX**
     **— ACCEPTED 2026-06-10** after 5-round operator review.
     Defines: bootstrap Superuser "Wolf" (autogenerated password
     printed once); per-org role model (Superuser / Admin /
     Engineer / Responder / Analyst — attached to UserOrganization);
     Responder direct-execute capability; org-consent gate for
     Superuser data access (Admin must explicitly grant); login UX
     with email+password only (cookie auth-only, per-tab
     `X-Organization-Id` header); session cookie blacklist
     (Redis); invite-link verification flow with same-network gate
     (no SMTP — copy-link out-of-band delivery). Phase 6.4
     (tenant→organization rename) is the unblocked pre-req. Phase
     6.5 = 9 sub-slices, 12-13 sessions. Propose/approve/execute
     enforcement decorators defer to Phase 6 (wolf-gateway). MFA
     deferred to v1.1.
   - **ADR 0019 — Web-first configurability mandate** **— ACCEPTED
     2026-06-10** after 1-round operator review. Two non-negotiable
     rules: GUI completeness (every Wolf knob has a GUI surface) +
     CLI ↔ GUI sync (DB is source of truth via wolf-server API).
     Resolved decisions: manual restart with "pending restart"
     indicator; REST endpoints nested under resources (`/install/*`,
     `/organizations/{id}/*`, `/users/{id}/*`); config-only scope
     (runtime observability is a separate concern). Cross-org
     user-scoped "My memory" view with Superuser-cannot-see-others
     caveat. Catalog of ~10 existing CLI surfaces becomes a tracked
     checklist that closes per-row as GUI counterparts ship.
   - **ADR 0020 — Superuser-owned Wazuh component mapping** **—
     ACCEPTED 2026-06-10** after 1-round operator review. Install-
     level Wazuh ecosystem topology (single-host + distributed) +
     per-org Wazuh API credentials, Superuser-only at both layers.
     Resolved decisions: random indexer node selection; Postgres +
     Fernet credentials (no Vault for v1); hard fail install-probe
     + soft fail per-org credential probe; one install = one Wazuh
     ecosystem (multi-ecosystem deferred); single shared dashboard
     URL; no restart needed on topology change (per-query DB read);
     credentials live in secrets backend only (separate from org
     metadata). 5 sub-slices under Phase 6.6, sequenced AFTER
     Phase 6.5.

   Roadmap updated with four new phases (7.5, 8.5, 9.5, 11.5) +
   Phase 12 renamed to wolf-pack + new Phase 6.5 (Bootstrap + RBAC
   + Login per ADR 0018) + new Phase 6.6 (Wazuh mapping per ADR
   0020). All four ADRs status: PROPOSED.

4. **Memory directory moved into the repo at `memory/`** (operator
   direction 2026-06-10). Previously lived at
   `~/.claude/projects/.../memory/`; now part of git history. New
   standing rules saved this session:
   - `wolf-bootstrap-superuser-flow.md`
   - `shell-wrapper-required-pattern.md`
   - `organization-renamed-to-organization.md`
   - `web-first-configurability.md`
   Full memory index at `memory/MEMORY.md`.

Operator direction 2026-06-05 stands: APT (.deb) is the
priority release channel; Phase 5.10 (DNF / .rpm packaging) is
deferred to the dedicated release phase that will land alongside
the official Wolf v1 cut. Next session opens with one of:
- Triage the 15 Dependabot PRs sitting in the queue
- Review + approve/modify ADR 0017
- Cut v0.1.0 (exercises the release workflow we shipped in
  Batch 3 for the first time)
- Open Phase 6 (Approval Gateway + wolf-gateway service) — the next
build phase whenever the operator opens it.

**Phase 5.9 — APT packaging — CLOSED 2026-06-05.** Five slices
shipped on 2026-06-04 / 2026-06-05 that turn the deployment
substrate into a real `.deb` distribution channel:

* **5.9-a** (`85f0807`) — `debian/` scaffold. Four binary
  packages declared in `debian/control` (wolf-database,
  wolf-server, wolf-dashboard, wolf meta). `debian/rules` with
  the dh sequencer. Build-Depends. changelog. copyright in
  machine-readable Apache-2.0 format.
* **5.9-b** (`76e4e53`) — wolf-database.deb. Bundles the
  wolf_database wheel; postinst creates the user/group/FHS
  dirs + builds the venv from the bundled wheel. Service
  unit installed via dh_installsystemd.
* **5.9-c** (`258def4`) — wolf-server.deb. Bundles wolf-server
  + wolf-cert + wolf-common + wolf-secrets + wolf-schema wheels
  PLUS every transitive production dep (fastapi, sqlalchemy,
  asyncpg, ...) as a self-contained `/usr/lib/wolf-server/
  wheels/` bundle. Postinst pip-installs from the bundle
  with `--no-index` — air-gapped installs work identically to
  connected ones.
* **5.9-d** (`9a74c26`) — wolf-dashboard.deb. Added
  `output: "standalone"` to next.config.ts so `npm run build`
  produces the self-contained Next.js server. Shim updated for
  the conventional flat production layout. Postinst is the
  simplest of the three (no Python venv to build).
* **5.9-e** (`<this commit>`) — meta-package + smoke + close-out.
  `debian/wolf.postinst` prints the operator bring-up sequence
  after `apt install wolf` completes. New `make smoke-deb`
  Makefile target runs `dpkg-buildpackage` in a clean
  debian:trixie Docker container; the CI `smoke-deb` job does
  the equivalent natively on ubuntu-latest and uploads the
  resulting `.debs` as a workflow artifact for review.

Phase 5.9 closeout state:

* `dpkg-buildpackage -b -us -uc` produces four `.debs`:
  `wolf-database_0.1.0_amd64.deb`, `wolf-server_0.1.0_amd64.deb`,
  `wolf-dashboard_0.1.0_amd64.deb`, `wolf_0.1.0_all.deb`.
* All four install cleanly via `apt install ./wolf-*.deb` on a
  fresh Debian/Ubuntu box (per the CI smoke).
* The `wolf` meta-package pulls all three components in one
  operator step: `sudo apt install wolf`.
* Component postinsts create the user/group/FHS dirs + the
  Python venvs (where applicable). Don't auto-start — operator
  runs `wolf-cert init` + `wolf-database init` + provisions
  `/etc/wolf-*/env` files, then `systemctl enable --now …`.
* Four pre-push smokes: `smoke-mtls` (5.6-e), `smoke-database`
  (5.7-d), `smoke-systemd` (5.8-d), `smoke-deb` (5.9-e). CI
  runs all four on every PR.

**Phase 5.8 — systemd units + `/bin` layout + FHS install paths —
CLOSED 2026-06-04.** Four slices shipped over a few hours that
together turn Wolf from "deploys on a dev shell" to "deploys as
daemonised services":

* **5.8-a** (`90a56b6`) — User-level systemd unit templates at
  `deploy/systemd/dev/`. `make install-user-systemd`
  substitutes `@REPO_ROOT@` + `@NODE_BIN@` at install time and
  drops them into `~/.config/systemd/user/`. Per ADR 0016 v3,
  no `After=` / `Requires=` / `Wants=` between Wolf units —
  fully independent. wolf-server got a `_wait_for_database()`
  retry loop in its lifespan hook (backoff cycle, 120s
  timeout) so a fresh boot where wolf-database is still
  coming up doesn't crash startup. +4 retry-loop tests
  (393 → 397).
* **5.8-b** (`da542db`) — System-level units at
  `deploy/systemd/system/` with per-component service users
  (wolf-database, wolf-server, wolf-dashboard, wolf-gateway —
  all in a shared `wolf` group, all `nologin`). Hardening:
  `ProtectSystem=strict`, `ProtectHome=true`, `PrivateTmp=true`,
  `NoNewPrivileges=true`, empty `CapabilityBoundingSet`,
  restricted `AddressFamilies`. `install-users.sh` idempotently
  creates the users, group, and FHS data + config dirs.
  Also caught + fixed a real bug in 5.8-a: hardcoded
  `/usr/bin/npm` in the dev wolf-dashboard unit broke nvm boxes;
  now uses `@NODE_BIN@` substitution.
* **5.8-c** (`bb4f128` + `b4beee9` + `8e01813`) — Shipped CLI
  shims at `deploy/bin/` (`wolf-cert`, `wolf-database`,
  `wolf-server`, `wolf-dashboard`). Each is a thin shell
  wrapper that execs the venv's CLI or fails loud with a
  helpful install hint when the venv is missing (exit 2 +
  named install path). `install.sh` drops the shims into
  `/usr/bin/` + creates the empty `/usr/lib/wolf-*/` dirs the
  .deb post-install will populate. Initial version had a
  `sudo`-strips-env footgun; CLI args (`--bin-dir=`,
  `--lib-dir=`) replaced env vars. Footer message also got the
  dynamic-path-reflection polish.
* **5.8-d** (`<this commit>`) — Phase close-out. ONBOARDING
  §3.4 Path A rewritten as the production-recommended path
  (was previously caveat-gated as "wait for Phase 5.8");
  Path B (system Postgres) demoted to fallback. New
  `make smoke-systemd` Makefile target validates the full
  Phase 5.8 surface in 5 checks: install-user-systemd
  materialises the templates, systemd-analyze --user passes
  on the installed dev units, systemd-analyze passes on the
  system-level unit templates (with expected `/usr/bin/wolf-*
  is not executable` complaints filtered — they land with
  5.9/5.10's .deb), every shim fails-loud with exit 2 when
  its venv is missing, install.sh --help works without sudo.
  New CI job `smoke-systemd`.

Phase 5.8 closeout state:

* Three Wolf components have both user-level (dev) and
  system-level (prod) systemd units that auto-restart on
  reboot. Per ADR 0016 v3 they're fully independent.
* wolf-server gracefully handles wolf-database not being
  ready at startup via app-level retry — no `After=` coupling.
* `/usr/bin/wolf-*` shims point at `/usr/lib/wolf-*/.venv/`
  where the .deb will install the production venvs. Until
  the .deb ships, each shim fails-loud with a clear install
  hint + dev-workspace fallback.
* `install-users.sh` (5.8-b) + `install.sh` (5.8-c) are the
  two idempotent root scripts that prepare a host for the
  systemd units. They touch disjoint paths so order doesn't
  matter.
* Three pre-push smokes (`smoke-mtls`, `smoke-database`,
  `smoke-systemd`); all three run on every CI PR.
* Backend pytest 393 → 397 (+4 retry-loop tests in 5.8-a).
* mypy / ruff / tsc / eslint all clean (94 Python source files).
* Operator-facing docs (ONBOARDING §3.4) now describes the
  production-recommended path end-to-end.

What's left for the official-release phase:

* **Phase 5.9** — APT packaging. `.deb` post-install hook
  invokes install-users.sh + install.sh + creates the
  /usr/lib/wolf-*/.venv/ via Python venv + pip + npm run
  build for the Next.js standalone. After 5.9, the operator
  command is `apt install wolf` + nothing else.
* **Phase 5.10** — DNF packaging. RPM equivalent. Same
  install-time work, different packaging tooling.

* **Slice 5.8-a — User-level systemd units + DB-retry loop** —
  SHIPPED 2026-06-04. Three user-level unit templates at
  `deploy/systemd/dev/{wolf-database,wolf-server,wolf-dashboard}.service`,
  installed via `make install-user-systemd` which substitutes
  `@REPO_ROOT@` at install time. Per ADR 0016 v3, no
  `After=`/`Requires=`/`Wants=` between Wolf units — fully
  independent. wolf-server's lifespan hook got a
  `_wait_for_database()` retry loop (backoff cycle, 120-second
  timeout) so a fresh boot where wolf-database is still coming
  up doesn't degenerate into a systemd restart flap. 4 new
  tests covering the retry semantics; total backend pytest
  393 → 397.
* **Slice 5.8-b — System-level units + service users + FHS paths** —
  next. `/lib/systemd/system/wolf-*.service` with `User=` +
  `Group=`. Per-component system users (`wolf-database`,
  `wolf-server`, `wolf-dashboard`, `wolf-gateway`) in a shared
  `wolf` group, all `nologin`. Data dirs at `/var/lib/wolf-*/`
  (0750), config at `/etc/wolf-*/` (0750), socket at
  `/var/run/wolf-*/` (0775). Hardening: `ProtectSystem=strict`,
  `PrivateTmp=true`, `NoNewPrivileges=true`.
* **Slice 5.8-c — `/usr/bin/wolf-*` shims** — packaged-CLI
  wrappers for `wolf-cert` + `wolf-database`. After install,
  operators run `wolf-database init` directly instead of
  `python -m wolf_database`.
* **Slice 5.8-d — Operator docs + smoke + close-out** —
  ONBOARDING §3.4 Path A rewrite (now genuinely production-
  parity), new `make smoke-systemd` integrity check, Phase
  5.8 close-out marking the whole phase done.

**Phase 5.7 — wolf-database extraction — CLOSED 2026-06-04.**
Four slices shipped in a single day that gave Wolf a bundled
Postgres component:

* **5.7-a** (`25f576f`) — wolf-database substrate (new
  `packages/database/` workspace package). `DatabaseLayout` +
  `resolve_layout()` for dev vs prod paths. `find_postgres_binaries()`
  with the Debian/RHEL distro-known-paths + PATH fallback +
  version gate. `PostgresqlConfOptions` + `PgHbaOptions` for
  rendering Wolf-owned config templates. `connection_url()`
  for wolf-server's DATABASE_URL. 34 new tests.
* **5.7-b** (`ea02f7c`) — wolf-database CLI. Five subcommands
  parallel to wolf-cert: `init` / `start` / `stop` / `status` /
  `reconfigure`. `init --port` to avoid 5432 collision with a
  system Postgres. Generates a random password on init, prints
  the DATABASE_URL operator pastes into wolf-server's .env.
  Live-smoke verified against real Postgres 17 — initdb /
  write_config / pg_ctl start + stop / pgvector check all
  working. 33 new tests; backend pytest 355 → 388.
* **5.7-c** (`1c13f54`) — dev-workflow integration. Five
  Makefile wrappers (`make wolf-database-init` /
  `-up` / `-down` / `-status` / `-reconfigure`).
  `.env.example` rewrite documenting three DB paths
  (wolf-database recommended, system Postgres still supported,
  Docker as ADR-0008 supplementary). ONBOARDING §3.4 rewritten
  as a three-path comparison.
* **5.7-d** (`<this commit>`) — `make smoke-database` Makefile
  target + CI job. End-to-end CLI lifecycle smoke (status →
  init → start → status → stop → status). On hosts without
  pgvector, gracefully degrades to "PARTIAL PASS" with a clear
  install hint rather than failing. CI job installs
  postgresql-17 + postgresql-17-pgvector first, then runs the
  full smoke.

Phase 5.7 closeout state:

* New deployable component, `wolf-database`, parallel to
  wolf-server / wolf-dashboard / wolf-gateway. Owns its data
  dir, config, and lifecycle.
* Operator has a one-command bring-up
  (`make wolf-database-init` → `make wolf-database-up`).
* Pre-Phase-5.7 workflow (system Postgres) still works for
  operators with existing infra — nobody's dev setup breaks.
* Backend pytest grew from 321 (Phase 5.6 close) to **388** —
  +67 tests across the new package.
* mypy / ruff / tsc / eslint all clean.
* `make smoke-mtls` (Phase 5.6-e) and `make smoke-database`
  (Phase 5.7-d) are the two recurring integrity checks
  operators run before every push; CI runs the same two on
  every PR.

What's still ahead of the official-release phase:

* **Phase 5.8** — systemd units + `/bin` + FHS install paths.
  Turns the three components into proper daemons.
* **Phases 5.9 / 5.10** — APT + DNF packaging. Deferred to
  the official-release phase per the 2026-06-03 operator
  direction.

* **Slice 5.7-a — wolf-database substrate** — SHIPPED 2026-06-04.
  New workspace package `packages/database/` with the foundation
  for everything later in the phase: `DatabaseLayout` (dev path
  under `<repo>/.local/wolf-database/`, prod path under
  `/var/lib/wolf-database/` + `/etc/wolf-database/` + `/var/run/
  wolf-database/`, env-var overrides on every path);
  `find_postgres_binaries()` for locating system `pg_ctl` /
  `initdb` / `psql` / `postgres` (Debian + RHEL known paths +
  PATH fallback + clear "install postgresql-17" error message);
  `postgres_major_version()` + `verify_postgres_supported()` for
  the 17+ version gate; `PostgresqlConfOptions` +
  `PgHbaOptions` for rendering Wolf-owned config templates
  (pgvector preloaded, loopback-only listen, scram-sha-256 auth);
  `connection_url()` helper for wolf-server's DATABASE_URL.
  34 tests; total backend pytest 321 → 355.
* **Slice 5.7-b — `wolf-database` CLI** — SHIPPED 2026-06-04.
  Five subcommands parallel to `wolf-cert`: `init` (one-shot
  initdb + config + role + db + pgvector), `start`, `stop`,
  `status`, `reconfigure`. `init --port` to avoid collision
  with a system Postgres on 5432. Generates a random password
  on init and prints the DATABASE_URL the operator pastes
  into wolf-server's .env. Live smoke against the dev host
  verified initdb / write_config / pg_ctl start + stop on a
  non-default port; pgvector-missing path correctly surfaces
  `apt install postgresql-17-pgvector` hint (real-world
  environmental check, working as designed). 33 new tests
  (process.py + cli.py); total backend pytest 355 → 388.
* **Slice 5.7-c — Dev-workflow integration** — SHIPPED
  2026-06-04. Five Makefile targets (`make wolf-database-init`
  with optional `PORT=` override, `-up`, `-down`, `-status`,
  `-reconfigure`). `.env.example` rewritten to document two
  supported paths (wolf-database recommended; system Postgres
  still works for operators with existing infra). ONBOARDING
  §3.4 rewritten as a three-path table (wolf-database / system
  Postgres / Docker) with the wolf-database path stepped out:
  install postgresql-17 + postgresql-17-pgvector via apt/dnf,
  disable the system postgresql.service so it doesn't fight
  port 5432, then `make wolf-database-init` →
  `make wolf-database-up`. docs/restart.md's
  "what restart doesn't touch" table row for Postgres now
  branches by which path the operator picked.
* **Slice 5.7-d — Operator docs polish + verification gate** —
  next. End-to-end smoke (wolf-cert init → wolf-database init →
  wolf-server starts against wolf-database → dashboard login
  works) codified as a Makefile target + CI job, closing
  Phase 5.7.

**Phase 5.6 — Edge-component architecture + mTLS — CLOSED 2026-06-04.**
Five slices shipped between 2026-06-03 and 2026-06-04 that
together kill the cross-origin NetworkError and put a mTLS
trust substrate under every component-to-component call:

* **5.6-a** (`ef6c6f5` + `41ba52b`) — Next.js catch-all reverse
  proxy at `services/dashboard/app/api/[...path]/route.ts`.
  Browser only sees one Wolf origin. SSE streaming preserved
  per-chunk; multi-Set-Cookie preserved. HTTPS-mode follow-up
  fix wired the proxy's outbound fetch through undici with a
  Wolf-CA-trusting Dispatcher.
* **5.6-b** (`9923c65`) — `wolf-cert init` now mints a third
  leaf, `dashboard-client` (`LeafKind.CLIENT`, CN =
  `wolf-dashboard-client`). 9 new tests; backend pytest 311 → 312.
* **5.6-c** (`495af0b`) — wolf-server's launcher passes
  `ssl_ca_certs=<Wolf CA>` + `ssl_cert_reqs=CERT_OPTIONAL`
  to uvicorn; a monkey-patch surfaces the verified peer cert
  into ASGI scope; `MtlsMiddleware` enforces the CN allowlist
  + bypasses GET /healthz from loopback. Dashboard proxy now
  presents the dashboard-client cert via undici Agent. 9 new
  middleware tests; backend pytest 312 → 321.
* **5.6-d** (`49be2d6`) — Launcher banner polish (`mTLS:
  ENABLED/DISABLED` line on both servers). ONBOARDING §3.12
  rewritten to cover HTTPS+mTLS as one lifecycle; new §3.13
  for distributed deployment with the cert-distribution
  table; new troubleshooting table for the common failure
  modes. `docs/restart.md` got a mTLS smoke section.
* **5.6-e** (`<this commit>`) — `make smoke-mtls` Makefile
  target codifies the three-curl smoke as a one-command
  integrity check, plus a new CI job (`smoke-mtls`) that
  mints certs and runs the smoke against a freshly-started
  wolf-server on every PR.

Phase 5.6 closeout state:
* Browser sees one Wolf origin (`wolf-dashboard:3000`); the
  cross-origin NetworkError from Phase 5.4 is permanently
  gone.
* wolf-server refuses any caller that isn't on the
  `MTLS_ALLOWED_CLIENT_CNS` allowlist; today only
  `wolf-dashboard-client` is on it.
* /healthz bypass on loopback keeps ops tooling working.
* Distributed deployment works the same way as all-in-one —
  one env-var edit (`WOLF_SERVER_URL`), copy the right cert
  files to the right hosts, done.
* Audit log records every mTLS accept/reject decision via
  structlog (`grep mtls_` in the journal).
* All quality gates green: 87 mypy files, 0 ruff, 0 tsc, 0
  eslint, **321/321 backend tests**, 6/6 organization-isolation.

APT / DNF packaging (Phases 5.9 / 5.10) remain deferred to the
official-release phase per the 2026-06-03 operator direction.

**Phase 5.5 — Component renaming refactor — CLOSED 2026-06-03.**
Pure refactor, zero functional change. The repo now matches ADR
0016's component naming end-to-end:

* `frontend/` → `services/dashboard/` (Next.js — the wolf-dashboard component)
* `services/orchestrator/` → `services/server/` (FastAPI — the wolf-server component)
* `services/orchestrator/app/` → `services/server/wolf_server/` (Python package — fixes Gotcha #1's two-app collision permanently)
* `services/gateway/app/` → `services/gateway/wolf_gateway/` (matches the wolf-gateway naming)
* `wolf-cert init` mints leaves named `server/` + `dashboard/` (was `orchestrator/` + `frontend/`)
* Server-side env vars / config defaults aligned (`TLS_CERT_PATH` defaults to `.local/certs/server/`)
* Dashboard env var renamed: `NEXT_PUBLIC_ORCHESTRATOR_URL` → `NEXT_PUBLIC_SERVER_URL`

Five commits, in order: initial 184-file rename (`a3d18ec`),
operator-tooling audit (`70d2d94`), exhaustive every-file audit
(`ad4868c`), three trailing references caught on re-read
(`0e428bc`), and the **total-rename closeout** sweep A→G
(`08dee03`) closing every remaining stale reference, including
one shipped CLI bug (`wolf-cert --leaf` help advertising leaf
names that no longer existed), the `package-lock.json` name
field, six dead `_ORCH = "services/orchestrator"` `sys.path`
bootstrap blocks (`tools/embedding_benchmark/*`, `tools/
seed_knowledge`, `tools/organization_isolation_test`, `services/server/
tests/test_seed_knowledge_ingesters.py`), 14 broken `services/
server/app/…` markdown links in `ONBOARDING.md`, ~30 in-source
comments narrating current behaviour with old names (including
the LLM-visible system prompt's "the orchestrator stamps organization
scope" rule), and shipped-package docstrings in `wolf_cert`,
`wolf_secrets`, `wolf_gateway`. Final gate: mypy 0 / ruff clean
/ tsc 0 / eslint clean / 311 backend tests / 6/6 organization-isolation.

The planning bundle (`docs/00`–`docs/16`) deliberately retains
its pre-rename language as descriptive specs — see §6 below.

**Phase 5.4 — Native HTTPS + `wolf-cert` CLI — CLOSED 2026-06-03.**
Five sub-slices shipped between 2026-06-02 and 2026-06-03:
* 5.4-a (`9a44b65`) — `wolf_cert` library (CA generation, leaf
  signing, PEM I/O with strict permissions, status parsing) + 24
  tests. Workspace package shipped with `py.typed` for downstream
  mypy. `LeafKind.CLIENT` hook in place for the future relay
  phase.
* 5.4-b (`80e0f10`) — `wolf-cert` CLI dispatcher (`init` / `status`
  / `export-ca` / `add-host` / `renew` / `revoke`) + 21 tests.
  Console-script entry point + `python -m wolf_cert` module form.
* 5.4-c (`5afd4e9`) — Orchestrator HTTPS auto-detect launcher
  (`python -m app`) with pure-function `resolve_tls()` + 6 tests.
  Cert files themselves are the signal — no env flag.
* 5.4-d (`c7fed44`) — Frontend HTTPS auto-detect via
  `scripts/dev.mjs`. Same posture as orchestrator; mirrors the
  cert-files-are-the-signal contract.
* 5.4-e (`b064b82`) — `ONBOARDING.md` per-OS trust-install
  walkthrough; chain verified via `openssl verify`.

End-to-end verified: `wolf-cert init` flips both servers to HTTPS
(login HTTP 200 with TLS verify_result = 0 against the freshly-
minted Wolf CA); `wolf-cert revoke --yes` drops back to HTTP
automatically.

**Phase 5 prep (the 5.0a → 5.0c series) — CLOSED 2026-06-02.** The
chat UI now matches the Claude/ChatGPT class of interactions:
progressive token-by-token rendering, narrated activity feed,
concurrent per-conversation streams with a Stop button, full
conversation-tree branching (Edit / Retry with `< N/M >` navigator),
chats history pane with full-text search across every branch.

The 5.0c series itself shipped as: c-a (four-chip grounding +
verdict rename), c-b (layout overhaul + resizable Evidence panel),
c-c (Platinum / Dusk Blue / Steel Blue / Icy Blue palette), c-d
(progressive answer rendering — Ollama `stream:true` +
`model.delta` SSE), c-e (live activity feed), c-f + c-g (polish
backlog + retry-nudge + English-only), c-h (async stream
lifecycle + immediate sidebar slot), c-i + i.2 → i.5 (conversation
rename + polish wave + native delete dialog + Markdown polish),
c-j (chats history pane with full-text search), c-k (Stop button +
concurrent per-conversation streams), c-l (conversation tree
branching). Two cross-cutting commits landed in the same window:
typing-foundation fix (`bf00c01` — Phase-0 PEP-561 blind spot
closed, mypy 56 → 0) and IP-agnostic local access (`a3fdd73` —
stops the LAN-IP-rotation paper-cut). One feature tried and
removed in the same window: in-conversation Find (six iteration
passes, then reverted at user's request — too fragile a DOM-
injection interaction with the surrounding scroll machinery; full
narrative in CHANGELOG 2026-05-31).

**Standing rules active across the project** (cross-session memory):
- *Integrity across the stack* (2026-05-30) — every change preserves
  integrity across frontend / backend / DB / libraries / UI; full
  backend suite + cross-organization gate on every `services/` change.
- *Quality + secure coding discipline* (2026-05-31) — features-first;
  quality + secure coding applied inline as each slice is built;
  dedicated hardening + audit pass deferred to a later phase but
  tracked, never abandoned.
- *No unaddressed errors* (2026-06-01) — never leave errors /
  warnings / silent diagnostics unaddressed; "pre-existing baseline"
  is not a pass; fix or track-with-plan, never just report-and-move-on.

**Phase 4 — multi-organization hardening — CLOSED 2026-05-27.** Four slices
shipped: two-organization live DB + RAG isolation tests (4.1, `338413f`),
`bootstrap_organization` validates + `--update` flag (4.2, `1da9e1c`),
`OrganizationScopedCache` + agent_name caching + audit-write isolation
(4.3, `3ff751c`), and the runnable `tools/organization_isolation_test` live
smoke + ONBOARDING gotchas + close-out (4.4). Live isolation suite:
6/6 checks pass against the dev two-organization state.

**Phase status:** **Phase 3 shipped end-to-end** (Slices 1, 1.5, 2A, 2B,
and 3). Phase 2 closed (ADR 0005). Phase 3 vertical:
RAG-over-real-corpus integrated into the agent loop with hybrid
retrieval + grounding validator surfacing inline `[unverified]`
markers on unsupported claims. Slice 3 added the production-grade
ingesters under `tools/seed_knowledge/`: MITRE ATT&CK STIX (697
techniques, matrix v19.1) and the Wazuh ruleset XML (4473 rules from
v4.9.2). The dev DB now carries **5170 shared chunks + 3
organization-private** = 5173 total. `make check` 174 passed (128 prior +
19 knowledge + 16 validator + 11 ingester tests). End-to-end verified
against a brand-new dedicated agent at 192.168.245.129
(`linux-test-agent`, id 001): SSH brute-force triggered 9× rule 5710
+ 1× rule 5712 in Wazuh, Wolf chat investigated with 3 tool calls
(`search_alerts` + `get_rule_definition` + `query_runbook`) fusing
live Wazuh data with retrieved ATT&CK + ruleset documentation; the
grounding validator caught a false-negative claim in one run
(marked `[unverified]`) and degraded gracefully when the judge LLM
returned malformed JSON on a harder run.

**Phase 2 exit criteria progress** (from `docs/10-build-roadmap.md`):
- [x] Wazuh OpenSearch client with forced organization filter (opt-in per organization)
- [x] Wazuh Server API client (read endpoints only)
- [x] Tool registry with strict input/output Pydantic schemas
- [x] First read tools: **9 of 9 verified live** against real Wazuh
- [x] Agent loop with three strategies (frontier / guided / pipeline)
- [x] Resource guardrails (time window, result count, per-organization rate limit)
- [x] Audit logging on every model call and every tool call
- [x] Minimal UI: login, organization picker, ask question, see cited answer
- [x] Analyst question end-to-end on **both** a frontier model AND a local
      Ollama model.  Local-Ollama: `qwen3:4b` in `guided` mode, ~76s
      cold, grounded cited answer.  Frontier-API: `nvidia/nemotron-3-
      super-120b-a12b:free` via OpenRouter in `frontier` mode, 17s,
      structured "Answer / Evidence / Citations" reply.  Both verified
      against the operator's real Wazuh on the same day (ADR 0005).

---

## 2. What's currently built and working

Status legend: ✅ working, 🟡 partial, ❌ broken/disabled, ⏳ planned only.

### Orchestrator (`services/orchestrator/`)
- ✅ FastAPI app, lifespan-driven Alembic migrations on startup
- ✅ Auth: bcrypt local accounts, JWT HS256 cookies, OIDC adapter stub
- ✅ Immutable `OrganizationContext`, AuthMiddleware, append-only audit log
- ✅ Model abstraction layer (`app/models/`): Anthropic, OpenAI, Ollama adapters (httpx-based, no SDK deps)
- ✅ `CapabilityDescriptor` + `KNOWN_MODELS` registry
- ✅ Tool registry + dispatcher (`app/tools/`): tier enforcement,
      Pydantic input/output validation, audit on every branch
- ✅ 9 Wazuh read tools + 1 Phase-3 RAG tool registered
      (`app/tools/registration.py`):
      `search_alerts`, `aggregate_alerts`, `count_alerts_by_severity`,
      `get_event_timeline`, `get_agent_alert_history`, `list_agents`,
      `get_agent_detail`, `get_rule_definition`, `get_cluster_health`,
      **`query_runbook`** (Phase 3 Slice 1, added 2026-05-24).
- ✅ Phase 3 knowledge layer (`app/knowledge/`): `EmbeddingProvider`
      protocol + two adapters — `OllamaEmbeddingAdapter`
      (nomic-embed-text, 768-dim, default) and
      `SentenceTransformersEmbeddingAdapter` (BGE-base-en-v1.5,
      opt-in via the `embeddings-local` extra; recorded in ADR 0012);
      `make_embedding_provider` factory selects via env
      (`EMBEDDING_PROVIDER=ollama|sentence-transformers`).
      `KnowledgeStore` protocol + `PgvectorKnowledgeStore` (organization-
      scoped retrieval enforced at the SQL clause); `KnowledgeChunk`
      SQLAlchemy model with `chunk_metadata` JSONB + `embedding`
      `Vector(768)` + `embedding_model` stamp for re-embedding triggers.
      HNSW cosine-distance index per doc 06.
- ✅ Embedding-stack benchmark CLI (`tools/embedding_benchmark/`):
      side-by-side cold-start / per-query latency / corpus-throughput /
      qualitative top-5 retrieval comparison between both adapters
      against the seeded dev corpus.  Re-runnable for future
      empirical evaluations.
- ✅ Agent loop with three strategies (`app/agent/`): frontier / guided /
      pipeline; `LoopEvent` emission for SSE; multi-turn `history` support
- ✅ Endpoints: `POST /api/v1/auth/{login,logout}`, `GET /me`,
      `GET /me/organizations`, `POST /api/v1/chat`, `POST /api/v1/chat/stream`
- ✅ Per-organization Wazuh resolver + secrets backend (encrypted-file)
- ✅ Bootstrap CLI (`app.management.bootstrap_organization`) and smoke-test CLI
      (`app.management.smoke_wazuh`)

### Gateway (`services/gateway/`)
- ⏳ Not started. Stub package only. Per the architecture, execute tools
      live here exclusively (Phase 6+ work — propose tools + approval gateway).

### Frontend (`frontend/`)
- ✅ Next.js 16 (Turbopack) + React 19 + Tailwind 4
- ✅ shadcn/ui primitives, Lucide icons
- ✅ Auth flow: login page, cookie-credentialed fetch, protected routes
- ✅ Organization switcher (consumes `/me/organizations`)
- ✅ Multi-turn conversations: sidebar shows conversations, message thread
      replays the active conversation, `history` sent with every submit
- ✅ SSE streaming: consumes `/api/v1/chat/stream`, renders LoopEvents
      (tool calls, citations) live
- ✅ Markdown rendering for assistant answers (react-markdown + remark-gfm)
- ✅ Citations panel
- ✅ `randomId()` fallback for HTTP / non-localhost contexts

### Shared packages (`packages/`)
- ✅ `common/wolf_common/`: structlog JSON logging, OpenTelemetry tracing,
      error taxonomy
- ✅ `secrets/wolf_secrets/`: abstract `SecretsBackend` protocol,
      Fernet-encrypted file backend
- ✅ `schema/wolf_schema/`: canonical types (`ToolSchema`, `ToolCall`,
      `ToolResult`, `ToolTier`, `CapabilityDescriptor`, `ChatRequest`,
      `ChatResponse`, `Message`)

### Tooling (`tools/`)
- ✅ `model_probe/`: built in Phase 1; 12 unit tests passing;
      **probed live against `llama3.2`, `qwen3:4b`, `gemma3:4b` on this
      hardware on 2026-05-22** — see ADRs 0001/0002/0003.  sys.path
      bootstrap added to `__main__.py` to resolve the two-`app/`-packages
      collision that blocked the CLI invocation (commit `e9cc316`).
- ⏳ `organization_isolation_test/`: stub only; the live isolation tests live in
      `services/orchestrator/tests/test_cross_organization_isolation.py`
- ⏳ `seed_knowledge/`: stub only (Phase 3 RAG work)

### Infrastructure
- ✅ Postgres 17 + pgvector on `localhost:5432`
- ✅ Ollama on `localhost:11434` with `llama3.2:latest` (3B, Q4_K_M, ~2 GB)
- ✅ User's real Wazuh on `192.168.76.129` (Indexer :9200, Server API :55000,
      self-signed TLS)
- ✅ CI workflow (lint / typecheck / test / safety-check / local-model-check)
- ❌ Docker Compose stack: not the current dev path; services run as
      foreground / `nohup` processes
- ❌ Keycloak / OpenBao: not yet up — local accounts + encrypted-file
      secrets are the current dev path

---

## 3. Current configuration

**Dev environment:**
- Host: Linux laptop, GPU-equipped (migrated from CPU-only VM 2026-05-24)
- GPU: NVIDIA GeForce RTX 4050 Laptop (6 GB VRAM, driver 595.71.05, CUDA 13.2)
  — Profile B tight-end per `docs/13`. All four pre-pulled models confirmed
  100% GPU offload via `ollama ps`; qwen3:8b at 85% GPU / 15% CPU spillover
  (tight fit; see ADR 0010).
- OS: Ubuntu 24.04 (system Postgres 17 + pgvector via PostgreSQL APT repo)
- Python: 3.13.13 (pinned in `.python-version`, managed via `uv` 0.11.16)
- Node: 24.16.0 LTS, npm 11.13.0
- Ollama: 0.24.0 — pulled models: qwen3:4b, qwen3.5:4b, qwen3:8b, gemma3:4b, llama3.2:3b
- Wazuh: real deployment at `192.168.245.128` (Indexer :9200, Server API :55000,
  self-signed TLS; credentials in operator-supplied `credentials/` drop, gitignored)

**Model defaults** (in `services/orchestrator/app/config.py`):
- `DEFAULT_MODEL_PROVIDER`: `ollama`
- `DEFAULT_MODEL_ID`: **`qwen3:4b`** (switched from `llama3.2` on
  2026-05-22 per ADR 0004; Apache 2.0 license)
- `OLLAMA_BASE_URL`: `http://localhost:11434`
- Adapters active: Anthropic, OpenAI, Ollama
- `llama3.2` remains in `KNOWN_MODELS` for operator opt-in via
  `DEFAULT_MODEL_ID=llama3.2`.

**Wazuh connection** (per `OrganizationWazuhConfig` for organization `acme`):
- Indexer: `https://192.168.76.129:9200` (self-signed; `verify_tls=False`)
- Server API: `https://192.168.76.129:55000`
- Credentials: in encrypted-file secrets backend at `.local/secrets.enc`
- `inject_organization_filter=False` (standalone Wazuh deployment, no per-doc organization_id)

**Service ports (dev, bound `0.0.0.0` for LAN access):**
- wolf-server: `7860` (running)
- wolf-dashboard: `3000` (running, Next.js 16 dev server)
- Ollama: `127.0.0.1:11434`
- Postgres: `127.0.0.1:5432` (system Postgres per ADR 0008)
- wolf-gateway: `8001` (not yet running)

**Wazuh organization 'acme' on this machine** — bootstrapped 2026-05-24:
- Indexer: `https://192.168.245.128:9200`
- Server API: `https://192.168.245.128:55000`
- `verify_tls=False`, `inject_organization_filter=False`
- Verified end-to-end: chat → guided strategy → `count_alerts_by_severity` tool
  → grounded answer ("325 alerts in 24h, 143 medium + 182 low") in 20.8s
  (vs ~76s cold on previous CPU-only VM — the GPU win materialized).

**Dev environment posture (per ADR 0008):** native is Wolf's primary
delivery channel; the dev environment uses system Postgres 17 +
pgvector (apt-installed, systemd-managed) to match the production
install path operators will use. Docker remains a supplementary
alternative for dev Postgres (documented in `ONBOARDING.md` §3.4)
and is the supplementary container-channel deployment for operators
who want to build their own images.

**CORS allow-origins:** `http://localhost:3000,http://127.0.0.1:3000,http://192.168.76.128:3000`

---

## 4. What's next

**Top of queue (2026-06-11):** Phase 6.4 (tenant→organization
rename) **SHIPPED**; **Phase 6.5-a (Bootstrap Superuser +
org-recovery) SHIPPED** same day (operator manual web-test signed
off); **Phase 6.5-b (role enforcement, Phase 6.5 subset) SHIPPED**
same day — capability matrix (`organization/rbac.py`, mirrors
ADR 0018 row-for-row) + `require_capability()` dependency +
"Last Admin" invariant guard + role rename approver→responder +
new engineer role (data migration 0008) + org CRUD API (Superuser)
+ org user-management API (Admin) + Superuser-membership
consent-gate endpoints + org audit-log view (Admin/Responder);
25 new tests, 440 total green; `wolf_server/api` added to the
strict-mypy set. Propose/approve/execute decorators deferred to
Phase 6 per the ADR. **Phase 6.5-g (session-cookie blacklist)
SHIPPED** same day — `SessionBlacklist` protocol; in-memory default
(correct for the single-process install) + Redis backend activated
by `REDIS_URL` (operator-chosen design, Slice 4.3 precedent; redis
client is a regular dep, redis SERVER never a .deb dependency);
middleware blacklist check on every authenticated request; triggers:
logout (session TTL = remaining token life), Superuser
password-reset (watermark revokes ALL target sessions — closes the
6.5-a deferred note), new force-revoke endpoint
`POST /api/v1/users/{id}/sessions/revoke`; `wolf_server/auth` joined
the strict-mypy set; 13 new tests, 453 total green. **Phase 6.5-c-i
(backend header-based org context) SHIPPED 2026-06-12** —
`X-Organization-Id` header carries the per-tab org (cookie = user,
header = org, capability gate = permission; membership validated
every request); transitional JWT-claim fallback until c-ii; login's
three-shape response per the ADR (superuser redirect /
auto-selected / needs_org_selection; zero-memberships 401);
audit-recording select-/switch-organization endpoints; /me honors
the header; 14 new tests, 467 total green. Same day: root-fixed the
GPG-signing CI failure on Dependabot PRs (signing steps now
push-only — GitHub withholds secrets from Dependabot runs by
design); all 15 pending Dependabot PRs unblocked, PR #11 validated
green. **Phase 6.5-c-ii (frontend login + per-tab org state)
SHIPPED 2026-06-12** (`051ee2a`) and the operator signed off all
four manual web-tests (multi-org picker / per-tab switch / Wolf →
Superuser dashboard / single-org straight to /chat). **6.5-c CLOSED
same day: the transitional fallback was removed** — the access
token now carries `sub`+`session_id` only (no org, no role claims);
`X-Organization-Id` is the ONLY org source (absent → 401);
`LoginRequest.organization_id` + the flat response fields are gone;
/me derives role from the User row when header-less; logout audit
org comes from a membership-validated header. Tests refactored off
the legacy login path 1:1 — still 467 green. Next: the 15 Dependabot
PRs, then **6.5-d (Organizations + Superuser-dashboard UI)**.
Build order a → b → g → c-i → c-ii → d → e → f → h. Phase 6.5
total estimate: 12-13 sessions.

**Immediate next steps** (in priority order; items below predate
the multi-organization design arc and remain valid backlog):
-1. ~~Multi-embedding RRF chaining (v1.5 + v2-moe via ADR 0014).~~
    **Shipped 2026-05-27.** Migration 0006 + secondary embedding
    column + 3-way RRF in `search()` + `--aux` mode on `wolf reembed`.
    Live-corpus benchmark: precision@5 35% → 60% on 20-query battery.
    Chained mode is `EMBEDDING_MODEL_AUX`-gated (empty default
    preserves Slice-2A behaviour). 99.5% of corpus (5145/5173)
    successfully embedded with v2-moe; remaining 28 chunks marked
    unembeddable but still retrievable via v1.5 + BM25 legs.
0. ~~Phase 3 follow-ups (judge model, agent_name lookup, reembed CLI,
   frontend integration).~~ **All four shipped 2026-05-27** in
   commit set following 05cb750. End-to-end verified with
   `GROUNDING_JUDGE_MODEL_ID=qwen3:8b` — judge caught a fabricated
   source-IP claim that qwen3:4b emitted confidently.
1. ~~Phase 3 Slice 3 — real seed corpora.~~ **Shipped 2026-05-27.**
   `tools/seed_knowledge` brings in 697 ATT&CK techniques + 4473
   Wazuh rules. End-to-end retest on the new dedicated agent at
   192.168.245.129 confirmed full pipeline: trigger brute force
   → Wazuh alerts → Wolf chat draws on both live alerts AND real
   ATT&CK/ruleset documentation.
2. **Stronger grounding judge** (now urgent with the rich corpus).
   qwen3:4b's judge JSON is unreliable at high evidence-prompt
   volumes — on the Slice 3 rich-corpus run the validator degraded
   gracefully (counts surfaced as None) because the judge returned
   malformed JSON. Options to evaluate: (a) route the validator to
   Nemotron 120B via the existing OpenRouter path (ADR 0005's
   hosted-API mechanism); (b) refine the judge prompt with explicit
   negative examples; (c) add a heuristic-overlap fallback that
   flags claims with low token overlap to citations. Worth an ADR
   now that real-corpus material exists to benchmark against.
3. **`search_alerts` agent-name lookup.** During the Slice 3 retest
   qwen3:4b passed `agent_id="linux-test-agent"` (the name) instead
   of `"001"` (the numeric ID) — Wazuh returned 0 hits. Adding an
   `agent_name` alias that resolves via a `list_agents` lookup
   eliminates this class of small-model confusion.
4. **`wolf reembed` helper** (queued from ADR 0012). Flipping
   `EMBEDDING_PROVIDER` without re-embedding silently degrades
   retrieval; the helper diffs `KnowledgeChunk.embedding_model`
   against the active provider and re-embeds the mismatches.
5. **Frontend integration of grounding verdict.** The chat response
   now carries `grounding_supported / unsupported / unverifiable`
   counts and the answer text contains `[unverified]` markers. The
   Next.js chat UI doesn't render these specially yet.
4. ~~Investigate Wazuh Server API 401 against `192.168.245.128`.~~
   **Resolved 2026-05-26.** Root cause: Wazuh Indexer and Server API
   maintain separate user databases; the operator's initial credential
   drop only provisioned the `wolf` user in the Indexer. Operator
   supplied the Server API admin (`wazuh-wui` / generated password).
   `bootstrap_organization` re-run with per-endpoint credentials. End-to-end
   `/api/v1/chat` now verified with both pure-RAG (model picks
   `query_runbook`, retrieves ACME SOC runbook, cited answer in 60s)
   and mixed-mode (`get_rule_definition` + `query_runbook` in one
   loop, both citations attached). No Wolf code changes were needed.
5. **Pending workstation-class probe ADRs remain blocked on
   workstation GPU hardware (24+ GB VRAM):** GLM 5.1 ~32B (priority
   #1 per doc 15), Gemma 3 12B/27B, Qwen 3 14B/32B. Not blocking
   Phase 3 work.

**Phase 3 design touchpoints** (the order doc 06 implies):
- Vector store interface; pgvector implementation
- Ingestion pipeline (structure-aware chunking, metadata extraction)
- Seed corpora: Wazuh docs (via `tools/seed_knowledge`), ATT&CK
- Hybrid retrieval (vector + BM25)
- The `query_runbook` tool with metadata filters as first-class args
- The grounding validator: rejects ungrounded factual claims
- Per-organization private corpus partition (storage-level isolation per
  doc 05's "RAG store" enforcement layer)

**Blocked / waiting:**
- Frontier-API verification needs an Anthropic or OpenAI key in the
  configured secrets backend (not blocking dev, only the formal exit check).

**Deferred** (deliberately not doing now):
- Phase 3 (RAG + grounding validator) — pending Phase 2 close-out.
  qwen3:4b's grounding-fabrication probe result makes Phase 3 *more*
  important if/when qwen becomes the default, not less.
- Phase 6 (gateway service + propose/execute tools) — structural, separate
  service; not until Phases 4 (multi-organization hardening) and 5 (cases) ship.
- Docker Compose stack as the primary dev path — current `nohup` flow is
  fine; revisit when adding more services.
- Refactor of the two-`app/`-packages collision (services/gateway/app/ and
  services/orchestrator/app/ both named `app`).  The probe sys.path
  bootstrap works around it; a deeper fix (rename one) is larger surgery.

---

## 5. Active decisions and open questions

Things that need a human call before they can proceed. Move resolved items
to `CHANGELOG.md` as ADRs.

- [x] **Switch `DEFAULT_MODEL_ID` from `llama3.2` to `qwen3:4b`** —
      resolved in ADR 0004 (commit `e092e21`) + config flip
      (commit `ca495df`) + KNOWN_MODELS amendment to match measured
      probe capability (commit `14cc727`).  Verified end-to-end via
      curl: guided strategy, one tool call, grounded answer.
- [ ] Whether `count_alerts_by_severity` should remain a standalone tool
      or be folded into `aggregate_alerts` with a `bucket_by_severity`
      mode. Currently both registered; the prompt routes severity
      questions to the new one.

---

## 6. Known issues and tech debt

- **Cross-origin `NetworkError` after `wolf-cert init`** (2026-06-03).
  **RESOLVED 2026-06-03 in Slice 5.6-a** by introducing the
  wolf-dashboard reverse proxy: every `/api/v1/...` request now
  hits Next.js's catch-all route handler at
  `services/dashboard/app/api/[...path]/route.ts` and gets
  forwarded server-side to wolf-server. The browser only ever
  sees the dashboard origin, so there's no second origin's cert
  to trust. Phase 5.6-c will add mTLS between the proxy and
  wolf-server using the shared Wolf CA.
- **Conversations are in-memory only** (frontend `useState`).
  A page refresh wipes them. Full persistence plan captured in
  cross-session memory `conversation-tree-persistence-plan.md`
  for the eventual DB-storage phase: two-table schema
  (`conversations`, `message_nodes`), explicit `position` integer
  for stable sibling order, atomic version-add transaction
  (INSERT new node + UPDATE parent's `selected_child_id` in one
  tx), no path flattening on save, lossless round-trip test,
  organization scoping via `OrganizationScopedQueryBuilder`. Land this when
  the project's general DB-storage phase begins; do not flatten
  to the active path on serialise — that would silently drop
  every off-branch subtree.
- **Planning bundle docs (`docs/00-vision-and-scope.md` →
  `docs/16-distribution-and-packaging.md`) still describe the
  pre-Phase-5.5 component names** (`services/orchestrator`,
  `frontend`, `app/`, etc.) throughout. Operationally inert —
  these are descriptive specs, not runtime configuration — but
  confusing for a new reader. Flagged for a dedicated doc-sweep
  slice after Phase 5.6 → 5.8 ship (likely alongside the
  installation-guide module). Found during the post-Phase-5.5
  exhaustive audit on 2026-06-03; deliberately deferred so the
  rename slice doesn't sprawl into a doc rewrite.
- **Inline security / efficiency gaps from Phase 5 prep.** The
  *quality-and-secure-coding-discipline* standing rule applies
  quality + secure coding inline at every slice but tracks
  deferred items (rate limits at the API boundary, additional
  audit-event categories for branch operations, secret-leakage
  scan of streaming text, etc.) for a dedicated post-feature
  hardening pass. Backlog accumulated through 5.0c — to be
  burned down in a focused slice labelled `5.0d` or similar
  before the open-source handover.
- Llama 3.2 on CPU-only inference is slow (~30-60s for first token cold
  start). Functional but a real UX limit; switching to `qwen3:4b` would
  also benefit here.
- Small-model fabrication: `llama3.2` occasionally embellishes details
  beyond what the tool returned. Phase 3's grounding validator is the
  designed solution.
- `services/orchestrator/app/tools/cluster.py` `manager_healthy` flag
  trusts the API responding == healthy; doesn't probe deeper signals.
  Adequate for Phase 2.

---

## 7. Test coverage status

- **260 backend tests passing** (orchestrator-side, `services/
  orchestrator`). 0 failures, 0 skipped.
- **ruff:** clean across the workspace.
- **mypy strict: 0 errors** across orchestrator (66 source files),
  gateway (2), and all three workspace packages (`wolf_common`,
  `wolf_secrets`, `wolf_schema`). The Phase-0 PEP-561 blind spot
  that had hidden 56 errors since the very first phase commit was
  closed in `bf00c01` (2026-06-01). Workspace packages now ship
  `py.typed` markers; mypy resolves their imports correctly end-
  to-end.
- **Cross-organization unit suite:** 8/8 passing
  (`services/orchestrator/tests/test_cross_organization_isolation.py`,
  runs as part of the main suite).
- **Live organization-isolation probe** (`tools/organization_isolation_test`):
  6/6 checks pass against the dev two-organization state. Run after every
  `services/` change per the *integrity-across-the-stack* standing
  rule.
- **Frontend:** `tsc --noEmit` clean, `eslint` clean. No frontend
  test framework wired yet — deferred to the dedicated hardening
  phase.
- **CI:** configured (`.github/workflows/ci.yml`); `origin/main` is
  current as of 2026-06-02 push.

---

## 8. Documentation status

- Planning bundle (`docs/00-13`): in git as of commit `c05cdce` (today).
- `docs/14-model-recommendations.md`: in git as of commit `b093761` (today).
- `docs/11-claude-code-instructions.md`: updated this session with the
  relaxed session-continuity protocol (reading required only for new env /
  new session / different model; end-of-session update remains mandatory).
  In git as of commit `b093761`.
- ADRs in `docs/decisions/`: 12 ADRs — 0001 (`llama3.2` baseline), 0002
  (`qwen3:4b`), 0003 (`gemma3:4b`), 0004 (default-model switch
  decision), 0005 (Phase 2 frontier-API exit-criterion verification),
  0006 (commitment to native support for four model families — Qwen 3,
  Llama 3, Gemma 3, GLM 5.1 ~32B), 0007 (native non-container
  delivery channel will be `.deb`/`.rpm` + systemd, fronted by a
  one-line install script — GitLab-style hybrid), 0008 (native
  delivery is primary; Docker is baseline-supported, not promoted;
  dev environment uses system Postgres), 0009 (qwen3.5:4b GPU probe —
  regression vs qwen3:4b on tool calling; supported but no default
  flip), 0010 (qwen3:8b GPU probe — same measured capability as
  qwen3:4b, tight VRAM fit with 85% GPU/15% CPU; KNOWN_MODELS
  amended), 0011 (opportunistic probe of IBM Granite 3.3 8B —
  outside the four-family commitment), 0012 (embedding stack —
  keep both Ollama and sentence-transformers adapters; Ollama
  default).  README index in place.
- `docs/15-supported-model-matrix.md`: directive document for the
  four-family commitment (added 2026-05-23 alongside ADR 0006).
- `docs/16-distribution-and-packaging.md`: living spec for the
  native-distribution channel committed to in ADR 0007 (added
  2026-05-23).  Implementation queued for post-Phase 4.
- `ONBOARDING.md` (repo root): single-entry onboarding doc — from
  `git clone` to first chat request — for a new contributor or a new
  Claude Code session on a different machine (added 2026-05-23).
- API docs: FastAPI auto-generates at `http://localhost:7860/docs`.
- README: in git as of commit `c05cdce`.

---

## 9. Hand-off note for next session

Phase 2 is functionally complete and closed at the exit-criteria
level (ADR 0005).  The default-model switch is done (`qwen3:4b`,
Apache 2.0, ADR 0004).  End-to-end re-verified on the user's real
Wazuh (192.168.76.129): qwen3:4b in `guided` mode, one tool call to
`count_alerts_by_severity`, grounded cited answer.  Multi-turn,
markdown, citations, organization switcher all work in the Next.js 16
frontend at `http://192.168.76.128:3000`.

**This session (2026-05-23) added two product-direction artifacts and
one onboarding artifact:**

1. **ADR 0006 + `docs/15-supported-model-matrix.md`** — formal
   commitment to natively supporting four model families locally in
   dev: Qwen 3, Llama 3, Gemma 3, GLM 5.1 ~32B.  Production posture is
   user-choice (operators pick one or multiple, including hosted
   APIs).  Six-item "natively support" checklist defines the quality
   bar; four probe ADRs are now expected when workstation-GPU
   hardware lands.
2. **`ONBOARDING.md` at repo root** — single-entry onboarding doc
   covering: 60-second orientation, mandatory reading order, system
   requirements, first-time setup from a clean clone (12 steps),
   verification (tests / lint / smoke / probe), operational tasks,
   seven real gotchas with fixes, session-continuity protocol, file
   reference table, troubleshooting matrix.  Written specifically to
   make a different-machine resume seamless.

**Single most important thing for the next session to know:** the
project owner is arranging a GPU dev machine.  When you (Claude Code
on the new machine) resume, **read `ONBOARDING.md` first**, then
`docs/PROGRESS.md` (this file), then `docs/CHANGELOG.md` recent
entries, then ADRs 0001–0006.  The next concrete work is either (a)
the four pending probe ADRs once Ollama is set up on the GPU machine
with the larger models pulled, or (b) Phase 3 design and the
grounding validator — both can be done in parallel.

Operator notes (unchanged from 2026-05-22 session):
- OpenRouter API key is stashed in `.local/secrets.enc` under
  `model.openrouter.api_key`.  Operator pasted it once for the ADR
  0005 verification; it should be rotated via openrouter.ai/keys.
  **NB:** `.local/` is gitignored — the encrypted secrets blob and
  Fernet key live only on the current dev VM.  A new dev machine
  starts from a fresh `.env` and an empty secrets backend (see
  `ONBOARDING.md` §3.5 and §3.10).
- To re-run the frontier verification any time, flip three env vars
  (DEFAULT_MODEL_PROVIDER=openai, DEFAULT_MODEL_ID=nvidia/nemotron-3-
  super-120b-a12b:free, OPENAI_BASE_URL=https://openrouter.ai/api),
  restart orchestrator, run the chat.  No key re-share needed.
- Run `uv run python -m app.management.smoke_wazuh --organization-slug acme
  --all-tools` any time you want to re-verify every read tool against
  the live deployment (e.g. after a Wazuh upgrade).

Operational note: services run as `nohup` background processes (not
systemd / compose).  On host reboot you must restart Ollama, the
orchestrator, and the frontend by hand.  Orchestrator needs the env
vars in Section 3; the canonical bundle lives at `/tmp/orchestrator.env`.
