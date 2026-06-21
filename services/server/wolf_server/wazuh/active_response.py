"""Active-response command catalog + API body construction (Phase 6, ADR 0025).

The single source of truth for *how to invoke a Wazuh active-response command
correctly and safely* via ``PUT /active-response``.  Grounded in what the live
cluster accepts (Wazuh **v4.14.3**, probed empirically) AND in the AR script
source on GitHub — verified across **v4.14.3 and v4.14.5** (the AR sources are
identical between them except ``netsh.c``'s *internal* Windows-firewall rule
construction, which does not change the input contract).  Not assumed:

A unifying correlation runs through every default command (shared helpers in
``src/active-response/active_responses.c``): srcip-based blockers
(firewall-drop, firewalld-drop, host-deny, route-null, netsh, win_route-null,
ipfw/npf/pf, ip-customblock) all read ``parameters.alert.data.srcip`` and
validate it via ``get_ip_version`` (numeric IPv4/IPv6 only); ``disable-account``
reads ``parameters.alert.data.dstuser``; ``restart-wazuh`` reads neither.  So a
single body builder with ``srcip`` / ``username`` covers them all.  Details:

* The body accepts **only** ``command``, ``arguments``, ``alert``.  ``custom``,
  ``timeout`` and ``location`` are **rejected** (HTTP 400 "Invalid field found").
  The original 6-b write client sent ``custom`` → every run failed at the API.
* To run a *specific named* command immediately (not rule-triggered), the
  ``command`` must be **``!``-prefixed** (``!firewall-drop``).  The bare name is
  treated as a rule-driven/custom lookup; the ``!`` form runs it now.
* The target is delivered in the ``alert`` the script reads from: firewall-style
  commands read ``parameters.alert.data.srcip``; ``disable-account`` reads the
  username (``dstuser``).  ``arguments`` are extra CLI args (``extra_args``).
* The per-response **timeout is config-side** (ossec.conf
  ``<active-response><timeout>``), NOT a per-call field — Wolf can't promise an
  arbitrary "block for N seconds" through the API; it runs the configured command.
* The API returns **HTTP 200 even on failure**, with ``error: 1`` +
  ``total_failed_items`` + ``failed_items[]`` (e.g. agent offline / not found).
  "affected" means *dispatched to the agent*, NOT *applied on the host* — the
  verification read records dispatch, and is honest about that distinction.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any

# Target kinds — what input a command needs to act on.
TARGET_SRCIP = "srcip"
TARGET_USERNAME = "username"
TARGET_NONE = "none"

# OS classes Wolf reasons about (Wazuh `os.platform`/`os.uname` are messy).
OS_LINUX = "linux"
OS_WINDOWS = "windows"
OS_MACOS = "macos"


@dataclass(frozen=True)
class ARCommand:
    """One active-response command and how to invoke it."""

    name: str
    platforms: frozenset[str]
    target: str  # one of TARGET_*
    stateful: bool  # supports timeout add/delete (reversible)
    summary: str


# The default Wazuh AR commands (matches this cluster's configured <command>
# set).  Extend as the manager's command list grows; ideally reconcile against
# the live `GET /manager/configuration?section=command` in a follow-on.
AR_COMMANDS: dict[str, ARCommand] = {
    "firewall-drop": ARCommand(
        "firewall-drop", frozenset({OS_LINUX}), TARGET_SRCIP, True,
        "Block a source IP via iptables.",
    ),
    "host-deny": ARCommand(
        "host-deny", frozenset({OS_LINUX}), TARGET_SRCIP, True,
        "Add a source IP to /etc/hosts.deny.",
    ),
    "route-null": ARCommand(
        "route-null", frozenset({OS_LINUX, OS_MACOS}), TARGET_SRCIP, True,
        "Null-route a source IP.",
    ),
    "disable-account": ARCommand(
        "disable-account", frozenset({OS_LINUX, OS_MACOS}), TARGET_USERNAME, True,
        "Disable a local user account.",
    ),
    "restart-wazuh": ARCommand(
        "restart-wazuh", frozenset({OS_LINUX, OS_WINDOWS, OS_MACOS}), TARGET_NONE, False,
        "Restart the Wazuh agent.",
    ),
    "netsh": ARCommand(
        "netsh", frozenset({OS_WINDOWS}), TARGET_SRCIP, True,
        "Block a source IP via netsh (Windows).",
    ),
    "win_route-null": ARCommand(
        "win_route-null", frozenset({OS_WINDOWS}), TARGET_SRCIP, True,
        "Null-route a source IP (Windows).",
    ),
}


def get_ar_command(name: str) -> ARCommand | None:
    """Look up a command by its bare name (any leading ``!`` is ignored)."""
    return AR_COMMANDS.get(name.lstrip("!"))


def is_valid_ip(value: str) -> bool:
    """True for a syntactically valid IPv4/IPv6 address."""
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def classify_os(*signals: str | None) -> str | None:
    """Map messy Wazuh OS strings (``os.platform``/``os.uname``/``os.name``) to an
    :data:`OS_LINUX`/:data:`OS_WINDOWS`/:data:`OS_MACOS` class, or ``None`` when
    it can't be confidently determined (caller should then NOT hard-gate)."""
    blob = " ".join(s for s in signals if s).lower()
    if not blob:
        return None
    if "windows" in blob or "microsoft" in blob:
        return OS_WINDOWS
    if "darwin" in blob or "macos" in blob or "mac os" in blob:
        return OS_MACOS
    if any(
        x in blob
        for x in ("linux", "ubuntu", "centos", "debian", "red hat", "rhel",
                  "fedora", "suse", "amazon", "alma", "rocky", "oracle")
    ):
        return OS_LINUX
    return None


def build_ar_body(
    command: str,
    *,
    srcip: str | None = None,
    username: str | None = None,
    arguments: list[str] | None = None,
) -> dict[str, Any]:
    """Build the ``PUT /active-response`` body for *running a named command now*.

    Always ``!``-prefixes the command (so it runs immediately, not via a rule),
    never includes ``custom``/``timeout``/``location`` (rejected by the API), and
    attaches the target in the ``alert`` the script reads from.
    """
    name = command.lstrip("!")
    body: dict[str, Any] = {"command": f"!{name}"}
    data: dict[str, Any] = {}
    if srcip:
        data["srcip"] = srcip
    if username:
        # disable-account reads the user from the alert's `dstuser` field.
        data["dstuser"] = username
    if data:
        body["alert"] = {"data": data}
    if arguments:
        body["arguments"] = list(arguments)
    return body


def interpret_ar_result(res: Any) -> tuple[bool, dict[str, Any]]:
    """Interpret a ``PUT /active-response`` response into (dispatched, detail).

    Wazuh returns **HTTP 200 even on failure**, so success can't be read from the
    status code.  ``dispatched`` is True iff the command reached the agent
    (``total_affected_items >= 1`` AND no ``failed_items``).  This is honest:
    "dispatched to the agent" is NOT "applied on the host" — active response has
    no synchronous read-back, so the detail says so and surfaces any failure
    message (agent offline / not found, etc.) for the approver.
    """
    data = res.get("data", {}) if isinstance(res, dict) else {}
    affected = data.get("total_affected_items", 0) or 0
    failed_items = data.get("failed_items", []) or []
    dispatched = affected >= 1 and not failed_items
    detail: dict[str, Any] = {
        "dispatched": dispatched,
        "total_affected_items": affected,
        "note": "Command dispatched to the agent (not a host-applied confirmation).",
    }
    if failed_items:
        first = failed_items[0] if isinstance(failed_items, list) else {}
        err = first.get("error", {}) if isinstance(first, dict) else {}
        detail["error"] = err.get("message") if isinstance(err, dict) else str(err)
    return dispatched, detail
