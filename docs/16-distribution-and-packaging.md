# 16 — Distribution and Packaging

This document is the **living contract** for how Wolf is distributed to
operators outside the container-first path. It specifies the package set,
install experience, file layout, supported distro matrix, the `wolf` CLI
surface, and the upgrade story.

The **decision** to take this approach (system packages + install-script
wrapper, rather than omnibus or Snap) is recorded in
[ADR 0007](decisions/0007-native-distribution-via-system-packages-and-install-script.md).
This file maintains the state; ADR 0007 preserves the reasoning.

It complements (does not replace):

- `docs/09-tech-stack-and-repo-layout.md` §"Container, build, CI" —
  the container-first delivery story remains the recommended path
  for operators who run Docker.
- `docs/13-system-requirements.md` §"What the platform deploys" —
  the four hardware profiles (A/B/C/D) each native-package
  deployment must serve.

If you are a future Claude Code session, a contributor on a different
machine, or a human reviewer: **read this file and ADR 0007 before
designing, implementing, or changing the native-distribution path.**
The decisions here are directives from the project owner.

---

## The model in one sentence

Wolf is delivered as **distribution-native system packages**
(`.deb` and `.rpm`) installed via APT/YUM and run as **systemd
services**, with a one-line **install script** that handles
prerequisite-repo setup before installing Wolf packages.

This is the "GitLab-script-plus-system-packages" hybrid pattern (also
used by Tailscale, Caddy, k3s, Docker itself). Operator UX is one
command; the underlying mechanic is plain Linux package management.

## The two-channel delivery posture

Wolf supports two delivery channels in parallel. Both serve the same
codebase from the same git tree.

| Channel | Status | Best for |
|---|---|---|
| **Container-first** (`docker compose` for single-host; Helm/k8s for clusters) | Documented in `docs/09`/`docs/13`; partially built today | Operators who run Docker, MSSPs at scale, demo / single-host eval |
| **Native packages** (this document) | Specified here and in ADR 0007; **implementation not yet started** | SOC/MSSP operators on RHEL or Ubuntu where Docker is unavailable or restricted by policy; operators who prefer standard Linux package management |

Choosing one channel does not lock out the other. An operator can
start with `docker compose` for evaluation and move to native packages
for production, or vice versa, with no Wolf-side migration — only
Postgres data migration, which uses standard `pg_dump` / `pg_restore`.

---

## The operator experience (target)

### First install

```
$ curl -fsSL https://wolf-project.org/install.sh | sudo bash
```

The script:

1. Detects the OS (Ubuntu / Debian / RHEL / Rocky / openSUSE).
2. Refuses to proceed on unsupported distros with a clear message.
3. Adds the required prerequisite repositories:
   - Python 3.13 (deadsnakes PPA on Ubuntu; equivalent on others).
   - Node 24 (NodeSource repo).
   - PostgreSQL 17 + pgvector (PostgreSQL APT/YUM repo).
4. Adds the Wolf APT/YUM repo (signed; key bootstrapped by the
   script).
5. Installs the `wolf` metapackage (which pulls in
   `wolf-orchestrator`, `wolf-gateway`, `wolf-frontend`, and the
   prerequisites).
6. Initializes the Postgres database, runs Alembic migrations,
   generates `SECRET_KEY` and the Fernet secrets key, writes
   `/etc/wolf/wolf.env` with sane defaults.
7. Enables and starts the systemd units.
8. Prints next-steps text: "Run `sudo wolf bootstrap-tenant ...` to
   create your first tenant, then visit http://localhost:3000."

Operator total: **one command, ~3 minutes, no Docker required.**

### Day-2 operations

```
$ sudo systemctl status wolf-orchestrator
$ sudo journalctl -u wolf-orchestrator -f
$ sudo wolf bootstrap-tenant --slug acme ...
$ sudo wolf set-secret --key model.openrouter.api_key
$ sudo wolf smoke-wazuh --tenant-slug acme --all-tools
```

The `wolf` CLI is a thin wrapper around the existing
`python -m app.management.*` invocations, with the right virtualenv
activated and the right paths injected. Operators do not need to know
Python is involved.

### Upgrades

```
$ sudo apt update && sudo apt upgrade wolf
```

The Wolf metapackage's post-install hook:

1. Stops the services.
2. Backs up `/etc/wolf/` to `/etc/wolf.bak-<timestamp>/`.
3. Installs new binaries.
4. Runs `alembic upgrade head` against the local Postgres.
5. Restarts the services.
6. Prints status of all three units.

If migration fails, the script aborts, leaves the old binaries in
place via the system package manager's rollback, and prints
recovery instructions.

### Removal

```
$ sudo apt remove wolf            # leaves data + config intact
$ sudo apt purge wolf             # also removes /etc/wolf and /var/lib/wolf
```

Per Debian / RPM convention.

---

## Package set

| Package | Contents | Depends on |
|---|---|---|
| `wolf-orchestrator` | FastAPI orchestrator binary, agent loop, tools, model adapters, Python virtualenv, systemd unit | `python3.13`, `wolf-common-libs` |
| `wolf-gateway` | FastAPI gateway binary (Phase 4+), Python virtualenv, systemd unit | `python3.13`, `wolf-common-libs` |
| `wolf-frontend` | Next.js production build, Node runtime hooks, systemd unit | `nodejs-24` |
| `wolf-common-libs` | The three workspace packages (`wolf-common`, `wolf-secrets`, `wolf-schema`) shared between orchestrator and gateway | `python3.13` |
| `wolf-cli` | The `wolf` CLI wrapper | `wolf-common-libs` |
| `wolf` | **Metapackage** — pulls in all of the above plus Postgres 17, pgvector, and the prerequisite repos | All of the above + `postgresql-17`, `postgresql-17-pgvector` |

The metapackage is what operators install. The individual packages
are available for advanced operators who want partial installs
(e.g. orchestrator on one host, gateway on another in a future split
deployment).

---

## File layout (FHS-conformant)

| Path | Contents | Notes |
|---|---|---|
| `/usr/lib/wolf/orchestrator/` | Orchestrator code + virtualenv | Read-only after install |
| `/usr/lib/wolf/gateway/` | Gateway code + virtualenv | Read-only after install |
| `/usr/lib/wolf/frontend/` | Frontend `.next/` build output | Read-only after install |
| `/usr/bin/wolf` | The `wolf` CLI | Symlink to the wrapper script |
| `/etc/wolf/wolf.env` | Environment variables (SECRET_KEY, Fernet key, DB URL, model defaults) | Mode `0640`, owned by `wolf:wolf` |
| `/etc/wolf/cors-origins` | CORS allow-list (one origin per line) | Mode `0644` |
| `/var/lib/wolf/secrets.enc` | Fernet-encrypted secrets backend | Mode `0600`, owned by `wolf:wolf` |
| `/var/log/wolf/orchestrator.log` | Orchestrator structured log (JSON) | Rotated via logrotate |
| `/var/log/wolf/gateway.log` | Gateway log | Rotated via logrotate |
| `/var/log/wolf/frontend.log` | Frontend log | Rotated via logrotate |
| `/lib/systemd/system/wolf-orchestrator.service` | systemd unit | Pulls env from `/etc/wolf/wolf.env` |
| `/lib/systemd/system/wolf-gateway.service` | systemd unit | Same |
| `/lib/systemd/system/wolf-frontend.service` | systemd unit | Same |
| `/etc/logrotate.d/wolf` | Log rotation config | Standard logrotate format |

A dedicated system user `wolf` (no shell, no home dir beyond `/var/lib/wolf`)
owns the runtime processes and the secrets file. The Postgres database
is owned by the standard `postgres` user; Wolf connects via local socket
or `localhost:5432` with credentials from `/etc/wolf/wolf.env`.

---

## The `wolf` CLI surface

The CLI is a thin wrapper that activates the right virtualenv and
forwards to existing `app.management.*` modules. Surface (first
version):

| Command | Wraps | Purpose |
|---|---|---|
| `wolf bootstrap-tenant ...` | `python -m app.management.bootstrap_tenant` | Create or update a tenant + admin + Wazuh config |
| `wolf set-secret --key K` (reads value from stdin) | `python -m app.management.set_secret` | Stash a secret in the encrypted backend |
| `wolf smoke-wazuh ...` | `python -m app.management.smoke_wazuh` | Live-test a tenant's read tools against its Wazuh |
| `wolf status` | `systemctl is-active wolf-*` | One-shot health of the three units |
| `wolf logs [unit]` | `journalctl -u wolf-{unit} -f` | Convenience tail |
| `wolf reconfigure` | regenerate `/etc/wolf/wolf.env` derivatives, run migrations, restart units | Used by the upgrade hook; can be called manually |

All flag names match the underlying Python CLIs exactly — the wrapper
adds no flag translation. This keeps the contract simple and means
the Python CLIs remain the source of truth for argument shapes.

---

## Supported distros (target)

| Distro | Version | Channel | Priority |
|---|---|---|---|
| Ubuntu | 24.04 LTS | APT | 1 (primary) |
| Ubuntu | 26.04 LTS | APT | 1 (when released) |
| Debian | 12 (Bookworm) | APT | 2 |
| RHEL | 9 | YUM | 1 |
| Rocky Linux | 9 | YUM | 2 |
| openSUSE Leap | 15.x | zypper | 3 (community-supported) |

Older distros (Ubuntu 22.04, RHEL 8) intentionally **not** supported
because the prerequisite-repo dance becomes excessive (would need
two PPAs *and* a compiler toolchain for some build steps). Operators
on older distros use the container channel.

---

## Security posture

- **Wolf APT/YUM repo signed** with a long-lived GPG key. Key
  fingerprint published on the project website; install script
  fetches and verifies before adding the repo.
- **Prerequisite repos** (deadsnakes, NodeSource, PostgreSQL APT)
  use their upstream signing keys, not Wolf's. The install script
  uses the upstream-published key for each; it does not bundle
  trust transitively.
- **Secrets file** (`/var/lib/wolf/secrets.enc`) is Fernet-encrypted
  on disk. Key is in `/etc/wolf/wolf.env` (`SECRETS_FILE_KEY`),
  mode `0640`, readable only by `root` and `wolf`.
- **Systemd hardening** — units use `ProtectSystem=strict`,
  `ProtectHome=true`, `PrivateTmp=true`, `NoNewPrivileges=true`,
  `CapabilityBoundingSet=` (empty). Listed in full in the unit
  files when shipped.
- **Network exposure** — orchestrator binds `0.0.0.0:8000` by
  default (LAN-reachable), frontend binds `0.0.0.0:3000`. Gateway
  binds `127.0.0.1:8001` only — never externally exposed (per
  `docs/01` and `docs/07`). The install script prints a warning
  if the public-internet interface is detected; operator must
  explicitly opt in for an internet-facing install.
- **Install-script integrity** — script served from
  `https://wolf-project.org/install.sh` over TLS; SHA-256 hash
  published in the same release notes that publish the script
  itself so operators who want to verify can.

---

## What the implementation will require

When the implementation slot arrives (post-Phase 4, per ADR 0007
§Consequences), the work breaks down as:

1. **Build pipeline.** Per-distro builders (probably GitHub Actions
   matrix or `pbuilder`/`mock` containers) producing `.deb` and
   `.rpm` artifacts for each release. ~1 week.
2. **APT/YUM repo hosting.** A static-file APT repo (signed) and
   a YUM repo (signed), served from a CDN-fronted bucket. Could
   use [aptly](https://www.aptly.info/) or just `dpkg-scanpackages`
   + nginx. ~3 days.
3. **The `wolf` CLI.** Thin Python or shell wrapper around the
   existing management commands. ~2 days.
4. **Systemd units + logrotate config.** ~1 day.
5. **The install script.** Bash, OS detection, repo registration,
   metapackage install, post-install bootstrap. ~3 days including
   testing against fresh VMs of each supported distro.
6. **Upgrade-test suite.** A reproducible test that installs an
   old release, upgrades to the new one, confirms data + tenants
   survive. ~3 days.
7. **Documentation.** Update `docs/09` and `docs/13` with the new
   channel as a peer to containers; new ONBOARDING section for
   operators using the native channel. ~2 days.

Total: **~3–4 weeks of focused engineering** for a first version.
Ongoing per-release packaging work: **~1 day per release**.

These numbers are estimates; the actual slot and scoping will be
decided when the work is queued (post-Phase 4).

---

## How current code should accommodate this commitment

The implementation is not yet started, but ongoing development must
not regress assumptions that this channel relies on. Specifically:

- **All configuration reads from environment** (already enforced via
  `pydantic-settings` in `services/orchestrator/app/config.py`).
  New configurable values must use the same pattern — no
  hard-coded paths, no Docker-specific constants.
- **No container-only paths.** Don't assume `/run/secrets/`,
  `/app/`, or any Docker-volume convention exists. The encrypted
  secrets file's location is read from `SECRETS_FILE_PATH` env
  var — the system-package install sets it to
  `/var/lib/wolf/secrets.enc`; the container install sets it to
  `/run/secrets/wolf_secrets.enc`. Same code, different env.
- **Management CLIs remain usable as `python -m`.** The `wolf` CLI
  wraps these; it does not replace them. Keeping the Python
  invocation form working keeps the contract simple and means the
  underlying logic is tested by both channels' integration tests.
- **Structured logging stays distro-neutral.** No "container-id"
  log enrichment. Log to stdout/stderr; let systemd's journal
  capture them and logrotate manage rotation on the file path
  configured.
- **Frontend Next.js build is `output: 'standalone'`.** This is
  what makes the system-package install possible without bundling
  `node_modules` — the standalone build produces a single
  self-contained directory the package can install. Frontend
  builds must continue to use this output mode.

A small `make package-check` target (added when the build pipeline
is implemented) will assert these constraints in CI so they cannot
regress silently.

---

## Maintenance

This file is a **directive**, not a snapshot. Update it when:

- The package set changes (split, merge, new component).
- A supported distro is added or dropped.
- The `wolf` CLI surface gains or loses a command.
- The security posture changes materially.
- Implementation progresses (move items from "target" to
  "shipped" in the relevant sections).

Do **not** update this file for routine release notes or per-package
version bumps — those belong in `docs/CHANGELOG.md` and the package
metadata.
