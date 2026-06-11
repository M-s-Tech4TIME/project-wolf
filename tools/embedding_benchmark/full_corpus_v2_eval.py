"""Full-corpus head-to-head: nomic-embed-text:v1.5 vs nomic-embed-text-v2-moe.

Both adapters via Ollama. v1.5 vectors come from the live DB (already
ingested by tools/seed_knowledge). v2-moe vectors are computed in memory
so the live system stays on v1.5 during the test (no destructive
re-embed). Outputs precision@1 and precision@5 on a query battery with
known-correct answers (rule IDs + technique IDs) and a qualitative
side-by-side for conceptual queries.

Run from repo root:
    cd services/server
    set -a && source ../../.env && set +a
    cd ../..
    uv run python -m tools.embedding_benchmark.full_corpus_v2_eval
"""

# ruff: noqa: T201, N806, B905, E501

import asyncio
import statistics
import sys
import time

from sqlalchemy import select
from wolf_server.database import db_session
from wolf_server.knowledge.embeddings import OllamaEmbeddingAdapter
from wolf_server.knowledge.models import KnowledgeChunk

# Queries with a "known correct answer" — the correct chunk(s) are the
# ones whose metadata matches the expected_rule_id or expected_technique.
# Multiple chunks can satisfy (e.g. a parent technique + its sub-techniques).
KEYED_QUERIES: list[tuple[str, str, str]] = [
    # (query, kind, expected_value)
    ("What does Wazuh rule 5712 do?", "rule_id", "5712"),
    ("Explain Wazuh rule 5710", "rule_id", "5710"),
    ("rule 31100 details", "rule_id", "31100"),
    ("What is rule 80700", "rule_id", "80700"),
    ("Wazuh sshd authentication failure rule", "rule_id", "5710"),
    ("brute force composite rule", "rule_id", "5712"),
    ("What is ATT&CK T1110?", "technique", "T1110"),
    ("ATT&CK technique T1078 Valid Accounts", "technique", "T1078"),
    ("MITRE T1059.001 PowerShell", "technique", "T1059.001"),
    ("Spearphishing attachment ATT&CK technique", "technique", "T1566.001"),
    ("Process Injection ATT&CK", "technique", "T1055"),
    ("T1003 OS Credential Dumping", "technique", "T1003"),
    ("Scheduled Task technique", "technique", "T1053"),
    ("ATT&CK Data from Local System", "technique", "T1005"),
    ("password spraying technique", "technique", "T1110.003"),
    ("DNS tunneling MITRE technique", "technique", "T1071.004"),
    ("Pass the Hash ATT&CK", "technique", "T1550.002"),
    ("Domain Trust Discovery", "technique", "T1482"),
    ("ATT&CK exfiltration over web service", "technique", "T1567"),
    ("MITRE T1547 Boot or Logon Autostart Execution", "technique", "T1547"),
]

# Conceptual queries — qualitative side-by-side, no objective correct answer.
CONCEPTUAL_QUERIES = [
    "How does an attacker establish persistence on a Linux host?",
    "techniques for lateral movement in an enterprise network",
    "how to detect data exfiltration via web traffic",
    "credential harvesting from memory",
    "evading endpoint detection and response",
]


def _correct_chunk_ids_for(
    kind: str, expected: str, all_chunks: list[tuple[str, str, dict]]
) -> set[str]:
    """Return the chunk IDs that satisfy the (kind, expected) ground truth.

    For technique queries, parent techniques (T1110) count as correct
    matches for any sub-technique query under them (T1110.001), and
    sub-techniques are also valid answers to a parent query.
    """
    correct: set[str] = set()
    for chunk_id, _source, meta in all_chunks:
        if kind == "rule_id":
            if str(meta.get("rule_id", "")) == expected:
                correct.add(chunk_id)
        elif kind == "technique":
            tech = str(meta.get("technique", ""))
            if not tech:
                continue
            if (
                tech == expected
                or tech.startswith(expected + ".")
                or expected.startswith(tech + ".")
            ):
                correct.add(chunk_id)
    return correct


def _cosine(a, b) -> float:
    # v1.5 vectors from DB are normalised (pgvector cosine_ops handles
    # this) but raw nomic outputs are not unit-length; v2-moe is the
    # same. We dot-product for ordering; normalisation only affects
    # scale, not ranking.
    return sum(x * y for x, y in zip(a, b))


def _top_k(
    query_vec: list[float],
    chunk_vecs: dict[str, list[float]],
    k: int,
) -> list[str]:
    """Return the chunk_ids with the K highest cosine similarity to query."""
    scored = [(cid, _cosine(query_vec, cv)) for cid, cv in chunk_vecs.items()]
    scored.sort(key=lambda x: -x[1])
    return [cid for cid, _ in scored[:k]]


async def main() -> int:
    print("=== Loading 5173 chunks + v1.5 vectors from live DB ===")
    async with db_session() as session:
        rows = (
            await session.execute(
                select(
                    KnowledgeChunk.id,
                    KnowledgeChunk.content,
                    KnowledgeChunk.source_type,
                    KnowledgeChunk.chunk_metadata,
                    KnowledgeChunk.embedding,
                )
            )
        ).all()
    print(f"  Loaded {len(rows)} chunks")

    all_chunks: list[tuple[str, str, dict]] = [(str(r[0]), r[2], r[3]) for r in rows]
    chunk_contents: dict[str, str] = {str(r[0]): r[1] for r in rows}
    v1_vectors: dict[str, list[float]] = {str(r[0]): list(r[4]) for r in rows}

    print("\n=== Embedding the same 5173 chunks with v2-moe (in memory) ===")
    # v2-moe has a 512-token context limit. Long ATT&CK descriptions
    # (the 2.4% of chunks measured earlier) exceed that and Ollama
    # returns 500 'unexpected EOF' rather than silently truncating.
    # In practice an operator running v2-moe MUST truncate inputs;
    # we approximate at 1800 chars (~450 tokens with safety margin).
    # This is the realistic v2-moe-in-production behaviour we should
    # measure, not an artificial "everything embeds" scenario.
    V2_CHAR_LIMIT = 1800
    v2_adapter = OllamaEmbeddingAdapter("http://localhost:11434", model="nomic-embed-text-v2-moe")
    v2_vectors: dict[str, list[float]] = {}
    t0 = time.perf_counter()
    chunk_ids = list(chunk_contents.keys())
    truncated_count = 0
    error_count = 0
    for i, cid in enumerate(chunk_ids):
        text = chunk_contents[cid]
        if len(text) > V2_CHAR_LIMIT:
            text = text[:V2_CHAR_LIMIT]
            truncated_count += 1
        try:
            vec = (await v2_adapter.embed([text]))[0]
        except Exception as exc:
            error_count += 1
            print(f"  v2-moe error on chunk {cid[:8]}: {type(exc).__name__}")
            # Use zero vector as placeholder so retrieval just won't
            # rank this chunk; honest representation of the failure mode.
            vec = [0.0] * 768
        v2_vectors[cid] = vec
        if (i + 1) % 250 == 0:
            elapsed = time.perf_counter() - t0
            print(
                f"  v2-moe progress: {i + 1}/{len(chunk_ids)} "
                f"({elapsed:.0f}s elapsed, truncated={truncated_count}, errors={error_count})"
            )
    v2_corpus_seconds = time.perf_counter() - t0
    print(
        f"  v2-moe corpus embed: {v2_corpus_seconds:.1f}s "
        f"({v2_corpus_seconds * 1000 / len(chunk_ids):.0f}ms/chunk)"
    )
    print(
        f"  truncated (>1800 chars): {truncated_count} chunks "
        f"({100 * truncated_count / len(chunk_ids):.1f}%)"
    )
    print(f"  errors after truncation:  {error_count} chunks")

    v1_adapter = OllamaEmbeddingAdapter("http://localhost:11434", model="nomic-embed-text")

    print("\n=== Scoring keyed queries (precision@1, precision@5) ===")
    per_query: list[dict] = []
    p1_v1 = p1_v2 = p5_v1 = p5_v2 = 0
    for q, kind, expected in KEYED_QUERIES:
        correct_ids = _correct_chunk_ids_for(kind, expected, all_chunks)
        if not correct_ids:
            print(f"  [skipped — no chunk matches] {q!r}")
            continue
        qv_v1 = (await v1_adapter.embed([q]))[0]
        qv_v2 = (await v2_adapter.embed([q]))[0]
        top5_v1 = _top_k(qv_v1, v1_vectors, 5)
        top5_v2 = _top_k(qv_v2, v2_vectors, 5)
        hit1_v1 = top5_v1[0] in correct_ids
        hit1_v2 = top5_v2[0] in correct_ids
        hit5_v1 = any(c in correct_ids for c in top5_v1)
        hit5_v2 = any(c in correct_ids for c in top5_v2)
        p1_v1 += int(hit1_v1)
        p1_v2 += int(hit1_v2)
        p5_v1 += int(hit5_v1)
        p5_v2 += int(hit5_v2)
        per_query.append(
            {
                "q": q,
                "expected": expected,
                "v1_hit1": hit1_v1,
                "v2_hit1": hit1_v2,
                "v1_hit5": hit5_v1,
                "v2_hit5": hit5_v2,
            }
        )

    n = len(per_query)
    print(f"\n  Keyed queries: {n}")
    print(
        f"  precision@1   v1.5: {p1_v1}/{n} ({100 * p1_v1 / n:.0f}%)  "
        f"v2-moe: {p1_v2}/{n} ({100 * p1_v2 / n:.0f}%)"
    )
    print(
        f"  precision@5   v1.5: {p5_v1}/{n} ({100 * p5_v1 / n:.0f}%)  "
        f"v2-moe: {p5_v2}/{n} ({100 * p5_v2 / n:.0f}%)"
    )

    print("\n=== Per-query results (where v1.5 and v2-moe disagree on top-1) ===")
    for r in per_query:
        if r["v1_hit1"] != r["v2_hit1"]:
            v1mark = "✓" if r["v1_hit1"] else "✗"
            v2mark = "✓" if r["v2_hit1"] else "✗"
            print(f"  q={r['q']!r:<55s}  v1.5:{v1mark}  v2-moe:{v2mark}  expected={r['expected']}")

    print("\n=== Conceptual queries (qualitative top-3) ===")
    by_chunk_meta = {str(r[0]): r[3] for r in rows}
    by_chunk_source = {str(r[0]): r[2] for r in rows}
    for q in CONCEPTUAL_QUERIES:
        qv_v1 = (await v1_adapter.embed([q]))[0]
        qv_v2 = (await v2_adapter.embed([q]))[0]
        top3_v1 = _top_k(qv_v1, v1_vectors, 3)
        top3_v2 = _top_k(qv_v2, v2_vectors, 3)

        def fmt(cid):
            m = by_chunk_meta.get(cid, {})
            src = by_chunk_source.get(cid, "?")
            title = m.get("title", m.get("technique", m.get("rule_id", cid[:8])))
            return f"{src}/{str(title)[:50]}"

        print(f"\n  Q: {q}")
        print(f"    v1.5:   {' | '.join(fmt(c) for c in top3_v1)}")
        print(f"    v2-moe: {' | '.join(fmt(c) for c in top3_v2)}")

    print("\n=== Per-query latency (v1.5 vs v2-moe, single embed) ===")
    v1_lats: list[float] = []
    v2_lats: list[float] = []
    for q, _, _ in KEYED_QUERIES[:5]:
        for _ in range(3):
            t = time.perf_counter()
            await v1_adapter.embed([q])
            v1_lats.append((time.perf_counter() - t) * 1000)
            t = time.perf_counter()
            await v2_adapter.embed([q])
            v2_lats.append((time.perf_counter() - t) * 1000)
    print(
        f"  v1.5    mean={statistics.mean(v1_lats):5.1f}ms  p50={statistics.median(v1_lats):5.1f}ms"
    )
    print(
        f"  v2-moe  mean={statistics.mean(v2_lats):5.1f}ms  p50={statistics.median(v2_lats):5.1f}ms"
    )
    print(
        f"  v2-moe is {statistics.mean(v2_lats) / statistics.mean(v1_lats):.1f}x slower per query"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
