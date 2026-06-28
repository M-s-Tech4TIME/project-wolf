"""Active-response catalog + body construction (Phase 6, 6-b.1).

Grounded in the live Wazuh v4.14.3 API + the actual AR script source
(get_srcip_from_json → parameters.alert.data.srcip; get_username_from_json →
parameters.alert.data.dstuser; get_ip_version → numeric IPv4/IPv6 only). The
body must NOT carry `custom` (the bug that broke every run), must `!`-prefix the
command (run it now), and the result interpreter must treat HTTP-200-with-
failed_items as a failure.
"""

from wolf_server.wazuh.active_response import (
    _INTENT_COMMANDS,
    AR_COMMANDS,
    AR_INTENTS,
    INTENT_BLOCK_IP,
    INTENT_DISABLE_USER,
    INTENT_RESTART,
    OS_FREEBSD,
    OS_LINUX,
    OS_MACOS,
    OS_NETBSD,
    OS_OPENBSD,
    OS_OPNSENSE,
    OS_WINDOWS,
    SEV_HIGH,
    SEV_LOW,
    SEV_MEDIUM,
    build_ar_body,
    classify_os,
    get_ar_command,
    interpret_ar_result,
    is_valid_ip,
    resolve_intent_command,
    resolve_method_command,
)

# ── build_ar_body ────────────────────────────────────────────────────────────


def test_body_prefixes_command_and_omits_custom() -> None:
    body = build_ar_body("firewall-drop", srcip="203.0.113.7")
    assert body["command"] == "!firewall-drop"  # run-now prefix
    assert "custom" not in body  # the 4.14.3 bug — rejected by the API
    assert "timeout" not in body
    assert body["alert"] == {"data": {"srcip": "203.0.113.7"}}


def test_body_does_not_double_prefix() -> None:
    assert build_ar_body("!firewall-drop", srcip="1.2.3.4")["command"] == "!firewall-drop"


def test_body_disable_account_uses_dstuser() -> None:
    # The disable-account script reads parameters.alert.data.dstuser.
    body = build_ar_body("disable-account", username="evil")
    assert body["alert"] == {"data": {"dstuser": "evil"}}


def test_body_restart_wazuh_has_no_alert() -> None:
    body = build_ar_body("restart-wazuh")
    assert body == {"command": "!restart-wazuh"}


def test_body_includes_arguments_when_given() -> None:
    body = build_ar_body("firewall-drop", srcip="1.2.3.4", arguments=["x", "y"])
    assert body["arguments"] == ["x", "y"]


# ── catalog / helpers ────────────────────────────────────────────────────────


def test_get_ar_command_strips_bang() -> None:
    assert get_ar_command("!firewall-drop") is get_ar_command("firewall-drop")
    assert get_ar_command("nope") is None


def test_is_valid_ip() -> None:
    assert is_valid_ip("203.0.113.7") is True
    assert is_valid_ip("2001:db8::1") is True
    assert is_valid_ip("not.an.ip") is False
    assert is_valid_ip("999.1.1.1") is False


def test_classify_os() -> None:
    assert classify_os("Microsoft Windows Server 2019") == OS_WINDOWS
    assert classify_os("Ubuntu 22.04") == OS_LINUX
    assert classify_os("CentOS Linux") == OS_LINUX
    assert classify_os("some-appliance") is None
    assert classify_os(None) is None


def test_classify_os_bsd_per_os() -> None:
    # 6-c.2a: each BSD is its own class (different default firewall).
    assert classify_os("FreeBSD 14.3-RELEASE") == OS_FREEBSD
    assert classify_os("OpenBSD 7.5") == OS_OPENBSD
    assert classify_os("NetBSD 10.0") == OS_NETBSD
    # OPNsense/pfSense are FreeBSD-based but detected ahead of generic FreeBSD —
    # this is the live agent 009 signal (os.platform=bsd, uname FreeBSD…OPNsense).
    assert classify_os("bsd FreeBSD |OPNsense.internal |14.3-RELEASE") == OS_OPNSENSE
    assert classify_os("pfSense") == OS_OPNSENSE
    # Bare "bsd" with no specific marker defaults to FreeBSD (pf is correct there).
    assert classify_os("bsd") == OS_FREEBSD
    # macOS is BSD-derived but must classify as macOS, not a BSD.
    assert classify_os("Darwin 23.0") == OS_MACOS


def test_every_command_declares_a_valid_severity() -> None:
    for name, cmd in AR_COMMANDS.items():
        assert cmd.severity in {SEV_LOW, SEV_MEDIUM, SEV_HIGH}, name


# ── reversal metadata (slice 6-d.1, ADR 0028) ────────────────────────────────


def test_reverses_via_is_present_iff_reversible() -> None:
    """``reverses_via`` (the delete-inverse description) is non-empty exactly when
    a command is reversible — the catalog stays the single source of truth for
    what an undo does (a test changes if the matrix drifts)."""
    for name, cmd in AR_COMMANDS.items():
        assert cmd.reversible == bool(cmd.reverses_via.strip()), name


def test_enforcement_commands_are_reversible_restart_is_not() -> None:
    # Every enforcement (block / disable) command has a host-level undo; the
    # one-shot restart leaves no state to reverse (ADR 0028 reversal matrix).
    assert AR_COMMANDS["restart-wazuh"].reversible is False
    for name in (
        "firewall-drop", "host-deny", "route-null", "disable-account", "netsh",
        "win_route-null", "pf", "ipfw", "npf", "opnsense-fw",
    ):
        assert AR_COMMANDS[name].reversible is True, name


# ── intent → platform-correct command selection (slice 6-c) ──────────────────


def test_intent_catalog_is_consistent() -> None:
    """Every command the intent table can select must exist in the catalog AND
    platform-fit the OS it is mapped under — the catalog stays the source of
    truth (a test must change if the mapping drifts)."""
    # Every OS class any OS-specific intent can target.
    targeted_oses = {
        os_class
        for entry in _INTENT_COMMANDS.values()
        if isinstance(entry, dict)
        for os_class in entry
    }
    for intent, entry in _INTENT_COMMANDS.items():
        assert intent in AR_INTENTS
        if isinstance(entry, str):  # OS-agnostic — must run on every targeted platform
            cmd = get_ar_command(entry)
            assert cmd is not None, f"{intent} → unknown command {entry!r}"
            assert targeted_oses <= cmd.platforms, (
                f"OS-agnostic {intent} → {entry!r} must run on every targeted OS"
            )
            continue
        for os_class, command in entry.items():
            cmd = get_ar_command(command)
            assert cmd is not None, f"{intent}/{os_class} → unknown command {command!r}"
            assert os_class in cmd.platforms, (
                f"{intent}/{os_class} selects {command!r} which is not for {os_class}"
            )
    # The pf→ipfw version-gate fallback must also platform-fit FreeBSD + macOS.
    ipfw = get_ar_command("ipfw")
    assert ipfw is not None
    assert {OS_FREEBSD, OS_MACOS} <= ipfw.platforms


def test_intent_block_ip_selects_per_platform() -> None:
    # The headline behavior: same intent, OS picks the command (6-c.2a per-OS split).
    assert resolve_intent_command(INTENT_BLOCK_IP, OS_LINUX).command == "firewall-drop"
    assert resolve_intent_command(INTENT_BLOCK_IP, OS_WINDOWS).command == "netsh"
    assert resolve_intent_command(INTENT_BLOCK_IP, OS_MACOS).command == "pf"  # #1: was route-null
    assert resolve_intent_command(INTENT_BLOCK_IP, OS_FREEBSD).command == "pf"
    assert resolve_intent_command(INTENT_BLOCK_IP, OS_OPENBSD).command == "pf"
    assert resolve_intent_command(INTENT_BLOCK_IP, OS_NETBSD).command == "npf"
    # OPNsense appliance → its own opnsense-fw (stock pf doesn't apply).
    assert resolve_intent_command(INTENT_BLOCK_IP, OS_OPNSENSE).command == "opnsense-fw"


def test_intent_block_ip_version_gate_pf_vs_ipfw() -> None:
    # Modern → pf; pre-pf versions (FreeBSD < 5.3 / macOS < 10.7) → ipfw.
    assert resolve_intent_command(
        INTENT_BLOCK_IP, OS_FREEBSD, os_signal="FreeBSD 14.3-RELEASE"
    ).command == "pf"
    assert resolve_intent_command(
        INTENT_BLOCK_IP, OS_FREEBSD, os_signal="FreeBSD 4.11-RELEASE"
    ).command == "ipfw"
    assert resolve_intent_command(
        INTENT_BLOCK_IP, OS_MACOS, os_signal="Mac OS X 10.6.8"
    ).command == "ipfw"
    assert resolve_intent_command(
        INTENT_BLOCK_IP, OS_MACOS, os_signal="macOS 14.2"
    ).command == "pf"
    # Unparseable / no signal → modern default (pf), never a wrong guess.
    assert resolve_intent_command(INTENT_BLOCK_IP, OS_FREEBSD, os_signal=None).command == "pf"


def test_intent_block_ip_refused_when_os_unknown() -> None:
    res = resolve_intent_command(INTENT_BLOCK_IP, None)
    assert res.ok is False
    assert "operating system could not be determined" in res.reason


def test_intent_disable_user_unsupported_on_windows() -> None:
    res = resolve_intent_command(INTENT_DISABLE_USER, OS_WINDOWS)
    assert res.ok is False
    assert "not supported on windows" in res.reason
    assert resolve_intent_command(INTENT_DISABLE_USER, OS_LINUX).command == "disable-account"


def test_intent_restart_is_os_agnostic() -> None:
    # Resolves the same command with OR without a known OS.
    assert resolve_intent_command(INTENT_RESTART, None).command == "restart-wazuh"
    assert resolve_intent_command(INTENT_RESTART, OS_WINDOWS).command == "restart-wazuh"


def test_intent_unknown_is_refused() -> None:
    res = resolve_intent_command("quarantine", OS_LINUX)
    assert res.ok is False
    assert "Unknown action intent" in res.reason


# ── method override + OS-unknown failover (slice 6-c.2b) ─────────────────────


def test_method_override_uses_named_command_when_platform_fits() -> None:
    # A specific stranded method (host-deny) on Linux is honored.
    res = resolve_method_command(INTENT_BLOCK_IP, "host-deny", OS_LINUX)
    assert res.ok is True
    assert res.command == "host-deny"


def test_method_override_refused_on_platform_mismatch() -> None:
    res = resolve_method_command(INTENT_BLOCK_IP, "netsh", OS_LINUX)
    assert res.ok is False
    assert "runs on windows" in res.reason


def test_method_override_refused_on_intent_target_mismatch() -> None:
    # disable-account acts on a username — can't satisfy block_ip.
    res = resolve_method_command(INTENT_BLOCK_IP, "disable-account", OS_LINUX)
    assert res.ok is False
    assert "does not match intent" in res.reason


def test_method_override_refused_for_unknown_command() -> None:
    res = resolve_method_command(INTENT_BLOCK_IP, "nuke", OS_LINUX)
    assert res.ok is False
    assert "Unknown active-response command" in res.reason


def test_method_failover_allows_unknown_os() -> None:
    # OS-unknown user-guided failover: the human asserts the platform via method.
    res = resolve_method_command(INTENT_BLOCK_IP, "pf", None)
    assert res.ok is True
    assert res.command == "pf"


# ── interpret_ar_result (HTTP 200 even on failure) ───────────────────────────


def test_interpret_dispatched() -> None:
    ok, detail = interpret_ar_result(
        {"data": {"affected_items": ["002"], "total_affected_items": 1, "failed_items": []}}
    )
    assert ok is True
    assert detail["dispatched"] is True
    assert detail["total_affected_items"] == 1


def test_interpret_failure_surfaces_error() -> None:
    ok, detail = interpret_ar_result(
        {
            "data": {
                "total_affected_items": 0,
                "failed_items": [{"error": {"message": "Agent does not exist"}, "id": ["99999"]}],
            },
            "error": 1,
        }
    )
    assert ok is False
    assert detail["dispatched"] is False
    assert detail["error"] == "Agent does not exist"
