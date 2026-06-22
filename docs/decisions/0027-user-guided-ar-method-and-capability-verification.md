# 0027 — User-guided AR method selection + capability verification (slice 6-c.2)

**Date:** 2026-06-22
**Status:** proposed — **design for operator review; no code until approved** (per the 6-c.2 sign-off)

## Context

Slice 6-c made active-response selection **intent-driven**: the model expresses
`block_ip` / `disable_user` / `restart`, and Wolf resolves the agent OS and
**deterministically** picks one platform-correct command (firewall-drop / netsh
/ route-null / pf …). 6-c.1 extended that to **BSD** (FreeBSD/OPNsense →
`pf`), grounded in a live read of the cluster:

- Agent `009 opnsense-firewall` reports `os.platform='bsd'`, uname
  `FreeBSD 14.3-RELEASE`.
- `GET /manager/configuration?section=command` shows the manager has
  `firewall-drop, host-deny, route-null, disable-account, restart-wazuh, netsh,
  win_route-null, pf, ipfw, npf` configured (NOT `opnsense-fw`).

6-c deliberately removed choice from the model (so it can't pick a wrong-platform
command). That left three gaps the operator wants closed:

1. **One default per (intent, OS).** `host-deny`, `win_route-null`, `ipfw`, `npf`
   are catalogued + platform-checked but **unreachable** — there's no way to ask
   for null-route instead of firewall, or `ipfw` instead of `pf`.
2. **No presence check.** Wolf's catalog is a hand-maintained mirror of the
   manager's configured commands. If they diverge (a command catalogued but not
   configured — e.g. `opnsense-fw`), Wolf would propose a command that no-ops at
   dispatch. The operator's point: *"the proposed action will work or not is also
   based on whether the required material/system to execute it is present."*
3. **OS-unknown is a hard refusal.** When `classify_os` returns `None`, an
   OS-specific intent is refused. The operator wants a **failover**: if a human
   guides Wolf ("try `pf` on that agent"), Wolf should propose it — *safely*,
   judging that the named mechanism actually belongs to that platform and exists.

`unrestricted ≠ unsafe` (ADR 0025) still governs: more reach, every action still
capability-bounded + human-approved + audited.

## Decision (proposed)

Three additive components on the propose path. None touches execution, the state
machine, or the approval gate — they shape *what becomes a proposal*.

### 1. Optional `method` input (override the auto-default)

`propose_active_response` gains an optional `method` field: a specific catalog
command (`pf`, `host-deny`, `ipfw`, `win_route-null`, `route-null`, …). Behaviour:

- **Absent** → 6-c auto-selection (the platform default) — unchanged.
- **Present** → Wolf uses the named command **iff** it passes the same gates the
  auto-selected one does: command ∈ catalog, **platform-fits the resolved OS**
  (`method`'s catalog `platforms` includes the agent's OS class), and satisfies
  the intent's target shape. A method that doesn't platform-fit is refused with a
  guided reason (e.g. *"`netsh` is Windows-only; agent is linux — use
  firewall-drop/host-deny/route-null"*).

This unlocks the stranded commands and lets a human pick the mechanism, without
re-opening the "model guesses a wrong-platform command" hole — the platform check
is unconditional.

### 2. Manager-config capability verification (presence at the manager)

At propose time Wolf reads the manager's configured command set
(`GET /manager/configuration?section=command`, the read used for grounding above)
and **refuses a command the manager has not configured**, with a clear reason
(*"`opnsense-fw` is not configured on this manager; configured blockers for BSD
are: pf, ipfw, npf"*). This makes the catalog **reconciled with reality** rather
than a static list, and directly answers "is the material to run this present?".

- **Cached** briefly (the command set changes rarely) to avoid a read per propose.
- **Fail-open on read failure** for the *auto-selected* default (don't block a
  normal propose if the introspection read is briefly unavailable — the catalog
  is still a good prior); **fail-closed** for a `method` override and for the
  OS-unknown failover (§3), where the configured-set is load-bearing for safety.
- **Honest limit:** this verifies the command is configured *on the manager*. It
  does **not** prove the AR *script* is present/executable on the agent host —
  that isn't API-introspectable. The post-execution verification read already
  records *dispatched ≠ host-applied*; the proposal/answer keeps saying so.

### 3. OS-unknown failover via user guidance (trust-but-verify)

When `classify_os` returns `None`, instead of a flat refusal Wolf may proceed on
a **human-asserted method** — but only when safety still holds:

- a `method` is supplied (Wolf still won't *guess*), AND
- the method ∈ catalog AND is **configured on the manager** (§2, fail-closed), AND
- the proposal is annotated *"OS auto-detection failed; proceeding on the
  requester's assertion that this agent runs <method>'s platform"* so the
  approver sees the reduced certainty.

Human approval remains the gate. This is the operator's exact case: OS
unclassified, human says "use `pf`", Wolf verifies `pf` is a real configured
mechanism and proposes it with the caveat surfaced — rather than refusing a
legitimate action or blindly trusting the hint.

### 4. The OPNsense case + dispatched-≠-applied (live finding, 2026-06-22)

A live approve→execute on agent 009 (FreeBSD/OPNsense) with `pf` **dispatched
successfully** (Wazuh log: `active-response/bin/pf - add`) but the IP **did not
appear in OPNsense's blocklist**. Findings that reshape this ADR:

- **Agent-side presence is the real determinant.** The `<command>` is defined on
  the manager, but the executable runs from `/var/ossec/active-response/bin/` on
  the **agent**. So §2's manager-config check is **necessary but NOT sufficient** —
  this case had the command configured AND the script present AND a clean
  dispatch, yet no host effect.
- **Generic `pf` ≠ OPNsense.** OPNsense manages pf via its own config/alias
  system; a raw `pfctl` add from the stock Wazuh `pf` script doesn't land in
  OPNsense's blocklist (and is overwritten on the next pf reload). OPNsense's
  official Wazuh guide therefore ships its own **`opnsense-fw`** AR script.
- **So OPNsense agents should route to `opnsense-fw`, not `pf`.** This needs
  OPNsense detection (the `os.uname` carries `OPNsense`/`pfSense`) — a refinement
  of the coarse `OS_BSD` class (ties into open question 4). Generic FreeBSD keeps
  `pf`.
- **Dispatched ≠ applied, and Wolf cannot close that gap via the Server API.**
  Active response has no synchronous read-back, and Wolf talks to Wazuh, not to
  OPNsense's API. `interpret_ar_result` stays honest ("dispatched to the agent,
  not a host-applied confirmation"). Real end-state verification (reading the
  firewall's actual blocklist) is a **Phase 12 / wolf-pack** capability, not
  6-c.2. To validate the pipeline produces real effects *today*, use a Linux
  agent + `firewall-drop` (an iptables rule is observable on the host).
- **Still open (revisit during 6-c.2 build):** confirm `opnsense-fw` is THE
  correct command for OPNsense (vs any other case), and root-cause why stock `pf`
  no-ops there. The operator has configured the manager for `opnsense-fw`.

6-c.2 scope therefore gains: add `opnsense-fw` to the catalog; OPNsense detection
→ route `block_ip` to `opnsense-fw`; keep the honest dispatch-only verification
with a clear caveat in the approver UI + the answer.

## Contract / surface changes

- `propose_active_response` input: `+ method: str = ""` (optional).
- `wazuh/capabilities.py` (or `wazuh/active_response.py`): a
  `fetch_configured_commands(server_api) -> set[str]` helper (cached) over
  `GET /manager/configuration?section=command`, fail-handling per §2.
- The proposal records the resolution provenance in `parameters`
  (`method_source`: `auto` | `override` | `user_asserted`) for the approver +
  audit, all content-hashed.
- No migration. No execution/state-machine/approval change. The action validator
  keeps its platform-fit check as the final backstop.

## Consequences

- The catalog stops being authoritative on its own — the **manager config**
  becomes the runtime source of truth for "what can Wolf actually run here",
  which is the correct dependency direction and self-heals as the manager's
  command set changes.
- One extra read per propose (cached) for the override/failover paths.
- Slightly more surface for the model (`method`) — mitigated: it's optional, and
  the platform check is unconditional so a bad `method` is refused, never run.

## Out of scope (tracked, not here)

- Agent-host script presence detection (not API-introspectable; would need
  wolf-pack / a remote probe — Phase 12).
- Per-method severity nuance beyond the 6-c.1 base-impact model.
- The other action classes (`rule_tuning` / `agent_action` / `config_change`).

## BSD firewall facts (grounding for §1 + the granularity question)

Verified against the platforms + the live cluster (manager has `pf`/`ipfw`/`npf`
configured; agent 009 is FreeBSD/OPNsense):

- **`pf`** — universal: originated on OpenBSD, ported to FreeBSD/NetBSD and
  macOS/Darwin. The right cross-BSD default; OPNsense's own firewall *is* pf.
- **`ipfw`** — FreeBSD-specific (+ legacy macOS ≤10.9). **Not** on OpenBSD
  (pf-only) or NetBSD (`npf`). OPNsense/pfSense are FreeBSD-based, so they have it.
- **`npf`** — NetBSD's filter.

6-c.1 ships a single coarse `OS_BSD` class, so `ipfw`/`npf` are tagged `{bsd}` and
would be *offered* on any BSD (e.g. `ipfw` on an OpenBSD host, where it doesn't
exist). For the live fleet (FreeBSD only) this is harmless, and the §2
manager-config check + the honest "dispatched ≠ host-applied" caveat both
mitigate. Doing it *correctly* needs FreeBSD/OpenBSD/NetBSD granularity — see
open question 4.

## Open questions for the operator

1. **Default for macOS `block_ip`** — keep `route-null` (current) or switch the
   default to `pf` now that pf is catalogued for macOS? (`method` lets either be
   chosen regardless; this is only about the *default*.)
2. **Who may use the OS-unknown failover** — any proposer, or gate it behind a
   capability/role (it deliberately relaxes auto-detection)?
3. **Fail-open vs fail-closed for the auto-default** when the manager-config read
   is unavailable — proposed fail-open above; confirm that's acceptable.
4. **BSD OS granularity** — split `OS_BSD` into `freebsd`/`openbsd`/`netbsd` so
   `ipfw`/`npf` are only offered where they actually exist (per the facts above),
   or rely on `method` + the §2 manager-config presence check to keep a wrong
   pick out of the queue? Granularity is more correct; method+presence is less
   code and self-reconciling. Recommendation: **method + presence check** in
   6-c.2, defer granularity until there's a non-FreeBSD BSD agent to justify it.
