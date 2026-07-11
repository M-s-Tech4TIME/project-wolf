"""embedding_bench pure helpers — the parts a wrong answer would corrupt."""

import math
import uuid

from wolf_server.management.embedding_bench import (
    Metrics,
    mrl_truncate,
    rrf_fuse,
    sanitize_question,
    score,
)


def test_mrl_truncate_keeps_head_and_renormalizes() -> None:
    # Same operation Ollama performs server-side for `dimensions` — the
    # head components, L2-renormalized (so cosine stays meaningful).
    vector = [3.0, 4.0, 100.0, -7.0]
    out = mrl_truncate(vector, 2)
    assert out == [3.0 / 5.0, 4.0 / 5.0]
    assert math.isclose(sum(x * x for x in out), 1.0)
    assert mrl_truncate([0.0, 0.0], 2) == [0.0, 0.0]  # zero-safe


def test_rrf_fuse_matches_store_semantics() -> None:
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    # a: rank 1 in one leg only; b: rank 2 in both legs -> b wins
    # (1/62 + 1/62 > 1/61), exactly the store's k=60 fusion arithmetic.
    fused = rrf_fuse({a: 1, b: 2}, {b: 2, c: 3})
    assert fused[0] == b
    assert fused[1] == a


def test_score_computes_known_item_metrics() -> None:
    gold1, gold2, other = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    results = [
        (gold1, [gold1, other]),  # rank 1
        (gold2, [other, other, gold2]),  # rank 3
    ]
    metrics = score(results)
    assert metrics == Metrics(
        recall_at_1=0.5,
        recall_at_5=1.0,
        recall_at_10=1.0,
        mrr_at_10=(1.0 + 1.0 / 3.0) / 2.0,
        n=2,
    )
    assert score([]).n == 0


def test_sanitize_question_strips_think_blocks_and_noise() -> None:
    raw = (
        "<think>\nreasoning...\n</think>\n"
        'Sure! Here you go:\n"Which rule fires on SSH brute force?"'
    )
    assert sanitize_question(raw) == "Which rule fires on SSH brute force?"
    assert sanitize_question("Plain question?") == "Plain question?"
    assert sanitize_question("") == ""
