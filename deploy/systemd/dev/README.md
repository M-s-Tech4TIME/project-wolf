# Wolf systemd units — dev (user-level)

User-level systemd unit templates for running Wolf components on a
single dev box without root. Phase 5.8-a.

These are **templates**: `@REPO_ROOT@` is substituted with the
operator's actual repo path at install time by
`make install-user-systemd` in the top-level Makefile.

## Why user-level

`systemctl --user enable wolf-*` runs the units as the operator's
own user, against the dev workspace (`<repo>/.local/wolf-database/`,
`<repo>/.local/certs/`, etc.). No root required, no service-user
setup, no FHS paths. Trades production parity for zero-friction
dev. The system-level units in `deploy/systemd/system/` (Phase
5.8-b) are the production-parity flavour.

Per ADR 0016 v3, every Wolf systemd unit is **fully independent**
— no `After=`, no `Requires=`, no `Wants=` between Wolf services.
wolf-server handles a wolf-database that's still coming up via an
app-level retry loop in its lifespan hook (Phase 5.8-a as well).
This keeps the same units usable in distributed deployments where
wolf-database lives on a different host.

## Install (one-time per box)

From the repo root:

```bash
make install-user-systemd
```

That copies the templates into `~/.config/systemd/user/` with
`@REPO_ROOT@` substituted for the current `$PWD`, then runs
`systemctl --user daemon-reload`.

## Enable persistent operation

```bash
# For wolf-database, after `make wolf-database-init`:
systemctl --user enable --now wolf-database

# So the user session stays alive across logout / SSH disconnect
# (needed for headless servers; not strictly needed if you stay
# logged in to a desktop session):
loginctl enable-linger $USER
```

After that, `systemctl --user start/stop/restart/status wolf-database`
is the day-to-day lifecycle.

## Files

* `wolf-database.service` — wraps `python -m wolf_database start`
* `wolf-server.service` — wraps `python -m wolf_server`
* `wolf-dashboard.service` — wraps `npm run dev` in services/dashboard

Each is a `Type=forking` unit (or `Type=simple` for wolf-server,
which runs uvicorn in the foreground), with `Restart=on-failure`
and a 5-second restart delay. Hardening directives like
`ProtectSystem=strict` are deferred to the system-level units
where they make sense; user-level units run inside the operator's
home and a too-strict `ProtectSystem` would block legitimate paths.
