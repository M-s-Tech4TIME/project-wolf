# 0007 — Native distribution: system packages + install-script wrapper

**Date:** 2026-05-23
**Status:** accepted
**Decider:** human (project owner) with claude-code drafting
**Related:** `docs/16-distribution-and-packaging.md` (the living spec
this ADR establishes), `docs/09-tech-stack-and-repo-layout.md`
§"Container, build, CI" (the pre-existing container-first delivery
discussion this complements), `docs/13-system-requirements.md`
§"What the platform deploys" (the four hardware profiles each
distribution channel must serve).

## Context

Wolf's delivery story documented prior to this ADR (`docs/09` and
`docs/13`) is **container-first**: `docker compose up` for small
deployments, Helm/Kubernetes for big ones. That covers operators who
already run Docker, but leaves a real persona unaddressed: **SOC /
MSSP operators on RHEL or Ubuntu hosts where Docker is unavailable
or forbidden by policy.** This persona is large and is precisely the
operator profile Wolf targets (see `docs/00` "Who it serves").

The product-owner discussion on 2026-05-23 surveyed three native
distribution approaches:

- **Option A** — `.deb`/`.rpm` packages depending on system Python,
  Node, and Postgres.
- **Option B** — GitLab-style omnibus packages bundling Wolf's own
  copy of every interpreter, library, and Postgres binary under
  `/opt/wolf/`.
- **Option C** — Snap / Flatpak universal packages.

The owner expressed preference for Option A on familiarity and
operational ergonomics grounds (operators "know" system packages
and systemd), but raised a concern about the third-party repo
prerequisite friction: Wolf needs Python 3.13, Node 24, and
Postgres 17, none of which ships natively on current LTS distros.

The hybrid pattern used by Tailscale, Caddy, k3s, Docker itself, and
GitLab (alongside their omnibus) — **system packages backed by an
install script that handles prerequisite-repo registration** —
addresses the friction without abandoning Option A's mechanics. This
ADR commits Wolf to that hybrid as the native-distribution track.

## Decision

**Wolf's native (non-container) distribution will be Option A — system
`.deb`/`.rpm` packages installed via APT/YUM and run as systemd
services — wrapped in a one-line install script that prepares
prerequisite repositories before installing Wolf packages.**

Concretely:

- Each Wolf service ships as its own system package
  (`wolf-orchestrator`, `wolf-gateway`, `wolf-frontend`), plus a
  metapackage `wolf` that pulls them all in along with the runtime
  prerequisites (Python 3.13, Node 24, Postgres 17 + pgvector).
- Files land in distro-conventional locations: binaries under
  `/usr/lib/wolf/` (or wherever the build chooses), config under
  `/etc/wolf/`, data under `/var/lib/wolf/`, logs under
  `/var/log/wolf/`, systemd units in `/lib/systemd/system/`.
- A single `wolf` CLI wraps the existing management commands
  (`bootstrap_tenant`, `set_secret`, `smoke_wazuh`) so operators do
  not invoke `python -m app.management.*` directly.
- A hosted install script at a stable URL (placeholder:
  `https://wolf-project.org/install.sh`) detects the OS, adds the
  required prerequisite repos (deadsnakes for Python 3.13 on Ubuntu,
  NodeSource for Node 24, the PostgreSQL APT/YUM repo for Postgres
  17 + pgvector), then installs the `wolf` metapackage.

The container-first delivery story (`docs/09`, `docs/13` Profile
A/B/C) remains the recommended path for operators who run Docker;
this ADR adds a second track, it does not replace the first.

Living spec — packages, file layout, install-script contract,
supported distro matrix, upgrade story, security expectations — is
maintained at `docs/16-distribution-and-packaging.md`.

## Alternatives considered

- **Option B (omnibus, GitLab-style).** Rejected as primary track.
  Strong on UX ("works on any distro, ignores system Python") but
  expensive engineering (per-distro build pipelines, an APT/YUM
  repo for the fat packages, ongoing per-release packaging work
  conservatively ~1 day each, a `wolf-ctl reconfigure` tool, an
  internal supervisor). The polyglot-stack rationale that drives
  GitLab's omnibus choice does apply to Wolf, but the prerequisite
  versions (Python 3.13, Node 24, Postgres 17) are recent-enough
  that they will land in stable distro repos within 1–2 LTS cycles,
  shrinking the version-mismatch problem omnibus exists to solve.
  Kept on the table as a *possible* second-tier offering only if
  operator demand demonstrates the need (e.g. an MSSP that cannot
  use third-party APT repos for compliance reasons).

- **Option C (Snap / Flatpak).** Rejected. Confinement adds friction
  for a SOC tool that needs unrestricted local-socket access
  (Postgres, Ollama), host secrets backends, and arbitrary outbound
  network. Auto-update behavior of Snap in particular conflicts with
  operator expectations of "I upgrade on my own schedule" common in
  the SOC space.

- **Container-only delivery (status quo before this ADR).**
  Rejected as sufficient. Real persona — RHEL/Ubuntu operators
  without Docker — is unserved. The container path remains the
  recommended track for Docker-comfortable operators; this ADR adds
  a second native track, it does not abandon containers.

- **Pure Option A without install-script wrapper.** Rejected.
  Requires the operator to add three third-party repos by hand
  before installing Wolf. Documented friction; pushed back by
  airgapped / regulated operators in particular. The install-script
  wrapper costs little to build and dramatically improves the
  first-install experience.

- **Both Option A and Option B in parallel from day one.**
  Rejected. Doubles the packaging burden. The hybrid wraps Option A
  well enough that omnibus's main advantage (version isolation) is
  partially gained without paying omnibus's full cost. If operator
  demand later shows the gap, omnibus can be added as a second
  channel; the codebase changes required to support it are minimal
  because all the relevant decisions (config from env, no
  hard-coded paths, structured logging) already hold.

## Consequences

- **`docs/16-distribution-and-packaging.md` becomes the living
  contract** for the native-distribution track. It specifies the
  package set, file-layout convention, install-script behavior,
  supported distro matrix, the `wolf` CLI surface, and the
  upgrade story. Updated as that work progresses; this ADR is
  frozen.

- **Code constraints for future development.** To keep the system-package
  path tractable when implementation lands, the codebase must
  continue to:
  - Read all configuration from environment variables (already
    holds via `pydantic-settings` in
    `services/orchestrator/app/config.py`).
  - Avoid hard-coded paths that assume container layout
    (`/app/`, `/run/secrets/`, etc.); use settings or
    distro-convention defaults instead.
  - Keep the management CLIs (`bootstrap_tenant`, `set_secret`,
    `smoke_wazuh`) usable as plain `python -m app.management.*`
    invocations — the `wolf` CLI wraps them, does not replace.
  - Avoid Docker-specific assumptions in logs (no
    "container-id" enrichment, no "via Docker socket" patterns).
  This is already largely true; new code should not regress it.

- **Implementation timing.** This ADR commits to the track, not to
  a delivery date. Realistic implementation effort: 3–4 weeks of
  focused work for a first version (per-distro builds, install
  script, `wolf` CLI, hosted APT/YUM repo, upgrade-test suite),
  plus ~1 day per release ongoing. The natural slot is **after
  Phase 4** (gateway + propose/execute), when the deployable
  surface has stabilized. Doing it earlier risks repackaging churn
  as the service surface still changes.

- **Documentation precedence.** Where `docs/09` and `docs/13`
  describe delivery, the native-distribution track is a peer
  (not a replacement) of the container track. The pointer added
  to `docs/09` §"Container, build, CI" in this commit acknowledges
  the second track at the same level as the existing one.

- **No code changes are required by this ADR.** The platform
  already has the abstractions needed (env-driven config,
  swappable secrets backends, distro-agnostic Python). This ADR
  records the *intent*; subsequent commits will deliver the
  implementation when the natural slot arrives.

- **Rollback path.** This commitment is reversible. If the
  install-script approach proves operationally fragile (e.g. one
  prerequisite-repo upstream changes signing keys and breaks new
  installs), the project can pivot to omnibus (Option B) by
  writing a new ADR that cites this one as superseded. The
  underlying Wolf code is the same in either case.

---

## Amendment (2026-05-23) — positioning clarified by ADR 0008

The Decision section above frames the container track and native track
as peers ("the container-first delivery story remains the recommended
path for Docker-comfortable operators; this ADR adds a second track, it
does not replace the first").

[ADR 0008](0008-native-primary-docker-supplementary.md), accepted
later the same day, sharpens this positioning:

- **Native is Wolf's primary delivery channel** — where polish,
  ergonomics, and operator-facing investment go.
- **Docker is baseline-supported, not promoted** — Dockerfiles,
  `docker-compose.yml`, and Makefile docker targets remain, build,
  and pass `make up` smoke. They serve operators who want to build
  their own container images (typically for Kubernetes). No polished
  compose-deployment experience is committed.

This amendment is a *positioning* change, not a *substance* change —
both ADRs commit to the same code paths and the same Apache 2.0
distribution. Read ADR 0008 for the full reasoning, the alternatives
weighed, and the concrete operational consequences.
