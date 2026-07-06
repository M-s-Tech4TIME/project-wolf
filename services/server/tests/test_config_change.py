"""config_change domain + validator + severity — 6-e.4, generalized in 6-f.4 (ADR 0032 B).

Free-form within rails: any well-formed section is authorable EXCEPT the
break-the-manager blocklist; single-instance sections are replaced (or ADDED
when absent); repeated / merge-semantic sections are addressed by
block-identity (one <integration> by its <name> — the virustotal fix); the
replacement must be exactly one well-formed ``<section>`` block within the
review-size cap.
"""

import pytest
from wolf_server.gateway.proposals import SEV_HIGH, compute_severity
from wolf_server.gateway.validator import validate_proposal
from wolf_server.wazuh.config_change import (
    BLOCKED_SECTIONS,
    MAX_SECTION_CHARS,
    block_persisted,
    block_removed,
    build_candidate,
    carries_value,
    content_carries_key,
    describe_instances,
    element_entries,
    find_identified_blocks,
    find_section_blocks,
    identity_of,
    insert_section_block,
    is_valid_section_block,
    is_valid_section_name,
    remove_identified_block,
    replace_section_block,
    section_persisted,
    upsert_identified_block,
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
  <integration>
    <name>slack</name>
    <hook_url>https://hooks.example.invalid/services/T0</hook_url>
    <level>10</level>
  </integration>
  <localfile>
    <log_format>syslog</log_format>
    <location>/var/log/auth.log</location>
  </localfile>
</ossec_config>
<ossec_config>
  <global>
    <logall>no</logall>
  </global>
  <integration>
    <name>shuffle</name>
    <hook_url>https://shuffle.example.invalid/api</hook_url>
  </integration>
</ossec_config>
"""

_VT_BLOCK = (
    "<integration>\n"
    "    <name>virustotal</name>\n"
    "    <api_key>REDACTED</api_key>\n"
    "    <group>syscheck</group>\n"
    "    <alert_format>json</alert_format>\n"
    "  </integration>"
)


# ── section finding / replacing (string-based, multi-root fragment) ───────────


def test_find_section_blocks_counts_occurrences() -> None:
    assert len(find_section_blocks(_OSSEC, "sca")) == 1
    assert len(find_section_blocks(_OSSEC, "syscheck")) == 1
    # <global> and <integration> are repeated — exactly why identity ops exist.
    assert len(find_section_blocks(_OSSEC, "global")) == 2
    assert len(find_section_blocks(_OSSEC, "integration")) == 2
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


def test_insert_section_block_appends_before_the_final_wrapper_close() -> None:
    out = insert_section_block(_OSSEC, "<remote>\n  <connection>secure</connection>\n</remote>")
    assert out is not None
    assert len(find_section_blocks(out, "remote")) == 1
    # inserted before the LAST </ossec_config> — after the shuffle integration.
    assert out.rindex("<remote>") > out.index("<name>shuffle</name>")
    assert out.rstrip().endswith("</ossec_config>")


def test_insert_section_block_refuses_without_a_wrapper_anchor() -> None:
    assert insert_section_block("not a config at all", "<remote></remote>") is None


# ── block identity (B2 — the virustotal fix) ─────────────────────────────────


def test_identity_of_reads_the_identity_element() -> None:
    blocks = find_section_blocks(_OSSEC, "integration")
    assert identity_of("integration", blocks[0]) == "slack"
    assert identity_of("integration", blocks[1]) == "shuffle"
    localfiles = find_section_blocks(_OSSEC, "localfile")
    assert identity_of("localfile", localfiles[0]) == "/var/log/auth.log"
    # No identity element defined for single-instance sections.
    assert identity_of("sca", "<sca><enabled>yes</enabled></sca>") is None
    # An integration block without a <name> has no identity.
    assert identity_of("integration", "<integration><level>3</level></integration>") is None


def test_find_identified_blocks_matches_exactly() -> None:
    assert len(find_identified_blocks(_OSSEC, "integration", "slack")) == 1
    assert len(find_identified_blocks(_OSSEC, "integration", "virustotal")) == 0
    # exact key, never substring
    assert len(find_identified_blocks(_OSSEC, "integration", "sla")) == 0


# ── any-unique-field disambiguation (6-f.5 — the duplicate-name web-test fix) ─

# Three <integration> blocks sharing one <name> — the operator's live tracecat
# scenario: only <hook_url>/<api_key> distinguish the instances.
_DUPES = """<ossec_config>
  <integration>
    <name>custom-tracecat</name>
    <hook_url>https://tc.example.invalid/hook/AAA</hook_url>
    <api_key>key-AAA</api_key>
    <level>5</level>
  </integration>
  <integration>
    <name>custom-tracecat</name>
    <hook_url>https://tc.example.invalid/hook/BBB</hook_url>
    <api_key>key-BBB</api_key>
    <level>5</level>
  </integration>
  <integration>
    <name>custom-tracecat</name>
    <hook_url>https://tc.example.invalid/hook/CCC</hook_url>
    <api_key>key-CCC</api_key>
    <level>5</level>
  </integration>
</ossec_config>
"""


def test_element_entries_and_carries_value_read_leaf_fields() -> None:
    block = find_section_blocks(_DUPES, "integration")[0]
    entries = dict(element_entries(block))
    assert entries["name"] == "custom-tracecat"
    assert entries["hook_url"] == "https://tc.example.invalid/hook/AAA"
    assert carries_value(block, "key-AAA") is True
    assert carries_value(block, "key-BBB") is False
    # exact value, never substring; empty never matches
    assert carries_value(block, "key-AA") is False
    assert carries_value(block, "") is False


def test_content_carries_key_accepts_identity_or_any_leaf_value() -> None:
    block = find_section_blocks(_DUPES, "integration")[1]
    assert content_carries_key("integration", block, "custom-tracecat") is True
    assert content_carries_key("integration", block, "key-BBB") is True
    assert content_carries_key("integration", block, "key-AAA") is False


def test_shared_name_is_ambiguous_but_a_unique_field_selects_one() -> None:
    # The shared <name> matches all three → ambiguous (callers refuse).
    assert len(find_identified_blocks(_DUPES, "integration", "custom-tracecat")) == 3
    # A unique <hook_url> or <api_key> selects exactly the one instance.
    by_url = find_identified_blocks(_DUPES, "integration", "https://tc.example.invalid/hook/BBB")
    assert len(by_url) == 1
    assert "key-BBB" in by_url[0]
    assert len(find_identified_blocks(_DUPES, "integration", "key-CCC")) == 1


def test_describe_instances_enumerates_the_discriminating_fields() -> None:
    matches = find_identified_blocks(_DUPES, "integration", "custom-tracecat")
    described = describe_instances(matches)
    # Every instance's unique fields are listed; the shared name/level are not.
    assert "instance 1" in described and "instance 3" in described
    assert "https://tc.example.invalid/hook/AAA" in described
    assert "<api_key>key-CCC</api_key>" in described
    assert "<name>" not in described
    assert "<level>" not in described


def test_describe_instances_empty_for_true_duplicates() -> None:
    clone = "<integration><name>x</name><level>3</level></integration>"
    assert describe_instances([clone, clone]) == ""


def test_upsert_and_remove_by_unique_field_value() -> None:
    new_bbb = (
        "<integration>\n"
        "    <name>custom-tracecat</name>\n"
        "    <hook_url>https://tc.example.invalid/hook/BBB</hook_url>\n"
        "    <api_key>key-BBB</api_key>\n"
        "    <level>3</level>\n"
        "  </integration>"
    )
    out = upsert_identified_block(
        _DUPES, "integration", "https://tc.example.invalid/hook/BBB", new_bbb
    )
    assert out is not None
    # Only the addressed instance changed level; its siblings kept level 5.
    assert out.count("<level>3</level>") == 1
    assert out.count("<level>5</level>") == 2
    assert block_persisted(out, "integration", "https://tc.example.invalid/hook/BBB", new_bbb)
    # The shared name stays ambiguous for upsert (refused at the domain level).
    assert upsert_identified_block(_DUPES, "integration", "custom-tracecat", new_bbb) is None
    # Removal by unique field removes exactly that instance.
    removed = remove_identified_block(_DUPES, "integration", "key-AAA")
    assert removed is not None
    assert "hook/AAA" not in removed
    assert "hook/BBB" in removed and "hook/CCC" in removed
    assert block_removed(removed, "integration", "key-AAA")


def test_upsert_identified_block_adds_a_new_instance() -> None:
    out = upsert_identified_block(_OSSEC, "integration", "virustotal", _VT_BLOCK)
    assert out is not None
    assert len(find_identified_blocks(out, "integration", "virustotal")) == 1
    # existing instances untouched
    assert len(find_identified_blocks(out, "integration", "slack")) == 1
    assert len(find_identified_blocks(out, "integration", "shuffle")) == 1


def test_upsert_identified_block_replaces_the_keyed_instance() -> None:
    new_slack = (
        "<integration><name>slack</name>"
        "<hook_url>https://hooks.example.invalid/services/NEW</hook_url></integration>"
    )
    out = upsert_identified_block(_OSSEC, "integration", "slack", new_slack)
    assert out is not None
    assert "services/NEW" in out
    assert "services/T0" not in out
    assert len(find_identified_blocks(out, "integration", "slack")) == 1
    # the OTHER instance is untouched
    assert "shuffle.example.invalid" in out


def test_upsert_identified_block_refuses_a_duplicated_key() -> None:
    doubled = _OSSEC + "\n<integration><name>slack</name></integration>\n"
    assert upsert_identified_block(doubled, "integration", "slack", _VT_BLOCK) is None


def test_remove_identified_block_removes_exactly_the_keyed_instance() -> None:
    out = remove_identified_block(_OSSEC, "integration", "slack")
    assert out is not None
    assert "hooks.example.invalid" not in out
    assert len(find_identified_blocks(out, "integration", "slack")) == 0
    # everything else survives
    assert "shuffle.example.invalid" in out
    assert "<interval>12h</interval>" in out


def test_remove_identified_block_refuses_missing_or_ambiguous_key() -> None:
    assert remove_identified_block(_OSSEC, "integration", "virustotal") is None
    doubled = _OSSEC + "\n<integration><name>slack</name></integration>\n"
    assert remove_identified_block(doubled, "integration", "slack") is None


def test_build_candidate_is_the_single_shared_transformation() -> None:
    # update on a present single-instance section → replace
    out = build_candidate(_OSSEC, "update_section", "sca", "", "<sca><enabled>no</enabled></sca>")
    assert out is not None and "<enabled>no</enabled>" in out
    # update on an ABSENT section → add
    out = build_candidate(_OSSEC, "update_section", "remote", "", "<remote></remote>")
    assert out is not None and len(find_section_blocks(out, "remote")) == 1
    # upsert / remove route to the identity helpers
    out = build_candidate(_OSSEC, "upsert_block", "integration", "virustotal", _VT_BLOCK)
    assert out is not None and "virustotal" in out
    out = build_candidate(_OSSEC, "remove_block", "integration", "slack", "")
    assert out is not None and "hooks.example.invalid" not in out
    # unknown op → no transformation
    assert build_candidate(_OSSEC, "explode", "sca", "", "<sca></sca>") is None


# ── persist verification (reformatting-tolerant, 6-e.4 fix) ──────────────────


def test_section_persisted_tolerates_manager_reindentation() -> None:
    # The model writes a compact block; the manager re-indents it on write. The
    # change DID land — persist verification must not false-negative on layout.
    proposed = "<sca><enabled>no</enabled></sca>"
    reindented = (
        "<ossec_config>\n  <sca>\n    <enabled>no</enabled>\n  </sca>\n"
        "  <syscheck><frequency>1</frequency></syscheck>\n</ossec_config>"
    )
    assert section_persisted(reindented, "sca", proposed) is True


def test_section_persisted_false_on_genuine_content_mismatch() -> None:
    proposed = "<sca><enabled>no</enabled></sca>"
    unchanged = "<ossec_config><sca><enabled>yes</enabled></sca></ossec_config>"
    assert section_persisted(unchanged, "sca", proposed) is False


def test_section_persisted_false_when_section_absent_or_repeated() -> None:
    proposed = "<sca><enabled>no</enabled></sca>"
    assert section_persisted("<ossec_config></ossec_config>", "sca", proposed) is False
    doubled = "<sca><enabled>no</enabled></sca>\n<sca><enabled>no</enabled></sca>"
    assert section_persisted(doubled, "sca", proposed) is False


def test_block_persisted_is_identity_scoped_and_reindent_tolerant() -> None:
    upserted = upsert_identified_block(_OSSEC, "integration", "virustotal", _VT_BLOCK)
    assert upserted is not None
    # compact proposal vs the (differently indented) written block still matches
    compact = _VT_BLOCK.replace("\n    ", "").replace("\n  ", "")
    assert block_persisted(upserted, "integration", "virustotal", compact) is True
    # other instances present — identity scoping means they never interfere
    assert block_persisted(upserted, "integration", "slack", compact) is False
    assert block_persisted(_OSSEC, "integration", "virustotal", _VT_BLOCK) is False


def test_block_removed_confirms_the_key_is_gone() -> None:
    assert block_removed(_OSSEC, "integration", "virustotal") is True
    assert block_removed(_OSSEC, "integration", "slack") is False
    removed = remove_identified_block(_OSSEC, "integration", "slack")
    assert removed is not None
    assert block_removed(removed, "integration", "slack") is True


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


def test_valid_section_names() -> None:
    for name in ("sca", "vulnerability-detection", "integration", "rule_test"):
        assert is_valid_section_name(name) is True
    for name in ("", "SCA", "<sca>", "sca zap", "1sca", "sca;rm"):
        assert is_valid_section_name(name) is False


# ── the registered structural validator (dispatch by action_class) ───────────


def _validate(section: str, action: str, content: str, block_key: str | None = None):
    target: dict[str, object] = {"section": section}
    if block_key is not None:
        target["block_key"] = block_key
    return validate_proposal(
        action_class="config_change",
        target=target,
        action=action,
        parameters={"section_content": content},
    )


def test_validator_accepts_any_unblocked_section() -> None:
    # B3 free-form within rails: blocklist not allowlist — sections beyond the
    # old 7-entry allowlist (e.g. <global>) now pass the STRUCTURAL gate; the
    # propose tool still refuses ambiguity against the live file.
    assert _validate("sca", "update_section", "<sca><enabled>no</enabled></sca>").ok is True
    assert _validate("global", "update_section", "<global><logall>yes</logall></global>").ok is True
    assert _validate("remote", "update_section", "<remote></remote>").ok is True


def test_validator_refuses_break_the_manager_sections() -> None:
    for section in sorted(BLOCKED_SECTIONS):
        res = _validate(section, "update_section", f"<{section}></{section}>")
        assert res.ok is False
        assert "not editable" in res.reason


def test_validator_refuses_unresolved_wildcard_or_malformed_section() -> None:
    assert _validate("", "update_section", "<sca></sca>").ok is False
    assert _validate("*", "update_section", "<sca></sca>").ok is False
    assert _validate("Not A Section", "update_section", "<sca></sca>").ok is False


def test_validator_refuses_unknown_operation() -> None:
    res = _validate("sca", "delete_section", "<sca></sca>")
    assert res.ok is False
    assert "Unknown config_change operation" in res.reason


def test_validator_refuses_forward_restore() -> None:
    # restore_config is reversal-only (created via create_reversal_proposal).
    res = _validate("sca", "restore_config", "<sca></sca>")
    assert res.ok is False


def test_validator_refuses_missing_or_malformed_content() -> None:
    assert (
        validate_proposal(
            action_class="config_change",
            target={"section": "sca"},
            action="update_section",
            parameters={},
        ).ok
        is False
    )
    assert _validate("sca", "update_section", "not xml").ok is False


def test_validator_block_ops_need_a_resolved_key() -> None:
    vt = "<integration><name>virustotal</name><group>syscheck</group></integration>"
    # good: identity section + key + content carrying the key
    assert _validate("integration", "upsert_block", vt, block_key="virustotal").ok is True
    assert _validate("integration", "remove_block", "", block_key="virustotal").ok is True
    # 6-f.5: block ops work on ANY unblocked section — a unique leaf value
    # addresses the instance (uniqueness enforced against the live file).
    ok = _validate("global", "upsert_block", "<global><logall>yes</logall></global>", "yes")
    assert ok.ok is True
    # missing / wildcard key still refused
    assert _validate("integration", "upsert_block", vt, block_key="").ok is False
    assert _validate("integration", "remove_block", "", block_key="*").ok is False
    assert _validate("integration", "remove_block", "", block_key=None).ok is False


def test_validator_upsert_content_must_carry_the_addressed_key() -> None:
    # addressing 'virustotal' while the block names 'slack' → refused (X-for-Y).
    slack = "<integration><name>slack</name></integration>"
    res = _validate("integration", "upsert_block", slack, block_key="virustotal")
    assert res.ok is False
    assert "must identify" in res.reason
    # a block with NO matching value anywhere is refused too
    anon = "<integration><level>3</level></integration>"
    assert _validate("integration", "upsert_block", anon, block_key="virustotal").ok is False
    # 6-f.5: the key may be carried as ANY leaf value, not only the identity
    # element — addressing an integration by its unique <hook_url> is valid.
    by_url = (
        "<integration><name>custom-tracecat</name>"
        "<hook_url>https://tc.example.invalid/hook/1</hook_url></integration>"
    )
    assert (
        _validate(
            "integration", "upsert_block", by_url, block_key="https://tc.example.invalid/hook/1"
        ).ok
        is True
    )


# ── severity ──────────────────────────────────────────────────────────────


def test_config_change_severity_is_high() -> None:
    # Manager-global + can take the manager down = highest blast radius.
    assert compute_severity("config_change", "update_section", {}) == SEV_HIGH
    assert compute_severity("config_change", "upsert_block", {}) == SEV_HIGH
    assert compute_severity("config_change", "remove_block", {}) == SEV_HIGH
    assert compute_severity("config_change", "restore_config", {}) == SEV_HIGH
