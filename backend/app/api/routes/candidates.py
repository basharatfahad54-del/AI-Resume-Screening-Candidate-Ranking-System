"""Candidate and resume endpoints (PRD sections 7-9, 26).

The stored document is never served as a static file: it is streamed through
this router so every read is authorised and audited.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.api.deps import PaginationDep, RecruiterUser, SessionDep, client_ip, user_agent
from app.core.logging import get_logger
from app.models import Candidate, CandidateSkill, Skill
from app.schemas.candidate import (
    BulkUploadResponse,
    CandidateCreate,
    CandidateRead,
    CandidateSkillRead,
    CandidateSummary,
    CandidateUpdate,
    ResumeUploadResponse,
    SkillRead,
)
from app.services import audit as audit_service
from app.services import resumes as resumes_service
from app.utils.jsonio import loads_dict, loads_list
from app.utils.storage import StorageError, ensure_storage_dirs, read_stored_file

logger = get_logger(__name__)
router = APIRouter(prefix="/candidates", tags=["candidates"])

#: Uploads accepted in one request. A larger batch goes through the async
#: endpoint so a single request cannot hold a worker for minutes.
_MAX_BULK_FILES = 20


def _client(request: Request) -> dict[str, str | None]:
    return {
        "ip_address": client_ip(request),
        "user_agent": user_agent(request),
    }


def _to_read(candidate: Candidate) -> CandidateRead:
    """Build the response, decoding the JSON-in-Text columns."""
    return CandidateRead(
        id=candidate.id,
        full_name=candidate.full_name,
        email=candidate.email,
        phone=candidate.phone,
        location=candidate.location,
        linkedin_url=candidate.linkedin_url,
        github_url=candidate.github_url,
        portfolio_url=candidate.portfolio_url,
        current_title=candidate.current_title,
        total_experience_years=float(candidate.total_experience_years or 0.0),
        relevant_experience_years=float(candidate.relevant_experience_years or 0.0),
        highest_degree=candidate.highest_degree,
        degree_field=candidate.degree_field,
        institution=candidate.institution,
        graduation_year=candidate.graduation_year,
        certifications=[str(item) for item in loads_list(candidate.certifications)],
        previous_positions=[str(item) for item in loads_list(candidate.previous_positions)],
        employment_history=loads_list(candidate.employment_history),
        status=candidate.status,
        processing_error=candidate.processing_error,
        shortlisted=candidate.shortlisted,
        original_filename=candidate.original_filename,
        created_at=candidate.created_at,
        skills=[
            CandidateSkillRead(
                skill=SkillRead.model_validate(link.skill),
                confidence=float(link.confidence or 0.0),
                years_experience=link.years_experience,
                is_certified=bool(link.is_certified),
                evidence=[str(item) for item in loads_list(link.evidence)],
            )
            for link in candidate.skills
            if link.skill is not None
        ],
    )


def _to_summary(candidate: Candidate) -> CandidateSummary:
    return CandidateSummary(
        id=candidate.id,
        full_name=candidate.full_name,
        email=candidate.email,
        location=candidate.location,
        current_title=candidate.current_title,
        total_experience_years=float(candidate.total_experience_years or 0.0),
        highest_degree=candidate.highest_degree,
        shortlisted=candidate.shortlisted,
        status=candidate.status,
        created_at=candidate.created_at,
        top_skills=[link.skill.name for link in candidate.skills[:8] if link.skill],
    )


# --------------------------------------------------------------------------- #
# Upload
# --------------------------------------------------------------------------- #
@router.post(
    "/upload",
    response_model=BulkUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and parse one or more resumes",
)
async def upload_resumes(
    request: Request,
    session: SessionDep,
    user: RecruiterUser,
    files: Annotated[list[UploadFile], File(description="PDF, DOCX or TXT resumes")],
) -> BulkUploadResponse:
    ensure_storage_dirs()
    if not files:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No files were uploaded."
        )
    if len(files) > _MAX_BULK_FILES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Upload at most {_MAX_BULK_FILES} resumes at a time.",
        )

    results: list[ResumeUploadResponse] = []
    errors: list[dict[str, str]] = []
    succeeded = failed = 0
    # Captured before the loop: a per-file SAVEPOINT rollback expires ORM
    # instances, so ``user.id`` would otherwise trigger a lazy load outside async
    # context when the audit record is written.
    user_id = user.id

    for upload in files:
        filename = upload.filename or "resume"
        try:
            content = await upload.read()
            # A SAVEPOINT, not a session-wide rollback: one unreadable file must
            # not discard the candidates already accepted in this batch, nor
            # expire objects the request is still using.
            async with session.begin_nested():
                outcome = await resumes_service.process_upload(
                    session, content=content, filename=filename
                )
        except StorageError as exc:
            failed += 1
            errors.append({"filename": filename, "error": str(exc)})
            continue
        except Exception:  # noqa: BLE001 - report the failure, keep the batch
            failed += 1
            errors.append({"filename": filename, "error": "The resume could not be processed."})
            logger.exception("Unexpected failure processing %r", filename)
            await session.rollback()
            continue

        await session.commit()
        if outcome.succeeded:
            succeeded += 1
            results.append(
                ResumeUploadResponse(
                    candidate=_to_read(outcome.candidate), warnings=outcome.warnings
                )
            )
        else:
            failed += 1
            errors.append(
                {"filename": filename, "error": outcome.error or "The resume could not be parsed."}
            )

    await audit_service.audit(
        session,
        action=audit_service.ACTION_RESUME_UPLOAD,
        user_id=user_id,
        entity_type="candidate_batch",
        detail={"uploaded": len(files), "succeeded": succeeded, "failed": failed},
        **_client(request),
    )
    await session.commit()

    return BulkUploadResponse(
        total=len(files),
        succeeded=succeeded,
        failed=failed,
        results=results,
        errors=errors,
    )


@router.post(
    "/{candidate_id}/resume",
    response_model=ResumeUploadResponse,
    summary="Replace the resume on an existing candidate",
)
async def replace_resume(
    candidate_id: int,
    request: Request,
    session: SessionDep,
    user: RecruiterUser,
    file: Annotated[UploadFile, File(description="PDF, DOCX or TXT resume")],
) -> ResumeUploadResponse:
    ensure_storage_dirs()
    candidate = await _load(session, candidate_id)
    content = await file.read()
    try:
        outcome = await resumes_service.process_upload(
            session, content=content, filename=file.filename or "resume", candidate_id=candidate.id
        )
    except StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    await audit_service.audit(
        session,
        action=audit_service.ACTION_RESUME_UPLOAD,
        user_id=user.id,
        entity_type="candidate",
        entity_id=candidate.id,
        detail={"replaced": True},
        **_client(request),
    )
    await session.commit()
    return ResumeUploadResponse(
        candidate=_to_read(outcome.candidate), warnings=outcome.warnings
    )


@router.post(
    "/{candidate_id}/reparse",
    response_model=ResumeUploadResponse,
    summary="Re-run parsing from the stored document",
)
async def reparse_resume(
    candidate_id: int, request: Request, session: SessionDep, user: RecruiterUser
) -> ResumeUploadResponse:
    """Re-parse after a parser upgrade without re-uploading the file."""
    candidate = await _load(session, candidate_id)
    outcome = await resumes_service.reparse_candidate(session, candidate)
    await audit_service.audit(
        session,
        action=audit_service.ACTION_RESUME_VIEW,
        user_id=user.id,
        entity_type="candidate",
        entity_id=candidate.id,
        detail={"reparse": True, "ok": outcome.succeeded},
        **_client(request),
    )
    await session.commit()
    if not outcome.succeeded:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=outcome.error or "The stored resume could not be re-parsed.",
        )
    return ResumeUploadResponse(
        candidate=_to_read(outcome.candidate), warnings=outcome.warnings
    )


# --------------------------------------------------------------------------- #
# Read
# --------------------------------------------------------------------------- #
@router.get("", summary="Search candidates")
async def search_candidates(
    session: SessionDep,
    user: RecruiterUser,
    page: PaginationDep,
    search: Annotated[str | None, Query(description="Name, email, title or location")] = None,
    location: str | None = None,
    current_title: str | None = None,
    min_experience: Annotated[float | None, Query(ge=0, le=60)] = None,
    max_experience: Annotated[float | None, Query(ge=0, le=60)] = None,
    education: str | None = None,
    certification: str | None = None,
    shortlisted_only: bool = False,
    skills: Annotated[list[str] | None, Query(description="Repeatable skill filter")] = None,
) -> dict[str, Any]:
    """Filter the candidate pool.

    Every filter is an AND, and ``skills`` requires *all* listed skills, which
    is how a recruiter narrows a pool. The response carries ``total`` and
    ``pages`` so the UI never has to guess whether more results exist.
    """
    stmt = (
        select(Candidate)
        .where(Candidate.is_deleted.is_(False))
        .options(selectinload(Candidate.skills).selectinload(CandidateSkill.skill))
    )

    if search and search.strip():
        needle = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                Candidate.full_name.ilike(needle),
                Candidate.email.ilike(needle),
                Candidate.current_title.ilike(needle),
                Candidate.location.ilike(needle),
            )
        )
    if location and location.strip():
        stmt = stmt.where(Candidate.location.ilike(f"%{location.strip()}%"))
    if current_title and current_title.strip():
        stmt = stmt.where(Candidate.current_title.ilike(f"%{current_title.strip()}%"))
    if education and education.strip():
        stmt = stmt.where(Candidate.highest_degree.ilike(f"%{education.strip()}%"))
    if min_experience is not None:
        stmt = stmt.where(Candidate.total_experience_years >= min_experience)
    if max_experience is not None:
        stmt = stmt.where(Candidate.total_experience_years <= max_experience)
    if shortlisted_only:
        stmt = stmt.where(Candidate.shortlisted.is_(True))

    if certification and certification.strip():
        # Certifications live in a JSON column, so this is a text match. The
        # column is small per row, and the filter is applied after the
        # candidate set is already narrowed by the SQL predicates above.
        needle = certification.strip().casefold()
        matching = select(Candidate.id).where(
            Candidate.is_deleted.is_(False), Candidate.certifications.ilike(f"%{needle}%")
        )
        stmt = stmt.where(Candidate.id.in_(matching))

    if skills:
        wanted = [item.strip().lower() for item in skills if item and item.strip()]
        if wanted:
            # AND semantics: keep only candidates holding every listed skill.
            for item in wanted:
                stmt = stmt.where(
                    Candidate.id.in_(
                        select(CandidateSkill.candidate_id)
                        .join(Skill, Skill.id == CandidateSkill.skill_id)
                        .where(Skill.normalized_name == item)
                    )
                )

    total = int(
        (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one() or 0
    )
    rows = (
        (
            await session.execute(
                stmt.order_by(Candidate.created_at.desc())
                .offset(page.offset)
                .limit(page.limit)
            )
        )
        .scalars()
        .unique()
        .all()
    )
    return {
        "total": total,
        "page": page.page,
        "page_size": page.page_size,
        "pages": max(1, -(-total // page.page_size)),
        "results": [_to_summary(candidate).model_dump(mode="json") for candidate in rows],
    }


@router.post(
    "",
    response_model=CandidateRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a candidate profile manually",
)
async def create_candidate(
    payload: CandidateCreate, request: Request, session: SessionDep, user: RecruiterUser
) -> CandidateRead:
    """Add a candidate without a file.

    Exists because an upload is not always possible: the candidate applied
    elsewhere, or the parse failed and the recruiter is transcribing the fields
    by hand. Supplying ``raw_text`` runs it through the same parser as an upload
    so both routes produce comparable scores.
    """
    candidate = await resumes_service.create_candidate(session, payload, created_by=user.id)
    await audit_service.audit(
        session,
        action=audit_service.ACTION_RESUME_UPLOAD,
        user_id=user.id,
        entity_type="candidate",
        entity_id=candidate.id,
        detail={"source": "manual"},
        **_client(request),
    )
    await session.commit()
    return _to_read(candidate)


@router.get("/{candidate_id}", response_model=CandidateRead, summary="Full candidate profile")
async def get_candidate(
    candidate_id: int, request: Request, session: SessionDep, user: RecruiterUser
) -> CandidateRead:
    candidate = await _load(session, candidate_id)
    await audit_service.audit(
        session,
        action=audit_service.ACTION_RESUME_VIEW,
        user_id=user.id,
        entity_type="candidate",
        entity_id=candidate.id,
        **_client(request),
    )
    await session.commit()
    return _to_read(candidate)


@router.get(
    "/{candidate_id}/resume",
    summary="Download the original resume document",
    response_class=StreamingResponse,
)
async def download_resume(
    candidate_id: int, request: Request, session: SessionDep, user: RecruiterUser
) -> StreamingResponse:
    candidate = await _load(session, candidate_id)
    if not candidate.resume_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No stored document for this candidate."
        )
    try:
        content = read_stored_file(candidate.resume_path, kind="resumes")
    except StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    await audit_service.audit(
        session,
        action=audit_service.ACTION_RESUME_VIEW,
        user_id=user.id,
        entity_type="candidate",
        entity_id=candidate.id,
        detail={"download": True},
        **_client(request),
    )
    await session.commit()

    filename = candidate.original_filename or "resume"
    return StreamingResponse(
        iter([content]),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/{candidate_id}/sections",
    summary="The parsed sections of a resume",
)
async def candidate_sections(
    candidate_id: int, session: SessionDep, user: RecruiterUser
) -> dict[str, Any]:
    """Expose what the parser actually saw.

    Recruiters need to check the extraction against the original document, so
    the sections are returned verbatim rather than summarised.
    """
    candidate = await _load(session, candidate_id)
    return {
        "candidate_id": candidate.id,
        "sections": loads_dict(candidate.sections),
        "employment_history": loads_list(candidate.employment_history),
        "previous_positions": loads_list(candidate.previous_positions),
        "processing_error": candidate.processing_error,
    }


# --------------------------------------------------------------------------- #
# Update / delete
# --------------------------------------------------------------------------- #
@router.patch("/{candidate_id}", response_model=CandidateRead, summary="Edit candidate fields")
async def update_candidate(
    candidate_id: int,
    payload: CandidateUpdate,
    request: Request,
    session: SessionDep,
    user: RecruiterUser,
) -> CandidateRead:
    """Correct extracted fields.

    Manual corrections are a first-class feature, not a workaround: the parser
    will always be wrong sometimes, and the recruiter is the only person who
    can tell.
    """
    candidate = await _load(session, candidate_id)
    for field, value in payload.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(candidate, field, value)

    await audit_service.audit(
        session,
        action=audit_service.ACTION_CANDIDATE_UPDATE,
        user_id=user.id,
        entity_type="candidate",
        entity_id=candidate.id,
        detail={"fields": sorted(payload.model_dump(exclude_unset=True, exclude_none=True))},
        **_client(request),
    )
    await session.commit()
    return _to_read(candidate)


@router.post(
    "/{candidate_id}/shortlist",
    response_model=CandidateRead,
    summary="Add or remove a candidate from the shortlist",
)
async def set_shortlist(
    candidate_id: int,
    request: Request,
    session: SessionDep,
    user: RecruiterUser,
    shortlisted: bool = True,
) -> CandidateRead:
    """Shortlisting is an explicit human decision.

    The endpoint never sets it automatically and never infers it from a score,
    which is what keeps the tool decision-support rather than decision-making.
    """
    candidate = await _load(session, candidate_id)
    candidate.shortlisted = shortlisted
    await audit_service.audit(
        session,
        action=audit_service.ACTION_CANDIDATE_UPDATE,
        user_id=user.id,
        entity_type="candidate",
        entity_id=candidate.id,
        detail={"shortlisted": shortlisted},
        **_client(request),
    )
    await session.commit()
    return _to_read(candidate)


@router.delete(
    "/{candidate_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete a candidate and erase their personal data",
)
async def delete_candidate(
    candidate_id: int,
    request: Request,
    session: SessionDep,
    user: RecruiterUser,
    hard: bool = False,
) -> None:
    """Right-to-erasure (PRD section 26).

    Deletes the stored document immediately and blanks the personal fields. The
    row itself is kept only as an audit stub unless ``hard=true``.
    """
    candidate = await _load(session, candidate_id)
    await resumes_service.delete_candidate(session, candidate, hard=hard)
    await audit_service.audit(
        session,
        action=audit_service.ACTION_CANDIDATE_DELETE,
        user_id=user.id,
        entity_type="candidate",
        entity_id=candidate_id,
        detail={"hard": hard},
        **_client(request),
    )
    await session.commit()


async def _load(session: SessionDep, candidate_id: int) -> Candidate:
    try:
        return await resumes_service.get_candidate(session, candidate_id)
    except resumes_service.CandidateNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc