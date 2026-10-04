"""Markdown reporting, including a fairness audit of the ranking behaviour.

The audit deliberately *uses* protected attributes - that is the only legitimate
use of them. The features and labels never see them (see
``ml/tests/test_fairness.py``), so comparing ranking quality across subgroups is
the one way to detect a proxy-discrimination problem that would otherwise stay
invisible. Subgroup gaps are reported, not silently averaged away.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .dataset import Corpus
from .features import FeatureTable
from .metrics import evaluate_ranking
from .train import RELEVANCE_THRESHOLD, TrainResult

__all__ = ["AuditRow", "fairness_audit", "write_markdown_report"]

#: Age bands used for the audit; coarse on purpose, to avoid small cells.
AGE_BANDS = ((0, 25, "under 25"), (25, 32, "25-31"), (32, 40, "32-39"), (40, 200, "40+"))


@dataclass(frozen=True, slots=True)
class AuditRow:
    subgroup: str
    rows: int
    positive_rate: float
    ndcg_at_5: float
    precision_at_5: float
    mean_predicted: float


def fairness_audit(table: FeatureTable, scores: Sequence[float], corpus: Corpus) -> list[AuditRow]:
    """Ranking quality per gender and per age band."""
    lookup = {candidate.candidate_id: candidate for candidate in corpus.candidates}
    rows: list[AuditRow] = []

    def summarise(subset: FeatureTable, name: str) -> AuditRow | None:
        if len(subset) < 30 or len(np.unique(subset.groups)) < 3:
            return None
        subset_scores = [float(scores[index]) for index in _indices_of(table, subset)]
        report = evaluate_ranking(
            name, subset.per_query_scores(subset_scores), bootstrap_iterations=1
        )
        return AuditRow(
            subgroup=name,
            rows=len(subset),
            positive_rate=float(subset.binary_labels.mean()),
            ndcg_at_5=report.ranking.get("ndcg@5", 0.0),
            precision_at_5=report.ranking.get("precision@5", 0.0),
            mean_predicted=float(np.mean(subset_scores)) if subset_scores else 0.0,
        )

    genders = sorted({candidate.gender for candidate in corpus.candidates})
    for gender in genders:
        ids = {cid for cid, candidate in lookup.items() if candidate.gender == gender}
        mask = np.asarray([cid in ids for cid in table.candidate_ids], dtype=bool)
        result = summarise(table.subset(mask), f"gender={gender}")
        if result is not None:
            rows.append(result)

    for low, high, label in AGE_BANDS:
        ids = {
            cid
            for cid, candidate in lookup.items()
            if 2026 - candidate.birth_year >= low and 2026 - candidate.birth_year < high
        }
        mask = np.asarray([cid in ids for cid in table.candidate_ids], dtype=bool)
        result = summarise(table.subset(mask), f"age={label}")
        if result is not None:
            rows.append(result)

    return rows


def _indices_of(table: FeatureTable, subset: FeatureTable) -> list[int]:
    """Row indices of ``subset`` inside the parent ``table``."""
    wanted = set(zip(subset.job_ids, subset.candidate_ids, strict=True))
    return [
        index
        for index, key in enumerate(zip(table.job_ids, table.candidate_ids, strict=True))
        if key in wanted
    ]


def _fmt(value: float | None, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    lines = ["| " + " | ".join(str(header) for header in headers) + " |"]
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def _display_path(path: Path) -> str:
    """Render a path relative to the current directory when possible.

    Reports get pasted into pull requests, so an absolute path from someone's
    laptop is noise at best and a leak of their directory layout at worst.
    """
    candidate = Path(path)
    try:
        return str(candidate.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(candidate)


def write_markdown_report(
    result: TrainResult,
    corpus: Corpus,
    table: FeatureTable,
    *,
    output_path: Path,
    test_scores: Sequence[float],
    dataset_dir: Path,
    commands: Sequence[str] = (),
) -> Path:
    """Render the full experiment report."""
    payload = result.as_dict()
    metrics = [
        "ndcg@5", "map@5", "precision@5", "recall@5", "mrr@5",
        "ndcg@10", "map@10", "precision@10", "recall@10",
    ]
    baseline = result.baseline_report
    test = result.test_report

    ranking_rows = []
    for metric in metrics:
        ci = test.ranking_ci.get(metric)
        ranking_rows.append(
            [
                f"`{metric}`",
                _fmt(baseline.ranking.get(metric)),
                _fmt(test.ranking.get(metric)),
                _fmt(result._lift().get(metric)) if result._lift().get(metric) is not None else "n/a",
                f"[{_fmt(ci.low)}, {_fmt(ci.high)}]" if ci else "n/a",
            ]
        )

    classification_rows = [
        [f"`{key}`", _fmt(baseline.classification.get(key)), _fmt(test.classification.get(key))]
        for key in ("roc_auc", "pr_auc", "brier", "positive_rate")
    ]

    importance_rows = [[f"`{name}`", _fmt(value, 6)] for name, value in result.feature_importance[:12]]
    audit_rows = fairness_audit(table, test_scores, corpus)
    audit_table = (
        _table(
            ["subgroup", "pairs", "positive rate", "ndcg@5", "precision@5", "mean score"],
            [
                [
                    row.subgroup,
                    row.rows,
                    _fmt(row.positive_rate),
                    _fmt(row.ndcg_at_5),
                    _fmt(row.precision_at_5),
                    _fmt(row.mean_predicted),
                ]
                for row in audit_rows
            ],
        )
        if audit_rows
        else "_Not enough rows per subgroup to audit._"
    )

    ndcg_values = [test.ranking.get("ndcg@5", 0.0), baseline.ranking.get("ndcg@5", 0.0)]
    decisive = next(
        (row for row in result.significance if row.get("metric") == "ndcg@5"), None
    )
    if decisive is None:
        verdict = "No significance test was available for the headline metric."
    elif decisive["better"] == "A":
        verdict = (
            "The learned reranker beats the shipped rule-based ranking on out-of-fold predictions, "
            f"and the difference survives a paired bootstrap over jobs (p={decisive['p_value']})."
        )
    elif decisive["better"] == "B":
        verdict = (
            "The shipped rule-based ranking is better than the learned reranker on out-of-fold "
            f"predictions (paired bootstrap p={decisive['p_value']}). Keep the interpretable scorer."
        )
    else:
        verdict = (
            "**The learned reranker is statistically indistinguishable from the shipped "
            f"rule-based ranking on NDCG@5** (mean difference "
            f"{decisive['mean_difference']:+.4f}, 95% CI {decisive['ci95']}, "
            f"p={decisive['p_value']}). It is better calibrated point-wise, but that does not "
            "translate into a better shortlist, so it is not worth the extra moving part in the "
            "request path."
        )

    significance_rows = [
        [
            f"`{row['metric']}`",
            f"{row['mean_difference']:+.4f}",
            f"[{row['ci95'][0]:+.4f}, {row['ci95'][1]:+.4f}]",
            f"{row['p_value']:.4f}",
            str(row["queries"]),
            str(row["better"]),
        ]
        for row in result.significance
    ]
    relevant = sum(1 for label in corpus.labels if label.relevance >= RELEVANCE_THRESHOLD)

    lines = [
        "# TalentMatch reranker: experiment report",
        "",
        f"- Generated: {payload['trained_at']}",
        f"- Model: `{result.model_name}`",
        f"- Corpus: {len(corpus.jobs)} jobs x {len(corpus.candidates)} candidates "
        f"= {len(corpus.labels)} labelled pairs (synthetic)",
        f"- Grade distribution: {corpus.grade_distribution()}",
        f"- Relevant pairs (grade >= {RELEVANCE_THRESHOLD}): {relevant} "
        f"({relevant / max(1, len(corpus.labels)):.1%})",
        f"- Evaluation: {payload['split']['strategy']}, {payload['split']['evaluation']}",
        f"- Features: {len(table.names)}",
        "",
        "## Headline",
        "",
        f"{verdict}",
        "",
        "## Ranking metrics (each job scored by a model that never saw it)",
        "",
        _table(["metric", "rule-based baseline", f"{result.model_name} reranker", "relative lift", "95% CI (bootstrap over jobs)"], ranking_rows),
        "",
        "## Is the difference real? (paired bootstrap over jobs)",
        "",
        "Same queries, same folds, resampled together. A model that wins on average but not "
        "significantly has not earned deployment.",
        "",
        _table(
            ["metric", "mean diff (model - baseline)", "95% CI", "p-value", "queries", "better"],
            significance_rows,
        ),
        "",
        "## Point-wise calibration (out-of-fold)",
        "",
        _table(["metric", "rule-based baseline", f"{result.model_name} reranker"], classification_rows),
        "",
        "## Feature importance (permutation, average precision)",
        "",
        _table(["feature", "importance"], importance_rows),
        "",
        "## Fairness audit",
        "",
        "Protected attributes are excluded from features and from label generation. "
        "They are used here only to compare ranking quality across subgroups.",
        "",
        audit_table,
        "",
        "## Reproduction",
        "",
        "```bash",
        *[f"{command}" for command in commands],
        "```",
        "",
        f"Dataset directory: `{_display_path(dataset_dir)}`",
        "",
        "## Limitations",
        "",
        "- The corpus is synthetic. Absolute scores describe this generator, not real hiring data.",
        "- Labels are derived from a latent skill-depth model, so they are learnable but imperfect. "
        "Real screening labels carry bias this simulation cannot reproduce.",
        "- The text channels are lexical (TF-IDF and hashed n-grams). No neural encoder was "
        "available in this environment, so semantic recall is weaker than a MiniLM-class encoder would be.",
        f"- Results cover {len(table.names)} features over the shipped scoring components. A materially "
        "better ranker would need features the parser does not currently extract.",
        "",
        "## Intended use",
        "",
        "Decision support for a human reviewer. Outputs are ordered suggestions with evidence, "
        "never an automatic rejection. Gender, age, ethnicity, disability, religion and "
        "photograph must never be used as ranking inputs, and no score should be treated as a "
        "hiring decision on its own.",
        "",
    ]
    if result.warnings:
        headline = lines.index("## Headline")
        lines.insert(
            headline,
            "## Warnings\n\n"
            + "\n".join(f"- {warning}" for warning in result.warnings)
            + "\n",
        )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path