"""Ranking and classification metrics, with bootstrap confidence intervals.

Why these metrics
-----------------
A screening model is judged on *ordering*, not on a per-row threshold, so the
headline numbers are NDCG@k (graded relevance, position-discounted) and MAP@k
(threshold-free average precision). ROC-AUC and PR-AUC are reported as
supporting evidence, and precision/recall@k give an operational view for a
recruiter who only looks at the first screen.

Confidence intervals come from a bootstrap over jobs rather than over rows.
Rows inside one job are not independent - they share a requirement list and a
candidate pool - so a row bootstrap would understate the variance.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from random import Random

__all__ = [
    "BootstrapResult",
    "EvaluationReport",
    "PairedComparison",
    "average_precision_at_k",
    "bootstrap_interval",
    "evaluate_ranking",
    "mean_reciprocal_rank",
    "ndcg_at_k",
    "paired_bootstrap",
    "precision_at_k",
    "recall_at_k",
    "classification_metrics",
]


# --------------------------------------------------------------------------- #
# Per-query ranking metrics
# --------------------------------------------------------------------------- #
def _dcg(gains: Sequence[float]) -> float:
    """Discounted cumulative gain, the standard ``log2(rank + 1)`` form."""
    return sum(gain / math.log2(rank + 2) for rank, gain in enumerate(gains))


def ndcg_at_k(ranked_relevance: Sequence[int], k: int = 5) -> float:
    """Normalised DCG over the top ``k`` of one ranked list."""
    if k <= 0:
        return 0.0
    top = list(ranked_relevance)[:k]
    ideal = sorted((int(value) for value in ranked_relevance), reverse=True)[:k]
    denominator = _dcg(ideal)
    if denominator <= 0:
        return 0.0
    return _dcg(top) / denominator


def average_precision_at_k(ranked_relevance: Sequence[int], k: int = 5, threshold: int = 1) -> float:
    """MAP@k: precision at each relevant hit, averaged over the top ``k``."""
    if k <= 0:
        return 0.0
    total = 0.0
    hits = 0
    for rank, value in enumerate(list(ranked_relevance)[:k], start=1):
        if int(value) >= threshold:
            hits += 1
            total += hits / rank
    return total / min(k, max(1, sum(1 for v in ranked_relevance if int(v) >= threshold)))


def precision_at_k(ranked_relevance: Sequence[int], k: int = 5, threshold: int = 1) -> float:
    top = list(ranked_relevance)[:k]
    if not top:
        return 0.0
    return sum(1 for value in top if int(value) >= threshold) / len(top)


def recall_at_k(ranked_relevance: Sequence[int], k: int = 5, threshold: int = 1) -> float:
    total_relevant = sum(1 for value in ranked_relevance if int(value) >= threshold)
    if total_relevant == 0:
        return 0.0
    top = list(ranked_relevance)[:k]
    return sum(1 for value in top if int(value) >= threshold) / total_relevant


def mean_reciprocal_rank(ranked_relevance: Sequence[int], threshold: int = 2) -> float:
    for rank, value in enumerate(ranked_relevance, start=1):
        if int(value) >= threshold:
            return 1.0 / rank
    return 0.0


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class BootstrapResult:
    point: float
    low: float
    high: float
    samples: int

    def as_dict(self) -> dict[str, float | int]:
        return {
            "point": round(self.point, 4),
            "ci_low": round(self.low, 4),
            "ci_high": round(self.high, 4),
            "bootstrap_samples": self.samples,
        }


def bootstrap_interval(
    groups: Sequence[Sequence[float]],
    *,
    statistic,
    iterations: int = 400,
    confidence: float = 0.95,
    seed: int = 1729,
) -> BootstrapResult:
    """Percentile bootstrap over ``groups`` (one group per query/job)."""
    usable = [list(group) for group in groups if len(group) > 0]
    if not usable:
        return BootstrapResult(0.0, 0.0, 0.0, 0)
    point = float(statistic([list(group) for group in usable]))
    rng = Random(seed)
    means: list[float] = []
    size = len(usable)
    for _ in range(iterations):
        sample = [usable[rng.randrange(size)] for _ in range(size)]
        means.append(float(statistic(sample)))
    means.sort()
    tail = max(1, int(round((1.0 - confidence) / 2 * iterations)))
    low = means[tail - 1]
    high = means[min(len(means) - 1, len(means) - tail)]
    return BootstrapResult(point, low, high, iterations)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


@dataclass(slots=True)
class EvaluationReport:
    """Metrics for one model, plus the per-query values used to compute them."""

    name: str
    ranking: dict[str, float] = field(default_factory=dict)
    ranking_ci: dict[str, BootstrapResult] = field(default_factory=dict)
    classification: dict[str, float] = field(default_factory=dict)
    per_query_ndcg: dict[int, float] = field(default_factory=dict)
    per_query: dict[str, dict[int, float]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "model": self.name,
            "ranking": {key: round(value, 4) for key, value in sorted(self.ranking.items())},
            "ranking_ci95": {
                key: result.as_dict() for key, result in sorted(self.ranking_ci.items())
            },
            "classification": {
                key: round(value, 4) for key, value in sorted(self.classification.items())
            },
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class PairedComparison:
    """Result of comparing two systems on the same held-out queries."""

    metric: str
    difference: float
    ci_low: float
    ci_high: float
    p_value: float
    queries: int
    better: str

    def as_dict(self) -> dict[str, object]:
        return {
            "metric": self.metric,
            "mean_difference": round(self.difference, 4),
            "ci95": [round(self.ci_low, 4), round(self.ci_high, 4)],
            "p_value": round(self.p_value, 4),
            "queries": self.queries,
            "better": self.better,
        }


def paired_bootstrap(
    metric: str,
    per_query_a: dict[int, float],
    per_query_b: dict[int, float],
    *,
    iterations: int = 2000,
    seed: int = 2718,
    alpha: float = 0.05,
) -> PairedComparison:
    """Paired bootstrap over queries: is system A actually better than B?

    Both systems are scored on the same queries, so the difference is paired and
    far more sensitive than comparing two independent intervals. The p-value is
    read off the bootstrap distribution rather than a normal approximation,
    because per-query differences are small, discrete and not normal.
    """
    queries = sorted(set(per_query_a) & set(per_query_b))
    if not queries:
        return PairedComparison(metric, 0.0, 0.0, 0.0, 1.0, 0, "incomparable")
    diffs = [per_query_a[query] - per_query_b[query] for query in queries]
    point = _mean(diffs)
    rng = Random(seed)
    size = len(diffs)
    means: list[float] = []
    for _ in range(iterations):
        sample = [diffs[rng.randrange(size)] for _ in range(size)]
        means.append(_mean(sample))
    means.sort()
    tail = max(1, int(round((alpha / 2) * iterations)))
    low = means[tail - 1]
    high = means[min(len(means) - 1, len(means) - tail)]
    non_positive = sum(1 for value in means if value <= 0.0) / len(means)
    positive = sum(1 for value in means if value >= 0.0) / len(means)
    p_value = min(1.0, 2.0 * min(non_positive, positive))
    if p_value < alpha:
        better = "A" if point > 0 else "B"
    else:
        better = "tie"
    return PairedComparison(metric, point, low, high, p_value, size, better)


def evaluate_ranking(
    name: str,
    per_query_scores: dict[int, list[tuple[float, int]]],
    *,
    k: int = 5,
    ks: tuple[int, ...] | None = None,
    relevance_threshold: int = 2,
    probabilities: Sequence[float] | None = None,
    labels: Sequence[int] | None = None,
    bootstrap_iterations: int = 400,
) -> EvaluationReport:
    """Score one ranked model.

    ``per_query_scores`` maps job id to ``[(score, graded_relevance), ...]``.
    Ties are broken by descending relevance and then candidate id so the metric
    cannot be inflated or destroyed by an unstable sort.

    Two cut-offs are reported by default: ``k`` (a shortlist a recruiter
    actually reads) and ``k*2`` (how deep the ranking still holds up). A model
    that only wins at one cut-off is not a real improvement.
    """
    report = EvaluationReport(name=name)
    cutoffs = tuple(dict.fromkeys(ks or (k, k * 2)))
    per_cutoff: dict[int, dict[str, list[float]]] = {
        cutoff: {"ndcg": [], "map": [], "precision": [], "recall": [], "rr": []}
        for cutoff in cutoffs
    }

    for job_id in sorted(per_query_scores):
        rows = sorted(per_query_scores[job_id], key=lambda item: (-item[0], -item[1]))
        ranked_relevance = [int(relevance) for _score, relevance in rows]
        ndcg = ndcg_at_k(ranked_relevance, k=k)
        report.per_query_ndcg[job_id] = ndcg
        for cutoff in cutoffs:
            per_cutoff[cutoff]["ndcg"].append(ndcg_at_k(ranked_relevance, k=cutoff))
            per_cutoff[cutoff]["map"].append(
                average_precision_at_k(ranked_relevance, k=cutoff, threshold=relevance_threshold)
            )
            per_cutoff[cutoff]["precision"].append(
                precision_at_k(ranked_relevance, k=cutoff, threshold=relevance_threshold)
            )
            per_cutoff[cutoff]["recall"].append(
                recall_at_k(ranked_relevance, k=cutoff, threshold=relevance_threshold)
            )
            per_cutoff[cutoff]["rr"].append(
                mean_reciprocal_rank(ranked_relevance, threshold=relevance_threshold)
            )

    for cutoff in cutoffs:
        values = per_cutoff[cutoff]
        report.ranking.update(
            {
                f"ndcg@{cutoff}": _mean(values["ndcg"]),
                f"map@{cutoff}": _mean(values["map"]),
                f"precision@{cutoff}": _mean(values["precision"]),
                f"recall@{cutoff}": _mean(values["recall"]),
                f"mrr@{cutoff}": _mean(values["rr"]),
            }
        )
    report.ranking["queries"] = float(len(per_query_scores))

    for cutoff in cutoffs:
        values = per_cutoff[cutoff]
        for metric, series in (
            ("ndcg", values["ndcg"]),
            ("map", values["map"]),
            ("precision", values["precision"]),
            ("recall", values["recall"]),
        ):
            key = f"{metric}@{cutoff}"
            report.per_query[key] = {
                job_id: float(value) for job_id, value in zip(sorted(per_query_scores), series, strict=True)
            }

    report.ranking_ci = {
        f"ndcg@{k}": bootstrap_interval(
            [[report.per_query_ndcg[job_id]] for job_id in sorted(per_query_scores)],
            statistic=lambda groups: _mean([value for group in groups for value in group]),
            iterations=bootstrap_iterations,
        ),
        f"precision@{k}": bootstrap_interval(
            [per_cutoff[k]["precision"]] if per_cutoff[k]["precision"] else [],
            statistic=lambda groups: _mean([value for group in groups for value in group]),
            iterations=bootstrap_iterations,
        ),
        f"recall@{k}": bootstrap_interval(
            [per_cutoff[k]["recall"]] if per_cutoff[k]["recall"] else [],
            statistic=lambda groups: _mean([value for group in groups for value in group]),
            iterations=bootstrap_iterations,
        ),
    }
    if labels is not None and len(labels) and len(set(int(value) for value in labels)) > 1:
        supplied = [float(value) for value in probabilities] if probabilities is not None else []
        report.classification = classification_metrics(labels, supplied)
    return report


def classification_metrics(labels: Sequence[int], probabilities: Sequence[float]) -> dict[str, float]:
    """Binary ROC-AUC, average precision and Brier score for the positive class."""
    import numpy as np
    from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

    y_true = np.asarray([int(1 if int(value) >= 2 else 0) for value in labels], dtype=np.int32)
    if probabilities is None or len(probabilities) != len(y_true):
        return {}
    scores = np.asarray([float(value) for value in probabilities], dtype=np.float64)
    scores = np.clip(scores, 1e-9, 1 - 1e-9)
    return {
        "roc_auc": float(roc_auc_score(y_true, scores)),
        "pr_auc": float(average_precision_score(y_true, scores)),
        "brier": float(brier_score_loss(y_true, scores)),
        "positive_rate": float(y_true.mean()),
    }