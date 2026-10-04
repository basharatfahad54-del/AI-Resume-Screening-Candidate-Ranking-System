"""Hand-checked metric tests plus the paired significance test."""

from __future__ import annotations

import math

import pytest

from talentmatch_ml.metrics import (
    average_precision_at_k,
    bootstrap_interval,
    evaluate_ranking,
    mean_reciprocal_rank,
    ndcg_at_k,
    paired_bootstrap,
    precision_at_k,
    recall_at_k,
)


def _dcg(gains: list[float]) -> float:
    return sum(gain / math.log2(rank + 2) for rank, gain in enumerate(gains))


def test_ndcg_perfect_ranking_is_one() -> None:
    assert ndcg_at_k([3, 2, 1, 0], k=4) == 1.0


def test_ndcg_reversed_ranking_matches_hand_calculation() -> None:
    ranked = [0, 0, 0, 3]
    gains = [float(value) for value in ranked]
    expected = _dcg(gains) / _dcg(sorted(gains, reverse=True))
    assert ndcg_at_k(ranked, k=4) == pytest.approx(expected, abs=1e-12)


def test_ndcg_of_no_relevant_items_is_zero() -> None:
    assert ndcg_at_k([0, 0, 0], k=3) == 0.0


def test_ndcg_respects_cutoff() -> None:
    # The grade-3 item sits at rank 4, so it counts at k=4 and not at k=3.
    assert ndcg_at_k([2, 2, 2, 3], k=3) < ndcg_at_k([2, 2, 2, 3], k=4)


def test_precision_and_recall() -> None:
    ranked = [3, 0, 2, 0, 1]
    assert precision_at_k(ranked, k=5, threshold=2) == pytest.approx(2 / 5)
    assert recall_at_k(ranked, k=5, threshold=2) == 1.0
    assert precision_at_k(ranked, k=1, threshold=2) == 1.0
    assert recall_at_k(ranked, k=1, threshold=2) == 0.5


def test_average_precision_rewards_early_hits() -> None:
    early = average_precision_at_k([3, 2, 0, 0, 2], k=5, threshold=2)
    late = average_precision_at_k([0, 0, 3, 2, 2], k=5, threshold=2)
    assert early > late


def test_mean_reciprocal_rank() -> None:
    assert mean_reciprocal_rank([0, 3, 2], threshold=2) == 0.5
    assert mean_reciprocal_rank([0, 1], threshold=2) == 0.0


def test_evaluate_ranking_groups_by_query() -> None:
    scores = {
        1: [(0.9, 3), (0.5, 1)],
        2: [(0.9, 0), (0.2, 3)],
    }
    report = evaluate_ranking("m", scores, bootstrap_iterations=20)
    assert report.ranking["queries"] == 2.0
    assert set(report.ranking) >= {"ndcg@5", "ndcg@10", "map@5", "precision@5"}
    # Query 1 puts the relevant item first; query 2 puts it last.
    assert report.per_query["ndcg@5"][1] > report.per_query["ndcg@5"][2]


def test_evaluate_ranking_breaks_ties_deterministically() -> None:
    scores = {1: [(0.5, 3), (0.5, 0)]}
    first = evaluate_ranking("m", scores, bootstrap_iterations=5)
    second = evaluate_ranking("m", scores, bootstrap_iterations=5)
    assert first.ranking == second.ranking


def test_bootstrap_interval_brackets_the_point_estimate() -> None:
    groups = [[0.1], [0.5], [0.9], [0.4], [0.6]]
    result = bootstrap_interval(
        groups, statistic=lambda collected: sum(sum(g) for g in collected) / len(collected)
    )
    assert result.low <= result.point <= result.high
    assert result.samples > 0


def test_paired_bootstrap_detects_a_real_difference() -> None:
    strong = {job: 0.9 for job in range(1, 30)}
    weak = {job: 0.1 for job in range(1, 30)}
    result = paired_bootstrap("ndcg@5", strong, weak, iterations=400)
    assert result.better == "A"
    assert result.p_value < 0.05
    assert result.difference == pytest.approx(0.8, abs=1e-9)


def test_paired_bootstrap_reports_a_tie_for_identical_systems() -> None:
    same = {job: 0.5 for job in range(1, 20)}
    result = paired_bootstrap("ndcg@5", same, dict(same), iterations=400)
    assert result.better == "tie"
    assert result.difference == pytest.approx(0.0, abs=1e-9)


def test_paired_bootstrap_handles_disjoint_queries() -> None:
    result = paired_bootstrap("ndcg@5", {1: 0.5}, {2: 0.4}, iterations=20)
    assert result.better == "incomparable"
    assert result.queries == 0