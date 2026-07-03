# Releasing Wolf

This document is the operator playbook for cutting a Wolf
release. Read it end-to-end before cutting your first release;
skim the **Cut the release** section for subsequent ones.

## What "release" means in Wolf today

Per `docs/17-release-engineering.md`, Wolf ships as Debian
packages (`.deb`) signed with the Wolf maintainers' GPG key.
A "release" is:

1. A git tag of the form `vMAJOR.MINOR.PATCH` (semver, see
   [SUPPORT.md](SUPPORT.md)).
2. Five signed `.deb` files attached as assets to a GitHub
   Release with that tag's name:
   - `wolf-database_X.Y.Z_amd64.deb`
   - `wolf-server_X.Y.Z_amd64.deb`
   - `wolf-dashboard_X.Y.Z_amd64.deb`
   - `wolf-search_X.Y.Z_all.deb` (SearXNG sidecar, ADR 0032)
   - `wolf_X.Y.Z_all.deb` (meta-package)
3. Detached signatures (`*.asc`) alongside each `.deb`.
4. A signed `SHA256SUMS` (+ `SHA256SUMS.asc`) covering all
   five `.deb` files.
5. Release notes pulled from `docs/CHANGELOG.md` (or a
   fallback if no matching section exists).

The hosted APT repository (`docs/17` gap 2) is a separate
concern — adds `apt install wolf` UX on top of "download the
signed .deb from GitHub Releases". Operators today install
via the GitHub Releases path; that path becomes `apt install`
once gap 2 lands.

## Versioning

Wolf follows [Semantic Versioning 2.0](https://semver.org/):
`MAJOR.MINOR.PATCH`. See [SUPPORT.md](SUPPORT.md) for the
support-window commitment (12 months per `v0.X` major).

Pre-1.0:
- Breaking changes can land in MINOR bumps (`v0.1.0` →
  `v0.2.0`) if explicitly documented in the release notes.
- PATCH bumps (`v0.1.0` → `v0.1.1`) are bugfix-only.

Post-1.0: full semver applies; breaking changes require
MAJOR.

## Pre-release checklist

Before tagging, verify:

- [ ] **CI is green** on the commit you're about to tag.
  Check `gh run list --branch main --limit 1` or the Actions
  tab. All 11 jobs must be passing.
- [ ] **`debian/changelog` is bumped to match the target
  version.** The release workflow asserts this match and
  fails fast if they diverge.
  ```
  # Edit debian/changelog, add a new entry at the top:
  #   wolf (0.2.0) unstable; urgency=low
  #
  #     * <one-line summary of what changed>
  #
  #    -- Wolf Maintainers <abid.syed.golam@gmail.com>  Mon, 09 Jun 2026 12:00:00 +0000
  ```
  Use ISO-format dates + a real timestamp. The `dch -i`
  command from devscripts can do this interactively if you
  have it installed locally.
- [ ] **`docs/CHANGELOG.md` has a section for this version.**
  Format `## YYYY-MM-DD — <descriptive headline>` at the top.
  Release notes are auto-extracted from here; if missing,
  the workflow generates a generic stub.
- [ ] **`docs/PROGRESS.md` reflects the current phase + LTS
  table.** If this is the v0.1.0 cut, populate the LTS table
  in SUPPORT.md.
- [ ] **GPG signing key still works.** Run a dry-run sign
  locally:
  ```bash
  echo "test" | gpg --clearsign --default-key 0x37723B2DE0ABFD65
  ```
  If it prompts for a passphrase you don't remember, recover
  from the 1Password backup before tagging.

## Cut the release

Three commands. Workflow does the rest.

```bash
# 1. Bump debian/changelog (manual edit; see checklist).
$EDITOR debian/changelog

# 2. Update docs/CHANGELOG.md with the release notes.
$EDITOR docs/CHANGELOG.md

# 3. Commit the version bump.
git add debian/changelog docs/CHANGELOG.md
git commit -m "chore(release): bump to v0.X.Y"
git push origin main

# 4. Wait for CI on this commit to pass.
gh run watch  # interactive; press Ctrl+C once green

# 5. Tag the green commit + push the tag.
git tag -a v0.X.Y -m "Wolf v0.X.Y"
git push origin v0.X.Y
```

Pushing the tag triggers `.github/workflows/release.yml`,
which:

1. Verifies the tag version matches `debian/changelog`.
2. Builds all five `.deb` files via `dpkg-buildpackage`.
3. Imports the Wolf signing key from `GPG_PRIVATE_KEY`
   secret + signs each `.deb` (detached `.asc` signature).
4. Generates `SHA256SUMS` + signs that too.
5. Extracts release notes from `docs/CHANGELOG.md` (or
   falls back to a stub).
6. Creates a GitHub Release at
   `https://github.com/M-s-Tech4TIME/project-wolf/releases/tag/v0.X.Y`
   with all artifacts attached.

Watch the workflow:

```bash
gh run watch                    # latest workflow run
gh run view --web              # open in browser
```

End-to-end: typically 4-6 minutes from `git push origin
v0.X.Y` to the release being live.

## Post-release verification

Run this once the release workflow completes:

```bash
# 1. Confirm the release exists with all 12 expected assets
#    (5 .debs + 5 .asc + SHA256SUMS + SHA256SUMS.asc = 12).
gh release view v0.X.Y

# 2. Download + verify locally as an operator would.
cd /tmp && mkdir wolf-verify && cd wolf-verify
gh release download v0.X.Y --pattern '*'

# Import the Wolf public key (one-time, if not already done):
gpg --import path/to/security/wolf-maintainers.gpg

# Verify SHA256SUMS first
gpg --verify SHA256SUMS.asc SHA256SUMS
sha256sum -c SHA256SUMS

# Verify each .deb's individual signature
for deb in wolf*.deb; do
    gpg --verify "${deb}.asc" "${deb}"
done

# 3. Install in a clean container to smoke-test the .debs.
docker run --rm -v "$PWD:/debs" debian:trixie bash -c \
    "apt-get update && \
     apt-get install -y /debs/wolf*.deb && \
     wolf-server --version"
```

Document the verification in the release announcement (e.g.,
Slack, mailing list, GitHub Discussions).

## Release notes shape

The release workflow extracts notes from `docs/CHANGELOG.md`
by matching the version string. Maintainer-edit-friendly
format:

```markdown
## 2026-06-09 — v0.1.0 release

### What ships
* The first stable Wolf release.
* All three components (wolf-database / wolf-server /
  wolf-dashboard) installable via signed `.debs`.
* mTLS substrate for inter-component traffic.
* RBAC + organization isolation (see [doc 05](docs/05-multi-organization.md)).

### Breaking changes
* (None for v0.1.0; document for future majors.)

### Known limitations
* Hosted APT repository not yet available (gap 2). Operators
  install via direct .deb download until then.
* (Other open release-engineering items.)

### Upgrade notes
* (Empty for v0.1.0; document migration steps for future
  cuts that change schema / config shape / etc.)
```

If `CHANGELOG.md` doesn't have a section matching the tag
version, the release workflow falls back to a generic stub
that links at the file. Always populate the dedicated section
in the same commit that bumps `debian/changelog`.

## Yank or amend a bad release

If a release ships with a bug that requires retraction:

```bash
# 1. Mark the GitHub Release as a draft (operators can't
#    download the assets accidentally):
gh release edit v0.X.Y --draft=true

# 2. Cut a fixed PATCH release immediately:
#    - Edit debian/changelog: v0.X.(Y+1) with the fix note.
#    - Commit, push, tag v0.X.(Y+1), push tag.

# 3. After the fixed release is live, update the bad release's
#    body to point at the fix:
gh release edit v0.X.Y --notes "**Retracted.** Replaced by v0.X.(Y+1) due to <reason>."

# 4. (Optional) Delete the tag itself if it was truly broken:
git push --delete origin v0.X.Y
git tag -d v0.X.Y
# Note: never delete a tag that operators have already started
# installing from. Their already-downloaded .debs still verify.
```

Document the retraction in `docs/CHANGELOG.md` + (eventually)
the security advisories page.

## Security-patch flow

If a vulnerability is reported via `SECURITY.md`:

1. Fix lands on a private branch (per the disclosure process).
2. Coordinated-release timeline locked with the reporter.
3. At release time:
   - Bump PATCH version (`v0.X.Y` → `v0.X.(Y+1)`).
   - `debian/changelog` entry mentions CVE if assigned.
   - `docs/CHANGELOG.md` security section calls out
     severity + affected versions.
   - Tag + push.
4. After release ships, publish the advisory in the
   `docs/security/advisories/` directory (when that exists
   per gap 9/12).

## Where the signing key lives

- **GitHub Actions Secrets** (`GPG_PRIVATE_KEY` +
  `GPG_PASSPHRASE`): the workflow's signing copy.
- **1Password vault**: the operator's offline source-of-truth
  backup. If the GitHub secret ever needs to be re-provisioned
  (e.g., after a rotation), restore from 1Password.
- **Revocation certificate** (if generated): also in 1Password.
  Use only if the private key is confirmed compromised.

Never paste the private key into the repo, into a PR
description, into a release note, or into any non-Secrets
location. If you accidentally do: revoke the key immediately
(see `SECURITY.md` revocation procedure) and re-key.

## Quick reference

| Operation | Command |
|---|---|
| Bump version | edit `debian/changelog` + `docs/CHANGELOG.md` |
| Cut a release | `git tag -a v0.X.Y -m "Wolf v0.X.Y" && git push origin v0.X.Y` |
| Watch the workflow | `gh run watch` |
| List recent releases | `gh release list` |
| View a release | `gh release view v0.X.Y` |
| Download release assets | `gh release download v0.X.Y` |
| Yank a release | `gh release edit v0.X.Y --draft=true` |
| Verify signature locally | `gpg --verify wolf-*.deb.asc wolf-*.deb` |
| Verify checksum | `gpg --verify SHA256SUMS.asc SHA256SUMS && sha256sum -c SHA256SUMS` |
