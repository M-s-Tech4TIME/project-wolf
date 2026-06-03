"""Embedding-provider side-by-side benchmark CLI.

Compares Ollama-hosted nomic-embed-text against the in-process
sentence-transformers BAAI/bge-base-en-v1.5 across three axes:

  1. Cold-start time — wall-clock from adapter construction to first
     successful embedding.
  2. Per-query latency on a fixed set of representative queries.
  3. Retrieval ordering — embeds the dev corpus with each adapter
     into an in-memory dict store and ranks the same query against
     both, showing top-5 by cosine similarity for qualitative review.

Does NOT touch the dev DB. Uses the existing seed_dev_knowledge corpus
in-process (loaded by importing SHARED_CHUNKS + runbook_chunks_for from
the management CLI) so results are reproducible.

This is the empirical input for ADR 0012.
"""

# File-level rule disable — this CLI writes to stdout by design.
# ruff: noqa: T201

import argparse
import asyncio
import json
import statistics
import sys
import time
from dataclasses import dataclass

from wolf_server.knowledge.embeddings import (
    EmbeddingProvider,
    OllamaEmbeddingAdapter,
    SentenceTransformersEmbeddingAdapter,
)
from wolf_server.management.seed_dev_knowledge import (
    SHARED_CHUNKS,
    runbook_chunks_for,
)

QUERIES = [
    "What does Wazuh rule 5712 do?",
    "How does Acme SOC respond to SSH brute-force attacks?",
    "Explain ATT&CK technique T1110.",
    "What is the active response disable_account command?",
    "Past incident with bastion host and SSH attack",
    "How do I tune the brute-force detection threshold?",
    "What is Password Guessing in MITRE ATT&CK?",
    "Acme SOC procedure for rule 5712 triage",
    "What is T1078 Valid Accounts?",
    "When should I block an IP at the perimeter?",
]


@dataclass
class AdapterResult:
    name: str
    cold_start_s: float
    query_latencies_ms: list[float]
    embed_corpus_s: float
    corpus_size: int
    top5: dict[str, list[tuple[str, float, str]]]  # query -> [(title, score, source_type)]


def _cosine(a: list[float], b: list[float]) -> float:
    # Both adapters produce normalized vectors (ST: explicit;
    # Ollama+nomic: model is L2-normalized by default), so dot-product
    # is cosine similarity.
    return sum(x * y for x, y in zip(a, b, strict=True))


async def _bench_one(
    name: str, build: callable, corpus: list[tuple[str, dict, str]]
) -> AdapterResult:
    print(f"\n[{name}] cold-start…", flush=True)
    t0 = time.perf_counter()
    adapter: EmbeddingProvider = build()
    # Warm with one tiny embed to count actual first-inference cost
    _ = await adapter.embed(["warmup"])
    cold = time.perf_counter() - t0
    print(f"[{name}] cold-start: {cold:.2f}s", flush=True)

    # Per-query latency (3 trials each, take median to dampen variance)
    print(f"[{name}] latency over {len(QUERIES)} queries × 3 trials…", flush=True)
    latencies_ms: list[float] = []
    for q in QUERIES:
        trials: list[float] = []
        for _ in range(3):
            t = time.perf_counter()
            await adapter.embed([q])
            trials.append((time.perf_counter() - t) * 1000.0)
        latencies_ms.append(statistics.median(trials))

    # Embed the full corpus and run retrieval
    print(f"[{name}] embedding {len(corpus)} corpus chunks…", flush=True)
    t = time.perf_counter()
    chunk_texts = [content for _, _, content in corpus]
    chunk_meta = [(source_type, meta) for source_type, meta, _ in corpus]
    chunk_vectors = await adapter.embed(chunk_texts)
    embed_corpus = time.perf_counter() - t

    print(f"[{name}] retrieving top-5 per query…", flush=True)
    top5: dict[str, list[tuple[str, float, str]]] = {}
    for q in QUERIES:
        # For BGE asymmetric retrieval, route query embeds through the
        # query-specific path if the adapter exposes it.
        if hasattr(adapter, "embed_query"):
            qv = await adapter.embed_query(q)
        else:
            qv = (await adapter.embed([q]))[0]
        scored = sorted(
            (
                (chunk_meta[i][1].get("title", "<no title>"), _cosine(qv, v), chunk_meta[i][0])
                for i, v in enumerate(chunk_vectors)
            ),
            key=lambda x: -x[1],
        )
        top5[q] = scored[:5]

    return AdapterResult(
        name=name,
        cold_start_s=cold,
        query_latencies_ms=latencies_ms,
        embed_corpus_s=embed_corpus,
        corpus_size=len(corpus),
        top5=top5,
    )


def _print_summary(r: AdapterResult) -> None:
    lats = r.query_latencies_ms
    print(f"\n=== {r.name} ===")
    print(f"  cold-start          : {r.cold_start_s:.2f} s")
    print(
        f"  query latency (n={len(lats)}): "
        f"mean={statistics.mean(lats):.1f}ms  "
        f"p50={statistics.median(lats):.1f}ms  "
        f"p95={sorted(lats)[int(0.95 * len(lats))]:.1f}ms  "
        f"max={max(lats):.1f}ms"
    )
    print(
        f"  embed corpus        : {r.embed_corpus_s * 1000:.0f}ms "
        f"for {r.corpus_size} chunks "
        f"({r.embed_corpus_s * 1000 / r.corpus_size:.0f}ms/chunk)"
    )


def _print_side_by_side(results: list[AdapterResult]) -> None:
    print("\n========== TOP-5 SIDE BY SIDE ==========")
    queries = list(results[0].top5.keys())
    for q in queries:
        print(f"\nQ: {q}")
        for r in results:
            print(f"  [{r.name}]")
            for rank, (title, score, source_type) in enumerate(r.top5[q], 1):
                print(f"    {rank}. {score:+.3f}  {source_type:14s}  {title}")


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Side-by-side benchmark of EmbeddingProvider adapters.",
    )
    parser.add_argument(
        "--ollama-base-url",
        default="http://localhost:11434",
        help="Ollama base URL (default http://localhost:11434)",
    )
    parser.add_argument(
        "--ollama-model",
        default="nomic-embed-text",
        help="Ollama embedding model (default nomic-embed-text)",
    )
    parser.add_argument(
        "--st-model",
        default="BAAI/bge-base-en-v1.5",
        help="sentence-transformers HF model name (default BAAI/bge-base-en-v1.5)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON of the results to stdout",
    )
    args = parser.parse_args()

    # Build the same corpus the seed CLI would have inserted for tenant 'acme'.
    corpus = list(SHARED_CHUNKS) + runbook_chunks_for("acme")

    print(f"Benchmarking against {len(corpus)} dev-corpus chunks.")

    ollama_result = await _bench_one(
        f"ollama:{args.ollama_model}",
        lambda: OllamaEmbeddingAdapter(args.ollama_base_url, model=args.ollama_model),
        corpus,
    )
    st_result = await _bench_one(
        f"st:{args.st_model}",
        lambda: SentenceTransformersEmbeddingAdapter(args.st_model),
        corpus,
    )

    _print_summary(ollama_result)
    _print_summary(st_result)
    _print_side_by_side([ollama_result, st_result])

    if args.json:
        sys.stdout.write(
            json.dumps(
                {
                    "ollama": {
                        "name": ollama_result.name,
                        "cold_start_s": ollama_result.cold_start_s,
                        "latency_ms": ollama_result.query_latencies_ms,
                        "embed_corpus_s": ollama_result.embed_corpus_s,
                    },
                    "sentence_transformers": {
                        "name": st_result.name,
                        "cold_start_s": st_result.cold_start_s,
                        "latency_ms": st_result.query_latencies_ms,
                        "embed_corpus_s": st_result.embed_corpus_s,
                    },
                },
                indent=2,
            )
            + "\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
