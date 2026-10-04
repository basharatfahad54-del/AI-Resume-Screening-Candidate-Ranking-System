"""Matching, ranking, comparison and export (PRD sections 10-15, 20).

This service is the bridge between the database and the pure scoring engine. It
converts ORM rows into :class:`app.ml.scoring` dataclasses, runs the
computation, persists the result with its evidence, and never computes a score
inline: every number in a response came from ``app.ml.scoring``.
"""

from __future__ import annotations

import csv
import io
import json
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.ml.explanation import build_evidence_rows, build_explanation, describe_degree
from app.ml.ranking import SORT_FIELDS, normalize_sort_field
from app.ml.scoring import (
    CandidateProfile,
    JobProfile,
    MatchComputation,
    SkillSignal,
    compute_match,
)
from app.models import Candidate, CandidateSkill, Job, MatchEvidence, MatchResult, ProcessingStatus
from app.schemas.matching import (
    CandidateComparisonRow,
    CompareResponse,
    ScoringWeights,
)
from app.utils.jsonio import dumps, loads_dict, loads_list

logger = get_logger(__name__)

__all__ = [
    "ComparisonError",
    "analyze_job",
    "compare_candidates",
    "export_ranking",
    "job_profile",
    "candidate_profile",
    "load_rankings",
]

_CRITERIA = {
    "required_skills": "Required skills",
    "experience": "Experience",
    "education": "Education",
    "preferred_skills": "Preferred skills",
    "semantic": "Semantic match",
    "certifications": "Certifications",
    "overall": "Overall score",
}

_EDUCATION_DISPLAY = {
    "required_skills": "Required skills",
    "experience": "Experience",
    "education": "Education",
    "preferred_skills": "Preferred skills",
    "semantic": "Semantic similarity",
    "certifications": "Certifications",
}


class ComparisonError(Exception):
    """Comparison cannot be performed as requested."""


# --------------------------------------------------------------------------- #
# ORM -> scoring dataclasses
# --------------------------------------------------------------------------- #
def job_profile(job: Job, embedding=None) -> JobProfile:
    return JobProfile(
        job_id=job.id,
        title=job.title,
        required_skills=tuple(str(item) for item in loads_list(job.required_skills)),
        preferred_skills=tuple(str(item) for item in loads_list(job.preferred_skills)),
        certifications=tuple(str(item) for item in loads_list(job.certifications)),
        experience_required_years=float(job.experience_required_years or 0.0),
        education_required=job.education_required,
        text=job.description or "",
        embedding=embedding,
    )


def candidate_profile(candidate: Candidate, embedding=None) -> CandidateProfile:
    signals: list[SkillSignal] = []
    for link in candidate.skills:
        if link.skill is None:
            continue
        signals.append(
            SkillSignal(
                normalized_name=link.skill.normalized_name,
                display_name=link.skill.name,
                confidence=float(link.confidence or 0.0),
                years_experience=link.years_experience,
                is_certified=bool(link.is_certified),
                evidence=tuple(str(item) for item in loads_list(link.evidence)),
            )
        )
    return CandidateProfile(
        candidate_id=candidate.id,
        full_name=candidate.full_name,
        skills=tuple(signals),
        total_experience_years=float(candidate.total_experience_years or 0.0),
        relevant_experience_years=float(candidate.relevant_experience_years or 0.0),
        highest_degree=candidate.highest_degree,
        certifications=tuple(str(item) for item in loads_list(candidate.certifications)),
        text=candidate.raw_text or "",
        embedding=embedding,
    )


def _embedding_of(row):
    from app.ml.embeddings import deserialize_embedding

    return deserialize_embedding(row.embedding)


def _with_embedding(candidate: Candidate):
    return candidate_profile(candidate, _embedding_of(candidate))


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #
async def _parseable_candidates(
    session: AsyncSession, job: Job, *, candidate_ids: list[int] | None, limit: int | None
) -> list[Candidate]:
    stmt = (
        select(Candidate)
        .where(
            Candidate.is_deleted.is_(False),
            Candidate.status == ProcessingStatus.parsed,
        )
        .options(selectinload(Candidate.skills).selectinload(CandidateSkill.skill))
        .order_by(Candidate.id)
    )
    if candidate_ids:
        stmt = stmt.where(Candidate.id.in_(candidate_ids))
    if limit:
        stmt = stmt.limit(limit)
    return list((await session.execute(stmt)).scalars().unique().all())


async def analyze_job(
    session: AsyncSession,
    job: Job,
    *,
    weights: ScoringWeights,
    candidate_ids: list[int] | None = None,
    limit: int | None = None,
    force: bool = False,
) -> tuple[int, int, int, int, list[int]]:
    """Score every eligible candidate against ``job`` and persist the results.

    Returns ``(analyzed, skipped, failed, duration_ms, top_candidate_ids)``.

    ``force=False`` skips candidates that already have a match for this job
    using the *same* weights, which makes re-running a batch cheap. Results
    computed under different weights are always recomputed.
    """
    started = time.perf_counter()
    candidates = await _parseable_candidates(
        session, job, candidate_ids=candidate_ids, limit=limit
    )
    weight_map = weights.model_dump()

    existing: dict[int, MatchResult] = {}
    if candidates:
        stmt = select(MatchResult).where(
            MatchResult.job_id == job.id,
            MatchResult.candidate_id.in_([item.id for item in candidates]),
        )
        existing = {row.candidate_id: row for row in (await session.execute(stmt)).scalars().all()}

    profile = job_profile(job, _embedding_of(job))
    analyzed = skipped = failed = 0
    top: list[tuple[float, int]] = []

    for candidate in candidates:
        previous = existing.get(candidate.id)
        if previous is not None and not force and loads_dict(previous.weights) == weight_map:
            skipped += 1
            top.append((float(previous.overall_score), candidate.id))
            continue

        try:
            computation = compute_match(profile, _with_embedding(candidate), weights)
            await _persist_match(session, job, candidate, profile, computation, previous)
            analyzed += 1
            top.append((computation.overall_score, candidate.id))
        except Exception as exc:  # noqa: BLE001 - one bad candidate must not fail the batch
            failed += 1
            logger.exception("Matching failed for candidate %s against job %s", candidate.id, job.id)
            logger.debug("candidate %s failure detail: %s", candidate.id, exc)

    top.sort(key=lambda pair: (-pair[0], pair[1]))
    duration_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "Analyzed job %s: analyzed=%s skipped=%s failed=%s in %sms",
        job.id,
        analyzed,
        skipped,
        failed,
        duration_ms,
    )
    return analyzed, skipped, failed, duration_ms, [cid for _score, cid in top[:10]]


async def _persist_match(
    session: AsyncSession,
    job: Job,
    candidate: Candidate,
    profile: JobProfile,
    computation: MatchComputation,
    previous: MatchResult | None,
) -> MatchResult:
    """Create or update the match row, replacing its evidence atomically."""
    if previous is None:
        previous = MatchResult(job_id=job.id, candidate_id=candidate.id)
        session.add(previous)
    else:
        for row in list(previous.evidence):
            await session.delete(row)

    previous.required_skills_score = computation.required_skills_score
    previous.experience_score = computation.experience_score
    previous.education_score = computation.education_score
    previous.preferred_skills_score = computation.preferred_skills_score
    previous.semantic_score = computation.semantic_score
    previous.certifications_score = computation.certifications_score
    previous.overall_score = computation.overall_score
    previous.weights = dumps(computation.weights)
    previous.explanation = dumps(build_explanation(profile, computation))
    await session.flush()

    rows = build_evidence_rows(computation)
    if rows:
        session.add_all(
            [
                MatchEvidence(
                    match_id=previous.id,
                    component=str(row["component"]),
                    label=str(row["label"])[:200],
                    detail=str(row["detail"]),
                    status=str(row["status"]),
                    weight=float(row["weight"] or 0.0),
                    snippet=(str(row["snippet"]) if row["snippet"] else None),
                )
                for row in rows
            ]
        )
        await session.flush()
    return previous


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #
async def load_rankings(
    session: AsyncSession,
    job: Job,
    *,
    sort_by: str = "overall",
    shortlisted_only: bool = False,
    limit: int | None = None,
    offset: int = 0,
) -> tuple[list[tuple[Candidate, MatchResult]], ScoringWeights | None]:
    """Fetch stored matches for a job, already ordered for display.

    Ordering happens in SQL on the requested sort column so pagination is
    correct and stable; ``app.ml.ranking.rank_entries`` is used for the
    in-memory paths (comparison, exports) where no pagination is involved.
    """
    sort_field = normalize_sort_field(sort_by)
    column = {
        "overall": MatchResult.overall_score,
        "required_skills": MatchResult.required_skills_score,
        "experience": MatchResult.experience_score,
        "education": MatchResult.education_score,
        "preferred_skills": MatchResult.preferred_skills_score,
        "semantic": MatchResult.semantic_score,
        "certifications": MatchResult.certifications_score,
    }[sort_field]

    stmt = (
        select(MatchResult, Candidate)
        .join(Candidate, Candidate.id == MatchResult.candidate_id)
        .where(MatchResult.job_id == job.id, Candidate.is_deleted.is_(False))
        .options(
            selectinload(Candidate.skills).selectinload(CandidateSkill.skill),
            selectinload(MatchResult.evidence),
        )
        # Ties break on id so pagination cannot drop or repeat a candidate.
        .order_by(column.desc(), MatchResult.id.asc())
    )
    if shortlisted_only:
        stmt = stmt.where(Candidate.shortlisted.is_(True))
    if offset:
        stmt = stmt.offset(offset)
    if limit:
        stmt = stmt.limit(limit)

    rows = (await session.execute(stmt)).all()
    pairs = [(candidate, match) for match, candidate in rows]

    weights: ScoringWeights | None = None
    for _candidate, match in pairs:
        stored = loads_dict(match.weights)
        if stored:
            try:
                weights = ScoringWeights(**{key: float(value) for key, value in stored.items()})
            except (ValueError, TypeError):
                weights = None
            break
    return pairs, weights


def sort_key_name(sort_by: str) -> str:
    return _EDUCATION_DISPLAY.get(sort_by, sort_by)


def rank_in_memory(pairs: list[tuple[Candidate, MatchResult]], sort_by: str) -> list[tuple[Candidate, MatchResult]]:
    """Sort an already-loaded page in Python, mirroring the SQL ordering."""
    field = SORT_FIELDS.get(normalize_sort_field(sort_by), "overall_score")
    return sorted(
        pairs,
        key=lambda pair: (-float(getattr(pair[1], field, 0.0) or 0.0), pair[1].id),
    )


# --------------------------------------------------------------------------- #
# Comparison (PRD section 14)
# --------------------------------------------------------------------------- #
async def compare_candidates(
    session: AsyncSession,
    *,
    candidate_ids: list[int],
    job_id: int | None,
    criteria: list[str] | None = None,
) -> CompareResponse:
    """Produce a factual side-by-side of extracted profile data.

    Shows what differs and who leads each criterion. It deliberately produces no
    recommendation - an automated "who should we hire" verdict would be exactly
    the autonomous decision the PRD forbids.
    """
    unique_ids = list(dict.fromkeys(candidate_ids))
    if len(unique_ids) < 2:
        raise ComparisonError("Select at least two candidates to compare")

    stmt = (
        select(Candidate)
        .where(Candidate.id.in_(unique_ids), Candidate.is_deleted.is_(False))
        .options(selectinload(Candidate.skills).selectinload(CandidateSkill.skill))
    )
    found = {row.id: row for row in (await session.execute(stmt)).scalars().unique().all()}
    missing = [cid for cid in unique_ids if cid not in found]
    if missing:
        raise ComparisonError(f"Candidate(s) not found: {', '.join(map(str, missing))}")
    candidates = [found[cid] for cid in unique_ids]

    selected = [key for key in (criteria or list(_CRITERIA)) if key in _CRITERIA and key != "overall"]
    if not selected:
        selected = ["required_skills", "experience", "education", "preferred_skills", "semantic"]

    scores: dict[int, dict[str, float | None]] = {}
    degrees: dict[int, str] = {}
    for candidate in candidates:
        scores[candidate.id] = await _score_map(session, candidate, job_id)
        degrees[candidate.id] = candidate.highest_degree or ""

    rows: list[CandidateComparisonRow] = []
    for key in selected:
        values: dict[str, float | None] = {}
        for candidate in candidates:
            if key == "education":
                values[candidate.full_name] = float(degree_rank_value(degrees[candidate.id]))
            else:
                values[candidate.full_name] = scores[candidate.id].get(key)
        best = [
            name
            for name, value in values.items()
            if value is not None and value >= max((v for v in values.values() if v is not None), default=0.0)
        ]
        rows.append(
            CandidateComparisonRow(
                criterion=key,
                label=_EDUCATION_DISPLAY.get(key, _CRITERIA.get(key, key)),
                values=values,
                best=best if len(best) < len(values) else [],
                note=_comparison_note(key, values),
            )
        )

    union = sorted(
        {link.skill.normalized_name for candidate in candidates for link in candidate.skills}
    )
    catalogue = {
        link.skill.normalized_name: link.skill.name for candidate in candidates for link in candidate.skills
    }
    matrix: dict[str, list[bool]] = {}
    for normalized in union:
        present = [
            any(
                link.skill is not None and link.skill.normalized_name == normalized
                for link in candidate.skills
            )
            for candidate in candidates
        ]
        matrix[catalogue.get(normalized, normalized)] = present

    return CompareResponse(
        job_id=job_id,
        criteria=selected,
        columns=[candidate.full_name for candidate in candidates],
        rows=rows,
        skills_matrix=matrix,
    )


def degree_rank_value(degree: str) -> int:
    from app.nlp.education import degree_rank

    return degree_rank(degree)


def _comparison_note(key: str, values: dict[str, float | None]) -> str | None:
    known = [value for value in values.values() if value is not None]
    if len(known) < 2:
        return "Not available for every candidate"
    if key == "education":
        return "Ordinal scale: higher means a higher qualification"
    spread = max(known) - min(known)
    if spread < 0.05:
        return "Effectively equal"
    if spread < 0.2:
        return "Small difference"
    if spread < 0.5:
        return "Clear difference"
    return "Large difference"


async def _score_map(
    session: AsyncSession, candidate: Candidate, job_id: int | None
) -> dict[str, float | None]:
    """Component scores for one candidate, preferring the requested job."""
    stmt = select(MatchResult).where(MatchResult.candidate_id == candidate.id)
    if job_id is not None:
        stmt = stmt.where(MatchResult.job_id == job_id)
    stmt = stmt.order_by(MatchResult.created_at.desc()).limit(1)
    match = (await session.execute(stmt)).scalar_one_or_none()
    if match is None:
        return {
            "required_skills": None,
            "experience": None,
            "education": None,
            "preferred_skills": None,
            "semantic": None,
            "certifications": None,
            "overall": None,
        }
    return {
        "required_skills": match.required_skills_score,
        "experience": match.experience_score,
        "education": match.education_score,
        "preferred_skills": match.preferred_skills_score,
        "semantic": match.semantic_score,
        "certifications": match.certifications_score,
        "overall": match.overall_score,
    }


# --------------------------------------------------------------------------- #
# Export (PRD section 30.9)
# --------------------------------------------------------------------------- #
async def export_ranking(
    session: AsyncSession,
    job: Job,
    *,
    export_format: str = "csv",
    include_explanation: bool = True,
    sort_by: str = "overall",
) -> tuple[str, str]:
    """Render the ranking as CSV or JSON. Returns ``(body, media_type)``."""
    pairs, _weights = await load_rankings(session, job, sort_by=sort_by)
    pairs = rank_in_memory(pairs, sort_by)

    rows: list[dict[str, object]] = []
    for index, (candidate, match) in enumerate(pairs, start=1):
        explanation = loads_dict(match.explanation)
        row: dict[str, object] = {
            "rank": index,
            "candidate_id": candidate.id,
            "name": candidate.full_name,
            "email": candidate.email or "",
            "location": candidate.location or "",
            "current_title": candidate.current_title or "",
            "total_experience_years": candidate.total_experience_years,
            "highest_degree": candidate.highest_degree or "",
            "shortlisted": candidate.shortlisted,
            "required_skills": match.required_skills_score,
            "experience": match.experience_score,
            "education": match.education_score,
            "preferred_skills": match.preferred_skills_score,
            "semantic": match.semantic_score,
            "certifications": match.certifications_score,
            "overall_score": match.overall_score,
            "missing_required": ", ".join(
                str(item) for item in explanation.get("missing_required", []) or []
            ),
            "weak_signals": ", ".join(
                str(item) for item in explanation.get("weak_signals", []) or []
            ),
        }
        if include_explanation:
            row["explanation_summary"] = str(explanation.get("summary") or "")
            row["matched_required"] = ", ".join(
                str(item) for item in explanation.get("strong_matches", []) or []
            )
        rows.append(row)

    if export_format == "json":
        payload = {
            "job": {"id": job.id, "title": job.title},
            "generated_at": job.updated_at.isoformat() if job.updated_at else None,
            "sort_by": normalize_sort_field(sort_by),
            "total_candidates": len(rows),
            "disclaimer": (
                "Decision-support output computed from resume text. Not a hiring decision."
            ),
            "candidates": rows,
        }
        return json.dumps(payload, indent=2, default=str), "application/json"

    buffer = io.StringIO()
    fieldnames = list(rows[0].keys()) if rows else [f"rank{i}" for i in range(1)] or ["no_results"]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in fieldnames})
    return buffer.getvalue(), "text/csv"


def describe_candidate_degree(degree: str | None) -> str:
    return describe_degree(degree)
