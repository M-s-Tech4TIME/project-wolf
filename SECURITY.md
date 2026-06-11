# Security Policy

Wolf is an open-source agentic-AI platform that interacts with
Wazuh security data. The product itself is security-adjacent: it
reads alerts, agent inventory, and audit logs from a live Wazuh
deployment. Vulnerabilities in Wolf can therefore have real
operational impact on the infrastructure of the operators who
run it. We take responsible disclosure seriously.

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Use one of two channels:

### Preferred — GitHub Security Advisories

Use GitHub's built-in private vulnerability reporting:
<https://github.com/M-s-Tech4TIME/project-wolf/security/advisories/new>

This creates a private advisory visible only to the Wolf
maintainers. You can attach evidence, code samples, exploit
proofs-of-concept, and impact assessments. The advisory thread
becomes the canonical record of disclosure → fix → public
release.

### Alternative — Email

If you can't or don't want to use GitHub Security Advisories,
email the Wolf maintainers at
**`abid.syed.golam@gmail.com`** (same address as in
`pyproject.toml`).

A GPG-encrypted email is preferred. The Wolf maintainers' GPG
public key is published at
[`security/wolf-maintainers.gpg`](security/wolf-maintainers.gpg)
in this repo.

**Fingerprint** (verify this before trusting the key):

```
D995 2267 30A6 59B3 B86F  CDE7 3772 3B2D E0AB FD65
```

**Long key ID**: `0x37723B2DE0ABFD65`

**Identity**: `M/s. Tech4TIME (Wolf package signing) <dev@tech4time.bd>`

**Algorithm**: RSA 4096-bit (capabilities `[SC]` on primary,
`[E]` on encryption subkey).

To import + verify the key:

```bash
# Fetch from the repo:
curl -fsSLO https://raw.githubusercontent.com/M-s-Tech4TIME/project-wolf/main/security/wolf-maintainers.gpg

# Verify the fingerprint BEFORE importing:
gpg --show-keys --with-fingerprint wolf-maintainers.gpg
# Expected: D995 2267 30A6 59B3 B86F  CDE7 3772 3B2D E0AB FD65

# If the fingerprint matches, import:
gpg --import wolf-maintainers.gpg
```

This key signs the Wolf `.deb` packages (release-engineering
gap 1 — wired into CI per Batch 3 of `docs/17`). Operators
adding the Wolf APT repository to their `sources.list` should
import this key into `apt`'s trust store first; after that,
`apt install wolf` automatically verifies the signature on
every package before installing.

### What to include

In either channel, include:

- **Affected version(s)** — git commit hash, release tag, or
  "main as of YYYY-MM-DD".
- **Description** — what the vulnerability is.
- **Steps to reproduce** — minimal reproducer.
- **Impact** — what an attacker could do with this. Include the
  attacker's required position (network access, authenticated
  user, organization administrator, host root, etc.).
- **Suggested mitigation** (optional but appreciated) — your
  best guess at the fix shape.
- **Credit preference** — name + handle to credit in the
  advisory, or "anonymous".

## Response timeline

Wolf is currently solo-maintained. Realistic, honest commitments:

| Stage | Target | What happens |
|---|---|---|
| Initial acknowledgment | 72 hours | Maintainer confirms receipt + an initial severity read. |
| Triage + reproduction | 7 days | Confirm the vulnerability + assess severity (CVSS or operator-impact framing). |
| Fix development | 7–30 days (severity-dependent) | A fix lands on a private branch; in-repo tests added; the advisory is updated with the fix plan. |
| Coordinated release | Aligned with the next release cut | The fix ships in the next stable release; the advisory becomes public; CVE assigned (if applicable). |

**Critical vulnerabilities** (remote code execution, cross-organization
data leak, authentication bypass, secret-credential exposure)
fast-track this timeline. We will cut an out-of-band release if
needed.

**Lower-severity issues** (DoS without auth bypass, missing
hardening of a defence-in-depth control, etc.) may be bundled
with the next regular release rather than triggering an
out-of-band cut.

## Coordinated disclosure

We follow responsible-disclosure norms:

- The advisory stays private until a fixed release is available.
- The reporter is credited in the advisory + release notes
  (unless they request anonymity).
- We aim to publish the advisory within 7 days of the fixed
  release shipping, so operators have the information to
  upgrade.
- Embargo on the reporter's side is requested until either
  (a) the fix releases, or (b) 90 days elapse from initial
  report, whichever is sooner.

90-day max embargo is the industry-standard ceiling. If we miss
the window for any reason, the reporter is free to publish.

## What's in scope

Vulnerability reports in any of these components are in scope:

- The `wolf-server` agent loop, API endpoints, and tool dispatch
  (`services/server/wolf_server/`).
- The `wolf-database` Postgres lifecycle CLI
  (`packages/database/`).
- The `wolf-dashboard` Next.js edge (`services/dashboard/`).
- The `wolf-cert` self-signed CA / leaf-cert CLI
  (`packages/cert/`).
- The `wolf-gateway` stub (`services/gateway/`) — though it's
  intentionally minimal until Phase 6's approval-gateway work.
- The Debian packaging substrate (`debian/`, `deploy/`,
  `packaging/`) — including the systemd hardening, the
  per-component service users, and the install scripts.
- Cross-organization isolation — any way to read or modify Organization B's
  data from a Organization A context is in-scope per ADR 0010's
  "no cross-organization access" invariant.

## What's out of scope

- **Upstream Wazuh vulnerabilities** — report those directly to
  the Wazuh project per their disclosure process.
- **Upstream library CVEs** — we monitor Python and Node deps
  via Dependabot (release-engineering gap 10). If a transitive
  dep has a CVE, report it upstream first; we'll integrate the
  fix during normal upgrade cycles.
- **Issues in operator-supplied configurations** — e.g., an
  operator who exposes `wolf-server` directly to the public
  internet without a reverse proxy. Document such anti-patterns
  in `ONBOARDING.md`; reports requesting that we prevent
  configurable footguns are accepted as documentation issues,
  not security issues.
- **Theoretical attacks requiring privileges already sufficient
  to compromise the host** — e.g., "root on the box can read
  `/var/lib/wolf-server/`". True but uninteresting; that's not
  a Wolf vulnerability.
- **Social engineering** against maintainers — that's not a
  product issue.

## Safe harbour

The Wolf maintainers will not pursue legal action against
researchers who:

- Make a good-faith effort to follow this policy.
- Avoid privacy violations, destruction of data, or service
  interruption.
- Stop testing and report immediately if they encounter
  operator data they weren't intended to see.
- Don't exfiltrate data beyond what's strictly necessary to
  prove the vulnerability.

We commit to working with you, not against you.

## Security architecture summary

Wolf's safety story is architectural, not prompt-based. The
high-level guarantees:

- The model sees only `read` and `propose` tools; `execute`
  tools are structurally absent from its schema (enforced by
  CI gate, see `.github/workflows/ci.yml`'s `safety-check`
  job).
- `wolf-server`'s dispatch is an allowlist; unknown tool calls
  are rejected and audited.
- Credentials are scoped per-organization; the data layer would
  refuse a forbidden cross-organization operation even if application
  logic failed (verified by the cross-organization isolation test
  suite).
- The (future) `wolf-gateway` will require a signed,
  hash-bound approval token before executing anything.
- All inter-component traffic uses mTLS (Phase 5.6 substrate).
- Service-level systemd hardening: `ProtectSystem=strict`,
  `NoNewPrivileges=true`, empty `CapabilityBoundingSet`,
  restricted `AddressFamilies`.

See [`docs/07-security-and-threat-model.md`](docs/07-security-and-threat-model.md)
for the full threat model.

## Public advisories

Once the first release ships, public advisories will be
published at:

- GitHub Security Advisories — primary channel
- `docs/security/advisories/` in the repo — mirrored copy with
  full text + remediation guidance

(No advisories exist yet; this section is the empty placeholder
for the first publication.)
