"""Job description management (PRD section 6)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value

from app.core.logging import get_logger
from app.ml.embeddings import embed_text, serialize_embedding
from app.models import Job, JobSkill
from app.nlp.job_parser import parse_job_description
from app.nlp.skill_taxonomy import normalize_skill
from app.schemas.job import JobCreate, JobExtractionResult, JobUpdate
from app.services import skills as skills_service
from app.utils.jsonio import dumps, loads_list

logger = get_logger(__name__)

__all__ = [
    "apply_job_skills",
    "create_job",
    "delete_job",
    "get_job",
    "job_embedding_text",
    "list_jobs",
    "parse_and_create_job",
    "refresh_job_embedding",
    "update_job",
]


class JobNotFound(Exception):
    """No job with that id (or it is soft-deleted)."""


def job_embedding_text(job: Job) -> str:
    """The text that represents a job for embedding purposes.

    Skills are repeated so a short description cannot dominate the vector, and
    the structured requirements - which is what matching actually keys on - are
    always included.
    """
    parts: list[str] = [job.title]
    if job.department:
        parts.append(job.department)
    parts.append(job.description or "")
    for label, values in (
        ("Required", loads_list(job.required_skills)),
        ("Preferred", loads_list(job.preferred_skills)),
        ("Certifications", loads_list(job.certifications)),
        ("Responsibilities", loads_list(job.responsibilities)),
        ("Soft skills", loads_list(job.soft_skills)),
    ):
        if values:
            parts.append(f"{label}: {', '.join(str(value) for value in values)}")
    if job.experience_required_years:
        parts.append(f"Requires {job.experience_required_years:g} years of experience.")
    if job.education_required:
        parts.append(f"Requires {job.education_required}.")
    return "\n".join(part for part in parts if part).strip()


async def refresh_job_embedding(session: AsyncSession, job: Job) -> None:
    """Recompute and persist the job's embedding."""
    try:
        vector = embed_text(job_embedding_text(job))
    except Exception as exc:  # noqa: BLE001 - a missing embedder must not block job creation
        logger.warning("Could not embed job %s: %s", job.id, exc)
        job.embedding = None
        return
    job.embedding = serialize_embedding(vector)


async def apply_job_skills(
    session: AsyncSession, job: Job, *, overwrite: bool = True
) -> list[JobSkill]:
    """Create the ``job_skills`` rows for a job's required/preferred lists.

    ``JobSkill`` gives per-requirement weight and minimum-years columns so a
    recruiter can later express "5 years of Python is required, Kubernetes is
    only a nice-to-have" without another migration.

    The rows are assigned through the collection rather than added
    individually. ``set_committed_value`` seeds the collection as already
    loaded: assigning to an unloaded collection makes SQLAlchemy read the
    previous value to compute orphans, and that implicit IO raises
    ``MissingGreenlet`` under asyncio.
    """
    # ``Job.required_skills`` is JSON-in-Text, so it must be decoded first:
    # iterating the raw string yields one skill row per character.
    required_names = [str(name) for name in loads_list(job.required_skills)]
    preferred_names = [str(name) for name in loads_list(job.preferred_skills)]

    catalogue = await skills_service.get_skills_by_names(
        session, required_names + preferred_names
    )
    required = {normalize_skill(name)[0] for name in required_names}

    rows = [
        JobSkill(
            skill=skill,
            importance="required" if skill.normalized_name in required else "preferred",
            weight=1.0,
        )
        for skill in catalogue.values()
    ]
    set_committed_value(job, "job_skills", [])
    if overwrite:
        job.job_skills = rows
    else:
        job.job_skills.extend(rows)
    await session.flush()
    return rows


async def create_job(
    session: AsyncSession,
    payload: JobCreate,
    *,
    created_by: int | None = None,
    source_file: str | None = None,
) -> Job:
    """Create a job from an explicit payload."""
    job = Job(
        title=payload.title.strip(),
        department=payload.department,
        location=payload.location,
        employment_type=payload.employment_type,
        description=payload.description or "",
        experience_required_years=payload.experience_required_years,
        education_required=payload.education_required,
        required_skills=dumps(payload.required_skills),
        preferred_skills=dumps(payload.preferred_skills),
        certifications=dumps(payload.certifications),
        responsibilities=dumps(payload.responsibilities),
        soft_skills=dumps(payload.soft_skills),
        source_file=source_file,
        created_by=created_by,
        is_active=True,
    )
    session.add(job)
    await session.flush()

    await refresh_job_embedding(session, job)
    await apply_job_skills(session, job)
    logger.info("Created job id=%s title=%r", job.id, job.title)
    return job


async def parse_and_create_job(
    session: AsyncSession,
    text: str,
    *,
    title: str | None = None,
    created_by: int | None = None,
    source_file: str | None = None,
) -> tuple[Job, JobExtractionResult]:
    """Parse a free-text job description and persist it.

    The parser's output is returned alongside the job so the UI can show what
    was detected and let the recruiter correct it - an auto-extracted
    requirement the recruiter cannot see and fix is a silent scoring error.
    """
    parsed = parse_job_description(text)
    resolved_title = (title or parsed.title or "").strip()
    if not resolved_title:
        resolved_title = "Untitled position"

    payload = JobCreate(
        title=resolved_title,
        description=parsed.raw_text,
        department=parsed.department,
        location=parsed.location,
        employment_type=parsed.employment_type,
        required_skills=parsed.required_skills,
        preferred_skills=parsed.preferred_skills,
        certifications=parsed.certifications,
        responsibilities=parsed.responsibilities,
        soft_skills=parsed.soft_skills,
        experience_required_years=parsed.experience_required_years,
        education_required=parsed.education_required,
    )
    job = await create_job(session, payload, created_by=created_by, source_file=source_file)
    return job, parsed.to_schema()


async def get_job(session: AsyncSession, job_id: int, *, include_skills: bool = True) -> Job:
    stmt = select(Job).where(Job.id == job_id)
    if include_skills:
        stmt = stmt.options(selectinload(Job.job_skills))
    job = (await session.execute(stmt)).scalar_one_or_none()
    if job is None:
        raise JobNotFound(f"Job {job_id} was not found")
    return job


async def list_jobs(
    session: AsyncSession,
    *,
    search: str | None = None,
    is_active: bool | None = True,
    created_by: int | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[Job]:
    """List jobs, newest first.

    ``limit`` is optional so internal callers can ask for everything; the API
    layer always passes a page window so a large account cannot return an
    unbounded response.
    """
    stmt = select(Job)
    if is_active is not None:
        stmt = stmt.where(Job.is_active.is_(is_active))
    if created_by is not None:
        stmt = stmt.where(Job.created_by == created_by)
    if search:
        needle = f"%{search.strip()}%"
        stmt = stmt.where(Job.title.ilike(needle) | Job.department.ilike(needle))
    stmt = stmt.order_by(Job.created_at.desc(), Job.id.desc())
    if limit is not None:
        stmt = stmt.offset(offset).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def count_jobs(
    session: AsyncSession,
    *,
    search: str | None = None,
    is_active: bool | None = True,
    created_by: int | None = None,
) -> int:
    """Count the rows :func:`list_jobs` would return, for pagination."""
    stmt = select(func.count(Job.id))
    if is_active is not None:
        stmt = stmt.where(Job.is_active.is_(is_active))
    if created_by is not None:
        stmt = stmt.where(Job.created_by == created_by)
    if search:
        needle = f"%{search.strip()}%"
        stmt = stmt.where(Job.title.ilike(needle) | Job.department.ilike(needle))
    return int((await session.execute(stmt)).scalar_one() or 0)


#: Job columns stored as JSON-in-Text.
_JSON_FIELDS = frozenset(
    {"required_skills", "preferred_skills", "certifications", "responsibilities", "soft_skills"}
)
#: Fields that change what a job *means*, and therefore require a new embedding.
_EMBEDDING_FIELDS = _JSON_FIELDS | {
    "title",
    "description",
    "experience_required_years",
    "education_required",
    "department",
    "location",
}


async def update_job(session: AsyncSession, job: Job, payload: JobUpdate) -> Job:
    """Apply a partial update.

    ``exclude_none`` is intentional: sending ``None`` means "leave unchanged",
    which is what a form that omits untouched fields produces.
    """
    data = payload.model_dump(exclude_unset=True, exclude_none=True)

    for field, value in data.items():
        setattr(job, field, dumps(value) if field in _JSON_FIELDS else value)

    if _JSON_FIELDS & set(data):
        await apply_job_skills(session, job)
    if _EMBEDDING_FIELDS & set(data):
        await refresh_job_embedding(session, job)

    await session.flush()
    logger.info("Updated job id=%s fields=%s", job.id, sorted(data))
    return job


async def delete_job(session: AsyncSession, job: Job) -> None:
    """Delete a job and, by cascade, its match results."""
    await session.delete(job)
    await session.flush()
    logger.info("Deleted job id=%s", job.id)


async def candidate_count(session: AsyncSession, job_id: int) -> int:
    from app.models import MatchResult

    stmt = select(func.count(MatchResult.id)).where(MatchResult.job_id == job_id)
    return int((await session.execute(stmt)).scalar_one() or 0)
