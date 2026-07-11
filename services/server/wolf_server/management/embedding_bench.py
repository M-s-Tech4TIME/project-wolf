"""Comparative retrieval benchmark across embedding configurations (ADR 0033).

Answers "which embedding setup is best FOR WOLF'S CORPUS" empirically
instead of by leaderboard reputation. Methodology (known-item retrieval):

  1. Sample N gold chunks (stratified by source_type) + M distractors from
     the live `knowledge_chunks` corpus (fixed RNG seed — reproducible).
  2. Generate one natural analyst question per gold chunk with the local
     chat model (the chunk is the gold answer). Questions are cached to a
     JSON file so re-runs score the SAME query set.
  3. Embed the sampled corpus under every configuration:
       nomic    — nomic-embed-text, 768, task prefixes
       moe      — nomic-embed-text-v2-moe, 768, task prefixes, 1800 cap
       qwen4096 — qwen3-embedding, native 4096, instruct query prefix
       qwen768  — derived from qwen4096 by MRL truncate+renormalize
                  (client-side; mathematically identical to Ollama's
                  server-side `dimensions` handling)
  4. Load everything into a scratch table (regular table, dropped at the
     end unless --keep) with one vector column per configuration plus a
     tsvector column — every configuration is ranked by the SAME exact
     `<=>` scan and fused with the SAME FTS leg, mirroring the store's
     RRF exactly (k=60, candidate limit 25).
  5. Score Recall@1/5/10 and MRR@10 for each configuration, vector-leg
     only AND hybrid (vector+FTS RRF; combos add the moe leg — the
     3-leg fusion the store runs in chained mode). Report mean query-embed
     latency per model (the real per-request cost difference).

Usage:
    cd services/server
    set -a && source ../../.env && set +a
    uv run python -m wolf_server.management.embedding_bench \
        --gold 100 --distractors 1000 \
        --queries-file ../../.local/embedding_bench_queries.json

Honesty notes:
  - LLM-generated questions are paraphrases; the cache file keeps the
    comparison apples-to-apples across runs and the seed keeps the chunk
    sample stable, but absolute numbers depend on the query style.
  - The scratch corpus (~1.1K rows) ranks by exact scan for every config —
    index effects (HNSW recall, BQ oversampling) are deliberately OUT of
    scope here; this measures the EMBEDDING GEOMETRY quality alone.
"""

import argparse
import asyncio
import json
import math
import os
import random
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text

from wolf_server.config import get_settings
from wolf_server.database import db_session
from wolf_server.knowledge.embeddings import OllamaEmbeddingAdapter
from wolf_server.knowledge.store import RANKER_CANDIDATE_LIMIT, RRF_K

_BENCH_TABLE = "wolf_eval_bench"

_QUESTION_PROMPT = (
    "You are creating retrieval-evaluation data for a Wazuh security "
    "assistant. Write ONE natural question a security analyst would ask "
    "that the following passage directly answers. Do not copy phrases "
    "verbatim from the passage; paraphrase. Output ONLY the question.\n\n"
    "PASSAGE:\n{passage}\n"
)


def mrl_truncate(vector: list[float], dim: int) -> list[float]:
    """MRL truncation: keep the first `dim` components, L2-renormalize.

    Exactly what Ollama's /api/embed `dimensions` field does server-side
    for MRL-trained models — computing it client-side from the native
    vector lets one expensive embedding pass serve two width configs.
    """
    head = vector[:dim]
    norm = math.sqrt(sum(x * x for x in head))
    if norm == 0.0:
        return head
    return [x / norm for x in head]


def rrf_fuse(*rank_legs: dict[uuid.UUID, int], k: int = RRF_K) -> list[uuid.UUID]:
    """Reciprocal Rank Fusion, identical to the store's search() fusion."""
    scores: dict[uuid.UUID, float] = {}
    for leg in rank_legs:
        for chunk_id, rank in leg.items():
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return [cid for cid, _ in sorted(scores.items(), key=lambda kv: -kv[1])]


@dataclass
class Metrics:
    """Known-item retrieval metrics over a query set."""

    recall_at_1: float
    recall_at_5: float
    recall_at_10: float
    mrr_at_10: float
    n: int

    def row(self, label: str) -> str:
        return (
            f"{label:<26} {self.recall_at_1:>7.3f} {self.recall_at_5:>7.3f} "
            f"{self.recall_at_10:>7.3f} {self.mrr_at_10:>7.3f}   (n={self.n})"
        )


def score(results: list[tuple[uuid.UUID, list[uuid.UUID]]]) -> Metrics:
    """`results` = [(gold_id, ranked_ids), ...] -> aggregate metrics."""
    r1 = r5 = r10 = 0
    mrr = 0.0
    for gold, ranked in results:
        top10 = ranked[:10]
        if gold in top10:
            rank = top10.index(gold) + 1
            mrr += 1.0 / rank
            r10 += 1
            if rank <= 5:
                r5 += 1
            if rank == 1:
                r1 += 1
    n = len(results)
    if n == 0:
        return Metrics(0.0, 0.0, 0.0, 0.0, 0)
    return Metrics(r1 / n, r5 / n, r10 / n, mrr / n, n)


def sanitize_question(raw: str) -> str:
    """Strip think-blocks / boilerplate from an LLM answer, keep the question."""
    text_out = raw
    if "</think>" in text_out:
        text_out = text_out.split("</think>")[-1]
    lines = [ln.strip().strip('"') for ln in text_out.strip().splitlines() if ln.strip()]
    if not lines:
        return ""
    # Prefer the last line that looks like a question.
    for line in reversed(lines):
        if line.endswith("?"):
            return line
    return lines[-1]


async def _generate_question(
    client: httpx.AsyncClient, base_url: str, model: str, passage: str
) -> str:
    response = await client.post(
        f"{base_url}/api/generate",
        json={
            "model": model,
            "prompt": _QUESTION_PROMPT.format(passage=passage[:4000]),
            "stream": False,
            "think": False,
            "options": {"temperature": 0.3, "num_ctx": 4096},
        },
    )
    response.raise_for_status()
    return sanitize_question(response.json().get("response", ""))


async def main() -> int:  # noqa: PLR0915  — a linear benchmark script
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gold", type=int, default=100)
    parser.add_argument("--distractors", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=1307)
    parser.add_argument("--queries-file", default="../../.local/embedding_bench_queries.json")
    parser.add_argument(
        "--gen-model",
        default="qwen3:8b",
        help=(
            "OLLAMA tag used to generate the questions (must be a local "
            "generation model — the configured default chat model may be a "
            "hosted OpenRouter id that /api/generate does not know)."
        ),
    )
    parser.add_argument("--keep", action="store_true", help="Keep the scratch table.")
    parser.add_argument("--report-file", default="", help="Also write the report here.")
    args = parser.parse_args()

    if not os.environ.get("DATABASE_URL"):
        sys.stderr.write("ERROR: DATABASE_URL is not set. Source .env first.\n")
        return 2

    settings = get_settings()
    base_url = settings.ollama_base_url

    # ── 1. Sample gold + distractor chunks (seeded, stratified) ────────────
    async with db_session() as session:
        rows = (
            await session.execute(text("SELECT id, source_type, content FROM knowledge_chunks"))
        ).all()
    rng = random.Random(args.seed)  # noqa: S311  — reproducible sampling, not crypto
    by_type: dict[str, list[Any]] = {}
    for row in rows:
        by_type.setdefault(row[1], []).append(row)
    gold_rows: list[Any] = []
    total = sum(len(v) for v in by_type.values())
    for _source_type, bucket in sorted(by_type.items()):
        take = max(1, round(args.gold * len(bucket) / total)) if len(bucket) >= 10 else 0
        gold_rows.extend(rng.sample(bucket, min(take, len(bucket))))
    gold_rows = gold_rows[: args.gold]
    gold_ids = {row[0] for row in gold_rows}
    remaining = [row for row in rows if row[0] not in gold_ids]
    corpus_rows = gold_rows + rng.sample(remaining, min(args.distractors, len(remaining)))
    sys.stdout.write(
        f"Sampled {len(gold_rows)} gold + {len(corpus_rows) - len(gold_rows)} "
        f"distractor chunks (seed {args.seed})\n"
    )

    # ── 2. Generate (or load cached) questions ─────────────────────────────
    queries_path = Path(args.queries_file)
    queries: dict[str, str] = {}
    if queries_path.exists():
        queries = json.loads(queries_path.read_text())
        sys.stdout.write(f"Loaded {len(queries)} cached questions from {queries_path}\n")
    missing = [row for row in gold_rows if str(row[0]) not in queries]
    if missing:
        sys.stdout.write(f"Generating {len(missing)} questions with {args.gen_model}…\n")
        async with httpx.AsyncClient(timeout=180.0) as client:
            for i, row in enumerate(missing, 1):
                question = await _generate_question(client, base_url, args.gen_model, row[2])
                if question:
                    queries[str(row[0])] = question
                if i % 10 == 0:
                    sys.stdout.write(f"  {i}/{len(missing)}\n")
        queries_path.parent.mkdir(parents=True, exist_ok=True)
        queries_path.write_text(json.dumps(queries, indent=2))
    gold_rows = [row for row in gold_rows if str(row[0]) in queries]
    sys.stdout.write(f"Query set: {len(gold_rows)} questions\n")

    # ── 3. Embed the corpus under every configuration ──────────────────────
    nomic = OllamaEmbeddingAdapter(
        base_url,
        model="nomic-embed-text",
        dimension=768,
        document_prefix="search_document: ",
        query_prefix="search_query: ",
        num_ctx=2048,
        timeout=300.0,
    )
    moe = OllamaEmbeddingAdapter(
        base_url,
        model="nomic-embed-text-v2-moe",
        dimension=768,
        document_prefix="search_document: ",
        query_prefix="search_query: ",
        max_input_chars=1800,
        timeout=300.0,
    )
    qwen = OllamaEmbeddingAdapter(
        base_url,
        model="qwen3-embedding:latest",
        dimension=4096,
        query_prefix=(
            "Instruct: Given a web search query, retrieve relevant passages "
            "that answer the query\nQuery: "
        ),
        num_ctx=2048,
        timeout=600.0,
    )
    contents = [row[2] for row in corpus_rows]
    corpus_vectors: dict[str, list[list[float]]] = {}
    for name, adapter in (("nomic", nomic), ("moe", moe), ("qwen4096", qwen)):
        started = time.monotonic()
        vectors: list[list[float]] = []
        if name == "moe":
            # v2-moe rejects a small number of chunks even truncated —
            # embed per-row so one failure doesn't sink the batch; failed
            # rows get a zero vector and are excluded from that leg's
            # ranking (mirrors the store's NULL-skip semantics).
            for content in contents:
                try:
                    vectors.append((await adapter.embed([content]))[0])
                except Exception:
                    vectors.append([0.0] * 768)
        else:
            vectors = await adapter.embed(contents)
        corpus_vectors[name] = vectors
        sys.stdout.write(
            f"Embedded corpus with {name}: {len(vectors)} vectors "
            f"in {time.monotonic() - started:.0f}s\n"
        )
    corpus_vectors["qwen768"] = [mrl_truncate(v, 768) for v in corpus_vectors["qwen4096"]]

    # ── 4. Load the scratch table ───────────────────────────────────────────
    async with db_session() as session:
        await session.execute(text(f"DROP TABLE IF EXISTS {_BENCH_TABLE}"))
        await session.execute(
            text(
                f"CREATE TABLE {_BENCH_TABLE} ("
                "id uuid PRIMARY KEY, content text NOT NULL, "
                "tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED, "
                "v_nomic vector(768), v_moe vector(768), "
                "v_qwen4096 vector(4096), v_qwen768 vector(768))"
            )
        )
        for i, row in enumerate(corpus_rows):
            moe_vec = corpus_vectors["moe"][i]
            await session.execute(
                text(
                    f"INSERT INTO {_BENCH_TABLE} "  # noqa: S608
                    "(id, content, v_nomic, v_moe, v_qwen4096, v_qwen768) "
                    "VALUES (:id, :content, :nomic, :moe, :q4096, :q768)"
                ),
                {
                    "id": row[0],
                    "content": row[2],
                    "nomic": str(corpus_vectors["nomic"][i]),
                    "moe": str(moe_vec) if any(moe_vec) else None,
                    "q4096": str(corpus_vectors["qwen4096"][i]),
                    "q768": str(corpus_vectors["qwen768"][i]),
                },
            )
        await session.commit()

    # ── 5. Rank every query under every configuration ──────────────────────
    async def leg(session: Any, column: str, qvec: list[float]) -> dict[uuid.UUID, int]:
        ranked = (
            await session.execute(
                text(
                    f"SELECT id FROM {_BENCH_TABLE} WHERE {column} IS NOT NULL "  # noqa: S608
                    f"ORDER BY {column} <=> (:qv)::vector LIMIT {RANKER_CANDIDATE_LIMIT}"
                ),
                {"qv": str(qvec)},
            )
        ).all()
        return {row[0]: rank for rank, row in enumerate(ranked, start=1)}

    async def fts_leg(session: Any, question: str) -> dict[uuid.UUID, int]:
        ranked = (
            await session.execute(
                text(
                    f"SELECT id FROM {_BENCH_TABLE} "  # noqa: S608
                    "WHERE tsv @@ plainto_tsquery('english', :q) "
                    "ORDER BY ts_rank_cd(tsv, plainto_tsquery('english', :q)) DESC "
                    f"LIMIT {RANKER_CANDIDATE_LIMIT}"
                ),
                {"q": question},
            )
        ).all()
        return {row[0]: rank for rank, row in enumerate(ranked, start=1)}

    variants: dict[str, list[tuple[uuid.UUID, list[uuid.UUID]]]] = {}
    query_latency: dict[str, list[float]] = {"nomic": [], "moe": [], "qwen": []}
    async with db_session() as session:
        for row in gold_rows:
            gold_id, question = row[0], queries[str(row[0])]
            started = time.monotonic()
            qv_nomic = await nomic.embed_query(question)
            query_latency["nomic"].append(time.monotonic() - started)
            started = time.monotonic()
            qv_moe = await moe.embed_query(question)
            query_latency["moe"].append(time.monotonic() - started)
            started = time.monotonic()
            qv_qwen = await qwen.embed_query(question)
            query_latency["qwen"].append(time.monotonic() - started)
            qv_qwen768 = mrl_truncate(qv_qwen, 768)

            legs = {
                "nomic": await leg(session, "v_nomic", qv_nomic),
                "moe": await leg(session, "v_moe", qv_moe),
                "qwen4096": await leg(session, "v_qwen4096", qv_qwen),
                "qwen768": await leg(session, "v_qwen768", qv_qwen768),
            }
            fts = await fts_leg(session, question)

            def record(variant: str, ranked: list[uuid.UUID], gold: uuid.UUID = gold_id) -> None:
                variants.setdefault(variant, []).append((gold, ranked))

            record("nomic (vector)", list(legs["nomic"]))
            record("qwen768 (vector)", list(legs["qwen768"]))
            record("qwen4096 (vector)", list(legs["qwen4096"]))
            record("nomic+FTS (hybrid)", rrf_fuse(legs["nomic"], fts))
            record("nomic+moe+FTS (combo)", rrf_fuse(legs["nomic"], legs["moe"], fts))
            record("qwen768+FTS (hybrid)", rrf_fuse(legs["qwen768"], fts))
            record("qwen4096+FTS (hybrid)", rrf_fuse(legs["qwen4096"], fts))
            record("qwen4096+moe+FTS (combo)", rrf_fuse(legs["qwen4096"], legs["moe"], fts))

    # ── 6. Report ───────────────────────────────────────────────────────────
    lines = [
        f"\n=== Embedding retrieval benchmark (seed {args.seed}, "
        f"{len(gold_rows)} queries, corpus {len(corpus_rows)}) ===",
        f"{'configuration':<26} {'R@1':>7} {'R@5':>7} {'R@10':>7} {'MRR@10':>7}",
    ]
    lines.extend(
        score(results).row(variant)
        for variant, results in sorted(variants.items(), key=lambda kv: -score(kv[1]).mrr_at_10)
    )
    lines.append("")
    lines.extend(
        f"mean query-embed latency [{name}]: {sum(vals) / len(vals) * 1000:.0f} ms"
        for name, vals in query_latency.items()
        if vals
    )
    report = "\n".join(lines) + "\n"
    sys.stdout.write(report)
    if args.report_file:
        Path(args.report_file).write_text(report)

    if not args.keep:
        async with db_session() as session:
            await session.execute(text(f"DROP TABLE IF EXISTS {_BENCH_TABLE}"))
            await session.commit()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
