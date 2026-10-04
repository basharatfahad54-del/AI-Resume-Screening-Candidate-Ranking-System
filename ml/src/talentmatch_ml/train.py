"""Training, evaluation and persistence for the learned reranker.

Split strategy
--------------
Rows are split **by job**, not by row. Every candidate appears against many
jobs, so a random row split would put the same resume on both sides of the
boundary and report an inflated score. ``GroupShuffleSplit`` keeps whole jobs
on one side of the split, which mirrors deployment: a model must rank unseen
openings, not memorise openings it was trained on.

Model choice
------------
``HistGradientBoostingClassifier`` is the default because it handles the
non-linear interactions that matter here (a weak required-skill match hurts far
more when several other requirements are already satisfied). A logistic
regression is also provided as an auditable, monotonic baseline; both write the
same artifact schema and their reports can be compared directly.
"""

from __future__ import annotations

import json
import platform
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.model_selection import GroupKFold, GroupShuffleSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .features import FeatureBuilder, FeatureTable, feature_names
from .metrics import EvaluationReport, evaluate_ranking, paired_bootstrap

__all__ = [
    "CV_FOLDS",
    "MODEL_NAMES",
    "RELEVANCE_THRESHOLD",
    "RerankerArtifact",
    "TrainResult",
    "evaluate_model",
    "load_artifact",
    "make_model",
    "out_of_fold_scores",
    "save_artifact",
    "train_model",
]

MODEL_NAMES = ("gbm", "logistic")
#: Grade at or above which a pair counts as a positive outcome.
RELEVANCE_THRESHOLD = 2
#: Number of job-grouped folds. Each row is predicted exactly once, by a model
#: that never saw its job during training.
CV_FOLDS = 5


def make_model(name: str, *, seed: int = 42) -> Pipeline:
    """Instantiate one of the supported rerankers."""
    if name == "gbm":
        estimator: Any = HistGradientBoostingClassifier(
            max_depth=4,
            max_iter=250,
            learning_rate=0.08,
            min_samples_leaf=40,
            l2_regularization=1.0,
            random_state=seed,
        )
        return Pipeline([("scale", StandardScaler()), ("clf", estimator)])
    if name == "logistic":
        return Pipeline(
            [
                ("scale", StandardScaler()),
                (
                    "clf",
                    LogisticRegression(
                        C=1.0,
                        max_iter=2000,
                        class_weight="balanced",
                        random_state=seed,
                    ),
                ),
            ]
        )
    raise ValueError(f"Unknown model {name!r}; choose one of {MODEL_NAMES}")


@dataclass(slots=True)
class TrainResult:
    model_name: str
    train_report: EvaluationReport
    test_report: EvaluationReport
    baseline_report: EvaluationReport
    test_jobs: list[int]
    feature_importance: list[tuple[str, float]] = field(default_factory=list)
    calibration: dict[str, float] = field(default_factory=dict)
    significance: list[dict[str, object]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    split: dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model_name,
            "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "split": dict(self.split)
            or {
                "strategy": "single holdout",
                "evaluation": "held-out jobs",
                "test_jobs": len(self.test_jobs),
                "relevance_threshold": RELEVANCE_THRESHOLD,
            },
            "train": self.train_report.as_dict(),
            "test": self.test_report.as_dict(),
            "baseline_rule_based": self.baseline_report.as_dict(),
            "lift_vs_baseline": self._lift(),
            "calibration": {key: round(value, 4) for key, value in self.calibration.items()},
            "significance": list(self.significance),
            "feature_importance": [
                {"feature": name, "importance": round(value, 6)}
                for name, value in self.feature_importance
            ],
            "environment": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "numpy": np.__version__,
                "scikit_learn": sklearn.__version__,
            },
            "warnings": list(self.warnings),
        }

    def _lift(self) -> dict[str, float]:
        """Relative change of each ranking metric against the rule-based baseline."""
        lift: dict[str, float] = {}
        for key, value in self.test_report.ranking.items():
            base = self.baseline_report.ranking.get(key)
            if base is None or key == "queries" or base == 0:
                continue
            lift[key] = round((value - base) / base, 4)
        return lift


def _split(table: FeatureTable, *, seed: int, test_size: float = 0.25):
    """Group-aware train/test split over job ids (single holdout)."""
    groups = table.groups
    if len(np.unique(groups)) < 4:
        raise ValueError("Need at least 4 jobs to hold out a test split")
    splitter = GroupShuffleSplit(n_splits=1, test_size=test_size, random_state=seed)
    train_index, test_index = next(splitter.split(table.X, table.binary_labels, groups=groups))
    return train_index, test_index


def out_of_fold_scores(
    table: FeatureTable, *, model_name: str, seed: int, folds: int = CV_FOLDS
) -> tuple[np.ndarray, list[int], list[int]]:
    """Predict every row with a model that never saw that row's job.

    Returns ``(scores, fold_sizes, fold_job_counts)``. Out-of-fold prediction
    covers all rows instead of a quarter of them, which both removes the
    "small holdout, wide interval" problem and makes the paired significance
    test sensitive enough to be worth reading.
    """
    unique_jobs = sorted(set(int(value) for value in table.groups))
    splits = min(folds, len(unique_jobs))
    if splits < 2:
        raise ValueError("Need at least 2 jobs for cross-validation")
    splitter = GroupKFold(n_splits=splits, shuffle=True, random_state=seed)
    scores = np.zeros(len(table), dtype=np.float64)
    fold_sizes: list[int] = []
    fold_jobs: list[int] = []
    for train_index, holdout_index in splitter.split(table.X, table.binary_labels, groups=table.groups):
        train_part = table.subset(np.isin(np.arange(len(table)), train_index))
        if len(np.unique(train_part.binary_labels)) < 2:
            raise ValueError(
                "A training fold contains a single class; generate a corpus with more overlap"
            )
        pipeline = make_model(model_name, seed=seed)
        pipeline.fit(train_part.X, train_part.binary_labels)
        scores[holdout_index] = pipeline.predict_proba(table.X[holdout_index])[:, 1]
        fold_sizes.append(int(len(holdout_index)))
        fold_jobs.append(
            len(set(int(value) for value in table.groups[holdout_index]))
        )
    return scores, fold_sizes, fold_jobs


def _baseline_scores(table: FeatureTable) -> np.ndarray:
    """The score the API already serves, used as the ranking baseline."""
    return table.X[:, table.names.index("overall_score")]


def train_model(
    table: FeatureTable,
    *,
    model_name: str = "gbm",
    seed: int = 42,
    folds: int = CV_FOLDS,
    bootstrap_iterations: int = 400,
) -> TrainResult:
    """Fit the reranker with grouped cross-validation and compare to the baseline.

    Evaluation uses out-of-fold predictions over *every* pair, so the reported
    numbers describe the whole corpus rather than one small holdout.
    """
    scores, fold_sizes, fold_jobs = out_of_fold_scores(
        table, model_name=model_name, seed=seed, folds=folds
    )
    baseline_scores = _baseline_scores(table)

    warnings: list[str] = []
    if len(table) < 500:
        warnings.append("Fewer than 500 rows: metrics will have wide intervals.")
    positive_rate = float(table.binary_labels.mean())
    if positive_rate > 0.9 or positive_rate < 0.05:
        warnings.append(
            f"Positives are {positive_rate:.1%} of the corpus; PR-AUC is the metric to trust."
        )
    if min(fold_sizes) < 50:
        warnings.append("At least one fold is very small; its queries dominate the averages.")

    model_report = evaluate_ranking(
        f"{model_name}-out-of-fold",
        table.per_query_scores(scores),
        bootstrap_iterations=bootstrap_iterations,
        probabilities=scores,
        labels=table.grades,
    )
    baseline_report = evaluate_ranking(
        "rule-based-overall-score",
        table.per_query_scores(baseline_scores),
        bootstrap_iterations=bootstrap_iterations,
        probabilities=baseline_scores,
        labels=table.grades,
    )
    if baseline_report.classification:
        baseline_binary = (baseline_scores >= 0.5).astype(np.float64)
        baseline_report.classification["brier"] = float(
            brier_score_loss(table.binary_labels, np.clip(baseline_binary, 1e-6, 1 - 1e-6))
        )

    calibration = {
        "roc_auc": float(roc_auc_score(table.binary_labels, scores)),
        "pr_auc": float(average_precision_score(table.binary_labels, scores)),
        "brier": float(brier_score_loss(table.binary_labels, scores)),
        "positive_rate": positive_rate,
    }

    # Importance is measured on the full data with a model that saw it all: the
    # question is "what does this feature set rely on", not "what did one fold learn".
    full_pipeline = make_model(model_name, seed=seed)
    full_pipeline.fit(table.X, table.binary_labels)
    importance = _feature_importance(full_pipeline, table, seed=seed)
    significance = [
        paired_bootstrap(
            metric,
            model_report.per_query.get(metric, {}),
            baseline_report.per_query.get(metric, {}),
            iterations=max(200, bootstrap_iterations * 5),
        ).as_dict()
        for metric in ("ndcg@5", "map@5", "precision@5", "ndcg@10")
    ]
    return TrainResult(
        model_name=model_name,
        train_report=model_report,
        test_report=model_report,
        baseline_report=baseline_report,
        test_jobs=sorted(set(int(value) for value in table.groups)),
        feature_importance=importance,
        calibration=calibration,
        significance=significance,
        warnings=warnings,
        split={
            "strategy": f"GroupKFold(n_splits={len(fold_sizes)}, shuffle=True, random_state={seed})",
            "evaluation": "out-of-fold predictions for every pair",
            "fold_rows": fold_sizes,
            "fold_jobs": fold_jobs,
            "relevance_threshold": RELEVANCE_THRESHOLD,
        },
    )


def _feature_importance(
    pipeline: Pipeline, train: FeatureTable, *, seed: int
) -> list[tuple[str, float]]:
    """Permutation importance: model agnostic and honest about collinearity."""
    result = permutation_importance(
        pipeline,
        train.X,
        train.binary_labels,
        scoring="average_precision",
        n_repeats=3,
        random_state=seed,
        n_jobs=1,
    )
    pairs = sorted(
        zip(train.names, result.importances_mean, strict=True),
        key=lambda item: item[1],
        reverse=True,
    )
    return [(name, float(value)) for name, value in pairs]


@dataclass(slots=True)
class RerankerArtifact:
    """Everything needed to score a pair at inference time, on disk."""

    pipeline: Pipeline
    feature_names: list[str]
    model_name: str
    metadata: dict[str, Any]
    builder: FeatureBuilder | None = None

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        """Probability that a pair is relevant (grade >= threshold)."""
        if list(self.feature_names) != list(feature_names()):
            raise ValueError(
                "Artifact was trained on a different feature contract; retrain the model"
            )
        return self.pipeline.predict_proba(features)[:, 1]


def save_artifact(result: TrainResult, table: FeatureTable, directory: Path) -> dict[str, Path]:
    """Persist the fitted pipeline, metrics JSON and the training feature list.

    The saved pipeline is refitted on the whole corpus: evaluation already used
    out-of-fold predictions, so the artifact is free to learn from every row.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    import joblib

    pipeline = make_model(result.model_name, seed=42)
    pipeline.fit(table.X, table.binary_labels)
    artifact = RerankerArtifact(
        pipeline=pipeline,
        feature_names=feature_names(),
        model_name=result.model_name,
        metadata=result.as_dict(),
    )
    model_path = directory / f"reranker_{result.model_name}.joblib"
    joblib.dump(artifact, model_path, compress=3)
    metrics_path = directory / "metrics.json"
    metrics_path.write_text(json.dumps(result.as_dict(), indent=2) + "\n", encoding="utf-8")
    return {"model": model_path, "metrics": metrics_path}


def load_artifact(path: Path) -> RerankerArtifact:
    """Load an artifact written by :func:`save_artifact`."""
    import joblib

    artifact = joblib.load(Path(path))
    if not isinstance(artifact, RerankerArtifact):  # pragma: no cover - corrupt file
        raise TypeError(f"{path} is not a RerankerArtifact")
    return artifact