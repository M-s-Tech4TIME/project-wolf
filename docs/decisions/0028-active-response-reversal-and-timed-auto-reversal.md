# 0028 — Active-response reversal, provenance recall + timed auto-reversal (slice 6-d)

**Date:** 2026-06-28
**Status:** accepted — implementing across **6-d.1 → 6-d.4**. The *physical* host
reversal is deferred to **wolf-pack (Phase 12)** by operator decision (Option A);
everything else (knowledge, provenance, reverse-intents, recall, timed-reversal
scheduling, lifecycle, audit, `/actions` surface) ships in 6-d.

## Context

Web-testing AR out-of-the-box surfaced a gap: Wolf can **block** an IP but cannot
**unblock** it — and, more generally, has no way to *undo a state-changing action
while keeping the record, reason and evidence of why it was done*. The operator
asked for this as a **generic reversal capability** (AR first), built **before**
the remaining action classes, plus a **timed-block** feature ("block for 1h" →
auto-reverse on expiry, fully contextualised in `/actions`).

`unrestricted ≠ unsafe` (ADR 0025) still governs; reversal inherits the same
machinery — capability-bounded, approved (or pre-consented, see §3), audited.

## 1. Reversal matrix — every AR script's undo (source-grounded)

Studied **every** active-response script in `wazuh/wazuh@v4.14.5`
(`src/active-response/`, `src/active-response/firewalls/`) and the local
`scriptreference/opnsense-fw`. The shared helper `active_responses.c`
(`setup_and_check_message`) reads a top-level `command` of `add` (`ADD_COMMAND`)
or `delete` (`DELETE_COMMAND`); **every enforcement script implements both**, and
its `delete` is the exact inverse of its `add`:

| Command | ADD (block) | DELETE (undo) | Reversible |
|---|---|---|---|
| `firewall-drop` (default-firewall-drop) | `iptables -I INPUT/FORWARD -s ip -j DROP` | `iptables -D …` | ✅ |
| `firewalld-drop` | `firewall-cmd --add-rich-rule` | `--remove-rich-rule` | ✅ |
| `host-deny` | append `ALL:ip` to `/etc/hosts.deny` | remove the line | ✅ |
| `route-null` | `route add ip reject` / `-blackhole` | `route del/delete` | ✅ |
| `netsh` | `advfirewall … add rule … action=block` (in+out) | `… delete rule` | ✅ |
| `win_route-null` | `route -p ADD` | `route DELETE` | ✅ |
| `disable-account` | `passwd -l` (lock) | `passwd -u` (unlock) | ✅ |
| `pf` | `pfctl -t wazuh_fwtable -T add` + `pfctl -k` | `-T delete` | ✅ |
| `ipfw` | `ipfw table add deny` | `table delete` | ✅ |
| `npf` | `npfctl` add | `npfctl` delete | ✅ |
| `opnsense-fw` | `pfctl -t __wazuh_agent_drop -T add` + `pfctl -k` | `-T delete` | ✅ |
| `ip-customblock` | create `/ipblock/<ip>` | remove the file | ✅ (custom) |
| `restart-wazuh` | `wazuh-control restart` | — | ❌ one-shot, no state |

Detection/notify scripts (`yara_*`, `wazuh-slack`, `kaspersky`) hold no
enforcement state → nothing to undo. The catalog (`AR_COMMANDS`) now carries
`reversible` + `reverses_via` per command (6-d.1); `reverses_via` is non-empty
**iff** reversible (test-enforced).

## 2. The decisive constraint — the Server API cannot dispatch a `delete`

Tracing the manager→agent path:

- **API → message** (`framework/wazuh/active_response.py` `run_command` →
  `ARJsonMessage.create_message`): the API's `command` field (e.g. `!firewall-drop`)
  becomes the message's **top-level `command`** with `parameters.alert` +
  `extra_args`. The API rejects a per-call `timeout`/`custom`/`location` (HTTP 400,
  already documented in `docs/reference/wazuh-active-response.md`).
- **agent execd** (`src/os_execd/execd.c` `ExecdRun`): on a fresh invocation it
  **unconditionally rewrites the top-level `command` to `"add"`** (ADD_ENTRY,
  `execd.c:276`) before running the script. The `"delete"` (DELETE_ENTRY,
  `execd.c:413`) is produced **only** for the timeout-list entry, executed later
  by `ExecdTimeoutRun` after the config-side `<timeout>`.

**Consequences:**
1. **No API path to undo.** `PUT /active-response` selects the *script* but always
   runs it as `add`. The real `delete` must run **on the host** → wolf-pack.
2. **Wazuh's native timed reversal is config-side & fixed per command** (the
   ossec.conf `<timeout>`), not per-request — so "block for *any* duration then
   auto-unblock" **cannot** be delegated to Wazuh.

This is a Wazuh platform fact, not a Wolf gap — and it is *why* the operator's
instinct to tie physical reversal to wolf-pack is correct.

## 3. Decisions

**A — Physical unblock execution = wolf-pack (Option A, operator-chosen).**
6-d ships the full reversal *intelligence*: propose → approve → record → recall
provenance → audit → GUI. The reversal's `perform` callable does **not** touch the
host now; it records an honest `{"deferred_to": "wolf-pack", "reverses_via": …}`
result. The block stays in effect (`succeeded`, marked *reversal-authorised*)
until wolf-pack runs the real `delete` and flips it `succeeded → rolled_back`.
**No fake host success** — Wolf never claims an IP is unblocked when it is not
(the `dispatched ≠ host-applied` honesty of ADR 0027, extended to reversal).

**B — Provenance recall.** A block captures reason + evidence + conversation
context (existing `rationale`/`evidence`). An `unblock_ip` / `enable_user`
recalls the originating block's reason + evidence + when/who via
`find_active_block(...)` and surfaces it to the user *before* unblocking
(a follow-up reminder), linking `reverses_proposal_id`. Re-blocking an
already-blocked IP surfaces the existing block as dedup/context. When there is no
record, Wolf refuses cleanly (it cannot verify host state until wolf-pack).
Wolf's "what's blocked" ledger (`list_active_blocks`) is its **own dispatch
record**, honestly labelled — not live host truth.

**C — Timed auto-reversal is Wolf-owned.** A block may carry a `block_duration`
(part of the content hash — the approver approves the *window*). On
execution-success Wolf stamps `auto_unblock_at`. A periodic in-process sweep
(`gateway/scheduler.py`, launched from `main.py` `lifespan`) atomically claims
due blocks (`SELECT … FOR UPDATE SKIP LOCKED`, single-instance-safe + idempotent)
and creates a **system-initiated auto-reversal** carrying the recalled
reason/evidence + an "automatic reversal: timed block expired @ <ts>" context,
queued + shown in `/actions`.

**The auto-reversal is pre-consented by the timed-block's approval** (mirroring
Wazuh's own config-timeout auto-delete): the approver who authorised "block for
1h" authorised the expiry reversal as the second half of that one time-boxed
action. So it fires automatically with **no second human approval**, yet is fully
recorded + audited + surfaced. This satisfies the B1 standing rule ("every write
needs explicit human approval"): the consent is the timed-block approval, not a
silent autonomous write. *(A user-initiated unblock of an indefinite block is a
fresh proposal that does require its own approval, with separation of duties.)*

**D — Generic reversal.** The linkage (`reverses_proposal_id`, the recall helper,
the wolf-pack `perform` seam) lives at the **proposal layer**, so the upcoming
action classes (rule_tuning / agent_action / config_change) inherit undo. 6-d
implements the **AR** reverse-intents (`unblock_ip`, `enable_user`).

## 4. Data model (migration 0016, additive/nullable — no backfill)

`action_proposals` gains:
- `reverses_proposal_id` — on a reversal row: the block it reverses.
- `auto_unblock_at` (indexed) — on a timed-block row: when the auto-reversal is
  due (`executed_at + block_duration`).
- `reversal_proposal_id` — on a block row: the reversal authorised for it (set at
  reversal creation; prevents the sweep double-firing; drives the GUI). Block →
  `rolled_back` only when wolf-pack confirms physical removal.

`block_duration_seconds` lives in `parameters` (hashed substance). The existing
`rolled_back` state + `rollback_plan` field (reserved in ADR 0025) are now used.

## 5. Verification boundary (unchanged honesty)

Wolf confirms what it *dispatched* and what it *authorised/recorded*. It does NOT
claim host-applied block/unblock state. True host-effect verification
(is the IP still blocked? did the reversal apply?) and the physical `delete`
arrive with **wolf-pack (Phase 12)**, which fills the single `perform` seam left
here.

## 6. Source-agnostic reversal (out-of-band blocks) — wolf-pack

Today an undo is **ledger-scoped**: `find_active_block` only matches blocks Wolf
itself dispatched, so unblocking an IP that was blocked **out of band** (by an
operator at the firewall, another tool, a different SOC workflow) is refused with
guidance — Wolf has no record AND, pre-wolf-pack, no way to see the host's real
state. This is the honest behaviour for now (never claim to undo something it
can't see), but it is a **limitation, not the target**.

**Target (wolf-pack, Phase 12):** Wolf should be able to reverse *any* active
response on *any* script regardless of who applied it. The wolf-pack daemon on
each host can read the actual enforcement state (the iptables/pf/netsh/hosts.deny
entry, the account lock), so reversal stops depending on Wolf being the *source*:
the provenance check (“did Wolf block this?”) collapses into a **host-state
query** (“is this IP actually blocked here, and by what?”). The provenance ledger
(reason/evidence recall) stays valuable *when Wolf does have a record*, but a
missing record no longer blocks the reversal — Wolf reads ground truth and
reverses it, recording an honest "block of unknown/out-of-band origin" provenance
when there's nothing to recall. So 6-d's ledger + linkage is the right model for
Wolf-originated actions; wolf-pack generalises it to source-agnostic reversal.

## 7. Out of scope (tracked)

- wolf-pack: the physical host `delete`, still-blocked verification, the
  `succeeded → rolled_back` flip on confirmed removal, and **source-agnostic
  reversal** (§6 — reverse any block via host-state query, not Wolf's ledger).
- A custom inverse-AR-command bridge (real API unblock via a deployed inverse
  script) — belongs with wolf-pack / Phase 6.11 provisioning.
- The remaining action classes — same pattern, now reversal-aware via §3-D.
- Scheduler interval as a Phase 6.10 settings consumer (env default for now).
- **Model intent-selection for undo** — the small chat model must reliably map
  "unblock X" → the `unblock_ip` intent (not fall back to `block_ip`); strengthen
  the tool/prompt guidance, and the deep-reasoning fine-tune is Phase 7.5.
