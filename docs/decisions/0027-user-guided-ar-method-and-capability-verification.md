# 0027 — User-guided AR method selection + the verification boundary (slice 6-c.2)

**Date:** 2026-06-22 (decisions settled 2026-06-23)
**Status:** accepted — all four open questions resolved by the operator; the
manager-config presence check was **dropped** (see §2); real host-effect
verification is deferred to **wolf-pack (Phase 12)**.

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
2. **OS-unknown is a hard refusal.** When `classify_os` returns `None`, an
   OS-specific intent is refused. The operator wants a **failover**: if a human
   guides Wolf ("try `pf` on that agent"), Wolf should propose it — *safely*,
   subject to the approval gate (it can't *guess* on its own).

(An earlier draft proposed a third gap — a manager-config "presence check". The
operator rejected it; see §2 for why and what replaces it.)

`unrestricted ≠ unsafe` (ADR 0025) still governs: more reach, every action still
capability-bounded + human-approved + audited.

## Decision

Two additive components on the propose path (§1 `method` override, §3 OS-unknown
failover) plus a deliberate **non-addition** (§2 — no manager-config check). None
touches execution, the state machine, or the approval gate — they shape *what
becomes a proposal*. Plus the per-BSD-OS selection split (resolved open
question 4, mapping table below).

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

### 2. No manager-config presence check — fail-open + the verification boundary

An earlier draft proposed reading `GET /manager/configuration?section=command`
to refuse commands the manager hasn't configured. **Dropped, by operator
decision** — a `<command>` tag being present (or absent) is **not a reliable
signal**: it can exist for any reason, with no actual OS behind it, or be a
misconfiguration; we can't know *why* it's there, and chasing it is hassle for no
real assurance.

So 6-c.2 keeps the existing checks only:

- **Capability verification stays RBAC-level** — the 6-c.1 pre-flight (does the
  per-org credential hold `active-response:command` on the agent's group). That's
  *authorization*, not "will the script work".
- **Selection is catalog-driven and fail-open** — Wolf proposes the auto-selected
  (or `method`-overridden) command and trusts its catalog. No extra config gate.
- **The real gates remain:** (1) human **approval** (separation of duties), and
  (2) **reality** — the action only takes effect if the supporting mechanism
  actually exists and works on that agent, which Wolf cannot confirm today.

**The verification boundary.** Wolf can observe three things, only the first two
of which exist today:

| Signal | Means | Available now? |
|---|---|---|
| dispatch ack (`interpret_ar_result`) | command **sent** to the agent | ✅ (6-b.1) |
| AR execution event (`active-responses.log` → a Wazuh alert) | script **ran** on the agent | possible, but **insufficient** |
| firewall/host state | command **applied** | ❌ — needs **wolf-pack (Phase 12)** |

The OPNsense case is exactly why even the middle signal is insufficient: the log
showed `pf - add` (it *ran*) yet the IP was never blocked (it didn't *apply*). So
querying the AR event would give false confidence; we do **not** add it. Honest
"dispatched ≠ applied" stays in the result, and **true host-effect verification —
plus direct discovery of which mechanisms actually exist on a given agent/OS — is
a wolf-pack (Phase 12) capability**, where Wolf has an on-host vantage over the
whole Wazuh/security-ops surface, not just the Server API.

### 3. OS-unknown failover via user guidance (approval is the gate)

When `classify_os` returns `None`, instead of a flat refusal Wolf may proceed on
a **human-asserted method** — subject to:

- a `method` is supplied (Wolf still won't *guess* on its own), AND
- the method ∈ catalog and satisfies the intent's target shape, AND
- the proposal is annotated *"OS auto-detection failed; proceeding on the
  requester's assertion that this agent runs <method>'s platform"* so the
  approver sees the reduced certainty.

**Any proposer may trigger it** (operator decision) — human **approval** is the
real gate (one person's assertion can't execute), and whether it *applies* still
depends on the mechanism existing on that agent (per §2). This is the operator's
exact case: OS unclassified, human says "use `pf`", Wolf proposes it with the
caveat surfaced rather than refusing a legitimate action or blindly trusting it.

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
- `wazuh/active_response.py`: retire `OS_BSD` for `OS_FREEBSD` / `OS_OPENBSD` /
  `OS_NETBSD` (+ OPNsense/pfSense appliance detection); add `opnsense-fw`;
  version-aware `block_ip` selection per the table below; macOS default → `pf`.
- The proposal records the resolution provenance in `parameters`
  (`method_source`: `auto` | `override` | `user_asserted`) for the approver +
  audit, all content-hashed.
- **No manager-config read** (dropped — §2). No migration. No
  execution/state-machine/approval change. The action validator keeps its
  platform-fit check as the final backstop.

## Consequences

- Wolf reaches more mechanisms (the stranded commands + `opnsense-fw`) and selects
  the right one per specific BSD OS — without re-opening the wrong-platform hole
  (the platform check is unconditional; a bad `method` is refused, never run).
- The catalog stays the prior; the **approval gate** + **on-agent reality** are
  the real safeguards. Wolf is honest that *dispatched ≠ applied* and that
  confirming actual effect needs wolf-pack (Phase 12).
- No extra propose-time reads (the manager-config check was dropped).

## Out of scope (tracked, not here)

- Agent-host script presence detection (not API-introspectable; would need
  wolf-pack / a remote probe — Phase 12).
- Per-method severity nuance beyond the 6-c.1 base-impact model.
- The other action classes (`rule_tuning` / `agent_action` / `config_change`).

## BSD firewall facts + the resolved per-OS mapping (open question 4 → RESOLVED: split)

**pf introduction timeline** (web-verified, sources below) — these set the exact
`ipfw → pf` cutoffs the operator asked for:

- **`pf`** originated on **OpenBSD 3.0** (Dec 2001), entered **FreeBSD's base
  system in 5.3** (Nov 2004), and came to **macOS in 10.7 "Lion"** (2011).
- **`ipfw`** is FreeBSD's older firewall and was macOS's firewall **through
  10.6**; Apple deprecated it in 10.7 and **removed it in 10.10 "Yosemite"** (2014).
- **`npf`** is NetBSD's native filter (since NetBSD 6.0, 2012).

**Decision (per the operator): split `OS_BSD` into specific BSD classes and
select per-OS, version-aware** — each BSD has a known, specified firewall, so a
coarse class is wrong. Mapping for `block_ip`:

| OS class | `block_ip` command | Rule |
|---|---|---|
| `freebsd` | `pf` (≥ 5.3) · else `ipfw` | pf in base since 5.3; ipfw on ancient FreeBSD |
| `openbsd` | `pf` | pf is native; ipfw never existed there |
| `netbsd` | `npf` | NetBSD's native filter |
| `macos` | `pf` (≥ 10.7) · else `ipfw` | pf since Lion; ipfw on ≤ 10.6 |
| `opnsense`/`pfsense` | `opnsense-fw` | FreeBSD-based appliance; stock pf doesn't apply (§4) |

Notes for the build: `classify_os` distinguishes free/open/net-BSD from the
`os.uname` blob (`FreeBSD`/`OpenBSD`/`NetBSD`); OPNsense/pfSense are detected
ahead of generic FreeBSD. The version-gate (FreeBSD < 5.3 / macOS < 10.7 → `ipfw`)
is *correct but practically rare* — no modern agent predates 2004/2011 — so it is
a thin, well-tested rule with a clear modern default of `pf`; `ipfw` also stays
selectable via the `method` override. `OS_BSD` is retired in favour of the
specific classes (every OS-agnostic command, e.g. `restart-wazuh`, must list them).

Sources: [FreeBSD Foundation — Introduction to PF](https://freebsdfoundation.org/resource/an-introduction-to-packet-filter-pf/);
[Yosemite: IPFW gone, moving to PF (Apple Discussions)](https://discussions.apple.com/thread/6645172).

## Operator decisions (resolved 2026-06-23) — all four

1. **macOS `block_ip` default → `pf`** ✅ (switch from `route-null`).
2. **OS-unknown failover → any proposer may trigger it** ✅. Human approval is the
   real gate (a one-person assertion can't execute); annotate the proposal so the
   approver sees it was a user-asserted platform.
3. **No manager-config presence check** ✅. A `<command>` tag's presence/absence
   is not a reliable signal (can be vestigial, OS-less, or misconfigured — we
   can't know why), so chasing it is hassle for no real assurance. Selection is
   **fail-open** (catalog-trust); the **approval gate** + **on-agent reality** are
   the safeguards. Real host-effect verification — and direct discovery of which
   mechanisms exist on an agent — is a **wolf-pack (Phase 12)** capability (§2).
4. **BSD OS granularity → SPLIT, version-aware** ✅. Retire `OS_BSD` for
   `freebsd`/`openbsd`/`netbsd` (+ `opnsense`/`pfsense` appliance detection) and
   select per the table above (pf/ipfw/npf/opnsense-fw, FreeBSD < 5.3 / macOS <
   10.7 → `ipfw`). Supersedes the earlier "defer granularity" recommendation.
