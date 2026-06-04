#!/usr/bin/env bash
# install-users.sh — Phase 5.8-b.
#
# Idempotent one-shot script that creates the system users, group,
# and FHS directories the Wolf systemd units expect. Run once with
# root (typically from a .deb / .rpm post-install hook in Phase
# 5.9 / 5.10; manual sudo invocation today).
#
# What it creates:
#
#   * Group: `wolf` (shared — every Wolf component user joins it)
#   * Users (all `nologin`, no home dir, in `wolf` group):
#       - wolf-database   (Postgres lifecycle)
#       - wolf-server     (FastAPI agent loop)
#       - wolf-dashboard  (Next.js edge)
#       - wolf-gateway    (Phase 6 propose/execute — user reserved
#                          ahead so Phase 6 can land its unit without
#                          a separate user-creation step)
#   * FHS directories:
#       - /var/lib/wolf-database/{data,config}     (0750 wolf-database:wolf)
#       - /var/lib/wolf-server                     (0750 wolf-server:wolf)
#       - /var/lib/wolf-dashboard                  (0750 wolf-dashboard:wolf)
#       - /etc/wolf-database                       (0750 wolf-database:wolf)
#       - /etc/wolf-server                         (0750 wolf-server:wolf)
#       - /etc/wolf-dashboard                      (0750 wolf-dashboard:wolf)
#       - /etc/wolf/certs                          (0750 root:wolf — shared
#                                                   CA cert lives here;
#                                                   group-readable so each
#                                                   component can validate
#                                                   peer certs)
#
# Run as: `sudo bash install-users.sh`
#
# Re-running is safe — every step is idempotent. If a user already
# exists, we leave it alone (no password reset, no shell change).
# If a directory already exists, we re-assert ownership + mode but
# don't touch contents.

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
    echo "FAIL: install-users.sh must run as root (sudo)." >&2
    exit 2
fi

WOLF_GROUP="wolf"
COMPONENTS=("wolf-database" "wolf-server" "wolf-dashboard" "wolf-gateway")

# ─── Group ───────────────────────────────────────────────────────────────────

if getent group "${WOLF_GROUP}" >/dev/null; then
    echo "  group ${WOLF_GROUP} already exists; leaving alone"
else
    groupadd --system "${WOLF_GROUP}"
    echo "  created group ${WOLF_GROUP}"
fi

# ─── Users ───────────────────────────────────────────────────────────────────

for user in "${COMPONENTS[@]}"; do
    if id -u "${user}" >/dev/null 2>&1; then
        echo "  user ${user} already exists; leaving alone"
        # Defensive: re-ensure they're in the wolf group (handles
        # the case where the user was created out-of-band).
        usermod --append --groups "${WOLF_GROUP}" "${user}" || true
    else
        useradd \
            --system \
            --gid "${WOLF_GROUP}" \
            --home-dir "/nonexistent" \
            --no-create-home \
            --shell "/usr/sbin/nologin" \
            --comment "Wolf ${user#wolf-} component" \
            "${user}"
        echo "  created user ${user} (nologin, in ${WOLF_GROUP} group)"
    fi
done

# ─── FHS directories ────────────────────────────────────────────────────────

# Each entry: PATH MODE OWNER:GROUP
DIRS=(
    "/var/lib/wolf-database/data    0750 wolf-database:wolf"
    "/var/lib/wolf-database/config  0750 wolf-database:wolf"
    "/var/lib/wolf-server           0750 wolf-server:wolf"
    "/var/lib/wolf-dashboard        0750 wolf-dashboard:wolf"
    "/etc/wolf-database             0750 wolf-database:wolf"
    "/etc/wolf-server               0750 wolf-server:wolf"
    "/etc/wolf-dashboard            0750 wolf-dashboard:wolf"
    "/etc/wolf/certs                0750 root:wolf"
)

for entry in "${DIRS[@]}"; do
    # shellcheck disable=SC2086
    set -- ${entry}
    path="$1"
    mode="$2"
    owner="$3"

    mkdir -p "${path}"
    chown "${owner}" "${path}"
    chmod "${mode}" "${path}"
    echo "  ensured ${path} (mode ${mode} ${owner})"
done

echo ""
echo "Done. Next steps:"
echo "  1. Install the three .service files into /lib/systemd/system/:"
echo "       sudo cp deploy/systemd/system/wolf-*.service /lib/systemd/system/"
echo "       sudo systemctl daemon-reload"
echo "  2. Drop the shipped CLIs into /usr/bin/ (Phase 5.8-c)."
echo "  3. Stash configs into the new /etc/wolf-*/ dirs:"
echo "       - /etc/wolf-server/env       (production .env equivalent)"
echo "       - /etc/wolf-dashboard/env    (WOLF_SERVER_URL etc)"
echo "       - /etc/wolf/certs/ca-cert.pem  (the Wolf CA, group-readable)"
echo "  4. Enable + start each component:"
echo "       sudo systemctl enable --now wolf-database wolf-server wolf-dashboard"
