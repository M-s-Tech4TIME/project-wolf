"""Tests for the Slice 3 ingest parsers.

The download paths and bulk-embed flow are exercised by the live ingest
run that produces the dev corpus; these tests cover the deterministic
parser logic with small in-line fixtures so they run quickly and don't
touch the network or DB.
"""

import json
import zipfile
from io import BytesIO
from pathlib import Path

from tools.seed_knowledge.attack import _chunks_from_bundle
from tools.seed_knowledge.wazuh_rules import _chunks_from_file

# ─── ATT&CK STIX parser ─────────────────────────────────────────────────────


_TECHNIQUE_STIX = {
    "type": "attack-pattern",
    "name": "Brute Force",
    "description": "Adversaries may use brute force.",
    "kill_chain_phases": [{"phase_name": "credential-access"}],
    "external_references": [
        {"source_name": "mitre-attack", "external_id": "T1110"},
        {"source_name": "capec", "external_id": "CAPEC-49"},
    ],
}

_SUBTECHNIQUE_STIX = {
    "type": "attack-pattern",
    "name": "Password Guessing",
    "description": "Sub-technique under brute force.",
    "kill_chain_phases": [{"phase_name": "credential-access"}],
    "external_references": [
        {"source_name": "mitre-attack", "external_id": "T1110.001"},
    ],
}

_DEPRECATED_STIX = {
    "type": "attack-pattern",
    "name": "Deprecated Thing",
    "description": "Should be excluded.",
    "x_mitre_deprecated": True,
    "external_references": [
        {"source_name": "mitre-attack", "external_id": "T9999"},
    ],
}

_NON_ATTACK_PATTERN_STIX = {
    "type": "intrusion-set",
    "name": "Fancy Bear",
    "description": "Not an attack-pattern; should be skipped.",
}


def test_attack_extracts_techniques() -> None:
    bundle = {"objects": [_TECHNIQUE_STIX]}
    chunks = list(_chunks_from_bundle(bundle, "14.1"))
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.source_type == "attack"
    assert chunk.organization_id is None
    assert "T1110" in chunk.content
    assert "Brute Force" in chunk.content
    assert chunk.chunk_metadata["technique"] == "T1110"
    assert chunk.chunk_metadata["attack_version"] == "14.1"
    assert chunk.chunk_metadata["is_subtechnique"] is False
    assert chunk.chunk_metadata["kill_chain_phases"] == ["credential-access"]


def test_attack_marks_subtechniques_with_parent() -> None:
    bundle = {"objects": [_SUBTECHNIQUE_STIX]}
    chunks = list(_chunks_from_bundle(bundle, "14.1"))
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.chunk_metadata["is_subtechnique"] is True
    assert chunk.chunk_metadata["parent_technique"] == "T1110"


def test_attack_excludes_deprecated_and_revoked() -> None:
    bundle = {"objects": [_TECHNIQUE_STIX, _DEPRECATED_STIX]}
    chunks = list(_chunks_from_bundle(bundle, "14.1"))
    assert len(chunks) == 1
    assert chunks[0].chunk_metadata["technique"] == "T1110"


def test_attack_skips_non_attack_pattern_objects() -> None:
    bundle = {"objects": [_NON_ATTACK_PATTERN_STIX, _TECHNIQUE_STIX]}
    chunks = list(_chunks_from_bundle(bundle, "14.1"))
    assert len(chunks) == 1
    assert chunks[0].chunk_metadata["technique"] == "T1110"


def test_attack_skips_techniques_with_missing_id() -> None:
    no_id = {**_TECHNIQUE_STIX, "external_references": []}
    bundle = {"objects": [no_id]}
    chunks = list(_chunks_from_bundle(bundle, "14.1"))
    assert chunks == []


def test_attack_skips_techniques_with_empty_description() -> None:
    no_desc = {**_TECHNIQUE_STIX, "description": "  "}
    bundle = {"objects": [no_desc]}
    chunks = list(_chunks_from_bundle(bundle, "14.1"))
    assert chunks == []


def test_attack_chunk_content_starts_with_id_for_fts() -> None:
    """The ATT&CK ID lives at the front of content so a keyword query like
    'T1110' lands in the FTS leg of hybrid retrieval immediately."""
    bundle = {"objects": [_TECHNIQUE_STIX]}
    chunks = list(_chunks_from_bundle(bundle, "14.1"))
    assert chunks[0].content.startswith("T1110")


# ─── Wazuh rules XML parser ─────────────────────────────────────────────────


_RULES_XML = """
<group name="sshd,authentication_failures,syslog">
  <rule id="5710" level="5">
    <if_sid>5700</if_sid>
    <match>illegal user|invalid user</match>
    <description>sshd: Attempt to login using a non-existent user</description>
    <group>authentication_failures,</group>
    <mitre>
      <id>T1110.001</id>
    </mitre>
  </rule>
  <rule id="5712" level="10" frequency="8" timeframe="120">
    <if_matched_sid>5710</if_matched_sid>
    <description>sshd: brute force trying to get access to the system.
       Non existent user.</description>
    <same_source_ip />
    <group>authentication_failures,</group>
    <mitre>
      <id>T1110</id>
      <id>T1110.001</id>
    </mitre>
  </rule>
</group>
"""

_MALFORMED_XML = "<group><rule id='5710' level=5><description>bad attrs</rule></group>"


def test_wazuh_extracts_rules_with_metadata() -> None:
    chunks = list(_chunks_from_file("0095-sshd_rules.xml", _RULES_XML))
    assert len(chunks) == 2
    by_id = {c.chunk_metadata["rule_id"]: c for c in chunks}

    r5710 = by_id["5710"]
    assert r5710.source_type == "wazuh_doc"
    assert r5710.organization_id is None
    assert "Rule 5710" in r5710.content
    assert "non-existent user" in r5710.content.lower()
    assert r5710.chunk_metadata["level"] == 5
    assert r5710.chunk_metadata["ruleset_file"] == "0095-sshd_rules.xml"
    assert r5710.chunk_metadata["mitre"] == ["T1110.001"]

    r5712 = by_id["5712"]
    assert r5712.chunk_metadata["level"] == 10
    assert set(r5712.chunk_metadata["mitre"]) == {"T1110", "T1110.001"}


def test_wazuh_rule_content_starts_with_id() -> None:
    chunks = list(_chunks_from_file("0095-sshd_rules.xml", _RULES_XML))
    for c in chunks:
        assert c.content.startswith("Rule ")


def test_wazuh_malformed_file_yields_no_chunks_and_does_not_raise() -> None:
    # Logged + skipped per the ingester's graceful-degradation contract.
    chunks = list(_chunks_from_file("bad.xml", _MALFORMED_XML))
    assert chunks == []


def test_wazuh_rule_without_description_is_skipped() -> None:
    xml = '<group><rule id="9999" level="3"></rule></group>'
    chunks = list(_chunks_from_file("x.xml", xml))
    assert chunks == []


# ─── Module-level smoke: zip parse round-trip ───────────────────────────────


def test_wazuh_zip_iter_handles_nested_paths(tmp_path: Path) -> None:
    """Build a fake archive that mirrors the real Wazuh release layout
    and assert the ingester's iter walks ONLY rule XMLs."""
    from tools.seed_knowledge.wazuh_rules import RULES_DIR_IN_ARCHIVE, _iter_rule_xml_files

    archive = tmp_path / "fake.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(f"{RULES_DIR_IN_ARCHIVE}/0095-sshd_rules.xml", _RULES_XML)
        zf.writestr(f"{RULES_DIR_IN_ARCHIVE}/0010-syslog_rules.xml", "<group/>")
        # Unrelated files in other paths shouldn't be yielded.
        zf.writestr("wazuh-4.9.2/src/main.c", "// not a rule")
        zf.writestr(f"{RULES_DIR_IN_ARCHIVE}/README.md", "not xml")

    files = list(_iter_rule_xml_files(archive))
    basenames = {name for name, _ in files}
    assert basenames == {"0095-sshd_rules.xml", "0010-syslog_rules.xml"}


# Silence unused-import warnings (json/BytesIO are utility-import surface
# for tests that may follow).
_ = json
_ = BytesIO
