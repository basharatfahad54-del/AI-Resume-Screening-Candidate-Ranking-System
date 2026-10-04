"""Dashboard aggregates (PRD section 16).

Aggregates are computed in SQL rather than in Python: pulling every candidate
row into memory to count it is what makes a dashboard fall over at volume.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.models import (
    Candidate,
    CandidateSkill,
    Job,
    MatchResult,
    ProcessingStatus,
    Skill,
)
from app.schemas.assistant import (
    DashboardResponse,
    DashboardStats,
    JobCandidateCount,
    MissingSkillCount,
    ScoreBucket,
    SkillCount,
)
from app.utils.jsonio import loads_dict

logger = get_logger(__name__)

__all__ = ["dashboard_overview"]

#: Fixed buckets so the distribution is comparable between refreshes.
_SCORE_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("0-20", 0.0, 0.2),
    ("20-40", 0.2, 0.4),
    ("40-60", 0.4, 0.6),
    ("60-80", 0.6, 0.8),
    ("80-100", 0.8, 1.0001),
)


async def dashboard_overview(
    session: AsyncSession,
    *,
    job_id: int | None = None,
    recent_limit: int = 10,
) -> DashboardResponse:
    """Build the dashboard payload.

    ``job_id`` narrows the ranking-derived panels (score distribution, average
    score, per-job counts) to one job, while the candidate and skill totals stay
    global so the header never contradicts the job view.
    """
    live = Candidate.is_deleted.is_(False)

    total_candidates = int(
        (await session.execute(select(func.count(Candidate.id)).where(live))).scalar_one() or 0
    )
    candidates_processed = int(
        (
            await session.execute(
                select(func.count(Candidate.id)).where(
                    live, Candidate.status == ProcessingStatus.parsed
                )
            )
        ).scalar_one()
        or 0
    )
    failed_processing = int(
        (
            await session.execute(
                select(func.count(Candidate.id)).where(
                    live, Candidate.status == ProcessingStatus.failed
                )
            )
        ).scalar_one()
        or 0
    )
    shortlisted = int(
        (
            await session.execute(
                select(func.count(Candidate.id)).where(live, Candidate.shortlisted.is_(True))
            )
        ).scalar_one()
        or 0
    )
    active_jobs = int(
        (
            await session.execute(
                select(func.count(Job.id)).where(Job.is_active.is_(True))
            )
        ).scalar_one()
        or 0
    )
    total_skills = int((await session.execute(select(func.count(Skill.id)))).scalar_one() or 0)

    match_filter = MatchResult.job_id == job_id if job_id else True
    ranked = int(
        (
            await session.execute(
                select(func.count(MatchResult.id)).where(match_filter)
            )
        ).scalar_one()
        or 0
    )
    average = (
        await session.execute(select(func.avg(MatchResult.overall_score)).where(match_filter))
    ).scalar_one()

    stats = DashboardStats(
        total_candidates=total_candidates,
        active_jobs=active_jobs,
        candidates_processed=candidates_processed,
        average_match_score=round(float(average or 0.0), 4),
        shortlisted_candidates=shortlisted,
        total_skills=total_skills,
        failed_processing=failed_processing,
    )

    return DashboardResponse(
        stats=stats,
        candidates_per_job=await _per_job(session),
        score_distribution=await _distribution(session, job_id, ranked),
        top_skills=await _top_skills(session),
        most_missing_skills=await _most_missing(session, job_id),
        recent_candidates=await _recent(session, recent_limit),
    )


async def _per_job(session: AsyncSession, limit: int = 20) -> list[JobCandidateCount]:
    stmt = (
        select(
            Job.id,
            Job.title,
            func.count(MatchResult.id),
            func.coalesce(func.avg(MatchResult.overall_score), 0.0),
        )
        .join(MatchResult, MatchResult.job_id == Job.id, isouter=True)
        .where(Job.is_active.is_(True))
        .group_by(Job.id, Job.title)
        .order_by(func.count(MatchResult.id).desc(), Job.title)
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [
        JobCandidateCount(
            job_id=job_id,
            job_title=title,
            candidate_count=int(count or 0),
            average_score=round(float(average or 0.0), 4),
        )
        for job_id, title, count, average in rows
    ]


async def _distribution(
    session: AsyncSession, job_id: int | None, ranked: int
) -> list[ScoreBucket]:
    if not ranked:
        return []
    stmt = select(MatchResult.overall_score).where(
        MatchResult.job_id == job_id if job_id else True
    )
    scores = [float(row) for row in (await session.execute(stmt)).scalars().all()]

    counts = {label: 0 for label, _low, _high in _SCORE_BUCKETS}
    for score in scores:
        for label, low, high in _SCORE_BUCKETS:
            if low <= score < high:
                counts[label] += 1
                break
    return [ScoreBucket(bucket=label, count=counts[label]) for label, _l, _h in _SCORE_BUCKETS]


async def _top_skills(session: AsyncSession, limit: int = 15) -> list[SkillCount]:
    stmt = (
        select(
            Skill.name,
            Skill.normalized_name,
            Skill.category,
            func.count(CandidateSkill.id),
        )
        .join(CandidateSkill, CandidateSkill.skill_id == Skill.id)
        .join(Candidate, Candidate.id == CandidateSkill.candidate_id)
        .where(Candidate.is_deleted.is_(False))
        .group_by(Skill.id, Skill.name, Skill.normalized_name, Skill.category)
        .order_by(func.count(CandidateSkill.id).desc(), Skill.name)
        .limit(limit)
    )
    return [
        SkillCount(skill=name, normalized_name=normalized, category=category, count=int(count))
        for name, normalized, category, count in (await session.execute(stmt)).all()
    ]


async def _most_missing(
    session: AsyncSession, job_id: int | None, limit: int = 15
) -> list[MissingSkillCount]:
    """Requirements the most scored candidates lack.

    Read from the stored explanation rather than recomputed, so the dashboard
    cannot disagree with the ranking the recruiter actually looked at.

    Every scored candidate counts, not just the shortlisted ones: a panel that
    only inspects the shortlist is empty exactly when the shortlist is strong,
    which is when a recruiter least needs the information. Ties are broken by
    skill name so the panel does not reshuffle between refreshes.
    """
    stmt = (
        select(MatchResult)
        .options(selectinload(MatchResult.evidence))
        .join(Candidate, Candidate.id == MatchResult.candidate_id)
        .where(
            Candidate.is_deleted.is_(False),
            MatchResult.job_id == job_id if job_id else True,
        )
        .order_by(MatchResult.overall_score.desc())
        .limit(200)
    )
    matches = (await session.execute(stmt)).scalars().unique().all()
    if not matches:
        return []

    tally: dict[tuple[int, str], int] = {}
    labels: dict[tuple[int, str], str] = {}
    for match in matches:
        # ``explanation`` is a JSON object, so it must be decoded as a dict;
        # ``loads_list`` would silently yield an empty default.
        explanation = loads_dict(match.explanation)
        for term in explanation.get("missing_required", []) or []:
            key = (match.job_id, str(term))
            tally[key] = tally.get(key, 0) + 1
            labels.setdefault(key, str(term))

    # Grouped per job, most-missing first, so a multi-job dashboard stays legible.
    ranked_keys = sorted(tally.items(), key=lambda pair: (-pair[1], pair[0][0], pair[0][1]))[:limit]
    return [
        MissingSkillCount(job_id=key[0], skill=labels[key], missing_count=count)
        for key, count in ranked_keys
    ]


async def _recent(session: AsyncSession, limit: int) -> list[dict[str, object]]:
    stmt = (
        select(Candidate)
        .where(Candidate.is_deleted.is_(False))
        .options(selectinload(Candidate.skills).selectinload(CandidateSkill.skill))
        .order_by(Candidate.created_at.desc())
        .limit(limit)
    )
    candidates = (await session.execute(stmt)).scalars().unique().all()
    return [
        {
            "id": candidate.id,
            "full_name": candidate.full_name,
            "current_title": candidate.current_title,
            "status": getattr(candidate.status, "value", candidate.status),
            "total_experience_years": candidate.total_experience_years,
            "skill_count": len(candidate.skills),
            "shortlisted": candidate.shortlisted,
            "created_at": candidate.created_at.isoformat() if candidate.created_at else None,
        }
        for candidate in candidates
    ]