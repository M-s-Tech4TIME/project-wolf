"""config_change domain + validator + severity — Phase 6-e.4 (ADR 0029).

The tightest structural gate of any class: only allowlisted, single-instance
ossec.conf sections are editable, the replacement must be exactly one well-formed
``<section>`` block within the review-size cap, and repeated / break-the-manager
sections are refused with guided messages.
"""

import pytest
from wolf_server.gateway.proposals import SEV_HIGH, compute_severity
from wolf_server.gateway.validator import validate_proposal
from wolf_server.wazuh.config_change import (
    EDITABLE_SECTIONS,
    MAX_SECTION_CHARS,
    find_section_blocks,
    is_valid_section_block,
    replace_section_block,
)

_OSSEC = """<ossec_config>
  <global>
    <email_notification>no</email_notification>
  </global>
  <sca>
    <enabled>yes</enabled>
    <interval>12h</interval>
  </sca>
  <syscheck>
    <frequency>43200</frequency>
  </syscheck>
</ossec_config>
<ossec_config>
  <global>
    <logall>no</logall>
  </global>
</ossec_config>
"""


# ── section finding / replacing (string-based, multi-root fragment) ───────────


def test_find_section_blocks_counts_occurrences() -> None:
    assert len(find_section_blocks(_OSSEC, "sca")) == 1
    assert len(find_section_blocks(_OSSEC, "syscheck")) == 1
    # <global> is repeated in stock — this is exactly why it's not editable.
    assert len(find_section_blocks(_OSSEC, "global")) == 2
    assert find_section_blocks(_OSSEC, "cluster") == []


def test_replace_section_block_replaces_the_single_occurrence() -> None:
    new_block = "<sca>\n    <enabled>no</enabled>\n  </sca>"
    out = replace_section_block(_OSSEC, "sca", new_block)
    assert out is not None
    assert "<enabled>no</enabled>" in out
    assert "<interval>12h</interval>" not in out  # old body gone
    assert len(find_section_blocks(out, "sca")) == 1
    # untouched sections survive
    assert "<frequency>43200</frequency>" in out


def test_replace_section_block_refuses_when_not_exactly_one() -> None:
    assert replace_section_block(_OSSEC, "global", "<global></global>") is None  # repeated
    assert replace_section_block(_OSSEC, "remote", "<remote></remote>") is None  # absent


# ── replacement-block shape validation ───────────────────────────────────────


def test_valid_section_block_accepts_a_clean_block() -> None:
    ok, reason = is_valid_section_block("sca", "<sca><enabled>no</enabled></sca>")
    assert ok is True
    assert reason == ""


@pytest.mark.parametrize(
    ("content", "needle"),
    [
        ("", "empty"),
        ("<sca><enabled>no</enabled></sca>trailing", "exactly one"),
        ("<syscheck><frequency>1</frequency></syscheck>", "exactly one"),  # wrong section
        ("<ossec_config><sca></sca></ossec_config>", "bare <section>"),
        ("x" * (MAX_SECTION_CHARS + 1), "exceeds"),
    ],
)
def test_valid_section_block_rejects_malformed(content: str, needle: str) -> None:
    ok, reason = is_valid_section_block("sca", content)
    assert ok is False
    assert needle in reason


# ── the registered structural validator (dispatch by action_class) ───────────


def _validate(section: str, action: str, content: str):
    return validate_proposal(
        action_class="config_change",
        target={"section": section},
        action=action,
        parameters={"section_content": content},
    )


def test_validator_accepts_allowlisted_section() -> None:
    assert _validate("sca", "update_section", "<sca><enabled>no</enabled></sca>").ok is True


def test_validator_refuses_section_outside_allowlist() -> None:
    # cluster / auth / indexer / ruleset are deliberately NOT editable in v1.
    for section in ("cluster", "auth", "indexer", "ruleset", "global"):
        assert section not in EDITABLE_SECTIONS
        res = _validate(section, "update_section", f"<{section}></{section}>")
        assert res.ok is False
        assert "not editable" in res.reason


def test_validator_refuses_unresolved_or_wildcard_section() -> None:
    assert _validate("", "update_section", "<sca></sca>").ok is False
    assert _validate("*", "update_section", "<sca></sca>").ok is False


def test_validator_refuses_unknown_operation() -> None:
    res = _validate("sca", "delete_section", "<sca></sca>")
    assert res.ok is False
    assert "Unknown config_change operation" in res.reason


def test_validator_refuses_forward_restore() -> None:
    # restore_config is reversal-only (created via create_reversal_proposal).
    res = _validate("sca", "restore_config", "<sca></sca>")
    assert res.ok is False


def test_validator_refuses_missing_or_malformed_content() -> None:
    assert validate_proposal(
        action_class="config_change",
        target={"section": "sca"},
        action="update_section",
        parameters={},
    ).ok is False
    assert _validate("sca", "update_section", "not xml").ok is False


# ── severity ──────────────────────────────────────────────────────────────


def test_config_change_severity_is_high() -> None:
    # Manager-global + can take the manager down = highest blast radius.
    assert compute_severity("config_change", "update_section", {}) == SEV_HIGH
    assert compute_severity("config_change", "restore_config", {}) == SEV_HIGH
