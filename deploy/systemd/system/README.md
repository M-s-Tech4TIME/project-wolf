# Wolf systemd units — system-level (production)

Production-parity systemd unit files for the three Wolf
components. Phase 5.8-b.

Differences from `deploy/systemd/dev/`:

| Aspect | dev (user-level) | system (production) |
|---|---|---|
| Install path | `~/.config/systemd/user/` | `/lib/systemd/system/` |
| Process owner | the invoking user | dedicated `wolf-{database,server,dashboard}` system users |
| Data dir | `<repo>/.local/...` | `/var/lib/wolf-*/` (0750) |
| Config dir | `<repo>/...` | `/etc/wolf-*/` (0750) |
| Sockets / runtime | `<repo>/.local/...` | systemd-managed `/run/wolf-*/` |
| Hardening | minimal — runs in the operator's home | `ProtectSystem=strict`, `ProtectHome=true`, `PrivateTmp=true`, `NoNewPrivileges=true`, empty `CapabilityBoundingSet` |
| ExecStart | `python -m wolf_*` from the venv | `/usr/bin/wolf-*` shipped CLI shims (Phase 5.8-c) |

## Install (manual, today)

Phase 5.9 / 5.10 will wrap all of this in a `.deb` / `.rpm`
post-install hook. Until then, the manual sequence is:

```bash
# 1. Create the wolf group, per-component system users (nologin),
#    and the FHS dirs they need. Idempotent — safe to re-run.
sudo bash deploy/systemd/system/install-users.sh

# 2. Stash the unit files into the system unit directory.
sudo cp deploy/systemd/system/wolf-database.service /lib/systemd/system/
sudo cp deploy/systemd/system/wolf-server.service /lib/systemd/system/
sudo cp deploy/systemd/system/wolf-dashboard.service /lib/systemd/system/
sudo systemctl daemon-reload

# 3. Drop the shipped CLI shims into /usr/bin/ (Phase 5.8-c; not
#    yet present in slice 5.8-b — the units reference them but
#    won't start until 5.8-c lands).

# 4. Stash the configs each component reads at startup:
#
#    - /etc/wolf-server/env (mode 0640 wolf-server:wolf) —
#      production .env. Must include DATABASE_URL, SECRET_KEY,
#      SECRETS_FILE_PATH, SECRETS_FILE_KEY, CORS_ALLOW_ORIGINS,
#      MTLS_ALLOWED_CLIENT_CNS, etc. Format = key=value lines (no
#      `export` prefix, no comments inside values).
#
#    - /etc/wolf-dashboard/env (mode 0640 wolf-dashboard:wolf) —
#      WOLF_SERVER_URL etc.
#
#    - /etc/wolf/certs/ca-cert.pem (mode 0644 root:wolf) —
#      the shared Wolf CA. wolf-server reads this to verify
#      client cert chains; the dashboard's proxy reads it to
#      trust the server cert.

# 5. Run `wolf-database init` once (as the wolf-database user via
#    runuser or `sudo -u`) to lay down the cluster + role + db.
sudo -u wolf-database \
    WOLF_DATABASE_PRODUCTION=1 \
    /usr/bin/wolf-database init

# 6. Capture the printed DATABASE_URL from step 5; paste it into
#    /etc/wolf-server/env.

# 7. Enable + start all three components:
sudo systemctl enable --now wolf-database
sudo systemctl enable --now wolf-server
sudo systemctl enable --now wolf-dashboard
```

After step 7, all three units auto-restart on reboot, journald
captures their stderr/stdout, and `systemctl status wolf-*`
reports running state.

## Architecture — independence per ADR 0016 v3

None of the three units declare `After=`, `Requires=`, or `Wants=`
against each other. They start in parallel; ordering happens at
the application layer (`_wait_for_database()` in wolf-server's
lifespan hook, retry loops in the dashboard proxy). Same units
work on an all-in-one host AND on a distributed deployment
where wolf-database lives on a different host than wolf-server.

## Hardening rationale

Every system-level unit applies:

* `ProtectSystem=strict` — / and /usr are read-only; only
  explicitly-listed `ReadWritePaths` are writable.
* `ProtectHome=true` — /root, /home are hidden.
* `PrivateTmp=true` — /tmp is a private namespace; no symlink
  attacks via shared /tmp.
* `NoNewPrivileges=true` — even if a subprocess sets the suid
  bit, it can't escalate.
* `CapabilityBoundingSet=` (empty) — no Linux capabilities; the
  process can't bind privileged ports (Wolf uses 7860, 3000 —
  both unprivileged), can't load kernel modules, etc.
* `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6` — TCP/IPv6
  + Unix sockets only. No raw sockets, no netlink.

These match the Wazuh hardening posture documented in `docs/16-
distribution-and-packaging.md` §"Systemd hardening."

## What's NOT in slice 5.8-b

* The `/usr/bin/wolf-*` CLI shims the units reference — those are
  slice 5.8-c.
* The end-to-end "boot a Linux VM, install Wolf, log in" smoke
  test — that's slice 5.8-d.
* The `.deb` / `.rpm` packaging that wraps all of this in a
  single `apt install wolf` — Phase 5.9 / 5.10, still deferred
  to the official-release phase.

## What's safe to do today on a dev box

Running `install-users.sh` is the cleanest thing you can test
right now: it creates the users / group / dirs without committing
you to switching workflows. The `.service` files can be copied
into `/lib/systemd/system/` and validated with `systemd-analyze
verify` (syntax check), but they won't actually start because
`/usr/bin/wolf-*` doesn't exist yet (5.8-c).

Full production install path becomes safe + supported once 5.8-c
+ 5.8-d ship.
