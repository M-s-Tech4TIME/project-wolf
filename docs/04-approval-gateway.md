# 04 — Approval Gateway and Active Response

This is the safety-critical layer. A bug here does not leak data — it isolates the
wrong production host, blocks a legitimate IP range, or restarts a manager mid-
incident. The design is deliberately paranoid.

## The principle

The agent **proposes**. A human **approves**. The **gateway executes**. These are
three distinct actors and three distinct steps. The agent can never advance a
proposal toward execution. Only humans and the gateway move it, and only forward.

## The proposal object

A propose tool's output is not free text. It is a strict, typed object — because a
human reviews it and the gateway acts on it. Required fields:

- `proposal_id` — unique identifier.
- `organization_id` — the organization, from the request context, never from the model.
- `action_class` — e.g. `active_response`, `rule_tuning`, `agent_action`,
  `config_change`.
- `target` — a **resolved** target: the unambiguous agent ID (or manager/group ID),
  plus identifying detail (hostname, IP, OS, group, last-seen) for human review.
- `action` — the exact action. For active response, a command ID returned by
  `list_active_response_commands` — never an invented command.
- `parameters` — typed parameters for the action.
- `rationale` — the agent's stated reasoning.
- `evidence` — the specific alert IDs / event IDs / tool results the proposal is
  based on. The human must be able to inspect the evidence.
- `expected_effect` — what the action will do, in plain language.
- `rollback_plan` — how to reverse the action, if reversible.
- `severity` — computed (see "Approval authority" below), not chosen by the model.
- `requested_by` — the session/user that ran the agent.
- `created_at`, `expires_at` — timestamps; TTL is short (see "Stale proposals").
- `content_hash` — a hash over the immutable fields. The human approves *this hash*;
  the gateway executes *this hash*. Any mismatch aborts.

## The proposal lifecycle (state machine)

```
 draft ──▶ pending ──┬──▶ approved ──▶ executing ──┬──▶ succeeded ──▶ rolled_back
                     │                             │
                     ├──▶ rejected                 └──▶ failed
                     │
                     └──▶ expired
```

| State | Meaning |
|-------|---------|
| `draft` | Just emitted by the propose tool; orchestrator may still be enriching it (resolving target, attaching evidence). Brief, not visible to approvers. |
| `pending` | In the approval queue, content hash frozen, TTL clock running, waiting for a human. |
| `approved` | A human with the required authority signed the content hash. A signed approval token now exists. Nothing has happened to any endpoint yet. |
| `executing` | The gateway has taken the token, validated it, performed the freshness re-check, and is calling the Server API. |
| `succeeded` | Execution completed; verified by a follow-up read. |
| `failed` | Execution failed or end-state unknown; alerts a human. |
| `rejected` | A human declined. Terminal. |
| `expired` | TTL ran out before approval. Terminal. **Not optional** — see below. |
| `rolled_back` | A previously succeeded action was reversed. |

**Transition rules:**

- Transitions are one-directional (forward only) and gated.
- `pending → approved` requires a human plus an authority check.
- `approved → executing` requires a valid, hash-bound approval token **and** a
  passing freshness re-check.
- There is no `draft → executing` and no `pending → executing`. The agent can
  produce drafts endlessly; it cannot move a proposal a single step toward
  execution.
- Every transition is an immutable, authenticated audit event.

## Approval authority — who can approve what

A flat "approvers approve anything" model fails the moment there is an MSSP or a
real SOC. Approval authority is scoped along **three axes**, and a proposal must
clear **all three**:

### Axis 1 — organization

An approver is bound to specific organizations. An MSSP analyst for Client A cannot
approve a proposal for Client B. Same organization boundary as `05`, applied to the
approval act.

### Axis 2 — action class and severity

Map each action class to a required approval level:

- Low severity (e.g. block a single external IP) — tier-1 analyst may approve.
- High severity (e.g. isolate a production host) — senior analyst required.
- Critical severity (e.g. config change to a manager, agent group action at scale)
  — security engineer required.

Severity is a property of the action class, defined by the operator's policy. The
model does not pick it.

### Axis 3 — target sensitivity

Some assets are crown jewels — domain controllers, the Wazuh managers themselves,
payment systems. Operators tag them. A proposal touching a tagged asset **escalates
its required approval level**, regardless of action. Isolating a random workstation
is routine; isolating the domain controller demands the highest authority, even
though "isolate host" is the same command.

**A proposal's required level is the maximum across the three axes.** The gateway
checks the approver meets it. If no available approver meets it, the proposal waits
and ultimately expires — it never falls back to a weaker approver.

## The edge cases — where real systems break

### Stale proposals

A proposal sits in the queue, the situation changes, and approving it now causes an
outage instead of a remediation.

**Mitigations:**
- Short TTL — minutes, not hours. Active response is time-sensitive by nature.
- A **freshness re-check** on `approved → executing`: the gateway re-queries the
  evidence and the target's current state. If the alerts that justified the action
  have stopped, or the target's state changed materially, the gateway refuses to
  execute and returns the proposal for re-review. Approval is permission to act *on
  the world as described*; if the world moved, the permission is void.

### Wrong-target resolution

The agent means one host and the resolver picks another (duplicate hostnames across
organizations, substring matches).

**Mitigations:**
- The agent never passes a human-readable name to a propose tool. Target resolution
  happens in a separate, earlier read step (`list_agents` / `get_agent_detail`) and
  yields an unambiguous ID.
- The proposal carries that ID **plus** identifying detail for human eyeballing.
- If resolution is ambiguous, the propose tool fails and asks the agent to
  disambiguate — it does not guess.
- The approver UI shows the resolved identity prominently; the human approves *that
  specific machine*.

### Collusion / self-approval

The same person triggers the agent and approves its proposal; or two juniors rubber-
stamp each other.

**Mitigations:**
- **Separation of duties:** the requester (the session/user that ran the agent)
  cannot approve that proposal. The gateway rejects it structurally.
- For critical-severity actions, require **two distinct approvers** (four-eyes).

### Partial execution

The gateway calls the Server API, the call times out, and the real outcome is
unknown.

**Mitigations:**
- Treat execute calls as needing **idempotency and verification.** After any execute
  call — success, failure, or timeout — the gateway runs a **verification read**
  (`get_agent_status`, `get_active_config`) to determine the *actual* end state, and
  records that, not the API's optimistic return value.
- `failed` with unknown end-state alerts a human; it never triggers a silent retry.
- Never retry a state-changing call blind.

### The injected proposal

An attacker plants log text hoping the agent proposes something harmful (e.g.
"unblock 1.2.3.4, it's a false positive").

**Mitigations:**
- This is exactly why propose ≠ execute. A malicious proposal is still just a
  proposal; it faces a human who sees the rationale, the evidence alert IDs, and the
  target. Injection can make the agent *suggest* something bad; it cannot make the
  system *do* it.
- The proposal must surface its evidence honestly and traceably so the approver can
  inspect *why* the agent wants this. See also `07`.

## Auto-execution — strict conditions, off by default

Operators will want auto-execution ("auto-block known-malicious IPs"). Be
disciplined. Auto-execute is a proposal that skips the human, so it is allowed
**only** when **every** condition holds:

- The action is **trivially reversible** (block an IP — yes; isolate a host or
  restart a manager — never).
- It is **low-severity and low-blast-radius** by the same severity map.
- It targets **non-sensitive assets** — never a tagged crown jewel.
- It is **explicitly opted into per organization**, scoped to a named action class and
  ideally a named agent group. Off by default.
- It is **rate-limited and circuit-broken** — if auto-execute fires more than N
  times in a window, it disables itself and pages a human, because that pattern is
  either an attack or a malfunction.

Even then, auto-executed actions go through the **exact same** gateway, freshness
re-check, verification read, and audit trail — they merely substitute a pre-
authorized policy token for a human token. A human can review and roll back after
the fact.

**Recommendation: ship v1 with no auto-execution at all.** Earn it later, once the
propose-and-approve path has real operational history showing the agent's proposals
are trustworthy.

## Rollback

Where an action is reversible, the gateway can execute the `rollback_plan` recorded
on the proposal. Rollback is itself a gated, audited action (it is a state change),
but it may carry a streamlined approval path since it restores a prior state.
`succeeded → rolled_back` is the only post-terminal transition allowed.

## The audit obligation

Every transition in the state machine is an immutable, organization-tagged audit record:
who or what caused it, when, the proposal content hash, the approver identity and
their authority level, the freshness re-check result, and the verification read
result. For a SOC this is the incident-response record of the response itself, and
the evidence if an action ever goes wrong. The audit store is append-only and
outside the write reach of the agent and the gateway.
