# File-level disable — this is the data-ingest path of a CLI: it
# legitimately writes progress to stdout (T201). TC003 wants stdlib
# imports used only in annotations moved to a TYPE_CHECKING block —
# noise for a CLI module where the slight runtime cost is irrelevant.
# ruff: noqa: T201, TC003
"""MITRE ATT&CK technique ingester (STIX 2.x).

Source: MITRE/CTI's enterprise-attack.json on GitHub. The STIX bundle
contains every domain object — attack-patterns (techniques), intrusion-sets,
malware, courses-of-action, etc. We extract attack-patterns only;
those are what map cleanly to "one chunk per technique" per doc 06.

Each technique becomes one KnowledgeChunk with:
  - source_type = 'attack'
  - content = "T<id> (<name>): <description>"  (plus tactic phases trailer)
  - chunk_metadata = {
        technique:      'T1110.001'
        title:          'Password Guessing'
        attack_version: '14.1'           # ATT&CK release this snapshot is from
        kill_chain_phases: ['credential-access']
        is_subtechnique: True
        parent_technique: 'T1110'        # for sub-techniques
    }

Deprecated and revoked techniques are excluded — they confuse retrieval
("T1499 is deprecated" surfacing alongside the active replacement).
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import structlog
from wolf_server.knowledge.store import ChunkInput

logger = structlog.get_logger(__name__)

# Pinned MITRE/CTI release. Bump this URL + clear the cache to pull a newer
# ATT&CK version; the bump is intentional (re-embedding is expensive).
ATTACK_URL = (
    "https://raw.githubusercontent.com/mitre-attack/attack-stix-data/master/"
    "enterprise-attack/enterprise-attack.json"
)
ATTACK_CACHE_FILE = "enterprise-attack.json"


def _download_if_missing(cache_dir: Path) -> Path:
    """Fetch the STIX bundle once. Subsequent runs read the cache."""
    dest = cache_dir / ATTACK_CACHE_FILE
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    cache_dir.mkdir(parents=True, exist_ok=True)
    print(f"  Downloading {ATTACK_URL} → {dest} ...")
    # urlopen is fine here — single one-shot fetch from a known public URL,
    # no user-controlled URL component, S310 ruff rule waived.
    with urllib.request.urlopen(ATTACK_URL, timeout=60) as response:  # noqa: S310
        dest.write_bytes(response.read())
    print(f"  Downloaded {dest.stat().st_size:,} bytes.")
    return dest


def _technique_id_from(obj: dict) -> str | None:
    """ATT&CK ID lives under external_references with source_name='mitre-attack'."""
    for ref in obj.get("external_references", []):
        if ref.get("source_name") == "mitre-attack":
            return ref.get("external_id")
    return None


def _kill_chain_phases(obj: dict) -> list[str]:
    return [
        p.get("phase_name", "") for p in obj.get("kill_chain_phases", []) if p.get("phase_name")
    ]


def _chunks_from_bundle(bundle: dict, attack_version: str) -> Iterator[ChunkInput]:
    for obj in bundle.get("objects", []):
        if obj.get("type") != "attack-pattern":
            continue
        if obj.get("revoked") or obj.get("x_mitre_deprecated"):
            continue
        technique_id = _technique_id_from(obj)
        if not technique_id:
            continue
        name = obj.get("name", "").strip()
        description = obj.get("description", "").strip()
        if not name or not description:
            continue

        phases = _kill_chain_phases(obj)
        is_subtechnique = "." in technique_id
        parent = technique_id.split(".")[0] if is_subtechnique else None

        # Compose content with the ATT&CK ID up front so keyword retrieval
        # ("T1110") hits cleanly via FTS. Tactics phase trailer aids
        # semantic search for queries like "credential access techniques".
        tactic_line = f" Tactic phases: {', '.join(phases)}." if phases else ""
        content = f"{technique_id} ({name}): {description}{tactic_line}"

        metadata: dict = {
            "technique": technique_id,
            "title": f"{technique_id} — {name}",
            "attack_version": attack_version,
            "kill_chain_phases": phases,
            "is_subtechnique": is_subtechnique,
        }
        if parent:
            metadata["parent_technique"] = parent

        yield ChunkInput(
            content=content,
            source_type="attack",
            organization_id=None,
            chunk_metadata=metadata,
        )


def ingest_attack(
    *,
    cache_dir: Path,
    limit: int | None = None,
) -> list[ChunkInput]:
    """Return ChunkInput list for the enterprise-ATT&CK matrix.

    Network call (once, cached) inside `_download_if_missing`; the rest is
    pure parsing so callers can test against fixture bundles.
    """
    path = _download_if_missing(cache_dir)
    print(f"  Parsing {path} ...")
    bundle = json.loads(path.read_text())

    # The bundle's x_mitre_attack_spec_version isn't the matrix version
    # operators care about; the matrix version is on the x-mitre-collection
    # object. Find it; fall back to "unknown" if the schema shifts.
    attack_version = "unknown"
    for obj in bundle.get("objects", []):
        if obj.get("type") == "x-mitre-collection":
            attack_version = obj.get("x_mitre_version", attack_version)
            break

    chunks = list(_chunks_from_bundle(bundle, attack_version))
    print(f"  Parsed {len(chunks)} active techniques (ATT&CK matrix version: {attack_version})")
    if limit is not None:
        chunks = chunks[:limit]
        print(f"  --limit applied: {len(chunks)} chunks will be ingested")
    return chunks
