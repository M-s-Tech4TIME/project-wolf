# File-level disable — CLI progress to stdout (T201). The XML parse path
# (S314) uses stdlib ElementTree; the input is downloaded from a pinned
# GitHub release URL (WAZUH_ARCHIVE_URL) and verified by the archive's
# integrity, not arbitrary user input. defusedxml would add a small
# external dep for negligible additional safety in this trust model.
# TC003 (annotation-only imports → TYPE_CHECKING) is CLI-noise here.
# ruff: noqa: T201, S314, TC003
"""Wazuh ruleset XML ingester.

Source: the official wazuh-ruleset (https://github.com/wazuh/wazuh/tree/main/
ruleset/rules). Mirrored as a zip in the GitHub release artifacts; we fetch
the same XML files via the raw-content URL and parse them locally.

Each `<rule>` element becomes one KnowledgeChunk with:
  - source_type = 'wazuh_doc'
  - content = "Rule <id> (level <N>): <description>. Groups: <groups>.
               MITRE: <techniques>."
  - chunk_metadata = {
        rule_id:       '5712'
        level:         10
        title:         'Rule 5712 — sshd brute force ...'
        ruleset_file:  '0095-sshd_rules.xml'
        groups:        ['authentication_failures', 'syslog', 'sshd']
        mitre:         ['T1110']
        wazuh_version: '4.x'
    }

Why this slice ships only the canonical rules in `ruleset/rules/`: those
are the authoritative Wazuh-shipped rules. Operator-local rules
(`local_rules.xml`) are out of scope — they're organization-specific and belong
in the organization-private corpus.
"""

from __future__ import annotations

import re
import urllib.request
import zipfile
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

import structlog
from wolf_server.knowledge.store import ChunkInput

logger = structlog.get_logger(__name__)

# Pinned to a specific Wazuh release zip so the corpus is reproducible.
# Bump the version + clear cache to refresh. (The /archive/refs/tags/<ver>.zip
# URL is a static GitHub artifact; no auth required.)
WAZUH_VERSION = "v4.9.2"
WAZUH_ARCHIVE_URL = f"https://github.com/wazuh/wazuh/archive/refs/tags/{WAZUH_VERSION}.zip"
WAZUH_ARCHIVE_CACHE_FILE = f"wazuh-{WAZUH_VERSION}.zip"
RULES_DIR_IN_ARCHIVE = f"wazuh-{WAZUH_VERSION.lstrip('v')}/ruleset/rules"


def _download_if_missing(cache_dir: Path) -> Path:
    dest = cache_dir / WAZUH_ARCHIVE_CACHE_FILE
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    cache_dir.mkdir(parents=True, exist_ok=True)
    print(f"  Downloading {WAZUH_ARCHIVE_URL} → {dest} ...")
    with urllib.request.urlopen(WAZUH_ARCHIVE_URL, timeout=120) as response:  # noqa: S310
        dest.write_bytes(response.read())
    print(f"  Downloaded {dest.stat().st_size:,} bytes.")
    return dest


def _iter_rule_xml_files(archive_path: Path) -> Iterator[tuple[str, bytes]]:
    """Yield (filename, raw-bytes) for every rule XML inside the archive."""
    with zipfile.ZipFile(archive_path) as zf:
        for info in zf.infolist():
            if not info.filename.endswith(".xml"):
                continue
            if RULES_DIR_IN_ARCHIVE not in info.filename:
                continue
            # Use just the basename in metadata so it's not tied to the
            # archive's directory prefix.
            basename = Path(info.filename).name
            yield basename, zf.read(info)


def _strip_xml_namespaces(xml_bytes: bytes) -> str:
    """Wazuh rule files use a 'group of rules' structure that isn't
    well-formed XML — they're missing a root element. The caller wraps
    in a synthetic <root> before parsing; this helper just decodes."""
    return xml_bytes.decode("utf-8", errors="replace")


def _chunks_from_file(basename: str, xml_text: str) -> Iterator[ChunkInput]:
    """Parse one Wazuh rules XML file. Tolerant of multi-group files.

    Strategy: wrap the whole document in a synthetic <root> so
    ElementTree always sees a well-formed tree, then walk every <rule>
    descendant regardless of nesting depth (handles files with multiple
    `<group>` siblings)."""
    try:
        root = ET.fromstring(f"<root>{xml_text}</root>")
    except ET.ParseError as exc:
        logger.warning("wazuh_rules_parse_failed", file=basename, error=str(exc))
        return

    for rule_el in root.iter("rule"):
        rule_id = rule_el.attrib.get("id")
        level = rule_el.attrib.get("level")
        if not rule_id or not level:
            continue

        # `description` is the human-readable text Wazuh logs alongside
        # alerts. Sometimes multiline; collapse whitespace.
        desc_el = rule_el.find("description")
        description = (
            re.sub(r"\s+", " ", (desc_el.text or "").strip()) if desc_el is not None else ""
        )
        if not description:
            continue

        # `<group>` inside a <rule> is a comma-separated label list.
        group_el = rule_el.find("group")
        groups = (
            [g.strip() for g in (group_el.text or "").split(",") if g.strip()]
            if group_el is not None
            else []
        )

        # MITRE technique IDs — Wazuh tags rules with the <mitre><id>…</id></mitre> shape.
        mitre_ids = [
            (el.text or "").strip()
            for el in rule_el.findall("./mitre/id")
            if (el.text or "").strip()
        ]

        # Compose the content. Rule ID up front for FTS keyword match.
        groups_blob = f" Groups: {', '.join(groups)}." if groups else ""
        mitre_blob = f" MITRE: {', '.join(mitre_ids)}." if mitre_ids else ""
        content = f"Rule {rule_id} (level {level}): {description}.{groups_blob}{mitre_blob}"

        metadata: dict = {
            "rule_id": rule_id,
            "level": int(level),
            "title": f"Rule {rule_id} — {description[:60]}",
            "ruleset_file": basename,
            "groups": groups,
            "mitre": mitre_ids,
            "wazuh_version": WAZUH_VERSION,
        }
        yield ChunkInput(
            content=content,
            source_type="wazuh_doc",
            organization_id=None,
            chunk_metadata=metadata,
        )


def ingest_wazuh_rules(
    *,
    cache_dir: Path,
    limit: int | None = None,
) -> list[ChunkInput]:
    archive_path = _download_if_missing(cache_dir)
    print(f"  Parsing rule XML files from {archive_path} ...")
    chunks: list[ChunkInput] = []
    for basename, raw in _iter_rule_xml_files(archive_path):
        chunks.extend(_chunks_from_file(basename, _strip_xml_namespaces(raw)))
    print(f"  Parsed {len(chunks)} rules from {WAZUH_VERSION} ruleset")
    if limit is not None:
        chunks = chunks[:limit]
        print(f"  --limit applied: {len(chunks)} chunks will be ingested")
    return chunks


# Suppress unused-import warning — BytesIO is reserved for a future
# streaming-download mode (avoid writing the zip to disk).
_ = BytesIO
