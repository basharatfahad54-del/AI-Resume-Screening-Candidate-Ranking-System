"""Job description endpoints (PRD section 6)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status

from app.api.deps import PaginationDep, RecruiterUser, SessionDep, client_ip, user_agent
from app.core.logging import get_logger
from app.models import Job
from app.schemas.job import JobCreate, JobExtractionResult, JobParseRequest, JobRead, JobUpdate
from app.services import audit as audit_service
from app.services import jobs as jobs_service
from app.services import matching as matching_service
from app.utils.storage import StorageError, delete_stored_file, ensure_storage_dirs, save_upload

logger = get_logger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])


def _client(request: Request) -> dict[str, str | None]:
    return {"ip_address": client_ip(request), "user_agent": request.headers.get("user-agent", "")[:300] or None}


@router.get("", summary="List jobs")
async def list_jobs(
    session: SessionDep,
    user: RecruiterUser,
    page: PaginationDep,
    search: str | None = None,
    is_active: bool | None = True,
) -> dict[str, Any]:
    """Paginated job list.

    Ordering is by ``created_at`` with ``id`` as the tie-breaker so two jobs
    created in the same second cannot swap places between pages.
    """
    jobs = await jobs_service.list_jobs(
        session,
        search=search,
        is_active=is_active,
        limit=page.page_size,
        offset=page.offset,
    )
    total = await jobs_service.count_jobs(session, search=search, is_active=is_active)
    return {
        "total": total,
        "page": page.page,
        "page_size": page.page_size,
        "pages": max(1, -(-total // page.page_size)),
        "results": [JobRead.model_validate(job).model_dump(mode="json") for job in jobs],
    }


@router.get("/{job_id}", response_model=JobRead, summary="Get one job")
async def get_job(
    job_id: int, request: Request, session: SessionDep, user: RecruiterUser
) -> JobRead:
    job = await _load(session, job_id)
    await audit_service.audit(
        session,
        action=audit_service.ACTION_JOB_VIEW,
        user_id=user.id,
        entity_type="job",
        entity_id=job.id,
        **_client(request),
    )
    await session.commit()
    return JobRead.model_validate(job)


@router.post("", response_model=JobRead, status_code=status.HTTP_201_CREATED, summary="Create a job")
async def create_job(
    payload: JobCreate, request: Request, session: SessionDep, user: RecruiterUser
) -> JobRead:
    job = await jobs_service.create_job(session, payload, created_by=user.id)
    await audit_service.audit(
        session,
        action=audit_service.ACTION_JOB_CREATE,
        user_id=user.id,
        entity_type="job",
        entity_id=job.id,
        detail={"title": job.title},
        **_client(request),
    )
    await session.commit()
    return JobRead.model_validate(job)


@router.patch("/{job_id}", response_model=JobRead, summary="Update a job")
async def update_job(
    job_id: int, payload: JobUpdate, request: Request, session: SessionDep, user: RecruiterUser
) -> JobRead:
    job = await _load(session, job_id)
    job = await jobs_service.update_job(session, job, payload)
    await audit_service.audit(
        session,
        action=audit_service.ACTION_JOB_UPDATE,
        user_id=user.id,
        entity_type="job",
        entity_id=job.id,
        detail={"fields": sorted(payload.model_dump(exclude_unset=True, exclude_none=True))},
        **_client(request),
    )
    await session.commit()
    return JobRead.model_validate(job)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None, summary="Delete a job")
async def delete_job(
    job_id: int, request: Request, session: SessionDep, user: RecruiterUser
) -> None:
    job = await _load(session, job_id)
    await jobs_service.delete_job(session, job)
    await audit_service.audit(
        session,
        action=audit_service.ACTION_JOB_DELETE,
        user_id=user.id,
        entity_type="job",
        entity_id=job_id,
        **_client(request),
    )
    await session.commit()


@router.post(
    "/parse",
    response_model=JobExtractionResult,
    summary="Extract requirements from pasted job description text",
)
async def parse_job_text(
    payload: JobParseRequest, request: Request, session: SessionDep, user: RecruiterUser
) -> JobExtractionResult:
    """Preview the parser, optionally persisting the result.

    A preview touches no rows at all - it is a pure function of the pasted text -
    so a recruiter can iterate on a description without filling the job table
    with drafts. The recruiter confirms or corrects the extraction before it
    becomes a scored requirement, because a silently mis-parsed requirement is a
    silent scoring error.
    """
    if payload.persist:
        job, extracted = await jobs_service.parse_and_create_job(
            session, payload.description, title=payload.title, created_by=user.id
        )
        await audit_service.audit(
            session,
            action=audit_service.ACTION_JOB_CREATE,
            user_id=user.id,
            entity_type="job",
            entity_id=job.id,
            detail={"source": "parse"},
            **_client(request),
        )
        await session.commit()
        return extracted.model_copy(update={"job_id": job.id})

    extracted = jobs_service.parse_job_description(payload.description).to_schema()
    return extracted.model_copy(update={"title": payload.title or extracted.title})


@router.post(
    "/upload",
    response_model=JobRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and parse a job description file",
)
async def upload_job(
    request: Request,
    session: SessionDep,
    user: RecruiterUser,
    file: UploadFile = File(..., description="PDF, DOCX or TXT job description"),
    title: str | None = Form(default=None),
) -> JobRead:
    ensure_storage_dirs()
    content = await file.read()
    try:
        stored = save_upload(content, file.filename or "job.txt", kind="jobs")
    except StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    from app.nlp.document import DocumentParseError, extract_text

    try:
        document = extract_text(content, stored.original_filename, kind="jobs")
    except DocumentParseError as exc:
        delete_stored_file(stored.relative_path, kind="jobs")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"The job description could not be read: {exc}",
        ) from exc

    job, _parsed = await jobs_service.parse_and_create_job(
        session, document.text, title=title, created_by=user.id, source_file=stored.relative_path
    )
    await audit_service.audit(
        session,
        action=audit_service.ACTION_JOB_CREATE,
        user_id=user.id,
        entity_type="job",
        entity_id=job.id,
        detail={"source": "upload", "filename": stored.original_filename},
        **_client(request),
    )
    await session.commit()
    return JobRead.model_validate(job)


@router.get("/{job_id}/candidates", summary="How many candidates were scored for a job")
async def job_candidate_count(
    job_id: int, session: SessionDep, user: RecruiterUser
) -> dict[str, int]:
    job = await _load(session, job_id)
    return {"job_id": job.id, "candidate_count": await jobs_service.candidate_count(session, job.id)}


@router.get("/{job_id}/requirements", summary="Structured requirements of a job")
async def job_requirements(job_id: int, session: SessionDep, user: RecruiterUser) -> dict[str, object]:
    job = await _load(session, job_id)
    profile = matching_service.job_profile(job)
    return {
        "job_id": job.id,
        "title": job.title,
        "required_skills": list(profile.required_skills),
        "preferred_skills": list(profile.preferred_skills),
        "certifications": list(profile.certifications),
        "experience_required_years": job.experience_required_years,
        "education_required": job.education_required,
        "scored_skill_rows": len(job.job_skills),
    }


async def _load(session: SessionDep, job_id: int) -> Job:
    try:
        return await jobs_service.get_job(session, job_id)
    except jobs_service.JobNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc