# 0021 — Notification infrastructure + real-time push (Phases 6.7, 6.8)

**Date:** 2026-06-15
**Status:** proposed
**Decider:** mixed (operator-requested; placement delegated to claude-code)
**Related:** [0018](0018-bootstrap-superuser-rbac-login.md) (consent gate — a primary notification source), `docs/10-build-roadmap.md` Phases 6.7 / 6.8, memory `notification-and-realtime-phases`

## Context

Through Phase 6.5 (RBAC + org/user management) a recurring need surfaced:
operations that affect a specific user — an Admin changing their role, an Admin
resetting their password, the full Superuser-access lifecycle (requested /
cancelled / approved / rejected / revoked / time-expired), and org-level changes
— happen with **no direct signal to the affected party**. Today the only
real-time surface is the 6.5-f Superuser-access banner, which is *state-derived*
and poll-refreshed; there is no general per-user notification channel.

The operator (2026-06-15, during the 6.5-f web-test) asked for a dedicated
notification feature, with a **hard constraint**: it must be *totally isolated*
from audit/logging. Audit (system / org / user) is the immutable compliance
record and stays exactly as it is; notifications are a separate, user-facing,
readable/dismissable concern. They must not be coupled, nor may one be derived
from the other as a shortcut. The operator also asked for SSE/WebSocket
real-time delivery, and delegated the phasing/placement decision.

## Decision

Two dedicated phases, sequenced after Phase 6.6 (Wazuh component mapping):

- **Phase 6.7 — Notification infrastructure.** A standalone per-user
  notification model + feed + bell UI, with its **own table** (never the audit
  table). Emitters fire from the relevant operations (role change, password
  reset, the Superuser-access lifecycle, org changes) to the *affected* party.
  v1 delivery reuses the 6.5-f banner pattern — poll + on-action + window-focus
  — so notifications are useful before any streaming transport exists.

- **Phase 6.8 — Real-time push (SSE).** A server-sent-events channel that pushes
  notification + Superuser-access-banner state live, replacing the poll. The
  chat answer stream already uses SSE, so the transport is familiar ground.

Notifications-first because the content/model carries value even poll-delivered;
SSE is then a pure transport upgrade over an already-working feature.

## Alternatives considered

- **Reuse the audit log as the notification source.** Rejected — violates the
  operator's isolation constraint and conflates an immutable compliance record
  with mutable, per-user read/dismiss state.
- **SSE first, notifications on top.** Rejected — couples a new feature to new
  transport infra; polling delivers value on day one and de-risks 6.7.
- **Fold notifications into 6.5.** Rejected — it is broad enough (model, feed,
  bell, emitters across many operations) to warrant its own phase rather than
  bloating the RBAC slice series.
- **Email/SMTP delivery now.** Out of scope — deferred to the existing future
  SMTP phase; in-app first.

## Consequences

- A clean separation: audit stays the system of record; notifications become the
  user-facing signal. Future operations get a one-line emitter call.
- 6.8 upgrades both the notification bell and the Superuser banner from poll →
  live without changing their semantics.
- Detailed schema + emitter inventory + delivery contract to be specified when
  the phases are scheduled (this ADR records the shape + sequencing only;
  status stays `proposed` until 6.7 starts).
