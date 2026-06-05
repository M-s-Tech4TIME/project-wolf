# Wolf Support Policy

This document states the support commitments for each released
version of Wolf. It exists so operators can plan around Wolf as
part of their infrastructure: knowing how long a given release
will receive security fixes lets you align Wolf upgrades with
your existing maintenance windows.

Wolf is community-supported open source. There is no paid
support tier today. The commitments below are best-effort by the
maintainers and the community.

## Versioning

Wolf follows [Semantic Versioning 2.0](https://semver.org/):
`MAJOR.MINOR.PATCH`.

- **MAJOR** version increments mark backwards-incompatible
  changes (schema migrations operators can't roll back, API
  shape changes, removed features). v0.X → v0.Y is a major
  bump while Wolf is pre-1.0.
- **MINOR** version increments add new features in a
  backwards-compatible way.
- **PATCH** version increments are bugfixes only, including
  security patches.

Pre-1.0 (the current state): minor bumps may include
backwards-incompatible changes if explicitly called out in the
release notes. Once Wolf cuts v1.0.0, full semver applies.

## Support window

Each MAJOR.MINOR release line receives bugfix + security support
for **12 months from its initial release date**. After 12 months,
the release line reaches end-of-life (EOL); we recommend
operators upgrade to a supported version.

Example timeline (hypothetical):

| Release | Initial release | EOL date | Status as of 2026-09 (hypothetical) |
|---|---|---|---|
| v0.1.X | 2026-Q3 | 2027-Q3 | Active |
| v0.2.X | 2026-Q4 | 2027-Q4 | Active |
| v0.3.X | 2027-Q1 | 2028-Q1 | Active (latest) |
| v0.0.X | (pre-release) | n/a | Not a stable release |

Multiple release lines can be supported simultaneously. We don't
promise more than the current + previous MAJOR/MINOR will be
maintained — but the 12-month floor applies to each.

## What "supported" means

A supported release line receives:

- **Critical security patches** — back-ported from `main` as
  needed. Out-of-band releases for severe issues.
- **Important bugfixes** — including data-integrity issues,
  cross-tenant isolation regressions, and cases where the
  software silently produces wrong answers.
- **Compatibility fixes** — for breaking changes in Wolf's
  declared dependencies (Postgres, pgvector, Python, Node.js)
  that affect existing installs.

A supported release line does NOT necessarily receive:

- **New features** — those land on `main` and ship in the next
  minor.
- **Cosmetic / quality-of-life improvements** — same.
- **Refactors** — kept on `main`.
- **Performance optimisations** — case-by-case; significant
  optimisations might be backported if low-risk.

## Upgrade path

We commit to making upgrades within a major version
(`v0.X.Y` → `v0.X.Z`) seamless: install the new version, restart
the services, no operator action required beyond watching the
service logs.

Upgrades across majors (`v0.X.0` → `v0.Y.0`) may require operator
action — documented in the release notes for that major. We aim
to make this:

- One-shot — no multi-stage upgrade required for normal cases.
- Documented — every breaking change has a migration guide
  paragraph in the release notes.
- Reversible — where the operator's data permits, downgrade
  remains possible by reinstalling the previous version (no
  irreversible schema changes within a major).

CI exercises the upgrade path automatically (release-engineering
gap 6 — "Upgrade testing matrix"; tracked in `docs/17`).

## What happens after EOL

Once a release line reaches EOL:

- No further patches will ship from the Wolf maintainers,
  including security patches.
- The release tag stays in the repo (no force-pushes; no
  deletion).
- Operators running EOL versions are on their own. The
  community may continue to provide support via discussion,
  but no commitment is made.

We will mark known-exploited vulnerabilities in EOL releases in
the `SECURITY.md` advisories section as "affects EOL release X;
upgrade required" rather than backporting.

## End-of-life schedule (this document is the source of truth)

Wolf hasn't cut v0.1.0 yet. Once it ships, this table will be
updated with concrete dates. Until then, this document is a
forward-looking commitment.

| Release | Initial release | EOL date | Status |
|---|---|---|---|
| (none yet) | — | — | — |

## How to know if you're running a supported version

Run `wolf-server --version` on any installed host. The output
includes the version number. Match it against the table above
to see if you're within the 12-month window.

For `apt`-installed deployments, also check the installed package
versions:

```bash
dpkg -l | grep wolf-
```

The major.minor part of the version is what determines support.
Patches within a supported major.minor are always installed via
`apt upgrade`.

## Reporting issues

- **Security issues** — see [`SECURITY.md`](SECURITY.md) for
  private disclosure channels.
- **Bug reports** — GitHub Issues:
  <https://github.com/M-s-Tech4TIME/project-wolf/issues>
- **Feature requests** — GitHub Discussions (community-driven
  prioritisation).
- **Questions / help** — GitHub Discussions or the maintainers'
  contact email in `pyproject.toml`.

## Long-term roadmap

This support policy is a v1 commitment. As Wolf matures, we
expect to:

- Lengthen the support window for v1+ stable releases
  (potentially 24 months for major releases that target
  enterprise operators).
- Designate specific releases as "LTS" with longer windows.
- Publish a security advisory feed (RSS / mailing list) so
  operators can subscribe rather than poll.

These are aspirational and will be revisited as Wolf approaches
v1.0.
