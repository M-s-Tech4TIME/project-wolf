"""Compare single-leg (v1.5 only) vs chained (v1.5 + v2-moe RRF) retrieval.

Both modes hit the live DB. The single-leg path queries embedding (v1.5).
The chained path queries embedding AND embedding_v2 (v2-moe), fusing
via RRF along with BM25 — exactly what the live PgvectorKnowledgeStore
does when EMBEDDING_MODEL_AUX is set.

Same 20-query battery with known-correct answers as full_corpus_v2_eval.
"""

# ruff: noqa: T201, E402, B905

import sys
from pathlib import Path

_ORCH = Path(__file__).resolve().parents[2] / "services" / "orchestrator"
if _ORCH.is_dir():
    _orch_str = str(_ORCH)
    sys.path[:] = [p for p in sys.path if p != _orch_str]
    sys.path.insert(0, _orch_str)

import asyncio
import statistics
import time
import uuid

from app.config import get_settings
from app.database import db_session
from app.knowledge.embeddings import (
    make_embedding_provider,
    make_embedding_provider_aux,
)
from app.knowledge.store import PgvectorKnowledgeStore
from app.tenancy.models import Tenant
from sqlalchemy import select

# Same 20-query battery as full_corpus_v2_eval.py.
KEYED_QUERIES: list[tuple[str, str, str]] = [
    ("What does Wazuh rule 5712 do?",                  "rule_id",   "5712"),
    ("Explain Wazuh rule 5710",                        "rule_id",   "5710"),
    ("rule 31100 details",                             "rule_id",   "31100"),
    ("What is rule 80700",                             "rule_id",   "80700"),
    ("Wazuh sshd authentication failure rule",         "rule_id",   "5710"),
    ("brute force composite rule",                     "rule_id",   "5712"),
    ("What is ATT&CK T1110?",                          "technique", "T1110"),
    ("ATT&CK technique T1078 Valid Accounts",          "technique", "T1078"),
    ("MITRE T1059.001 PowerShell",                     "technique", "T1059.001"),
    ("Spearphishing attachment ATT&CK technique",      "technique", "T1566.001"),
    ("Process Injection ATT&CK",                       "technique", "T1055"),
    ("T1003 OS Credential Dumping",                    "technique", "T1003"),
    ("Scheduled Task technique",                       "technique", "T1053"),
    ("ATT&CK Data from Local System",                  "technique", "T1005"),
    ("password spraying technique",                    "technique", "T1110.003"),
    ("DNS tunneling MITRE technique",                  "technique", "T1071.004"),
    ("Pass the Hash ATT&CK",                           "technique", "T1550.002"),
    ("Domain Trust Discovery",                         "technique", "T1482"),
    ("ATT&CK exfiltration over web service",           "technique", "T1567"),
    ("MITRE T1547 Boot or Logon Autostart Execution",  "technique", "T1547"),
]


def _correct(retrieved: list, kind: str, expected: str) -> bool:
    """True iff any of the retrieved chunks' metadata matches the ground truth."""
    for hit in retrieved:
        if kind == "rule_id":
            if str(hit.chunk_metadata.get("rule_id", "")) == expected:
                return True
        elif kind == "technique":
            tech = str(hit.chunk_metadata.get("technique", ""))
            if not tech:
                continue
            if (tech == expected
                or tech.startswith(expected + ".")
                or expected.startswith(tech + ".")):
                return True
    return False


async def _eval(label: str, store: PgvectorKnowledgeStore, tenant_id: uuid.UUID) -> None:
    p1 = p5 = 0
    latencies: list[float] = []
    misses_at_5: list[str] = []
    for q, kind, expected in KEYED_QUERIES:
        t0 = time.perf_counter()
        hits = await store.search(tenant_id=tenant_id, query_text=q, limit=5)
        latencies.append((time.perf_counter() - t0) * 1000)
        if not hits:
            misses_at_5.append(q)
            continue
        if _correct(hits[:1], kind, expected):
            p1 += 1
        if _correct(hits, kind, expected):
            p5 += 1
        else:
            misses_at_5.append(q)
    n = len(KEYED_QUERIES)
    print(f"\n=== {label} ===")
    print(f"  precision@1: {p1}/{n} ({100*p1/n:.0f}%)")
    print(f"  precision@5: {p5}/{n} ({100*p5/n:.0f}%)")
    print(f"  latency:     mean={statistics.mean(latencies):.0f}ms  "
          f"p50={statistics.median(latencies):.0f}ms  "
          f"max={max(latencies):.0f}ms")
    if misses_at_5:
        print(f"  missed @5:   {', '.join(repr(m) for m in misses_at_5)}")


async def main() -> int:
    settings = get_settings()
    async with db_session() as session:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.slug == "acme"))
        ).scalar_one()
        tenant_id = tenant.id

    primary = make_embedding_provider(settings)
    aux = make_embedding_provider_aux(settings)
    if aux is None:
        print("ERROR: EMBEDDING_MODEL_AUX is not set; chained mode unavailable.")
        return 2

    print("Comparing single-leg vs chained retrieval on the live corpus.")
    print(f"  Primary embedder: {primary.model_id}")
    print(f"  Aux embedder:     {aux.model_id}")

    # Use two separate DB sessions because each store holds its session
    # for the duration of the eval (search() opens/closes per call).
    async with db_session() as session_a:
        store_single = PgvectorKnowledgeStore(session_a, primary, embedder_aux=None)
        await _eval("Single-leg (BM25 + v1.5)", store_single, tenant_id)

    async with db_session() as session_b:
        store_chained = PgvectorKnowledgeStore(session_b, primary, embedder_aux=aux)
        await _eval("Chained (BM25 + v1.5 + v2-moe)", store_chained, tenant_id)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
