# 17 — Release Engineering

> **Status (2026-06-05):** tracking document. Captures the gap
> between Wolf's current state and the Wazuh-equivalent release-
> engineering model we're targeting. Each gap is a discrete piece
> of work that lands in either a "build-now" slice or the
> dedicated release phase that ships alongside Wolf v1.

## Why this doc exists

Wolf follows the Wazuh model: **open-source, on-premises**.
Customers install Wolf on their own infrastructure. There is no
Wolf-hosted production environment, no SaaS, no cloud tenancy.
Distribution happens through the same channels Wazuh uses — an
APT repository, signed packages, a documented install workflow,
versioned releases.

Under this model, "DevOps" in the cloud/SaaS sense is largely
irrelevant. What matters is **release engineering**: producing
reliable shipping artifacts that other people install on
infrastructure we don't control.

This doc lists what's missing between Wolf's current state and
that model, with enough detail to scope the work without
shipping in a single session.

## The Wazuh model we're targeting

For reference, the things a Wazuh operator actually does:

1. Add the Wazuh APT repository's GPG key + sources.list entry
2. `sudo apt update && sudo apt install wazuh-manager wazuh-indexer wazuh-dashboard`
3. (Optional) Use the all-in-one install script:
   `curl -sO https://packages.wazuh.com/4.10/wazuh-install.sh
   && sudo bash wazuh-install.sh -a`
4. Receive upgrade notifications via standard `apt upgrade` flow
5. Read versioned docs at `documentation.wazuh.com/<version>/`
6. Get security advisories at `wazuh.com/security/advisories/`

Every piece of this is something Wolf needs an equivalent of.

## What Wolf has today

| Artifact | Where | Status |
|---|---|---|
| Open-source license (Apache 2.0) | `LICENSE`, `debian/copyright` | ✅ |
| CI with full quality gates (ruff, mypy, tsc, eslint, pytest, 4 smokes) | `.github/workflows/ci.yml` | ✅ |
| `dpkg-buildpackage` produces 4 `.deb` files | `debian/` (Phase 5.9) | ✅ |
| CI uploads `.debs` as workflow artifacts | smoke-deb job | ✅ |
| Per-component packages + meta-package | `debian/control` | ✅ |
| FHS install paths + service users + hardened systemd | `deploy/systemd/system/`, `install-users.sh` | ✅ |
| Operator-facing ONBOARDING walkthrough | `ONBOARDING.md` §3.4 Path A | ✅ |
| `SECURITY.md` for vulnerability reports | `SECURITY.md` | ⚠️ Exists but minimal — no formal disclosure process |
| `CONTRIBUTING.md` | `CONTRIBUTING.md` | ✅ |

This is a solid release-engineering foundation. The next layer
of work is making the `.debs` actually reach operators.

## The gaps — what Wolf is missing

Each gap below names: what it is, why it matters, how Wazuh
solves it, what Wolf needs to build, and where in the build
sequence it should land.

### Gap 1 — GPG signing of `.deb` artifacts

**What it is:** Every package shipped to operators should be
cryptographically signed by a Wolf-maintainer key. Operators
import the public key into their apt keyring; from then on, any
`.deb` from a Wolf repo is automatically verified at install
time. An unsigned or tampered `.deb` fails verification.

**Why it matters:** Without signing, an operator who installs
Wolf via `apt install` has no way to verify the `.deb` they
received hasn't been modified between our CI and their box. For
a security-adjacent product (which Wolf is — it has read access
to Wazuh data), this is non-negotiable for a real release.

**How Wazuh solves it:** every package is signed with the Wazuh
maintainers' GPG key. The key is published at
`packages.wazuh.com/key/GPG-KEY-WAZUH`. The operator adds it to
apt's trust store as the first install step.

**What Wolf needs to build:**

* Generate a Wolf maintainers' GPG keypair (operator-side; the
  private key NEVER goes in the repo).
* Publish the public key at a stable URL.
* Wire GitHub Actions to sign every `.deb` produced by the
  smoke-deb job using the private key stored as a GitHub
  Secret.
* Document the operator's first-install step
  ("`curl ... | apt-key add -`") in ONBOARDING.

**Sequencing:** Build-now enabler. The signing wiring is small;
publishing the public key is operator-driven (you generate the
key).

---

### Gap 2 — A hosted APT repository

**What it is:** A URL operators add to their
`/etc/apt/sources.list.d/wolf.list`, then `apt update` finds the
Wolf packages and `apt install wolf` works. This requires an
HTTPS-served directory with a specific layout (`dists/`,
`pool/`, `Packages.gz`, `Release` / `InRelease` signed by our
GPG key).

**Why it matters:** Without a hosted repo, the only way an
operator gets Wolf is to download the `.deb` files individually
from GitHub Releases (manual). With a hosted repo, `apt install
wolf` is one command and standard `apt upgrade` brings in new
versions automatically.

**How Wazuh solves it:** `packages.wazuh.com/4.x/apt/` — their
own infrastructure, signed by their maintainers' GPG key.

**What Wolf needs to build:**

* Architectural decision: where does the apt repo live?
  Options (in increasing cost-and-control order):
  - GitHub Pages + `reprepro` / `aptly` (free; URL is GitHub-y
    but cleanable via custom domain)
  - Cloudflare R2 + custom domain (~$0–10/mo at low scale)
  - Self-hosted HTTPS server (operator-controlled VPS;
    ~$5–20/mo)
* The repo-management tooling that takes signed `.debs` from
  CI and publishes them into the repo layout.
* The release-cut workflow that triggers this (e.g.,
  `git tag v0.1.0` → CI builds + signs + publishes).

**Sequencing:** Dedicated release phase. The architectural
decision (where the repo lives) is more important than the
technical work around it.

---

### Gap 3 — Versioned release tagging + release notes

**What it is:** A discipline of cutting Wolf releases via
`git tag v0.1.0` (semver), each tag producing shipping artifacts
+ a release notes entry. Today every commit on `main` has the
same version (`0.1.0` in pyproject.toml and `debian/changelog`).
For a real release channel, every release needs its own
version + its own artifacts.

**Why it matters:** Without tags, operators have no way to pin
to a specific release. They'd either install whatever's latest
on `main` (unstable) or never upgrade (frozen at install time).
With tags, operators run `apt install wolf=0.2.0` to pin or `apt
upgrade wolf` to follow latest stable.

**How Wazuh solves it:** Wazuh follows semver. Their release
notes live at `documentation.wazuh.com/<version>/release-notes/`.
Each tag triggers CI that produces signed packages + a release
artifact bundle.

**What Wolf needs to build:**

* Semver convention documented in `RELEASING.md`.
* A `release` GitHub Actions workflow triggered on `v*` tags
  that builds + signs + uploads `.debs`.
* GitHub Releases page automation (the workflow creates the
  release + uploads artifacts).
* A release-notes template + the discipline of populating it
  per release.

**Sequencing:** Build-now enabler (the workflow file + RELEASING.md
docs). The release-notes discipline starts when v0.1.0 cuts.

---

### Gap 4 — Quickstart install script (Wazuh's `wazuh-install.sh` equivalent)

**What it is:** A one-liner that installs all three components
on a single host with sensible defaults:

```bash
curl -sO https://wolf-project.org/install.sh
sudo bash install.sh
```

The script handles: adding the APT repo, importing the GPG key,
`apt install wolf`, generating certs via `wolf-cert init`,
initializing wolf-database, provisioning the env files, and
starting all three systemd units.

**Why it matters:** The single biggest UX win Wazuh gives
operators. Without this, the install is the multi-step process
in ONBOARDING §3.4. With this, an operator with a fresh Ubuntu
box has a working Wolf in a few minutes.

**How Wazuh solves it:** their famous `wazuh-install.sh -a`
all-in-one mode.

**What Wolf needs to build:**

* `packaging/install.sh` — the script itself.
* A `--distributed` mode that prompts for which components to
  install (vs. installing all three).
* A `--unattended` mode for CI / automated installs.
* A way to host the script (could be the same hosting as the
  APT repo).

**Sequencing:** Dedicated release phase. The script is
moderately complex + depends on the APT repo (gap 2) existing.

---

### Gap 5 — Distributed-deployment install docs per release

**What it is:** Step-by-step instructions for installing each
component on a separate host, with the cert-distribution + env-
provisioning steps documented per topology.

**Why it matters:** Operators with security infrastructure
constraints (the brain box on a hardened VLAN, the dashboard on
a DMZ, etc.) need explicit guidance. ONBOARDING §3.4 covers the
all-in-one path well; the distributed path is mentioned in §3.13
but not stepped through.

**How Wazuh solves it:**
`documentation.wazuh.com/<version>/installation-guide/wazuh-cluster/`
walks the operator through each topology.

**What Wolf needs to build:**

* Expand ONBOARDING §3.13 (or create a separate
  distributed-install doc) with per-topology walkthroughs.
* Per-host cert + env provisioning recipes.
* Network-topology diagrams showing the mTLS flows
  (browser→dashboard, dashboard→server, server→database).

**Sequencing:** Build-now (mostly docs work) or alongside the
release phase.

---

### Gap 6 — Upgrade testing matrix

**What it is:** CI that installs Wolf v0.1.0 on a clean box,
creates real data (tenants, sessions, audit events), then
upgrades to v0.2.0 and verifies all data + behaviour is intact.

**Why it matters:** alembic migrations + config-file shape
changes + venv recreation + systemd unit changes ALL have
potential to break operator data on upgrade. Without an upgrade
test, an operator on v0.1.0 finds out at `apt upgrade` time.

**How Wazuh solves it:** they have an internal upgrade-testing
infrastructure + documented upgrade guides per major-version
transition.

**What Wolf needs to build:**

* A CI job (`smoke-upgrade`) that runs `apt install wolf=$(PREVIOUS)`,
  creates fixture data, runs `apt upgrade wolf` to current,
  verifies fixture data is still there and queryable.
* An upgrade-guide doc generated per release that lists any
  breaking changes / required operator action.

**Sequencing:** Dedicated release phase. Requires v0.1.0 to
exist first (no "previous" to upgrade from until then).

---

### Gap 7 — Security disclosure policy

**What it is:** A documented process for reporting Wolf
vulnerabilities: where to send the report, what response time
to expect, how the fix will be coordinated.

**Why it matters:** Wolf has read access to Wazuh data + manages
secrets + runs as system services. A vulnerability disclosure
process is table stakes for any security-adjacent product.

**How Wazuh solves it:** `wazuh.com/security/` page +
security advisories at `wazuh.com/security/advisories/`. They
follow responsible disclosure with a stated timeline.

**What Wolf needs to build:**

* Expand `SECURITY.md` to document the report process (which
  email, what to include, expected response time).
* (Optional) GitHub Security Advisories integration —
  GitHub's built-in private-disclosure flow.
* A public advisories page generated from the security
  advisories repo (or a `docs/security/` directory).

**Sequencing:** Build-now (docs only); the advisories
infrastructure can wait for the dedicated release phase.

---

### Gap 8 — Long-term support policy

**What it is:** A stated commitment: "We maintain v0.X.0
through YYYY-MM-DD" with explicit security-only / no-new-features /
EOL transitions.

**Why it matters:** Operators planning around Wolf need to know
how long they can stay on a given version without forced
upgrades. Enterprise security teams often require this in
writing.

**How Wazuh solves it:** their support matrix at
`documentation.wazuh.com/<version>/release-notes/` names
maintenance + EOL dates per major.

**What Wolf needs to build:** a `SUPPORT.md` doc that names the
policy. Initial version can be aspirational ("v0.1.0 will be
supported for 12 months from release") and refined over time.

**Sequencing:** Build-now (it's a one-paragraph commitment).

---

### Gap 9 — Real `.deb` install verification in CI

**What it is:** Today's CI builds the `.debs` but doesn't `apt
install` them on a clean image and verify the services start.
That's a real gap — the build succeeding doesn't prove the
postinst scripts work.

**Why it matters:** A postinst script that creates the wolf-
database user but forgets to chown a dir would build fine but
fail at install. We'd find out when an operator hits it.

**How Wazuh solves it:** they have install-test jobs that
spin up clean Debian + Ubuntu + RHEL images, install the
packages, and verify the services come up.

**What Wolf needs to build:**

* A CI job (`smoke-deb-install`) downstream of `smoke-deb`
  that installs the produced `.debs` on a clean
  `debian:trixie` Docker container and verifies the systemd
  units come up (in container-friendly form — systemd-in-docker
  is awkward; might use `apt-get install` + service-start
  semantics manually).

**Sequencing:** Build-now (high value, modest work).

---

### Gap 10 — Dependency vulnerability scanning

**What it is:** Automated CVE scanning across our Python +
Node + Postgres + Postgres-package deps. Surfaces known
vulnerabilities so we can patch + cut a release before
operators are exposed.

**Why it matters:** Wolf bundles 100+ Python packages in
wolf-server's venv. Without automated scanning, a CVE in (say)
SQLAlchemy that lands on a Tuesday could sit in our shipping
.deb until someone notices.

**How Wazuh solves it:** they run automated scanning on their
build pipeline + monitor upstream CVE feeds.

**What Wolf needs to build:**

* Dependabot or Renovate configured to scan Python + Node
  deps and open PRs for security updates.
* A CI job that runs `pip-audit` or `safety` on wolf-server's
  bundled venv.

**Sequencing:** Build-now (Dependabot is one config file +
free).

---

### Gap 11 — Secrets / credential scanning

**What it is:** CI guards against accidentally committing
secrets (API keys, passwords, private keys).

**Why it matters:** A single committed credential to a public
repo is a permanent leak — even if rebased away, archives
preserve it.

**How Wazuh solves it:** they have internal pre-commit + CI
guards; specifics aren't public.

**What Wolf needs to build:**

* `pre-commit` hook with `detect-secrets` or `gitleaks`.
* CI job that runs the same scanner.
* A `.secrets.baseline` file to mark known-safe matches.

**Sequencing:** Build-now (small footprint, high value).

---

### Gap 12 — Documentation site

**What it is:** A browsable, searchable version of the
`docs/` directory at a stable URL like `docs.wolf-project.org`.

**Why it matters:** Operators don't navigate GitHub trees
well. A documentation site with search makes the planning
bundle + ONBOARDING + per-release upgrade guides reachable.

**How Wazuh solves it:** `documentation.wazuh.com` — a
versioned docs site generated from their source.

**What Wolf needs to build:**

* A documentation generator (MkDocs Material, Docusaurus, or
  mdBook are the popular choices).
* A `docs.yml` CI workflow that builds + publishes on every
  push to main (free via GitHub Pages).
* Per-release versioned docs (Wazuh-style).

**Sequencing:** Dedicated release phase. Until then, GitHub
renders the markdown adequately.

## Summary — build-now vs dedicated release phase

| Gap | Sequencing |
|---|---|
| 1 — GPG signing | Build-now |
| 2 — Hosted APT repository | Dedicated release phase |
| 3 — Versioned release tagging + release notes | Build-now |
| 4 — Quickstart install script | Dedicated release phase |
| 5 — Distributed-deployment install docs | Build-now (docs) |
| 6 — Upgrade testing matrix | Dedicated release phase |
| 7 — Security disclosure policy | Build-now (docs) |
| 8 — Long-term support policy | Build-now (docs) |
| 9 — Real `.deb` install verification in CI | Build-now |
| 10 — Dependency vulnerability scanning | Build-now |
| 11 — Secrets / credential scanning | Build-now |
| 12 — Documentation site | Dedicated release phase |

**Build-now items**: 8 (1, 3, 5, 7, 8, 9, 10, 11). Each is a
small slice — collectively ~half a phase of work.

**Dedicated release phase items**: 4 (2, 4, 6, 12). These need
the APT repo decision + the v0.1.0 release as a starting point.

## Open architectural decisions

These need operator input before scoping any of the dependent
gaps:

1. **APT repository hosting** — GitHub Pages? Cloudflare R2?
   Self-hosted VPS? Affects gap 2 directly, gap 4 indirectly.
2. **Custom domain for the repo + docs** — `wolf-project.org`?
   Subdomain pattern? Affects gaps 2, 4, 12.
3. **GPG keypair generation + storage** — Hardware token?
   Encrypted backup? Multiple signers? Affects gap 1.
4. **Release cadence** — Time-based (monthly?) or
   feature-driven? Affects gaps 3, 6, 8.
5. **Major-version EOL window** — How long does v0.X stay
   supported? Affects gap 8.

## Acceptance criteria for "Wolf is releasable as v1"

The bar that says "Wolf is ready for general operator use":

* [ ] All 12 gaps closed (or explicitly deferred to a
  post-v1 phase with documented rationale).
* [ ] One operator (other than the maintainer) successfully
  installs Wolf via the quickstart script on a fresh box and
  reaches "logged in + asked a Wazuh question + got an
  answer."
* [ ] The CI upgrade matrix passes for v0.1.0 → v0.2.0
  (proves the upgrade infrastructure works at least once).
* [ ] The GPG signing chain verified end-to-end: an
  operator imports the public key, runs `apt install`,
  apt confirms the signature is valid.
* [ ] A `SECURITY.md` disclosure email + at least one
  drilled response (even if just a test report).

This list is the "definition of done" for the dedicated
release phase.
