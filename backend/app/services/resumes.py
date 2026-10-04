"""Resume upload, parsing and candidate profile generation (PRD sections 7-9).

Pipeline: validate -> store securely -> extract text -> clean -> detect
sections -> extract fields -> normalise skills -> embed -> persist profile.

Design notes
------------
* The stored file is never served directly; it is read back through this
  service so access control applies.
* A parse failure marks the candidate ``failed`` and records the reason rather
  than aborting the whole batch - one unreadable resume must not lose the other
  49.
* Personal data is minimised: only professional and contact fields are kept,
  and no protected characteristic is inferred (see ``docs/responsible-ai.md``).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, inspect as sa_inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value

from app.core.logging import get_logger
from app.ml.embeddings import embed_text, serialize_embedding
from app.models import Candidate, CandidateSkill, ProcessingStatus
from app.nlp.document import DocumentParseError, ExtractedDocument, extract_text
from app.nlp.resume_parser import ParsedResume, parse_resume
from app.nlp.skill_extractor import ExtractedSkill, extract_skills
from app.services import skills as skills_service
from app.utils.jsonio import dumps, loads_dict, loads_list
from app.utils.storage import (
    StorageError,
    delete_stored_file,
    ensure_storage_dirs,
    save_upload,
)

logger = get_logger(__name__)

__all__ = [
    "CandidateNotFound",
    "ParseOutcome",
    "candidate_embedding_text",
    "create_candidate",
    "delete_candidate",
    "get_candidate",
    "process_upload",
    "refresh_candidate_embedding",
    "reparse_candidate",
]

_MAX_SKILLS_PER_CANDIDATE = 200


class CandidateNotFound(Exception):
    """No candidate with that id (or it was deleted)."""


@dataclass(slots=True)
class ParseOutcome:
    """Result of one resume parse, successful or not."""

    candidate: Candidate
    warnings: list[str]
    parsed: ParsedResume | None = None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


def candidate_embedding_text(candidate: Candidate) -> str:
    """Text used to embed a candidate.

    Structured sections are concatenated rather than the raw text: the parser
    has already separated the header (which is mostly contact noise) from the
    professional content, and embedding the header adds noise that every
    candidate shares - which destroys ranking power.
    """
    sections = loads_dict(candidate.sections)
    parts: list[str] = []
    for key in ("summary", "experience", "skills", "projects", "education", "certifications", "achievements"):
        value = sections.get(key)
        if value:
            parts.append(str(value))
    if not parts:
        return candidate.raw_text or ""

    parts.insert(0, candidate.current_title or "")
    return "\n".join(part for part in parts if part).strip()[:20000]


async def refresh_candidate_embedding(session: AsyncSession, candidate: Candidate) -> None:
    try:
        vector = embed_text(candidate_embedding_text(candidate))
    except Exception as exc:  # noqa: BLE001 - never block profile creation
        logger.warning("Could not embed candidate %s: %s", candidate.id, exc)
        candidate.embedding = None
        return
    candidate.embedding = serialize_embedding(vector)


# --------------------------------------------------------------------------- #
# Profile construction
# --------------------------------------------------------------------------- #
async def _attach_skills(
    session: AsyncSession, candidate: Candidate, parsed: ParsedResume
) -> None:
    """Replace the candidate's skills with the freshly extracted set."""
    catalogue = await skills_service.get_skills_by_names(
        session, [skill.normalized_name for skill in parsed.skills]
    )

    # Clear the old rows first, and in their own flush. ``candidate_skills`` is
    # unique on (candidate_id, skill_id), so re-running a reparse or replacing a
    # resume - where most skills are unchanged - would otherwise insert a
    # duplicate key that still has a pending DELETE queued in the same flush.
    # A Core delete is used rather than iterating ``candidate.skills``: that
    # attribute is lazily loaded, and reading it under asyncio raises
    # MissingGreenlet on a candidate never queried with selectinload.
    if sa_inspect(candidate).persistent:
        await session.execute(
            delete(CandidateSkill).where(CandidateSkill.candidate_id == candidate.id)
        )
        await session.flush()

    certification_terms = {name.strip().casefold() for name in parsed.certifications}
    by_normalized = {skill.normalized_name: skill for skill in parsed.skills}

    # Built only after the delete has been flushed: assigning ``skill=`` appends
    # to that Skill's collection, and a still-transient row seen by an autoflush
    # in between would be dropped with a "not in session" warning.
    rows: list[CandidateSkill] = []
    for normalized_name, skill_row in catalogue.items():
        extracted = by_normalized.get(normalized_name)
        if extracted is None:
            continue
        display_cased = extracted.display_name.casefold()
        rows.append(
            CandidateSkill(
                candidate_id=candidate.id,
                skill_id=skill_row.id,
                # Loaded eagerly so serialising the candidate never triggers a
                # lazy load (which would raise MissingGreenlet under asyncio).
                skill=skill_row,
                confidence=round(min(1.0, max(0.0, extracted.confidence)), 4),
                years_experience=extracted.years_experience,
                is_certified=bool(
                    extracted.is_certified
                    or any(term in display_cased or display_cased in term for term in certification_terms)
                ),
                evidence=dumps(list(extracted.evidence)[:3]),
            )
        )
        if len(rows) >= _MAX_SKILLS_PER_CANDIDATE:
            logger.warning(
                "Candidate %s hit the %s skill cap; the remainder was not stored",
                candidate.id,
                _MAX_SKILLS_PER_CANDIDATE,
            )
            break

    # ``set_committed_value`` marks the collection loaded before assigning;
    # otherwise SQLAlchemy reads the old value to compute orphans, and that
    # implicit IO raises MissingGreenlet under asyncio. The rows are added
    # explicitly first: built with bare foreign keys they are transient, and
    # assigning them to a collection would otherwise rely on an ambiguous
    # save-update cascade from two relationships at once.
    if rows:
        session.add_all(rows)
    set_committed_value(candidate, "skills", [])
    candidate.skills = rows
    await session.flush()

    # ``set_committed_value`` marks the collection loaded before assigning;
    # otherwise SQLAlchemy reads the old value to compute orphans, and that
    # implicit IO raises MissingGreenlet under asyncio. The rows are added
    # explicitly first: built with bare foreign keys they are transient, and
    # assigning them to a collection would otherwise rely on an ambiguous
    # save-update cascade from two relationships at once.
    if rows:
        session.add_all(rows)
    set_committed_value(candidate, "skills", [])
    candidate.skills = rows
    await session.flush()


def _apply_parsed(candidate: Candidate, parsed: ParsedResume) -> None:
    """Copy parsed fields onto the ORM row."""
    candidate.full_name = (parsed.full_name or candidate.full_name or "Unknown candidate")[:200]
    candidate.email = parsed.email or candidate.email
    candidate.phone = parsed.phone or candidate.phone
    candidate.location = parsed.location or candidate.location
    candidate.linkedin_url = parsed.linkedin_url or candidate.linkedin_url
    candidate.github_url = parsed.github_url or candidate.github_url
    candidate.portfolio_url = parsed.portfolio_url or candidate.portfolio_url
    candidate.current_title = parsed.current_title or candidate.current_title
    candidate.total_experience_years = parsed.total_experience_years
    candidate.relevant_experience_years = parsed.relevant_experience_years
    candidate.highest_degree = parsed.highest_degree or candidate.highest_degree
    candidate.degree_field = parsed.degree_field or candidate.degree_field
    candidate.institution = parsed.institution or candidate.institution
    candidate.graduation_year = parsed.graduation_year or candidate.graduation_year
    candidate.certifications = dumps(parsed.certifications)
    candidate.previous_positions = dumps(parsed.previous_positions)
    candidate.employment_history = dumps(parsed.employment_history)
    candidate.sections = dumps(parsed.section_map)
    candidate.raw_text = parsed.raw_text
    candidate.status = ProcessingStatus.parsed
    candidate.processing_error = None


# --------------------------------------------------------------------------- #
# Upload entry points
# --------------------------------------------------------------------------- #
async def process_upload(
    session: AsyncSession,
    *,
    content: bytes,
    filename: str,
    candidate_id: int | None = None,
) -> ParseOutcome:
    """Validate, store, parse and persist one resume.

    Omit ``candidate_id`` to ingest a new profile, or pass it to replace the
    document on an existing profile (the previous file is deleted).
    """
    ensure_storage_dirs()

    try:
        stored = save_upload(content, filename, kind="resumes")
    except StorageError as exc:
        logger.info("Rejected upload %r: %s", filename, exc)
        raise

    if candidate_id is not None:
        candidate = await get_candidate(session, candidate_id, include_skills=True)
        if candidate.resume_path:
            delete_stored_file(candidate.resume_path, kind="resumes")
    else:
        candidate = Candidate(
            full_name="Pending parse",
            status=ProcessingStatus.uploaded,
            resume_path=stored.relative_path,
            original_filename=stored.original_filename,
            is_deleted=False,
        )
        session.add(candidate)
        await session.flush()

    candidate.resume_path = stored.relative_path
    candidate.original_filename = stored.original_filename
    candidate.status = ProcessingStatus.parsing

    try:
        document = extract_text(content, filename)
        parsed = parse_resume(document)
    except DocumentParseError as exc:
        candidate.status = ProcessingStatus.failed
        candidate.processing_error = str(exc)[:2000]
        await session.flush()
        logger.warning("Parse failed for %r: %s", filename, exc)
        return ParseOutcome(candidate=candidate, warnings=[], error=str(exc))

    _apply_parsed(candidate, parsed)
    await _attach_skills(session, candidate, parsed)
    await refresh_candidate_embedding(session, candidate)
    await session.flush()

    logger.info(
        "Parsed candidate id=%s name=%r skills=%s experience=%s",
        candidate.id,
        candidate.full_name,
        len(candidate.skills),
        candidate.total_experience_years,
    )
    return ParseOutcome(candidate=candidate, warnings=parsed.warnings, parsed=parsed)


def _merge_skills(
    parsed: list[ExtractedSkill], declared: list[ExtractedSkill]
) -> list[ExtractedSkill]:
    """Union two skill sets, keeping the higher-confidence entry per skill."""
    merged: dict[str, ExtractedSkill] = {}
    for item in [*parsed, *declared]:
        existing = merged.get(item.normalized_name)
        if existing is None or item.confidence > existing.confidence:
            merged[item.normalized_name] = item
    return list(merged.values())


async def create_candidate(
    session: AsyncSession,
    payload: "CandidateCreate",
    *,
    created_by: int | None = None,
) -> Candidate:
    """Create a candidate profile without an uploaded document.

    Covers the two cases a file upload cannot: a recruiter transcribing a
    candidate who applied elsewhere, and repairing a profile whose parse failed.
    When ``raw_text`` is supplied it goes through the same parser as an upload,
    so a pasted resume is scored on exactly the same basis as an uploaded one.
    """
    candidate = Candidate(
        full_name=payload.full_name.strip(),
        email=payload.email,
        phone=payload.phone,
        location=payload.location,
        linkedin_url=payload.linkedin_url,
        github_url=payload.github_url,
        portfolio_url=payload.portfolio_url,
        current_title=payload.current_title,
        total_experience_years=payload.total_experience_years,
        relevant_experience_years=payload.total_experience_years,
        raw_text=payload.raw_text,
        status=ProcessingStatus.parsed,
        is_deleted=False,
    )
    session.add(candidate)
    await session.flush()

    if payload.raw_text:
        document = ExtractedDocument(
            text=payload.raw_text,
            filename="<pasted>",
            extension=".txt",
        )
        parsed = parse_resume(document)
        # An explicit value the recruiter typed beats the parser's guess.
        for field_name, value in (
            ("full_name", payload.full_name),
            ("email", payload.email),
            ("phone", payload.phone),
            ("location", payload.location),
            ("current_title", payload.current_title),
        ):
            if value:
                setattr(parsed, field_name, value)
        if payload.skills:
            parsed.skills = _merge_skills(parsed.skills, extract_skills(payload.skills))
    else:
        # No text to parse, so resolve the declared skills against the catalogue.
        # Anything unrecognised is skipped rather than invented: a fabricated
        # skill row would silently inflate a score.
        parsed = ParsedResume(
            full_name=candidate.full_name,
            email=payload.email,
            phone=payload.phone,
            location=payload.location,
            linkedin_url=payload.linkedin_url,
            github_url=payload.github_url,
            portfolio_url=payload.portfolio_url,
            current_title=payload.current_title,
            total_experience_years=payload.total_experience_years,
            relevant_experience_years=payload.total_experience_years,
            skills=extract_skills(payload.skills),
            raw_text=payload.raw_text or "",
        )

    _apply_parsed(candidate, parsed)
    await _attach_skills(session, candidate, parsed)
    await refresh_candidate_embedding(session, candidate)
    await session.flush()
    logger.info(
        "Created candidate id=%s manually (skills=%s, by_user=%s)",
        candidate.id,
        len(candidate.skills),
        created_by,
    )
    return candidate


async def reparse_candidate(session: AsyncSession, candidate: Candidate) -> ParseOutcome:
    """Re-run parsing from the stored document, e.g. after a parser upgrade."""
    from app.nlp.document import extract_stored

    if not candidate.resume_path:
        return ParseOutcome(
            candidate=candidate, warnings=[], error="No stored document for this candidate"
        )

    candidate.status = ProcessingStatus.parsing
    try:
        document = extract_stored(
            candidate.resume_path,
            filename=candidate.original_filename or "resume",
            kind="resumes",
        )
        parsed = parse_resume(document)
    except (StorageError, DocumentParseError) as exc:
        candidate.status = ProcessingStatus.failed
        candidate.processing_error = str(exc)[:2000]
        await session.flush()
        return ParseOutcome(candidate=candidate, warnings=[], error=str(exc))

    _apply_parsed(candidate, parsed)
    await _attach_skills(session, candidate, parsed)
    await refresh_candidate_embedding(session, candidate)
    await session.flush()
    return ParseOutcome(candidate=candidate, warnings=parsed.warnings, parsed=parsed)


# --------------------------------------------------------------------------- #
# Reads / deletes
# --------------------------------------------------------------------------- #
async def get_candidate(
    session: AsyncSession, candidate_id: int, *, include_skills: bool = True
) -> Candidate:
    stmt = select(Candidate).where(Candidate.id == candidate_id, Candidate.is_deleted.is_(False))
    if include_skills:
        stmt = stmt.options(
            selectinload(Candidate.skills).selectinload(CandidateSkill.skill)
        )
    candidate = (await session.execute(stmt)).scalar_one_or_none()
    if candidate is None:
        raise CandidateNotFound(f"Candidate {candidate_id} was not found")
    return candidate


async def delete_candidate(session: AsyncSession, candidate: Candidate, *, hard: bool = False) -> None:
    """Remove a candidate and their personal data (PRD section 26).

    The default is a soft delete so an accidental removal is recoverable, but
    the stored document is deleted immediately either way - the file is the
    sensitive part.
    """
    delete_stored_file(candidate.resume_path, kind="resumes")
    if hard:
        await session.delete(candidate)
        logger.info("Hard-deleted candidate id=%s", candidate.id)
        return

    candidate.is_deleted = True
    candidate.status = ProcessingStatus.failed
    candidate.processing_error = "Deleted at the candidate's or administrator's request"
    # Blank the personal fields so the record cannot be re-identified even in
    # an "anonymised" analytics export.
    candidate.full_name = "Deleted candidate"
    candidate.email = None
    candidate.phone = None
    candidate.linkedin_url = None
    candidate.github_url = None
    candidate.portfolio_url = None
    candidate.location = None
    candidate.raw_text = ""
    candidate.sections = "{}"
    candidate.embedding = None
    # Cleared through the collection so the delete-orphan cascade removes the
    # rows; a core DELETE would leave this in-memory list stale.
    if candidate.skills:
        candidate.skills.clear()
    await session.flush()
    logger.info("Soft-deleted candidate id=%s", candidate.id)


def candidate_skills_payload(candidate: Candidate) -> list[str]:
    return [link.skill.name for link in candidate.skills if link.skill is not None]


def candidate_certifications(candidate: Candidate) -> list[str]:
    return [str(item) for item in loads_list(candidate.certifications)]
