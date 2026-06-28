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
import re
from dataclasses import dataclass
from typing import Any

# Target kinds — what input a command needs to act on.
TARGET_SRCIP = "srcip"
TARGET_USERNAME = "username"
TARGET_NONE = "none"

# OS classes Wolf reasons about (Wazuh `os.platform`/`os.uname` are messy).
# BSD is split per-OS (6-c.2a) because each has a *different* default firewall:
# FreeBSD/OpenBSD → pf, NetBSD → npf, and OPNsense/pfSense appliances → opnsense-fw
# (they are FreeBSD-based but manage pf via their own config, so stock pf no-ops).
OS_LINUX = "linux"
OS_WINDOWS = "windows"
OS_MACOS = "macos"
OS_FREEBSD = "freebsd"
OS_OPENBSD = "openbsd"
OS_NETBSD = "netbsd"
OS_OPNSENSE = "opnsense"  # OPNsense / pfSense firewall appliances (FreeBSD-based)

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
    reversible: bool  # has a delete-inverse (an undo) — see ``reverses_via``
    severity: str  # base impact: SEV_LOW | SEV_MEDIUM | SEV_HIGH
    summary: str
    # How the undo works: the script's DELETE_COMMAND inverse (ADR 0028), grounded
    # in the Wazuh AR source. Non-empty IFF ``reversible`` (enforced by tests).
    # NOTE: the inverse only runs *on the host* — the Server API cannot dispatch a
    # `delete` (execd always rewrites a fresh call to `add`), so the physical
    # reversal is wolf-pack-bound (Phase 12); this string records *what* it does.
    reverses_via: str = ""


# The default Wazuh AR commands. Grounded against this cluster's manager command
# set during 6-c.1/6-c.2a design — NOT read at runtime: an active response is just
# a `PUT /active-response` Server API call, and Wolf reports whatever the API
# returns (no manager-config presence check — ADR 0027 §2). Severity is the
# command's *base* impact (block = high; disable a user = medium; restart = low).
# Each command lists the OS classes it actually runs on (6-c.2a per-BSD-OS split).
AR_COMMANDS: dict[str, ARCommand] = {
    "firewall-drop": ARCommand(
        "firewall-drop", frozenset({OS_LINUX}), TARGET_SRCIP, True, SEV_HIGH,
        "Block a source IP via iptables.",
        reverses_via="iptables -D on INPUT and FORWARD removes the DROP rule for the IP.",
    ),
    "host-deny": ARCommand(
        "host-deny", frozenset({OS_LINUX}), TARGET_SRCIP, True, SEV_HIGH,
        "Add a source IP to /etc/hosts.deny.",
        reverses_via="Removes the 'ALL:<ip>' line from /etc/hosts.deny.",
    ),
    "route-null": ARCommand(
        "route-null", frozenset({OS_LINUX, OS_MACOS}), TARGET_SRCIP, True, SEV_HIGH,
        "Null-route a source IP.",
        reverses_via="route del/delete <ip> removes the null/blackhole route.",
    ),
    "disable-account": ARCommand(
        "disable-account", frozenset({OS_LINUX, OS_MACOS}), TARGET_USERNAME, True, SEV_MEDIUM,
        "Disable a local user account.",
        reverses_via="passwd -u unlocks the account (AIX: chuser account_locked=false).",
    ),
    "restart-wazuh": ARCommand(
        "restart-wazuh",
        frozenset({OS_LINUX, OS_WINDOWS, OS_MACOS, OS_FREEBSD, OS_OPENBSD, OS_NETBSD, OS_OPNSENSE}),
        TARGET_NONE, False, SEV_LOW, "Restart the Wazuh agent.",
        # Not reversible: a one-shot restart leaves no enforcement state to undo.
    ),
    "netsh": ARCommand(
        "netsh", frozenset({OS_WINDOWS}), TARGET_SRCIP, True, SEV_HIGH,
        "Block a source IP via netsh (Windows).",
        reverses_via="netsh advfirewall firewall delete rule removes the in/out block rule.",
    ),
    "win_route-null": ARCommand(
        "win_route-null", frozenset({OS_WINDOWS}), TARGET_SRCIP, True, SEV_HIGH,
        "Null-route a source IP (Windows).",
        reverses_via="route DELETE <ip> removes the null route (Windows).",
    ),
    "pf": ARCommand(
        "pf", frozenset({OS_FREEBSD, OS_OPENBSD, OS_MACOS}), TARGET_SRCIP, True, SEV_HIGH,
        "Block a source IP via the pf packet filter (FreeBSD/OpenBSD/macOS ≥ 10.7).",
        reverses_via="pfctl -t wazuh_fwtable -T delete <ip> removes the IP from the pf table.",
    ),
    "ipfw": ARCommand(
        "ipfw", frozenset({OS_FREEBSD, OS_MACOS}), TARGET_SRCIP, True, SEV_HIGH,
        "Block a source IP via ipfw (legacy FreeBSD < 5.3 / macOS ≤ 10.6).",
        reverses_via="ipfw table delete removes the IP from the deny table.",
    ),
    "npf": ARCommand(
        "npf", frozenset({OS_NETBSD}), TARGET_SRCIP, True, SEV_HIGH,
        "Block a source IP via npf (NetBSD).",
        reverses_via="npfctl removes the IP from the block table.",
    ),
    "opnsense-fw": ARCommand(
        "opnsense-fw", frozenset({OS_OPNSENSE}), TARGET_SRCIP, True, SEV_HIGH,
        "Block a source IP via opnsense-fw (OPNsense/pfSense appliance firewall).",
        reverses_via="pfctl -t __wazuh_agent_drop -T delete <ip> removes the IP from the table.",
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
# not guess a platform).  The chosen command is the platform-correct default per
# OS; selecting a *method* within an intent (e.g. host-deny vs firewall-drop) is
# the 6-c.2b `method` override.  block_ip on FreeBSD/macOS resolves to `pf` but
# falls back to `ipfw` on versions predating pf (`_predates_pf`).  Every command
# here is asserted to exist in AR_COMMANDS and to platform-fit its OS by
# test_active_response (the catalog stays the source of truth).
_INTENT_COMMANDS: dict[str, str | dict[str, str]] = {
    INTENT_RESTART: "restart-wazuh",  # OS-agnostic — runs on every platform
    INTENT_BLOCK_IP: {
        OS_LINUX: "firewall-drop",
        OS_WINDOWS: "netsh",
        OS_MACOS: "pf",  # ≥ 10.7 (Lion); ipfw on ≤ 10.6 via the version gate
        OS_FREEBSD: "pf",  # ≥ 5.3; ipfw on < 5.3 via the version gate
        OS_OPENBSD: "pf",  # pf is native to OpenBSD
        OS_NETBSD: "npf",  # NetBSD's native filter
        OS_OPNSENSE: "opnsense-fw",  # appliance — stock pf doesn't apply (ADR 0027 §4)
    },
    INTENT_DISABLE_USER: {
        # No default disable-account AR ships for Windows / BSD appliances — left
        # unmapped so disable_user there is refused with a clear reason.
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

# The target kind each intent acts on — used to validate a `method` override is
# consistent with the intent (6-c.2b): you can't block_ip with a username command.
INTENT_TARGETS: dict[str, str] = {
    INTENT_BLOCK_IP: TARGET_SRCIP,
    INTENT_DISABLE_USER: TARGET_USERNAME,
    INTENT_RESTART: TARGET_NONE,
}


@dataclass(frozen=True)
class IntentResolution:
    """Outcome of mapping a high-level intent + resolved OS → a concrete command."""

    ok: bool
    command: str = ""
    reason: str = ""


def _predates_pf(os_class: str, signal: str | None) -> bool:
    """True only when the OS version is *confidently* older than pf's arrival
    (FreeBSD 5.3, Nov 2004 / macOS 10.7 "Lion", 2011), so block_ip must fall back
    to ``ipfw``.  Best-effort + fail-safe: an unparseable or modern version → False
    (use ``pf``).  No modern agent predates 2004/2011, so this is a rare legacy
    path — but it keeps the per-OS mapping honest (ADR 0027 §4)."""
    if not signal:
        return False
    s = signal.lower()
    if os_class == OS_FREEBSD:
        # FreeBSD versions look like "14.3-RELEASE", "4.11-RELEASE", "5.2.1-STABLE".
        m = re.search(r"(\d+)\.(\d+)[\d.]*-(?:release|stable|current|p\d)", s)
        if not m:
            return False
        return (int(m.group(1)), int(m.group(2))) < (5, 3)
    if os_class == OS_MACOS:
        # Classic "10.6" / "Mac OS X 10.6.8"; Darwin 11 == macOS 10.7.
        m = re.search(r"(?:mac ?os(?: ?x)?|macos)\D*(\d+)\.(\d+)", s)
        if m:
            return (int(m.group(1)), int(m.group(2))) < (10, 7)
        d = re.search(r"darwin\D*(\d+)", s)
        if d:
            return int(d.group(1)) < 11
    return False


def resolve_intent_command(
    intent: str, os_class: str | None, os_signal: str | None = None
) -> IntentResolution:
    """Deterministically select the platform-correct AR command for ``intent`` on
    an agent of the given OS class (``os_class`` is :func:`classify_os` of the live
    OS signal; pass the raw ``os_signal`` too so the FreeBSD/macOS pf↔ipfw version
    gate can run).

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
    # Legacy version gate: pf only exists from FreeBSD 5.3 / macOS 10.7 — older
    # hosts block via ipfw instead.
    if command == "pf" and _predates_pf(os_class, os_signal):
        command = "ipfw"
    return IntentResolution(ok=True, command=command)


def resolve_method_command(
    intent: str, method: str, os_class: str | None
) -> IntentResolution:
    """Resolve an explicit caller-chosen ``method`` (a specific catalog command)
    for ``intent`` — the 6-c.2b override + OS-unknown user-guided failover.

    Validates the method ∈ catalog, that its target matches the intent (you can't
    ``block_ip`` with a username command), and — when the OS is KNOWN — that the
    method platform-fits.  An UNKNOWN OS is allowed: this is the user-guided
    failover, where Wolf proceeds on the requester's asserted platform and the
    caller annotates the proposal (human approval remains the gate).
    """
    cmd = get_ar_command(method)
    if cmd is None:
        return IntentResolution(
            ok=False,
            reason=(
                f"Unknown active-response command {method!r}. Choose one of "
                f"{', '.join(sorted(AR_COMMANDS))}, or omit `method` to let Wolf "
                "select automatically."
            ),
        )
    intent_target = INTENT_TARGETS.get(intent)
    if intent_target is None:
        return IntentResolution(
            ok=False,
            reason=(
                f"Unknown action intent {intent!r}. Supported intents: "
                f"{', '.join(sorted(AR_INTENTS))}."
            ),
        )
    if cmd.target != intent_target:
        return IntentResolution(
            ok=False,
            reason=(
                f"Method {method!r} does not match intent {intent!r}: it acts on "
                f"{cmd.target!r}, but {intent!r} needs a {intent_target!r} command."
            ),
        )
    if os_class is not None and os_class not in cmd.platforms:
        return IntentResolution(
            ok=False,
            reason=(
                f"Method {method!r} runs on {'/'.join(sorted(cmd.platforms))}, but "
                f"the agent is {os_class}. Pick a {os_class}-compatible command."
            ),
        )
    return IntentResolution(ok=True, command=cmd.name)


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
    # macOS first — Darwin is BSD-derived but its default firewall differs.
    if "darwin" in blob or "macos" in blob or "mac os" in blob:
        return OS_MACOS
    # OPNsense/pfSense are FreeBSD-based appliances — detect BEFORE generic FreeBSD
    # (their uname says `FreeBSD`, but their AR command is opnsense-fw, not pf).
    if "opnsense" in blob or "pfsense" in blob:
        return OS_OPNSENSE
    if "freebsd" in blob:
        return OS_FREEBSD
    if "openbsd" in blob:
        return OS_OPENBSD
    if "netbsd" in blob:
        return OS_NETBSD
    # Bare "bsd" with no specific marker → FreeBSD (the common case; pf is correct
    # for FreeBSD AND OpenBSD, and NetBSD always reports "netbsd").
    if "bsd" in blob:
        return OS_FREEBSD
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
