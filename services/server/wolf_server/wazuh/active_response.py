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
# `bsd` covers FreeBSD/OpenBSD/NetBSD agents (incl. OPNsense/pfSense firewalls,
# which Wazuh reports as `os.platform='bsd'`, uname `FreeBSD …`).
OS_LINUX = "linux"
OS_WINDOWS = "windows"
OS_MACOS = "macos"
OS_BSD = "bsd"

# Severity tiers — the *base* impact of an action (escalated by context in
# wolf_server.gateway.proposals.compute_severity).
SEV_LOW = "low"
SEV_MEDIUM = "medium"
SEV_HIGH = "high"


@dataclass(frozen=True)
class ARCommand:
    """One active-response command and how to invoke it."""

    name: str
    platforms: frozenset[str]
    target: str  # one of TARGET_*
    stateful: bool  # supports timeout add/delete (reversible)
    severity: str  # base impact: SEV_LOW | SEV_MEDIUM | SEV_HIGH
    summary: str


# The default Wazuh AR commands.  Verified against this cluster's live
# `GET /manager/configuration?section=command` (2026-06-22): the manager has
# firewall-drop, host-deny, route-null, disable-account, restart-wazuh, netsh,
# win_route-null AND the BSD blockers pf / ipfw / npf configured.  Severity is
# the command's *base* impact (block = high — network enforcement with collateral
# risk; disable a user = medium; restart = low).  Extend as the command list
# grows; a live reconciliation against the manager config is a tracked follow-on.
AR_COMMANDS: dict[str, ARCommand] = {
    "firewall-drop": ARCommand(
        "firewall-drop", frozenset({OS_LINUX}), TARGET_SRCIP, True, SEV_HIGH,
        "Block a source IP via iptables.",
    ),
    "host-deny": ARCommand(
        "host-deny", frozenset({OS_LINUX}), TARGET_SRCIP, True, SEV_HIGH,
        "Add a source IP to /etc/hosts.deny.",
    ),
    "route-null": ARCommand(
        "route-null", frozenset({OS_LINUX, OS_MACOS}), TARGET_SRCIP, True, SEV_HIGH,
        "Null-route a source IP.",
    ),
    "disable-account": ARCommand(
        "disable-account", frozenset({OS_LINUX, OS_MACOS}), TARGET_USERNAME, True, SEV_MEDIUM,
        "Disable a local user account.",
    ),
    "restart-wazuh": ARCommand(
        "restart-wazuh", frozenset({OS_LINUX, OS_WINDOWS, OS_MACOS, OS_BSD}), TARGET_NONE, False,
        SEV_LOW, "Restart the Wazuh agent.",
    ),
    "netsh": ARCommand(
        "netsh", frozenset({OS_WINDOWS}), TARGET_SRCIP, True, SEV_HIGH,
        "Block a source IP via netsh (Windows).",
    ),
    "win_route-null": ARCommand(
        "win_route-null", frozenset({OS_WINDOWS}), TARGET_SRCIP, True, SEV_HIGH,
        "Null-route a source IP (Windows).",
    ),
    "pf": ARCommand(
        "pf", frozenset({OS_BSD, OS_MACOS}), TARGET_SRCIP, True, SEV_HIGH,
        "Block a source IP via the pf packet filter (BSD/macOS — incl. OPNsense/pfSense).",
    ),
    "ipfw": ARCommand(
        "ipfw", frozenset({OS_BSD}), TARGET_SRCIP, True, SEV_HIGH,
        "Block a source IP via ipfw (FreeBSD).",
    ),
    "npf": ARCommand(
        "npf", frozenset({OS_BSD}), TARGET_SRCIP, True, SEV_HIGH,
        "Block a source IP via npf (NetBSD).",
    ),
}


def get_ar_command(name: str) -> ARCommand | None:
    """Look up a command by its bare name (any leading ``!`` is ignored)."""
    return AR_COMMANDS.get(name.lstrip("!"))


# ── Intent → platform-correct command (slice 6-c) ────────────────────────────
#
# The model expresses a high-level INTENT (block an IP / disable a user / restart
# the agent); Wolf — not the model — resolves the agent's OS and deterministically
# selects the platform-correct command from the catalog.  This removes the whole
# "model picked netsh on a Linux host" failure class (6-b.1's lenient validator
# refusal was the *safety* net; this is the *smart selection* that makes a wrong
# pick impossible in the first place).
INTENT_BLOCK_IP = "block_ip"
INTENT_DISABLE_USER = "disable_user"
INTENT_RESTART = "restart"

# Per-intent command selection.  A **string** value is OS-agnostic (the same
# command on every platform — restart).  A **dict** value is OS-SPECIFIC: only
# the listed OS classes are supported, and a resolved OS is REQUIRED (Wolf will
# not guess a platform).  The chosen command is always one platform-paired
# default per OS (firewall-drop↔netsh; route-null on macOS); selecting a
# *method* within an intent (e.g. host-deny vs firewall-drop) is a tracked
# follow-on, not v1.  Every command here is asserted to exist in AR_COMMANDS and
# to platform-fit its OS by test_active_response (the catalog stays the source
# of truth).
_INTENT_COMMANDS: dict[str, str | dict[str, str]] = {
    INTENT_RESTART: "restart-wazuh",  # OS-agnostic — runs on every platform
    INTENT_BLOCK_IP: {
        OS_LINUX: "firewall-drop",
        OS_WINDOWS: "netsh",
        OS_MACOS: "route-null",
        OS_BSD: "pf",  # FreeBSD/OpenBSD incl. OPNsense (pfctl); manager has pf configured
    },
    INTENT_DISABLE_USER: {
        # No default disable-account AR ships for Windows — left unmapped so
        # disable_user on a Windows agent is refused with a clear reason.
        OS_LINUX: "disable-account",
        OS_MACOS: "disable-account",
    },
}

# The intents the propose tool exposes to the model (source of truth = the table).
AR_INTENTS = frozenset(_INTENT_COMMANDS)

# Human-readable phrasing for the approver-facing summary / expected-effect.
INTENT_LABELS: dict[str, str] = {
    INTENT_BLOCK_IP: "Block source IP",
    INTENT_DISABLE_USER: "Disable account",
    INTENT_RESTART: "Restart the Wazuh agent",
}


@dataclass(frozen=True)
class IntentResolution:
    """Outcome of mapping a high-level intent + resolved OS → a concrete command."""

    ok: bool
    command: str = ""
    reason: str = ""


def resolve_intent_command(intent: str, os_class: str | None) -> IntentResolution:
    """Deterministically select the platform-correct AR command for ``intent`` on
    an agent of the given :data:`OS_LINUX`/:data:`OS_WINDOWS`/:data:`OS_MACOS`
    class (``os_class`` is :func:`classify_os` applied to the live OS signal).

    - **OS-agnostic** intents (restart) resolve even when the OS is unknown.
    - **OS-specific** intents (block_ip / disable_user) REQUIRE a resolved OS: an
      unknown OS is refused with guidance (Wolf never guesses a platform), and an
      intent that has no command for the resolved OS (e.g. disable_user on
      Windows) is refused with a clear, actionable reason.

    A refusal here keeps a doomed/ambiguous dispatch out of the approval queue.
    """
    entry = _INTENT_COMMANDS.get(intent)
    if entry is None:
        return IntentResolution(
            ok=False,
            reason=(
                f"Unknown action intent {intent!r}. Supported intents: "
                f"{', '.join(sorted(AR_INTENTS))}."
            ),
        )
    if isinstance(entry, str):  # OS-agnostic — same command on every platform
        return IntentResolution(ok=True, command=entry)
    if os_class is None:
        return IntentResolution(
            ok=False,
            reason=(
                f"Cannot select a platform-correct command for intent {intent!r}: "
                "the agent's operating system could not be determined. Resolve the "
                "agent's OS (get_agent_detail) and retry."
            ),
        )
    command = entry.get(os_class)
    if command is None:
        supported = ", ".join(sorted(entry)) or "none"
        return IntentResolution(
            ok=False,
            reason=(
                f"Intent {intent!r} is not supported on {os_class} via the default "
                f"active-response catalog (supported: {supported})."
            ),
        )
    return IntentResolution(ok=True, command=command)


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
    # macOS first — Darwin is BSD-derived but pf/route-null differ from FreeBSD.
    if "darwin" in blob or "macos" in blob or "mac os" in blob:
        return OS_MACOS
    # "bsd" catches free/open/net-bsd and Wazuh's literal os.platform='bsd';
    # opnsense/pfsense are FreeBSD-based firewall appliances.
    if any(x in blob for x in ("bsd", "opnsense", "pfsense")):
        return OS_BSD
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
