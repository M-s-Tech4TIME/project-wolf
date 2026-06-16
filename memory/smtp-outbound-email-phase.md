---
name: smtp-outbound-email-phase
description: "Phase 6.9 (future, ADR 0022, operator-requested 2026-06-16): Wolf outbound email — provider-agnostic SMTP CLIENT (never an MTA) relaying through a FREE-tier ESP; deliverability via operator-authenticated domain (SPF/DKIM/DMARC); executes BEFORE 6.7 so notifications ship with an email channel."
metadata: 
  node_type: memory
  type: project
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

PLAN (captured 2026-06-16, operator-requested): Wolf gains outbound system
email — verification/invite links (incl. the future Superuser invite flow),
on-demand reports/summaries, templated system messages. Captured as **ADR 0022**
+ roadmap **Phase 6.9** (status proposed). Build it when scheduled.

**Operator's hard requirements:** must work with a **completely free** SMTP
service, and mail **must not get blocked / land in spam**. Operator has **no
existing MTA/smarthost** and no ESP preference.

**The settled design (don't re-litigate — these are decided):**
- Wolf is an **SMTP client, never an MTA** (self-hosting Postfix is what gets
  blocked: blocklisted cloud IPs, port 25 blocked outbound). Relay through a
  **provider-agnostic generic SMTP** endpoint (host/port/encryption/user/pass/
  from/reply-to) → operator plugs in a **free-tier ESP**: Brevo (~300/day
  forever), SMTP2GO (~1k/mo), Resend/MailerSend (~3k/mo), or SES/paid later.
  Switching providers is config only, never code.
- **Deliverability ≈ 90% domain auth + reputation, not the provider.** Operator
  authenticates a sending domain with **SPF + DKIM + DMARC** (a ~$10/yr domain is
  the one recommended non-free cost — the biggest anti-spam lever). Wolf ships
  **`wolf-mail doctor`** to verify those DNS records at setup.
- **Architecture:** `MailService` core + **`wolf-mail` shell wrapper**
  ([[shell-wrapper-required-pattern]]); **web-first config** ([[web-first-configurability]])
  — Superuser dashboard, DB source of truth, audited — with the SMTP password in
  the **secrets backend**; durable **`email_outbox`** table (queue/retry/history)
  drained by an **in-process poller** (no broker dep in v1); **Jinja multipart
  text+HTML templates** in-repo.
- **First consumer:** verification/invite email (extends 6.5-h). Email
  **augments** the copy-link flow, never replaces it (copy-link is the
  no-SMTP/air-gapped fallback). Reports = on-demand first; scheduled digests later.
- **Sequencing:** Phase 6.9 **executes before 6.7** (notifications) so the bell
  ships with an email channel — despite the higher number (roadmap number≠order
  divergence; noted in the ordering line).
- **Audit vs notification:** email *sends* ARE audited (`email.sent`/`email.failed`)
  — a system action, distinct from [[notification-and-realtime-phases]]'s
  audit-isolation rule (that governs the notification feature, not email).
- Bounce handling: v1 log-and-suppress; ESP-webhook ingestion as a fast-follow.

Related: [[notification-and-realtime-phases]], [[web-first-configurability]],
[[shell-wrapper-required-pattern]], [[wolf-bootstrap-superuser-flow]].
