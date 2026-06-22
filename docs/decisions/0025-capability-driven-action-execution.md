# 0025 — Capability-driven action execution (Phase 6 reframe)

**Date:** 2026-06-18
**Status:** accepted
**Decider:** human (project owner), with claude-code drafting
**Related:** `docs/03-tool-catalog-and-capability-tiers.md` (the read/propose/execute
tiers + the "four structural facts"), `docs/04-approval-gateway.md` (the proposal
state machine + approval authority + safety machinery — **preserved by this ADR**),
[ADR 0017](0017-wolf-central-brain.md) (the action validator, subsystem 3),
[ADR 0018](0018-bootstrap-superuser-rbac-login.md) (the RBAC capability matrix),
[ADR 0020](0020-superuser-owned-wazuh-mapping.md) (per-org Wazuh credentials +
the `/security/users/me/policies` introspection this builds on), memory
`wolf-unrestricted-full-power`. Opens **Phase 6**.

## Context

Phase 6 was originally scoped (doc 03 + doc 04) as a **hard read-only wall**: the
agent can only `read` and `propose`; a separate **wolf-gateway** service is the
only thing that can `execute`; and the Wazuh credential the agent uses is
*physically read-only*. The safety guarantee — *"the AI cannot alter or delete a
log, or change config on its own"* — rests on doc 03's **four structural facts**:

1. execute tools are absent from the model's tool schema;
2. dispatch is an allowlist (read runs, propose emits, anything else rejected);
3. **the credential physically lacks write permissions**;
4. an execute tool demands a signed, hash-bound approval token.

The `wolf-unrestricted-full-power` directive (2026-06-18) reframes this: **Wolf
is not a read-only agent.** It should act within whatever the per-org Wazuh
credential's RBAC authorizes — the boundary is *Wazuh's own RBAC* (the
credential's capabilities + index DLS), not Wolf limiting itself. 6.6-f proved
the credential is the real wall (acme's user physically can't see beyond
`agent.labels.group:acme`). The directive is explicit that **`unrestricted ≠
unsafe`**: every action is capability-bounded, approved, and audited.

## Decision

Adopt **capability-driven action execution**. The directive inverts exactly
**fact #3** (the credential *may* carry write/manage/active-response permissions
per its RBAC, and Wolf uses what it's authorized for); the other three facts and
**all of doc 04's safety machinery survive**.

Three operator decisions (2026-06-18) shape the implementation:

### (A2) Execute locus — wolf-server, in-process gateway module
Credential-bounded writes execute **inside wolf-server** via the per-org
credential. The proposal→approval→execution→audit logic is a new **in-process
module** (`wolf_server/gateway/`), not a separate network service. The Phase-0
`services/gateway/` stub **stays a reserved stub**; a separate out-of-process
executor is revisited only if wolf-pack / multi-host topology needs one. This
matches the directive ("the read-only client gains credential-bounded write
paths") with the least infrastructure.

### (B1) Approval default — every write needs explicit human approval
v1 ships with **no autonomous writes**. Every state-changing action requires an
explicit human approval, modelled as a **configurable policy** (default-on),
**not** a hard cap — honoring doc 04's "ship v1 with no auto-execution" and the
directive's "`unrestricted ≠ unsafe`": the *capability* is fully wired; the
default *policy* stays safe. Severity-tiered authority / four-eyes / auto-exec
loosen this per-policy in later phases (6.10 settings / Phase 13), never by
ripping out the gate.

### (C1) v1 scope — this ADR + a foundational slice
One action class end-to-end (**active-response**), proving
propose→approve→execute→freshness→verify→audit. The other action classes
(`rule_tuning`, `agent_action`, `config_change`) and the approval-queue GUI
repeat the established pattern in follow-on slices.

## How the four structural facts change

| Fact | Original | Under this ADR |
|---|---|---|
| #1 execute tools absent from model schema | yes | **unchanged** — `model_tools()` still returns read+propose only |
| #2 allowlist dispatch | yes | **unchanged** — model calls read+propose; execute is never model-reachable |
| #3 credential physically read-only | yes | **inverted** — the credential may write per its Wazuh RBAC; Wolf pre-flights `/security/users/me/policies` and offers only authorized actions |
| #4 signed, hash-bound approval token | yes | **unchanged in spirit** — approval is mandatory (B1) and bound to the proposal `content_hash` |

So execution becomes: **capability (RBAC permits it) + policy (approval
required) + content-hash binding + audit (every transition)**. The model still
cannot move a proposal toward execution; only a human approver + the in-process
execute path can.

## The bounded write surface

The read-only `WazuhServerApiClient` (rejects non-GET at the method boundary)
is **kept exactly as-is**. A *separate*, deliberate `WazuhServerApiActionClient`
adds ONLY whitelisted write paths (v1: `execute_active_response` →
`PUT /active-response`), each **capability-checked against the pre-flighted
RBAC** before issuing, and invoked **only** by `gateway/execution.py` (never the
model, never the read path). This is the principled "gain write paths via an
ADR" the directive demanded — not an ad-hoc opening of the read guard.

### Capability check mirrors Wazuh's agent resource expansion (slice 6-a.1)

The capability check on an agent-targeted action is **not** a literal
`agent:id:<id>` lookup. The live smoke against the real cluster (2026-06-19)
showed a per-org credential grants active-response on **`agent:group:<org>`**
(e.g. `wolf-acme` → `agent:group:acme`), never on `agent:id:*` — so an id-only
check falsely refused *every* agent the credential was genuinely authorized for.
The gate now mirrors how Wazuh RBAC itself evaluates an agent action: **allowed
on `agent:id:<id>` (or a matching wildcard) OR on `agent:group:<g>` for ANY
group the target agent belongs to**, deny-wins across the whole candidate set
(`CredentialCapabilities.can_on_agent`). The agent's groups are resolved
**fresh** at decision time (`resolve_agent_groups`, fail-closed to `[]`), both in
the propose-tool pre-flight and at execution — a stale proposal can't smuggle in
a membership that has since changed. This keeps single-org ↔ MSSP parity: a
broad single-org credential granted `agent:id:*` still works with no groups at
all.

### Active-response API contract corrected + command catalog (slice 6-b.1)

The first web-test execution failed: `Server API write returned 400 … Invalid
field found {'custom'}`. The write client sent a body shape Wazuh 4.14.x rejects.
Verified empirically against the live cluster (v4.14.3) **and** the AR script
source (v4.14.3 + v4.14.5 — identical except `netsh.c`'s internal rule build):

- `PUT /active-response` accepts **only** `command`, `arguments`, `alert`.
  `custom`, `timeout`, `location` are rejected. The command must be **`!`-prefixed**
  to run a named command now. The target rides in the alert the script reads:
  srcip blockers read `parameters.alert.data.srcip` (validated as numeric
  IPv4/IPv6 by `get_ip_version`), `disable-account` reads `…data.dstuser`. The
  per-call **timeout is not API-expressible** (config-side reversal only). The API
  returns **HTTP 200 even on failure** (`error:1` + `failed_items`).
- New `wolf_server/wazuh/active_response.py`: the command **catalog** (platform /
  target / reversible per command), `build_ar_body` (the correct body — no
  `custom`, `!`-prefix, `alert.data.*`), `classify_os` + `is_valid_ip`, and
  `interpret_ar_result` (dispatch ≠ host-applied; honest verification).
- The validator is now catalog-driven: command ∈ catalog; required target present
  + well-formed (valid IP / non-empty user); **lenient** platform check (refuse
  only a *confirmed* mismatch, never an unknown OS — the 6-a.1 no-false-refusal
  lesson). The propose tool takes structured `srcip`/`username`, resolves the
  agent OS, and freezes them into the content-hashed proposal.
- Full source-grounded analysis: `docs/reference/wazuh-active-response.md`.

### Intent-driven, platform-aware command selection (slice 6-c)

6-b.1 made a wrong-platform command *safe* (the validator refuses firewall-drop
on a Windows agent); 6-c makes a wrong pick *impossible* by moving command
selection off the model entirely. The model now expresses a high-level **intent**
(`block_ip` / `disable_user` / `restart`) + agent + target; Wolf resolves the
agent's OS (`resolve_agent_os` → `classify_os`) and **deterministically** selects
the platform-correct command from the catalog (`resolve_intent_command`):
`block_ip` → firewall-drop (Linux) / netsh (Windows) / route-null (macOS),
`disable_user` → disable-account (Linux/macOS), `restart` → restart-wazuh (any).

- **Selection is server-side and catalog-backed.** The intent→command table
  lives in `active_response.py` next to `AR_COMMANDS`; a test asserts every
  selectable command exists in the catalog and platform-fits its OS, so the
  catalog stays the single source of truth.
- **OS-specific intents require a resolved OS.** `block_ip`/`disable_user` are
  refused with guidance when the OS can't be determined (Wolf never guesses a
  platform) or when the intent has no command for the OS (`disable_user` on
  Windows — no default AR ships). `restart` is OS-agnostic and resolves without
  one. This is *stricter* than 6-b.1's fail-open validator, on purpose: an
  ambiguous selection should never reach the queue.
- **Nothing downstream changes.** The proposal still stores a concrete `action`
  (the resolved command) plus the originating `intent` in its content-hashed
  parameters; the validator's platform check stays as a defense-in-depth
  backstop; execution is unchanged. Model facts #1/#2 hold — execute tools stay
  out of the schema; the model calls read + propose only.
- Selecting a *method* within an intent (host-deny vs firewall-drop; null-route
  vs firewall) is a tracked follow-on, not v1.

## Consequences

- New in-process module `wolf_server/gateway/` (proposal model + state machine +
  proposals/approval/execution/validator); migration **0015** adds
  `action_proposals` (forced `organization_id` filter; in the cross-org
  isolation gate). New `wolf_server/wazuh/capabilities.py` (RBAC introspection).
  New `Capability.ACTION_PROPOSE` + `Capability.ACTION_APPROVE` rows.
- doc 04's machinery (resolved target, content hash, evidence, freshness
  re-check, verification read, separation of duties, audit-every-transition)
  is **implemented**, not discarded — four-eyes / crown-jewel / severity-tiered
  authority are policy hooks defaulting to "approval required for all writes".
- The action validator (ADR 0017 subsystem 3) is a **hard gate** before a
  proposal becomes `pending`.
- Reversible: removing the propose tool + the gateway endpoints returns Wolf to
  read-only; the credential's RBAC is the only thing that grants real write
  power, and that's operator-controlled in Wazuh.
- docs 03/04 are **amended, not superseded**: their safety design stands; only
  the "credential is read-only" premise (fact #3) is reframed to "credential is
  RBAC-bounded".

## Out of scope (follow-on)

Other action classes; the approval-queue GUI (6-b); severity-tiered authority /
four-eyes / crown-jewel tagging; auto-execution (Phase 13); a separate
out-of-process wolf-gateway service; rollback *execution* beyond recording the
`rollback_plan`; deep-think + memory (ADR 0017 / Phase 7.5).
