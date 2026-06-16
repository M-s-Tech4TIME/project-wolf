# 0022 — Outbound email via generic SMTP relay (Phase 6.9)

**Date:** 2026-06-16
**Status:** proposed
**Decider:** mixed (operator-requested; design + placement by claude-code)
**Related:** [0018](0018-bootstrap-superuser-rbac-login.md) (invite-link verification, 6.5-h — the first email consumer), [0021](0021-notification-infrastructure-and-realtime-push.md) (notifications, 6.7 — email becomes a delivery channel), [0019](0019-web-first-configurability.md) (web-first config), [0016](0016-wolf-component-architecture-and-packaging.md) (secrets backend, component model), `docs/10-build-roadmap.md` Phase 6.9

## Context

Wolf has **no outbound email**. 6.5-h shipped invite-link verification as
**copy-link only** by design (no SMTP) — the Admin copies the link and delivers
it out of band. The operator (2026-06-16) asked for Wolf to send system email:
verification/invite links (incl. the future Superuser invite-link flow),
on-demand reports/summaries of analytics/action data, and similar templated
system messages.

The hard requirements from the operator: it must work with a **completely free**
SMTP service, and mail **must not be blocked or land in spam**. The operator has
**no existing SMTP smarthost/MTA** and no fixed preference for a transactional
ESP.

The governing reality: inbox placement is ~90% **domain authentication +
sender reputation**, ~10% which service is used. A free relay with correct DNS
auth lands in the inbox; the thing that *does* get blocked is self-hosting an MTA
(cloud IPs are widely blocklisted, port 25 is often blocked outbound). So "free"
and "not spam-foldered" are not in tension — given a reputable relay IP pool plus
an authenticated sending domain.

## Decision

A dedicated **Phase 6.9 — Outbound email (SMTP)**. Shape:

1. **Wolf is an SMTP *client*, never an MTA.** It relays through an
   operator-configured, **provider-agnostic generic SMTP** endpoint
   (host / port / encryption / username / password / from-address / from-name /
   reply-to). Recommended relays are **free-tier transactional ESPs** — Brevo
   (~300/day forever), SMTP2GO (~1k/mo), Resend / MailerSend (~3k/mo) — with
   Amazon SES or any paid SMTP as a drop-in later. Switching providers is config
   only, never code.

2. **Deliverability is a documented contract, not magic.** The operator
   authenticates a sending domain (or subdomain) with **SPF + DKIM + DMARC**;
   Wolf ships a **`wolf-mail doctor`** check that queries those records and warns
   on missing/misaligned auth, so misdelivery is caught at setup. A sending
   domain (~$10/yr) is the one recommended non-free cost — the single biggest
   anti-spam lever. Message hygiene Wolf controls: multipart text+HTML, real
   subject, valid From/Reply-To, minimal links, no shorteners, `List-Unsubscribe`
   on anything bulk, transactional and bulk kept on separate streams.

3. **Architecture (fits existing Wolf patterns).** A `MailService` Python core +
   a **`wolf-mail` shell wrapper** (the shell-wrapper-required pattern:
   `wolf-mail test`/`send-test`/`doctor`/`status`); **web-first config** (Superuser
   dashboard, DB as source of truth, CLI↔GUI synced, every config change audited),
   with the SMTP **password in the secrets backend** (never DB/plaintext); a
   durable **`email_outbox` table** (queue + retry-with-backoff + send history)
   drained by an **in-process poller** (no message-broker dependency in v1);
   **Jinja multipart templates** (system templates versioned in-repo:
   `verification`, `invite`, `password-reset`, `report-summary`).

4. **Sequencing: Phase 6.9 lands before/with Phase 6.7 (notifications)** so the
   notification feature ships with an email **delivery channel** rather than
   retrofitting one. (Number-vs-execution-order divergence, consistent with the
   roadmap's existing divergence section.)

5. **First consumer = verification/invite email** — low volume (fits any free
   tier), directly extends 6.5-h, and enables the Superuser invite-link flow.
   Email **augments** the copy-link flow; it never replaces it (copy-link remains
   the no-SMTP / air-gapped fallback). Reports = on-demand "email me this" first;
   scheduled digests later. Verification uses links (reuse 6.5-h's token); numeric
   OTP codes are a later add.

6. **Audit vs notification distinction.** Email *sends* are system actions and
   **are** audited (`email.sent` / `email.failed`) — this is distinct from ADR
   0021's notification-isolation rule, which governs the *notification feature*,
   not whether sending email is an auditable action. Email may later serve as a
   delivery channel for 0021 notifications; the notification model still stays
   isolated from audit per 0021.

7. **Bounce/complaint handling:** v1 logs bounces + supports manual suppression
   of repeat-failing addresses; ESP-webhook ingestion (Brevo/SMTP2GO/Resend all
   provide one) is a fast-follow.

8. **Security:** TLS required (STARTTLS or implicit); refuse plaintext auth over
   cleartext; **header/CRLF-injection defense** — recipients are server-resolved
   from a known user/recipient context, never free-typed into headers; **no open
   relay**; per-org + global rate limits so a bug or abuse can't torch the relay's
   reputation.

## Alternatives considered

- **Self-host an MTA (Postfix on the Wolf host).** Rejected — cloud/residential
  IPs are widely blocklisted, port 25 is frequently blocked outbound, and IP
  warm-up + feedback loops are a full-time job. This is exactly the "gets blocked"
  failure the operator wants to avoid.
- **Lock to one ESP's HTTP API.** Rejected — generic SMTP keeps Wolf
  provider-neutral and lets the operator swap free tiers (or move to paid/SES)
  without code change.
- **Gmail SMTP for production.** Rejected — ~500/day, sends *as* the Gmail
  account, can't DKIM-sign a custom domain, and risks the account. Acceptable for
  dev smoke-testing only.
- **Skip the sending domain (send from the provider's shared subdomain).**
  Discouraged — noticeably worse deliverability + no branding; the ~$10/yr domain
  is the recommended path.
- **A real message queue (Redis/Celery) in v1.** Deferred — an in-process outbox
  poller suffices for system-email volume; revisit only if volume demands.
- **Email-first invites (replace copy-link).** Rejected — copy-link is the
  no-SMTP, air-gapped fallback and must remain; email augments it.

## Consequences

- Wolf gains provider-neutral outbound email; operators point it at any free or
  paid SMTP relay and are never locked in.
- Deliverability becomes a documented operator responsibility (DNS auth), made
  safe by `wolf-mail doctor` at setup time.
- 6.5-h invites, the future Superuser invite flow, 6.7 notifications, and reports
  all gain an email path off one `MailService`.
- The detailed `email_outbox` schema, template inventory, config surface, and the
  deliverability setup doc are specified when Phase 6.9 is scheduled; this ADR
  records the shape + sequencing only. Status stays `proposed` until 6.9 starts.
