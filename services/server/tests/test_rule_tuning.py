"""rule_tuning helpers + validator + severity + capability scoping — 6-e.3 (ADR 0029).

Pure unit coverage of the load-bearing, non-API pieces: the local_rules.xml
override construction (preserve matching conditions, idempotent re-tuning), the
structural validator (rule-id + op + level), severity (high), and the capability
class-availability scoping (rule_tuning offered only with rules:update).
"""

from wolf_server.gateway.proposals import compute_severity
from wolf_server.gateway.validator import validate_proposal
from wolf_server.wazuh.active_response import SEV_HIGH
from wolf_server.wazuh.capabilities import (
    ACTION_ACTIVE_RESPONSE,
    ACTION_UPDATE_RULES,
    CredentialCapabilities,
)
from wolf_server.wazuh.rule_tuning import (
    apply_override,
    build_override_block,
    extract_rule_block,
    has_override,
    is_valid_level,
    is_valid_rule_id,
    strip_tuning_block,
)

_SAMPLE = """<!-- Local rules -->
<group name="sshd,">
  <rule id="100001" level="5">
    <if_sid>5716</if_sid>
    <srcip>1.1.1.1</srcip>
    <description>sshd: auth failed.</description>
  </rule>
</group>
"""


# ── id / level validation ─────────────────────────────────────────────────────


def test_is_valid_rule_id() -> None:
    assert is_valid_rule_id("100001")
    assert is_valid_rule_id("1")
    assert not is_valid_rule_id("0")
    assert not is_valid_rule_id("1000000")  # > 999999
    assert not is_valid_rule_id("abc")
    assert not is_valid_rule_id("*")
    assert not is_valid_rule_id("")


def test_is_valid_level() -> None:
    assert is_valid_level(0)
    assert is_valid_level(16)
    assert not is_valid_level(17)
    assert not is_valid_level(-1)
    assert not is_valid_level(True)  # bool is not a level
    assert not is_valid_level("5")


# ── override construction ───────────────────────────────────────────────────


def test_extract_rule_block_found_and_missing() -> None:
    block = extract_rule_block(_SAMPLE, "100001")
    assert block is not None
    assert "<if_sid>5716</if_sid>" in block
    assert "<srcip>1.1.1.1</srcip>" in block
    assert extract_rule_block(_SAMPLE, "999999") is None


def test_build_override_block_preserves_conditions_and_sets_overwrite() -> None:
    block = extract_rule_block(_SAMPLE, "100001")
    assert block is not None
    override = build_override_block(block, level=0)
    # Matching conditions preserved verbatim …
    assert "<if_sid>5716</if_sid>" in override
    assert "<srcip>1.1.1.1</srcip>" in override
    # … only the level changed + overwrite added.
    assert 'level="0"' in override
    assert 'level="5"' not in override
    assert 'overwrite="yes"' in override


def test_build_override_block_adds_level_when_absent() -> None:
    block = '<rule id="200">\n  <match>x</match>\n</rule>'
    override = build_override_block(block, level=3)
    assert 'level="3"' in override
    assert 'overwrite="yes"' in override
    assert "<match>x</match>" in override


def test_build_override_block_normalises_existing_overwrite() -> None:
    block = '<rule id="200" level="7" overwrite="no"><match>x</match></rule>'
    override = build_override_block(block, level=2)
    assert override.count("overwrite=") == 1
    assert 'overwrite="yes"' in override
    assert 'level="2"' in override


def test_apply_override_appends_then_idempotently_replaces() -> None:
    base = '<group name="x,">\n</group>\n'
    block = extract_rule_block(_SAMPLE, "100001")
    assert block is not None
    first = apply_override(base, "100001", build_override_block(block, level=0))
    assert first.count("wolf-tuning:rule=100001") == 1
    assert 'overwrite="yes"' in first
    # A second tune of the SAME rule must REPLACE, never stack a duplicate id.
    second = apply_override(first, "100001", build_override_block(block, level=2))
    assert second.count("wolf-tuning:rule=100001") == 1
    assert second.count("<rule") == 1  # only one override block for this id
    assert 'level="2"' in second
    # The untouched base content survives.
    assert '<group name="x,">' in second


def test_has_override_detects_marked_block() -> None:
    # The authoritative "our write persisted" check used by the executor.
    base = '<group name="x,">\n</group>\n'
    block = extract_rule_block(_SAMPLE, "100001")
    assert block is not None
    applied = apply_override(base, "100001", build_override_block(block, level=0))
    assert has_override(applied, "100001") is True
    assert has_override(applied, "999999") is False
    assert has_override(base, "100001") is False


def test_strip_tuning_block_removes_only_wolf_block() -> None:
    base = '<group name="x,">\n</group>\n'
    block = extract_rule_block(_SAMPLE, "100001")
    assert block is not None
    applied = apply_override(base, "100001", build_override_block(block, level=0))
    stripped = strip_tuning_block(applied, "100001")
    assert "wolf-tuning" not in stripped
    assert '<group name="x,">' in stripped


# ── validator ─────────────────────────────────────────────────────────────────


def test_validate_disable_rule_ok() -> None:
    v = validate_proposal(
        action_class="rule_tuning", target={"rule_id": "100001"}, action="disable_rule",
        parameters={"level": 0},
    )
    assert v.ok


def test_validate_adjust_level_ok() -> None:
    v = validate_proposal(
        action_class="rule_tuning", target={"rule_id": "100001"}, action="adjust_level",
        parameters={"level": 3},
    )
    assert v.ok


def test_validate_adjust_level_requires_valid_level() -> None:
    v = validate_proposal(
        action_class="rule_tuning", target={"rule_id": "100001"}, action="adjust_level",
        parameters={"level": 99},
    )
    assert not v.ok
    assert "level" in v.reason.lower()


def test_validate_unresolved_rule_id_refused() -> None:
    v = validate_proposal(
        action_class="rule_tuning", target={}, action="disable_rule", parameters={},
    )
    assert not v.ok
    assert "rule id" in v.reason.lower()


def test_validate_invalid_rule_id_refused() -> None:
    v = validate_proposal(
        action_class="rule_tuning", target={"rule_id": "nope"}, action="disable_rule",
        parameters={},
    )
    assert not v.ok


def test_validate_unknown_op_refused() -> None:
    v = validate_proposal(
        action_class="rule_tuning", target={"rule_id": "100001"}, action="delete_rule",
        parameters={},
    )
    assert not v.ok


def test_validate_restore_is_not_a_forward_op() -> None:
    # restore_rules is reversal-only (created via create_reversal_proposal, which
    # bypasses the validator) — a *forward* restore must be refused.
    v = validate_proposal(
        action_class="rule_tuning", target={"rule_id": "100001"}, action="restore_rules",
        parameters={},
    )
    assert not v.ok


# ── severity + capability scoping ──────────────────────────────────────────────


def test_rule_tuning_severity_is_high() -> None:
    assert compute_severity("rule_tuning", "disable_rule", {"level": 0}) == SEV_HIGH
    assert compute_severity("rule_tuning", "adjust_level", {"level": 10}) == SEV_HIGH


def test_rule_tuning_offered_only_with_rules_update() -> None:
    with_rules = CredentialCapabilities(policies={ACTION_UPDATE_RULES: {"*:*:*": "allow"}})
    assert "rule_tuning" in with_rules.available_action_classes()
    # A per-org-style credential (AR only, no rules:update) is NOT offered rule_tuning.
    per_org = CredentialCapabilities(
        policies={ACTION_ACTIVE_RESPONSE: {"agent:group:acme": "allow"}}
    )
    assert "rule_tuning" not in per_org.available_action_classes()
