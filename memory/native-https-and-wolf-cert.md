---
name: native-https-and-wolf-cert
description: "Phase 5.4 (post-5.0c, pre-RBAC) — native HTTPS for Wolf via a wolf-cert CLI that owns self-signed cert lifecycle with effectively-unlimited (100-year) validity"
metadata: 
  node_type: memory
  type: project
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

User asked (2026-05-29) for native HTTPS as a Wolf feature with a dedicated CLI that handles assigning / commissioning / renewing self-signed certificates. The user wants "no expiration" if technically possible. Decision: slot as **Phase 5.4** right after Slice 5.0c-e and before Phase 5 (Organizations + RBAC).

## Reality check on "no expiration"

RFC 5280 forbids unlimited validity — every X.509 cert must have a `notAfter`. The standard workarounds:
- `9999-12-31` — RFC-compliant but a few clients overflow. Risky.
- **100 years** — the established "practical infinity" pattern. Long enough that no living operator will renew; safe across every TLS stack.

**Default 100 years.** Operator can pass `--years N` for any value. Honest answer documented up-front so no one believes Wolf is somehow circumventing RFC 5280.

## `wolf-cert` CLI surface

| Command | What it does |
|---|---|
| `wolf-cert init` | Generate Wolf Root CA + orchestrator + frontend leaf certs. Auto-detect local hostnames + LAN IPs and add them as SANs. Store under `.local/certs/` with 0600 on keys. |
| `wolf-cert add-host <hostname-or-ip>` | Add a new SAN and reissue the leaf cert. CA key stays put. |
| `wolf-cert renew [--years N]` | Reissue leaves (and optionally the root). |
| `wolf-cert status` | Subject, SANs, issuer, expiry, fingerprint. |
| `wolf-cert export-ca [--format pem\|der]` | Emit the root CA for installation in OS/browser trust stores — the one-time per-machine step to get the green padlock. |
| `wolf-cert revoke` | Invalidate and force regen of all leaves. |

## Integration touch-points

- **Orchestrator**: `uvicorn --ssl-keyfile <key> --ssl-certfile <cert>` — uvicorn supports this natively.
- **Frontend**: Next.js 13.5+ supports `next dev --experimental-https --experimental-https-key … --experimental-https-cert …`. The dev script needs updating.
- **`.env`** gains `WOLF_TLS_CERT_PATH`, `WOLF_TLS_KEY_PATH`, `WOLF_TLS_CA_PATH` (optional; absent = HTTP fallback).
- **`ONBOARDING.md`** gains "Trust the Wolf root CA on your machine" with per-OS one-time install steps (Linux, macOS, Windows, browser-specific where needed).
- **Storage layout**:
  ```
  .local/certs/
    ca/
      ca-cert.pem      0644
      ca-key.pem       0600
    orchestrator/
      cert.pem         0644
      key.pem          0600
    frontend/
      cert.pem         0644
      key.pem          0600
  ```
- **Library**: `cryptography` (already in `wolf_secrets`'s dependency tree). No new system deps.

## Why this matters beyond removing the HTTP-clipboard hack

Every "Claude-style" secure-context API unlocks at the same time:
- Clipboard API (the immediate trigger — kills the [execCommand fallback in markdown.tsx](file:///home/alsechemist/Codespace/project-wolf/frontend/components/markdown.tsx))
- Web Crypto (subtle crypto for any future client-side signing)
- Service Workers (offline shell, background sync)
- Geolocation, Push, etc.

It also raises Wolf's credibility — a security tool served over plaintext HTTP is a poor demo.

## Suggested ordering inside Phase 5.4

1. `cryptography`-backed `cert_authority.py` module: generate CA, generate leaf signed by CA.
2. `app/management/wolf_cert.py` CLI dispatcher with subcommands above.
3. Uvicorn launcher (orchestrator entry point) reads the new env vars.
4. Frontend `dev` script updated to pass `--experimental-https` when certs exist.
5. ONBOARDING.md trust-install steps + screenshots.
6. Tests: cert generation, SAN handling, validity boundary (renew when expired), permission bits.
7. (Future Settings UI panel) — superuser-only "Certificates" page showing status + Renew / Add Host / Export CA buttons. Belongs in Phase 5 (RBAC) since it needs the role gate; the CLI is sufficient until then.

## Related memory

- [[per-slice-web-test-checkpoints]] — Phase 5.4 follows the same reset → self-validate → reset → user-test workflow.
- [[grounding-enrichment-tools-future-phase]] — separate Phase, no overlap, but a Settings UI panel will eventually surface both.
