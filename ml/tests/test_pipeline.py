"""End-to-end smoke test: corpus -> features -> CV -> artifact -> report."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from talentmatch_ml.cli import main
from talentmatch_ml.dataset import generate_corpus
from talentmatch_ml.features import build_feature_table, feature_names
from talentmatch_ml.train import (
    load_artifact,
    make_model,
    out_of_fold_scores,
    save_artifact,
    train_model,
)


@pytest.fixture(scope="module")
def corpus():
    return generate_corpus(jobs=10, candidates=30, seed=1234)


@pytest.fixture(scope="module")
def result(corpus):
    table = build_feature_table(corpus)
    return table, train_model(table, model_name="logistic", seed=1234, folds=3, bootstrap_iterations=40)


def test_out_of_fold_scores_cover_every_row(corpus) -> None:
    table = build_feature_table(corpus)
    scores, fold_rows, fold_jobs = out_of_fold_scores(
        table, model_name="logistic", seed=1234, folds=3
    )
    assert len(scores) == len(table)
    assert np.all((scores >= 0.0) & (scores <= 1.0))
    assert sum(fold_rows) == len(table)
    assert sum(fold_jobs) == len(set(int(value) for value in table.groups))


def test_no_job_appears_in_two_folds(corpus) -> None:
    table = build_feature_table(corpus)
    _scores, _fold_rows, _fold_jobs = out_of_fold_scores(
        table, model_name="gbm", seed=1234, folds=5
    )
    # GroupKFold assigns each job to exactly one fold, so the sum of fold job
    # counts equals the number of distinct jobs.
    assert sum(_fold_jobs) == len(set(int(value) for value in table.groups))


def test_metrics_are_reported_for_both_systems(result) -> None:
    _table, trained = result
    payload = trained.as_dict()
    assert payload["baseline_rule_based"]["ranking"]["ndcg@5"] > 0.0
    assert payload["test"]["ranking"]["ndcg@5"] > 0.0
    assert payload["significance"], "a significance test must always run"
    assert all("p_value" in row for row in payload["significance"])


def test_significance_covers_every_query(result) -> None:
    _table, trained = result
    for row in trained.significance:
        assert row["queries"] == len(set(int(value) for value in trained.test_jobs))


def test_feature_importance_is_sorted(result) -> None:
    _table, trained = result
    values = [value for _name, value in trained.feature_importance]
    assert values == sorted(values, reverse=True)


def test_artifact_round_trip(tmp_path: Path, corpus, result) -> None:
    table, trained = result
    paths = save_artifact(trained, table, tmp_path)
    assert paths["model"].exists() and paths["metrics"].exists()
    payload = json.loads(paths["metrics"].read_text(encoding="utf-8"))
    assert payload["model"] == "logistic"

    artifact = load_artifact(paths["model"])
    assert artifact.feature_names == feature_names()
    scores = artifact.predict_proba(table.X[:20])
    assert scores.shape == (20,)
    assert np.all((scores >= 0.0) & (scores <= 1.0))


def test_artifact_refuses_a_different_feature_contract(tmp_path: Path, corpus, result) -> None:
    table, trained = result
    paths = save_artifact(trained, table, tmp_path)
    artifact = load_artifact(paths["model"])
    artifact.feature_names = ["wrong_column"]
    with pytest.raises(ValueError, match="feature contract"):
        artifact.predict_proba(table.X[:5])


def test_unknown_model_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown model"):
        make_model("transformer")


def test_corpus_too_small_to_split_is_reported(corpus) -> None:
    tiny = corpus.__class__(
        jobs=corpus.jobs[:1], candidates=corpus.candidates[:2], labels=[]
    )
    table = build_feature_table(tiny)
    with pytest.raises(ValueError, match="at least 2 jobs"):
        train_model(table, model_name="gbm", folds=5)


def test_cli_end_to_end(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "dataset"
    exit_code = main(
        [
            "all",
            "--jobs",
            "8",
            "--candidates",
            "20",
            "--seed",
            "7",
            "--model",
            "logistic",
            "--folds",
            "2",
            "--dataset",
            str(dataset_dir),
            "--runs",
            str(tmp_path / "runs"),
        ]
    )
    assert exit_code == 0
    assert (dataset_dir / "jobs.csv").exists()
    assert (dataset_dir / "manifest.json").exists()
    reports = list((tmp_path / "runs").glob("*/report.md"))
    assert len(reports) == 1
    body = reports[0].read_text(encoding="utf-8")
    assert "## Headline" in body
    assert "## Fairness audit" in body
    assert "## Intended use" in body
    metrics = json.loads(reports[0].with_name("metrics.json").read_text(encoding="utf-8"))
    assert metrics["significance"]


def test_cli_evaluate_prints_json(tmp_path: Path, capsys) -> None:
    exit_code = main([
        "evaluate",
        "--jobs", "6",
        "--candidates", "15",
        "--seed", "3",
        "--model", "logistic",
        "--folds", "2",
        "--dataset", str(tmp_path / "cli-evaluate"),
    ])
    assert exit_code == 0
    output = capsys.readouterr().out
    payload = json.loads(output[output.index("{") :])
    assert "baseline" in payload and "significance" in payload