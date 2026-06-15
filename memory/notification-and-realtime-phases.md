---
name: notification-and-realtime-phases
description: Operator-requested future phases (2026-06-15) — in-app Notification infrastructure (Phase 6.7) then SSE/WebSocket real-time push (Phase 6.8); notifications STRICTLY isolated from audit/logs
metadata:
  type: project
---

Operator request (2026-06-15, during 6.5-f web-test) for two dedicated future
phases. Placement was explicitly delegated to me; I slotted them after Phase 6.6
(Wazuh component mapping): **Phase 6.7 — Notification infrastructure**, then
**Phase 6.8 — Real-time push (SSE)**. Sequenced notifications-first because the
content/model is useful even poll-delivered; SSE is a pure transport upgrade.

**HARD CONSTRAINT (operator-stated):** the notification feature must be
*totally isolated* from audit/logging. Audit (system/org/user) stays exactly as
it is — immutable compliance record. Notifications are a *separate* user-facing
infrastructure (own table/model, own delivery, dismissable/readable). Do NOT
couple them or read one from the other as a shortcut.

**Phase 6.7 — Notifications (in-app, poll v1):** per-user notification feed +
bell UI; emitted when a relevant operation touches a user, e.g. org Admin changes
a user's role → that user is notified; Admin resets a user's password → that user
is notified; Superuser-access lifecycle (Requested / Cancelled / Approved /
Rejected / Revoked / time-Expired) → notify the relevant parties (requesting
Superuser + the org's Admins/members as appropriate); org-related changes. v1
delivery = same pattern as the 6.5-f banner (poll + on-action + window-focus),
no SSE yet.

**Phase 6.8 — Real-time push (SSE):** a server-sent-events channel that pushes
notification + Superuser-access-banner state live, replacing the poll. The chat
stream already uses SSE, so the transport is familiar ground. Upgrades
[[notification-and-realtime-phases]] consumers (banner, bell) from poll → live.

Relates to [[wolf-bootstrap-superuser-flow]] (Superuser-access events are a
primary notification source) and the 6.5-f consent gate. Roadmap doc entries +
ADR to be authored when the phases are scheduled.
