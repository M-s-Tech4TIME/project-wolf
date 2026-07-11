# Wolf packaging

How to build the Wolf `.deb` packages from source. Phase 5.9 work
in progress — the scaffold lives here; per-component build logic
fills in across slices 5.9-b (wolf-database), 5.9-c (wolf-server),
5.9-d (wolf-dashboard).

## The four packages

| Package | What ships | Depends |
|---|---|---|
| `wolf-database` | wolf_database CLI + venv + systemd unit + service user | postgresql-18, postgresql-18-pgvector |
| `wolf-server` | wolf_server + wolf_cert + wolf_common venv + systemd unit + service user | python3.13, python3-venv |
| `wolf-dashboard` | Next.js standalone build + systemd unit + service user | nodejs (>= 20) |
| `wolf` (meta) | nothing of its own | wolf-database + wolf-server + wolf-dashboard |

Per ADR 0016, each component is independently installable.
Distributed deployments install just the components needed on
each host. The `wolf` meta-package is the convenience entry point
for all-in-one boxes.

## Building locally (dev box)

Requires a Debian or Ubuntu host with the build tools:

```bash
sudo apt install -y \
    debhelper \
    devscripts \
    dh-python \
    python3-all \
    python3-venv \
    nodejs npm

# From the repo root:
dpkg-buildpackage -b -us -uc

# Produces:
#   ../wolf-database_0.1.0_<arch>.deb
#   ../wolf-server_0.1.0_<arch>.deb
#   ../wolf-dashboard_0.1.0_<arch>.deb
#   ../wolf_0.1.0_all.deb
```

`-b` builds binary packages only. `-us -uc` skips signing (sign
properly when uploading to a real repo). On `unstable`-targeting
Debian or recent Ubuntu the package versions are the workspace
version (`0.1.0` as of Phase 5.9-a).

## Building in a clean chroot

`pbuilder` or `sbuild` give a clean-room build matching what a
fresh Debian / Ubuntu would see. For Phase 5.9-a's CI, a clean
docker `debian:trixie` is enough:

```bash
docker run --rm -v "$PWD:/src" -w /src debian:trixie bash -c \
    "apt-get update && \
     apt-get install -y debhelper devscripts dh-python python3-all python3-venv nodejs npm && \
     dpkg-buildpackage -b -us -uc"
```

## Validating debian/ without a full build (slice 5.9-a smoke)

A quick syntax check that doesn't require building:

```bash
# Parse debian/control:
dpkg-parsechangelog | head -10
# Walk debian/changelog versioning:
dpkg-parsechangelog --show-field Version

# Verify dh sequencer recognises our rules file:
dh clean --no-act  # dry-run; doesn't actually clean anything
```

If these run clean, debian/ is at minimum syntactically valid.

## Slice status

* **5.9-a — debian/ skeleton** — this slice. Four binary packages
  declared in debian/control, dh sequencer wired up in
  debian/rules, changelog at 0.1.0, copyright is Apache-2.0.
  No per-package files yet (.install / .postinst / .service);
  those land in 5.9-b/c/d.
* **5.9-b — wolf-database.deb** — bundle the wolf_database
  package into a venv at /usr/lib/wolf-database/.venv/. Wire
  install-users.sh + install.sh + the systemd unit into the
  postinst. Manage the wolf-database service user via the
  Wolf packaging conventions (not adduser/useradd direct).
* **5.9-c — wolf-server.deb** — same shape for wolf-server +
  wolf-cert + wolf-common. The two CLI shims (`wolf-server`,
  `wolf-cert`) go in /usr/bin/.
* **5.9-d — wolf-dashboard.deb** — build the Next.js standalone
  app at build time via npm; ship the built artifact under
  /usr/lib/wolf-dashboard/.
* **5.9-e — meta-package + `make smoke-deb` + Phase close-out**.
  Verifies all three packages can be built + installed in a
  clean Debian docker image + the services start.
