#!/usr/bin/env bash
# install.sh — Phase 5.8-c.
#
# Installs the four shipped Wolf CLI shims to /usr/bin/ and creates
# the /usr/lib/wolf-*/ directories where the .deb / .rpm packages
# (Phase 5.9 / 5.10) will drop their Python venvs + Next.js
# standalone build.
#
# This script is the *plumbing* slice 5.8-c ships; Phase 5.9/5.10's
# packaging is what actually populates the venvs and standalone
# build inside the dirs we create here.
#
# What it creates:
#
#   * /usr/bin/wolf-cert       (mode 0755 root:root — shim → wolf-server venv)
#   * /usr/bin/wolf-database   (mode 0755 root:root — shim → wolf-database venv)
#   * /usr/bin/wolf-server     (mode 0755 root:root — shim → wolf-server venv)
#   * /usr/bin/wolf-dashboard  (mode 0755 root:root — shim → Next.js standalone)
#   * /usr/lib/wolf-database   (mode 0755 root:root — packaged code lands here)
#   * /usr/lib/wolf-server     (mode 0755 root:root — packaged code lands here)
#   * /usr/lib/wolf-dashboard  (mode 0755 root:root — packaged code lands here)
#
# Run as: `sudo bash install.sh` (or `sudo bash deploy/bin/install.sh`
# from the repo root). Idempotent — safe to re-run; existing files
# get overwritten with the latest shim contents.
#
# Pair with deploy/systemd/system/install-users.sh (creates wolf
# group + per-component users + /var/lib + /etc dirs) for the full
# pre-packaging install plumbing. Order doesn't matter; the two
# scripts touch disjoint paths.

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
    echo "FAIL: install.sh must run as root (sudo)." >&2
    exit 2
fi

# Resolve the repo root from this script's location, so the script
# works whether the operator invokes it from the repo root or via
# `sudo bash deploy/bin/install.sh` from anywhere.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHIMS=("wolf-cert" "wolf-database" "wolf-server" "wolf-dashboard")
LIB_DIRS=("wolf-database" "wolf-server" "wolf-dashboard")

# Where the shims go (operator-facing — these paths show up in
# systemd unit ExecStart=, in shell completions, in --help text).
TARGET_BIN_DIR="${TARGET_BIN_DIR:-/usr/bin}"
TARGET_LIB_DIR="${TARGET_LIB_DIR:-/usr/lib}"

# ─── Install shims ──────────────────────────────────────────────────────────

for shim in "${SHIMS[@]}"; do
    src="${SCRIPT_DIR}/${shim}"
    dst="${TARGET_BIN_DIR}/${shim}"
    if [[ ! -f "${src}" ]]; then
        echo "FAIL: source shim ${src} missing — slice 5.8-c packaging bug." >&2
        exit 2
    fi
    install -m 0755 -o root -g root "${src}" "${dst}"
    echo "  installed: ${dst}"
done

# ─── Create /usr/lib/wolf-* dirs ───────────────────────────────────────────

for lib in "${LIB_DIRS[@]}"; do
    path="${TARGET_LIB_DIR}/${lib}"
    mkdir -p "${path}"
    chown root:root "${path}"
    chmod 0755 "${path}"
    echo "  ensured ${path} (empty; packaging will populate)"
done

echo ""
echo "Done. Shims installed; /usr/lib/wolf-*/ dirs created."
echo ""
echo "These dirs are empty after this script. Phase 5.9 / 5.10 .deb"
echo "package post-install will:"
echo "  1. Create a Python venv at /usr/lib/wolf-server/.venv"
echo "     and install the wolf-server + wolf-cert + wolf-common packages."
echo "  2. Create a Python venv at /usr/lib/wolf-database/.venv"
echo "     and install the wolf-database package."
echo "  3. Run \`npm run build\` for wolf-dashboard with"
echo "     output: \"standalone\" — drops .next/standalone/server.js"
echo "     at /usr/lib/wolf-dashboard/.next/standalone/."
echo ""
echo "Until then, the shims correctly detect the missing venvs and"
echo "exit with a helpful error message naming the install path."
