"""Side-by-side benchmark for EmbeddingProvider adapters.

Measures cold-start time, per-query latency (mean / p50 / p95), and
retrieval-ordering correctness on the seeded dev corpus for the Ollama
and sentence-transformers adapters.

Run:
    cd services/orchestrator
    set -a && source ../../.env && set +a
    uv run python -m tools.embedding_benchmark
"""
