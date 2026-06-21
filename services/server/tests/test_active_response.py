"""Active-response catalog + body construction (Phase 6, 6-b.1).

Grounded in the live Wazuh v4.14.3 API + the actual AR script source
(get_srcip_from_json → parameters.alert.data.srcip; get_username_from_json →
parameters.alert.data.dstuser; get_ip_version → numeric IPv4/IPv6 only). The
body must NOT carry `custom` (the bug that broke every run), must `!`-prefix the
command (run it now), and the result interpreter must treat HTTP-200-with-
failed_items as a failure.
"""

from wolf_server.wazuh.active_response import (
    OS_LINUX,
    OS_WINDOWS,
    build_ar_body,
    classify_os,
    get_ar_command,
    interpret_ar_result,
    is_valid_ip,
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
