"""Command line interface for the offline ML pipeline.

::

    python -m talentmatch_ml.cli synthesize --out ml/data/sample
    python -m talentmatch_ml.cli train --dataset ml/data/sample --model gbm
    python -m talentmatch_ml.cli evaluate --dataset ml/data/sample
    python -m talentmatch_ml.cli all --jobs 48 --candidates 240

Every subcommand is deterministic for a given seed and writes machine-readable
JSON next to the human-readable report, so CI can diff metrics without parsing
prose.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

if __package__ in (None, ""):  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from talentmatch_ml.dataset import Corpus, generate_corpus, load_corpus, write_corpus
from talentmatch_ml.features import FeatureTable, build_feature_table
from talentmatch_ml.report import write_markdown_report
from talentmatch_ml.train import (
    TrainResult,
    load_artifact,
    out_of_fold_scores,
    save_artifact,
    train_model,
)

DEFAULT_SEED = 20240517
DEFAULT_RUN_DIR = Path("ml/runs")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _run_dir(base: Path, prefix: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = Path(base) / f"{prefix}-{stamp}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _echo(message: str) -> None:
    print(message, flush=True)


def cmd_synthesize(args: argparse.Namespace) -> int:
    corpus = generate_corpus(
        jobs=args.jobs, candidates=args.candidates, seed=args.seed
    )
    paths = write_corpus(corpus, args.out)
    _echo(f"Corpus written to {args.out}")
    _echo(f"  jobs={len(corpus.jobs)} candidates={len(corpus.candidates)} pairs={len(corpus.labels)}")
    _echo(f"  grade distribution={corpus.grade_distribution()} positive_rate={corpus.positive_rate():.3f}")
    for name, path in paths.items():
        _echo(f"  {name}: {path}")
    return 0


def _load_or_create_dataset(args: argparse.Namespace) -> tuple[Corpus, Path]:
    dataset_dir = Path(args.dataset)
    if dataset_dir.exists() and (dataset_dir / "jobs.csv").exists():
        _echo(f"Loading dataset from {dataset_dir}")
        return load_corpus(dataset_dir), dataset_dir
    _echo(f"No dataset at {dataset_dir}; generating one with seed {args.seed}")
    corpus = generate_corpus(jobs=args.jobs, candidates=args.candidates, seed=args.seed)
    write_corpus(corpus, dataset_dir)
    return corpus, dataset_dir


def _run_directory(args: argparse.Namespace) -> Path:
    return _run_dir(Path(args.runs or DEFAULT_RUN_DIR), f"{args.model}-seed{args.seed}")


def _train(corpus: Corpus, args: argparse.Namespace) -> tuple[TrainResult, FeatureTable, Path, np.ndarray]:
    """Featurise, train and persist. Returns the result plus out-of-fold scores."""
    table = build_feature_table(corpus)
    _echo(f"Featurised {len(table)} pairs x {len(table.names)} features")
    result = train_model(table, model_name=args.model, seed=args.seed, folds=args.folds)
    run_dir = _run_directory(args)

    paths = save_artifact(result, table, run_dir)
    _echo(f"  metrics: {paths['metrics']}")

    # The fairness audit uses out-of-fold scores: what the deployed model does on
    # jobs it never saw, which is the only number worth auditing.
    scores, _fold_rows, _fold_jobs = out_of_fold_scores(
        table, model_name=args.model, seed=args.seed, folds=args.folds
    )
    return result, table, run_dir, scores


def cmd_train(args: argparse.Namespace) -> int:
    corpus, dataset_dir = _load_or_create_dataset(args)
    result, table, run_dir, scores = _train(corpus, args)
    payload = result.as_dict()
    _echo(f"Saved model to {run_dir}")
    _echo(f"  baseline ndcg@5 = {payload['baseline_rule_based']['ranking']['ndcg@5']}")
    _echo(f"  model    ndcg@5 = {payload['test']['ranking']['ndcg@5']}")
    for warning in result.warnings:
        _echo(f"  warning: {warning}")
    report = write_markdown_report(
        result,
        corpus,
        table,
        output_path=run_dir / "report.md",
        test_scores=scores,
        dataset_dir=dataset_dir,
        commands=[
            f"python -m talentmatch_ml.cli all --jobs {len(corpus.jobs)} "
            f"--candidates {len(corpus.candidates)} --seed {args.seed} "
            f"--model {args.model} --folds {args.folds}"
        ],
    )
    _echo(f"  report: {report}")
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    corpus, _dataset_dir = _load_or_create_dataset(args)
    table = build_feature_table(corpus)
    result = train_model(
        table, model_name=args.model, seed=args.seed, folds=args.folds, bootstrap_iterations=100
    )
    payload = result.as_dict()
    _echo(json.dumps({
        "model": result.model_name,
        "split": payload["split"],
        "baseline": payload["baseline_rule_based"]["ranking"],
        "model_metrics": payload["test"]["ranking"],
        "lift": payload["lift_vs_baseline"],
        "significance": payload["significance"],
    }, indent=2))
    return 0


def cmd_all(args: argparse.Namespace) -> int:
    corpus = generate_corpus(jobs=args.jobs, candidates=args.candidates, seed=args.seed)
    dataset_dir = Path(args.dataset)
    write_corpus(corpus, dataset_dir)
    _echo(f"1/2 dataset -> {dataset_dir} ({len(corpus.labels)} pairs)")
    namespace = argparse.Namespace(
        dataset=str(dataset_dir),
        model=args.model,
        seed=args.seed,
        runs=args.runs,
        jobs=args.jobs,
        candidates=args.candidates,
        folds=args.folds,
    )
    result, table, run_dir, scores = _train(corpus, namespace)
    report = write_markdown_report(
        result,
        corpus,
        table,
        output_path=run_dir / "report.md",
        test_scores=scores,
        dataset_dir=dataset_dir,
        commands=[
            f"python -m talentmatch_ml.cli all --jobs {args.jobs} "
            f"--candidates {args.candidates} --seed {args.seed} "
            f"--model {args.model} --folds {args.folds}"
        ],
    )
    _echo(f"2/2 report -> {report}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    root = _repo_root()
    parser = argparse.ArgumentParser(
        prog="talentmatch_ml",
        description="Offline reranker training and evaluation (synthetic corpus).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--jobs", type=int, default=48, help="number of synthetic jobs")
        sub.add_argument(
            "--candidates", type=int, default=240, help="number of synthetic candidates"
        )
        sub.add_argument("--seed", type=int, default=DEFAULT_SEED)
        sub.add_argument(
            "--dataset",
            default=str(root / "ml" / "data" / "sample"),
            help="dataset directory (CSV + manifest.json)",
        )
        sub.add_argument("--runs", default=None, help="directory for run artefacts")
        sub.add_argument("--model", choices=("gbm", "logistic"), default="gbm")
        sub.add_argument(
            "--folds", type=int, default=5, help="job-grouped cross-validation folds"
        )

    synthesize = subparsers.add_parser("synthesize", help="generate the synthetic corpus")
    add_common(synthesize)
    synthesize.set_defaults(func=cmd_synthesize, out=root / "ml" / "data" / "sample")

    for name, handler, help_text in (
        ("train", cmd_train, "train, evaluate and report"),
        ("evaluate", cmd_evaluate, "print metrics as JSON only"),
        ("all", cmd_all, "synthesize + train + report"),
    ):
        subparser = subparsers.add_parser(name, help=help_text)
        add_common(subparser)
        subparser.set_defaults(func=handler)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "command", None) == "synthesize":
        args.out = Path(getattr(args, "out", None) or args.dataset)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())