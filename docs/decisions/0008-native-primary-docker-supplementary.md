# 0008 — Native delivery is primary; Docker is baseline-supported

**Date:** 2026-05-23
**Status:** accepted
**Decider:** human (project owner) with claude-code drafting
**Related:** [ADR 0007](0007-native-distribution-via-system-packages-and-install-script.md)
(committed Wolf to a native distribution channel; this ADR amends 0007's
positioning), `docs/16-distribution-and-packaging.md` (the living spec
for the native channel), `docs/09-tech-stack-and-repo-layout.md`
(the original container-first delivery discussion this ADR repositions),
`docs/PROGRESS.md` §3 (dev environment changes from Docker Postgres to
system Postgres).

## Context

[ADR 0006](0006-supported-model-families-commitment.md) and
[ADR 0007](0007-native-distribution-via-system-packages-and-install-script.md)
both landed on 2026-05-23 and established two structural commitments:
support four model families natively, and deliver Wolf as native
system packages alongside the existing container-first path.

ADR 0007's language framed the container and native channels as
**peers**: "the container-first delivery story remains the recommended
path for Docker-comfortable operators; this ADR adds a second track,
it does not replace the first."

In the same-day follow-up discussion, the project owner raised a
sharper question: given operator-persona alignment and ADR 0007's
commitment, **should Wolf focus development energy on the native
channel and treat Docker as supplementary rather than peer?** Three
postures were considered:

- **Option A** — Native primary, Docker supplementary
  (baseline-supported, not actively polished).
- **Option B** — Native only, Docker abandoned (Dockerfiles, compose,
  Makefile targets deleted).
- **Option C** — Native only for now, Docker preserved-but-frozen
  (Dockerfiles kept, `docker-compose.yml` deleted).

The owner chose Option A. This ADR records that choice and clarifies
what "primary" and "supplementary" each mean concretely.

This also surfaces a related operational change: with native as
primary, the **dev environment should use system Postgres** (apt-installed,
systemd-managed) instead of the Docker Postgres container the dev
workflow has used through Phase 2. Dev/prod parity for the native
channel is the goal; using the same Postgres install path in dev as
operators will use in production catches install-time issues that
container-hosted Postgres hides.

## Decision

### Strategic posture

- **Native distribution (`.deb`/`.rpm` + systemd + install script,
  per ADR 0007) is Wolf's primary delivery channel.** All operator-facing
  polish, install ergonomics, and documentation precedence go here.
- **Docker remains baseline-supported, not promoted.** Dockerfiles,
  `docker-compose.yml`, and Makefile docker targets stay in the
  repository, continue to build, and continue to pass `make up`
  smoke. They serve operators who want to build their own container
  images (typically for Kubernetes deployment on infrastructure that
  expects containers). No polished `docker compose up`-and-done
  experience is committed; no Helm chart investment is committed
  near-term.
- **A pre-release check** confirms `make up` still works (the
  container path doesn't bit-rot silently). This is a manual step
  today; a CI job formalizing it is a Phase 3+ task.

### Development environment

- **Dev uses system Postgres**, installed via the PostgreSQL APT
  repo (or distro equivalent) on the developer's host. The same
  Postgres 17 + pgvector install path operators will use via the
  native install script.
- The dev orchestrator, frontend, and gateway continue to run
  directly on the host (unchanged from Phase 2 practice — only
  Postgres switches from Docker-hosted to system-hosted).
- `docker compose up -d postgres` remains a documented *alternative*
  in [`ONBOARDING.md`](../../ONBOARDING.md) for contributors who
  prefer it (macOS contributors, anyone running multiple Postgres-using
  projects, anyone wanting `docker compose down -v` reset
  convenience). Recommended path is system Postgres; supported
  alternative is Docker Postgres. Code is identical either way
  because `DATABASE_URL` is env-driven.

### Code constraints (unchanged from ADR 0007, restated for emphasis)

The codebase already honors all of these; new code must not regress:

- All configuration read from environment variables via
  `pydantic-settings`.
- No hard-coded paths assuming container layout (`/app/`,
  `/run/secrets/`, Docker-volume conventions).
- Management CLIs (`bootstrap_tenant`, `set_secret`, `smoke_wazuh`)
  remain usable as plain `python -m app.management.*` invocations.
- Frontend stays on Next.js `output: 'standalone'` build mode.
- No "container-id" log enrichment or Docker-specific log patterns.

### Documentation precedence

- `docs/16-distribution-and-packaging.md` is the **primary** delivery
  spec.
- `docs/09-tech-stack-and-repo-layout.md` §"Container, build, CI"
  is repositioned to describe the container path as the
  baseline-supported alternative (operator-build-your-own-image
  rather than polished compose deployment).
- ADR 0007's "container channel remains the recommended path for
  Docker-comfortable operators" wording is softened by amendment
  to "container channel remains a baseline-supported path for
  operators who want to build their own images" in light of this
  ADR's repositioning.

## Alternatives considered

- **Option B — Native only, Docker abandoned.** Rejected. Removing
  the Dockerfiles closes the door on operators running Kubernetes
  or other container substrates, with no offsetting benefit beyond
  aesthetics. The cost to maintain the existing container path is
  small (no active development, ~10 minutes per release to
  verify `make up`); the cost to revive it later is much larger
  (rebuild from scratch, re-derive design decisions, re-test
  integration).

- **Option C — Native only, `docker-compose.yml` deleted but
  Dockerfiles kept.** Rejected as the middle option that captures
  neither end's benefit. If Dockerfiles stay, `docker-compose.yml`
  is the natural way to exercise them locally (smoke check that
  the images still work). Deleting it makes the smoke test
  awkward without saving meaningful effort. Operators wanting
  Kubernetes deploy still want some reference compose setup to
  port from, which compose provides.

- **Keep the peer framing from ADR 0007 unchanged.** Rejected.
  "Peer" implies equal investment in polish and ergonomics; the
  project owner has decided that's not the case. The honest
  signal — to contributors, future Claude Code sessions, and
  potential operators — is that native is where the polish goes
  and Docker is where things "just work without ceremony."
  Naming this explicitly prevents future drift where contributors
  half-invest in both paths.

## Consequences

- **`docs/09` §"Container, build, CI" and §"What this stack costs
  to run" are reduced** to reflect Docker's supplementary role.
  doc 16 becomes the place to look for "how do we deliver Wolf?"

- **ADR 0007 gets an amendment footer** noting the positioning
  change. Per the ADR README protocol, ADRs are append-only —
  the amendment footer adds context without rewriting the body.

- **ONBOARDING.md §3.4 is rewritten** to lead with system-Postgres
  install steps and mention Docker-Postgres as an alternative.
  Related sections (§3.7 migrations, §5 reboot procedure) are
  updated to reference system Postgres commands. Section 2
  "System requirements" reclassifies Docker from "Mandatory" to
  "Optional (for the container alternative)."

- **`Makefile` gains a comment block** clarifying which targets
  serve native dev (`make test`, `make lint`, `make typecheck`,
  `make migrate-local`, `make probe`) vs which serve the
  container channel (`make up`, `make down`, `make dev`,
  `make logs`, `make migrate`). No targets are deleted.

- **`docker-compose.yml` gains a top-of-file comment** noting it
  is the container-channel deployment stack, not the recommended
  dev workflow.

- **Auto-memory entry `native_distribution_commitment.md` is updated**
  to reflect "native primary" instead of "peer of container."
  Future Claude Code sessions on any machine see the strategic
  posture immediately.

- **Dev/prod parity for the native channel is maximized.** A
  developer working against system Postgres will hit and fix
  the same install-time issues operators will hit when running
  the install script — including PostgreSQL APT repo trust,
  pgvector extension enablement, and FHS-correct file locations.

- **Container channel keeps working.** The pre-release check
  (`make up`, verify health endpoints, exercise one chat
  request) is the regression guard. It is cheap to run and
  prevents the bit-rot scenario.

- **No code changes are required by this ADR.** The platform
  already has the abstractions needed; the constraints listed
  above are already honored. This ADR records the *posture*;
  subsequent edits land it in `ONBOARDING.md`, `docs/09`,
  `docs/16`, `Makefile`, `docker-compose.yml`, and `PROGRESS.md`.

- **Rollback path.** This commitment is reversible. If, for
  example, the install script proves operationally fragile and
  the project pivots back to container-first delivery, a new
  ADR can supersede this one. The underlying Wolf code is the
  same in either case; the rollback is a documentation-and-posture
  change, not a code change.
