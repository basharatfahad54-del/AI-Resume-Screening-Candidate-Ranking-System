"""Matching, ranking, comparison, export and scoring-weight endpoints.

Every response that contains a score also carries a disclaimer, and the
explanation travels with the number. A score the reviewer cannot interrogate is
worse than no score at all.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from sqlalchemy import select

from app.api.deps import AdminUser, PaginationDep, RecruiterUser, SessionDep, client_ip, user_agent
from app.core.logging import get_logger
from app.ml.ranking import normalize_sort_field
from app.models import Job, MatchResult, ScoringWeight
from app.schemas.matching import (
    AnalyzeRequest,
    AnalyzeResponse,
    CompareRequest,
    CompareResponse,
    ExportRequest,
    MatchExplanation,
    MatchResultRead,
    RankedCandidate,
    RankingResponse,
    ScoringWeights,
    SortField,
    WeightProfileCreate,
    WeightProfileRead,
)
from app.services import audit as audit_service
from app.services import jobs as jobs_service
from app.services import matching as matching_service
from app.services import weights as weights_service
from app.utils.jsonio import loads_dict

logger = get_logger(__name__)
router = APIRouter(tags=["matching"])

_DISCLAIMER = (
    "Scores are decision-support signals computed from resume text. They do not "
    "reflect candidate suitability as a whole, and the decision to progress or "
    "reject a candidate stays with a human reviewer."
)


def _client(request: Request) -> dict[str, str | None]:
    return {
        "ip_address": client_ip(request),
        "user_agent": user_agent(request),
    }


def _ranked(index: int, candidate, match: MatchResult) -> RankedCandidate:
    from app.api.routes.candidates import _to_summary

    return RankedCandidate(
        rank=index,
        candidate=_to_summary(candidate),
        match=_build_match_read(match),
    )


def _build_match_read(match: MatchResult) -> MatchResultRead:
    explanation = loads_dict(match.explanation)
    return MatchResultRead(
        id=match.id,
        job_id=match.job_id,
        candidate_id=match.candidate_id,
        required_skills_score=match.required_skills_score,
        experience_score=match.experience_score,
        education_score=match.education_score,
        preferred_skills_score=match.preferred_skills_score,
        semantic_score=match.semantic_score,
        certifications_score=match.certifications_score,
        overall_score=match.overall_score,
        weights={str(k): float(v) for k, v in loads_dict(match.weights).items()},
        explanation=MatchExplanation(**explanation) if explanation else None,
        evidence=[
            {
                "component": row.component,
                "label": row.label,
                "detail": row.detail,
                "status": row.status,
                "weight": row.weight,
                "snippet": row.snippet,
            }
            for row in match.evidence
        ],
        created_at=match.created_at,
    )


# --------------------------------------------------------------------------- #
# Scoring weights
# --------------------------------------------------------------------------- #
@router.get("/weights", response_model=list[WeightProfileRead], summary="List weight profiles")
async def list_weight_profiles(session: SessionDep, user: RecruiterUser) -> list[WeightProfileRead]:
    profiles = await weights_service.list_profiles(session)
    return [_weight_read(profile) for profile in profiles]


@router.get("/weights/default", response_model=ScoringWeights, summary="Default weights")
async def default_weights(session: SessionDep, user: RecruiterUser) -> ScoringWeights:
    row = await weights_service.ensure_default_profile(session)
    return weights_service.weights_for_profile(row)


@router.post(
    "/weights",
    response_model=WeightProfileRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a weight profile",
)
async def create_weight_profile(
    payload: WeightProfileCreate, request: Request, session: SessionDep, user: AdminUser
) -> WeightProfileRead:
    """Create a named weighting.

    Admin-only: changing the weights changes everyone's ranking, so it is
    restricted rather than available to every recruiter.
    """
    profile = await weights_service.create_profile(
        session,
        name=payload.name,
        weights=payload.weights,
        description=payload.description,
        is_default=payload.is_default,
        created_by=user.id,
    )
    await audit_service.audit(
        session,
        action=audit_service.ACTION_WEIGHTS_UPDATE,
        user_id=user.id,
        entity_type="weight_profile",
        entity_id=profile.id,
        detail={"name": profile.name, "weights": payload.weights.model_dump()},
        **_client(request),
    )
    await session.commit()
    return _weight_read(profile)


@router.delete(
    "/weights/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete a weight profile",
)
async def delete_weight_profile(
    profile_id: int, request: Request, session: SessionDep, user: AdminUser
) -> None:
    if not await weights_service.delete_profile(session, profile_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That weight profile does not exist."
        )
    await audit_service.audit(
        session,
        action=audit_service.ACTION_WEIGHTS_UPDATE,
        user_id=user.id,
        entity_type="weight_profile",
        entity_id=profile_id,
        detail={"deleted": True},
        **_client(request),
    )
    await session.commit()


def _weight_read(profile: ScoringWeight) -> WeightProfileRead:
    stored = loads_dict(profile.payload)
    return WeightProfileRead(
        id=profile.id,
        name=profile.name,
        description=profile.description,
        weights=ScoringWeights(**{key: float(value) for key, value in stored.items()}),
        is_default=profile.is_default,
        created_at=profile.created_at,
    )


# --------------------------------------------------------------------------- #
# Analyze / rank
# --------------------------------------------------------------------------- #
@router.post(
    "/matches/analyze",
    response_model=AnalyzeResponse,
    summary="Score candidates against a job",
)
async def analyze(
    payload: AnalyzeRequest, request: Request, session: SessionDep, user: RecruiterUser
) -> AnalyzeResponse:
    """Run the scoring pipeline.

    Synchronous by design for small pools: the endpoint reports how long it took
    and how many candidates failed, so a partial failure is visible rather than
    silently reducing the candidate set.
    """
    job = await _load_job(session, payload.job_id)
    weights = await weights_service.resolve_weights(
        session, explicit=payload.weights, profile_name=payload.weight_profile
    )

    analyzed, skipped, failed, duration_ms, _top_ids = await matching_service.analyze_job(
        session,
        job,
        weights=weights,
        candidate_ids=payload.candidate_ids,
        limit=payload.limit,
        force=payload.force,
    )

    await audit_service.audit(
        session,
        action=audit_service.ACTION_MATCH_ANALYZE,
        user_id=user.id,
        entity_type="job",
        entity_id=job.id,
        detail={
            "analyzed": analyzed,
            "skipped": skipped,
            "failed": failed,
            "weights": weights.model_dump(),
        },
        **_client(request),
    )
    await session.commit()

    pairs, _stored = await matching_service.load_rankings(session, job, sort_by="overall", limit=10)
    return AnalyzeResponse(
        job_id=job.id,
        analyzed=analyzed,
        skipped=skipped,
        failed=failed,
        duration_ms=duration_ms,
        top_candidates=[_ranked(index, candidate, match) for index, (candidate, match) in enumerate(pairs, 1)],
    )


@router.get(
    "/jobs/{job_id}/ranking",
    response_model=RankingResponse,
    summary="Ranked candidates for a job",
)
async def ranking(
    job_id: int,
    request: Request,
    session: SessionDep,
    user: RecruiterUser,
    page: PaginationDep,
    sort_by: Annotated[SortField, Query(description="Column to order by")] = "overall",
    shortlisted_only: bool = False,
) -> RankingResponse:
    """Paginated ranking.

    Ordering and pagination both happen in SQL on the requested column, with the
    candidate id as a tie-breaker so a page boundary cannot drop or repeat a
    candidate when scores are equal.
    """
    job = await _load_job(session, job_id)
    pairs, weights = await matching_service.load_rankings(
        session,
        job,
        sort_by=sort_by,
        shortlisted_only=shortlisted_only,
        limit=page.page_size,
        offset=page.offset,
    )

    total = await _ranked_total(session, job.id, shortlisted_only)
    if weights is None:
        weights = await weights_service.resolve_weights(session)

    await audit_service.audit(
        session,
        action=audit_service.ACTION_MATCH_RANKING_VIEW,
        user_id=user.id,
        entity_type="job",
        entity_id=job.id,
        detail={"sort_by": normalize_sort_field(sort_by), "returned": len(pairs), "total": total},
        **_client(request),
    )
    await session.commit()

    return RankingResponse(
        job_id=job.id,
        job_title=job.title,
        total_candidates=total,
        sort_by=normalize_sort_field(sort_by),
        weights=weights,
        results=[_ranked(index, candidate, match) for index, (candidate, match) in enumerate(pairs, page.offset + 1)],
    )


@router.get(
    "/matches/{match_id}",
    summary="The evidence behind a single match",
)
async def match_detail(
    match_id: int, session: SessionDep, user: RecruiterUser
) -> dict[str, object]:
    """Full audit trail for one score.

    This is the endpoint a reviewer uses to challenge a result, so it returns
    the raw evidence rows and the weights the score was computed with.
    """
    match = (
        await session.execute(select(MatchResult).where(MatchResult.id == match_id))
    ).scalar_one_or_none()
    if match is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such match result.")

    read = _build_match_read(match)
    return {
        "match": read.model_dump(mode="json"),
        "evidence_count": len(match.evidence),
        "disclaimer": _DISCLAIMER,
    }


@router.post(
    "/matches/compare",
    response_model=CompareResponse,
    summary="Side-by-side comparison of candidates",
)
async def compare(
    payload: CompareRequest, session: SessionDep, user: RecruiterUser
) -> CompareResponse:
    """Factual comparison only.

    Deliberately produces no overall verdict: an automated "who should we hire"
    answer is the autonomous decision this tool exists to avoid.
    """
    try:
        return await matching_service.compare_candidates(
            session,
            candidate_ids=payload.candidate_ids,
            job_id=payload.job_id,
            criteria=payload.criteria,
        )
    except matching_service.ComparisonError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/matches/export", summary="Export a ranking as CSV or JSON")
async def export(
    payload: ExportRequest, request: Request, session: SessionDep, user: RecruiterUser
) -> Response:
    job = await _load_job(session, payload.job_id)
    body, media_type = await matching_service.export_ranking(
        session,
        job,
        export_format=payload.format,
        include_explanation=payload.include_explanation,
    )
    await audit_service.audit(
        session,
        action=audit_service.ACTION_CANDIDATE_EXPORT,
        user_id=user.id,
        entity_type="job",
        entity_id=job.id,
        detail={"format": payload.format},
        **_client(request),
    )
    await session.commit()

    extension = payload.format
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="ranking-job-{job.id}.{extension}"'
        },
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
async def _load_job(session: SessionDep, job_id: int) -> Job:
    try:
        return await jobs_service.get_job(session, job_id)
    except jobs_service.JobNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


async def _ranked_total(session: SessionDep, job_id: int, shortlisted_only: bool) -> int:
    from sqlalchemy import func

    from app.models import Candidate

    stmt = (
        select(func.count(MatchResult.id))
        .join(Candidate, Candidate.id == MatchResult.candidate_id)
        .where(MatchResult.job_id == job_id, Candidate.is_deleted.is_(False))
    )
    if shortlisted_only:
        stmt = stmt.where(Candidate.shortlisted.is_(True))
    return int((await session.execute(stmt)).scalar_one() or 0)