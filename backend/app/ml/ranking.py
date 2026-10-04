"""Candidate ranking (PRD sections 11 and 12).

Ranking is a *presentation* concern layered over scoring:

* results are ordered by a recruiter-selected sort field, not hard-coded to the
  overall score;
* ties are broken deterministically (score, then candidate id) so repeated
  requests return a stable order and pagination cannot drop or duplicate rows;
* the returned payload always carries the weighting profile and a disclaimer,
  because a ranking without its configuration is not interpretable.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ml.scoring import MatchComputation

__all__ = ["RankedEntry", "rank_entries", "SORT_FIELDS"]

#: Sort keys accepted by the API, mapped onto ``MatchComputation`` attributes.
SORT_FIELDS: dict[str, str] = {
    "overall": "overall_score",
    "required_skills": "required_skills_score",
    "experience": "experience_score",
    "education": "education_score",
    "preferred_skills": "preferred_skills_score",
    "semantic": "semantic_score",
    "certifications": "certifications_score",
}


@dataclass(slots=True)
class RankedEntry:
    """One scored candidate, ready to be rendered."""

    candidate_id: int
    computation: MatchComputation
    sort_value: float

    @property
    def overall_score(self) -> float:
        return self.computation.overall_score


def _sort_value(computation: MatchComputation, attribute: str) -> float:
    return float(getattr(computation, attribute, 0.0) or 0.0)


def rank_entries(
    computations: list[tuple[int, MatchComputation]],
    sort_by: str = "overall",
) -> list[RankedEntry]:
    """Order scored candidates for display.

    ``computations`` maps candidate id to its :class:`MatchComputation`. An
    unknown ``sort_by`` falls back to the overall score rather than raising, so
    a stale client cannot break the ranking view.
    """
    attribute = SORT_FIELDS.get(sort_by, "overall_score")

    entries = [
        RankedEntry(
            candidate_id=candidate_id,
            computation=computation,
            sort_value=_sort_value(computation, attribute),
        )
        for candidate_id, computation in computations
    ]

    # Descending on the sort field; ties fall back to the overall score and then
    # to the candidate id so the order is total and reproducible.
    entries.sort(key=lambda entry: (-entry.sort_value, -entry.overall_score, entry.candidate_id))
    return entries


def normalize_sort_field(sort_by: str | None) -> str:
    if sort_by and sort_by in SORT_FIELDS:
        return sort_by
    return "overall"


def score_band(score: float) -> str:
    """Coarse band used for dashboard bucketing and UI colouring."""
    if score >= 0.85:
        return "excellent"
    if score >= 0.7:
        return "strong"
    if score >= 0.55:
        return "moderate"
    if score >= 0.4:
        return "weak"
    return "poor"
