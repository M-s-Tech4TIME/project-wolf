# Wolf shipped-CLI shims

Production `/usr/bin/wolf-*` entry-point scripts. Phase 5.8-c.

These are the operator-facing commands that show up on `$PATH`
after a production install — `wolf-cert init`, `wolf-database
start`, etc. They're thin shell wrappers around the Python venv
(or the Next.js standalone build for wolf-dashboard) that the
.deb / .rpm packages drop at `/usr/lib/wolf-*/`.

## Files

| Shim | Targets | Underlying command |
|---|---|---|
| `wolf-cert` | `/usr/lib/wolf-server/.venv/bin/wolf-cert` | `wolf_cert.cli:main` (console script) |
| `wolf-database` | `/usr/lib/wolf-database/.venv/bin/wolf-database` | `wolf_database.cli:main` (console script) |
| `wolf-server` | `/usr/lib/wolf-server/.venv/bin/python -m wolf_server` | the Phase 5.4-c launcher |
| `wolf-dashboard` | `/usr/lib/wolf-dashboard/.next/standalone/server.js` (via node) | Next.js standalone server |

Why wolf-cert + wolf-server share one venv: wolf-cert is a wolf-
server dependency at the Python-package level, and shipping two
venvs that both contain the cryptography library wastes ~30 MB
per install. wolf-database gets its own venv because it has zero
Python dependency overlap with wolf-server (no FastAPI,
SQLAlchemy, asyncpg) — keeping them separate means a wolf-
database security update doesn't rebuild wolf-server's venv.

## What slice 5.8-c ships vs what comes later

This slice ships:

* The four shim scripts (this dir).
* `install.sh` — drops the shims into `/usr/bin/` and creates
  empty `/usr/lib/wolf-*/` dirs (Phase 5.9/5.10 .deb post-install
  populates them).

What Phase 5.9 / 5.10 will add:

* The actual `.deb` / `.rpm` packages that:
  - Create the Python venv at `/usr/lib/wolf-server/.venv/` and
    `pip install wolf-server wolf-cert wolf-common` into it.
  - Create `/usr/lib/wolf-database/.venv/` and `pip install
    wolf-database` into it.
  - Run `npm run build` for wolf-dashboard in
    `/usr/lib/wolf-dashboard/` with `output: "standalone"` so
    `.next/standalone/server.js` exists.

Until that lands, the shims are CORRECT but their target venvs
don't exist — invoking `/usr/bin/wolf-database` after this slice
prints a helpful "not installed; install the package first"
message and exits 2.

## Install (manual, today)

```bash
# Drops the four shims into /usr/bin/ and creates the empty
# /usr/lib/wolf-*/ dirs. Idempotent.
sudo bash deploy/bin/install.sh

# Verify:
ls -la /usr/bin/wolf-*
# expect: 4 shims, mode 0755 root:root

# Try one — should fail-loud with the install hint:
/usr/bin/wolf-database --help
# expect:
#   FAIL: wolf-database not installed at /usr/lib/wolf-database/.venv/bin/wolf-database.
#     Install the wolf-database package (apt install wolf-database)
#     or run from a dev workspace via:
#       uv run --project services/server python -m wolf_database "$@"
```

This last step verifies the shim is syntactically valid + on
PATH + the fail-loud branch fires when the venv doesn't exist.

## Pair with install-users.sh

`deploy/systemd/system/install-users.sh` (Phase 5.8-b) creates
the wolf group, per-component system users, and the FHS data /
config dirs. The two scripts touch disjoint paths so order
doesn't matter:

```bash
sudo bash deploy/systemd/system/install-users.sh   # /var/lib/, /etc/
sudo bash deploy/bin/install.sh                    # /usr/bin/, /usr/lib/
```

After both have run + the systemd units are copied to
`/lib/systemd/system/`, an operator could in principle:

```bash
sudo systemctl daemon-reload
sudo systemctl start wolf-database
# → still fails: /usr/lib/wolf-database/.venv doesn't exist yet
```

That last step works after Phase 5.9 / 5.10 ships the .deb.

## Operator override

`wolf-dashboard` honors `WOLF_DASHBOARD_NODE` so operators with
node at a non-standard location (nvm, custom build, etc.) can
point the shim at it without editing /usr/bin/. The other three
shims have no overrides today — the venv path is hard-coded to
`/usr/lib/wolf-{server,database}/.venv/`.
