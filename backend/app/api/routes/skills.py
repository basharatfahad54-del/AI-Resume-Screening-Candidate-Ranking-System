"""Skill taxonomy endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import AdminUser, RecruiterUser, SessionDep
from app.models import SkillCategory
from app.schemas.candidate import SkillRead
from app.services import skills as skills_service

router = APIRouter(prefix="/skills", tags=["skills"])


@router.get("", response_model=list[SkillRead], summary="List taxonomy skills")
async def list_skills(
    session: SessionDep,
    user: RecruiterUser,
    search: str | None = None,
    category: Annotated[SkillCategory | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[SkillRead]:
    """The controlled vocabulary used for matching.

    Exposed so the UI can offer a searchable picker instead of free text; a
    requirement typed by hand that is not in the catalogue will never match.
    """
    skills = await skills_service.list_skills(
        session, search=search, category=category, limit=limit
    )
    return [SkillRead.model_validate(skill) for skill in skills]


@router.get("/count", summary="How many skills are in the catalogue")
async def skill_count(session: SessionDep, user: RecruiterUser) -> dict[str, int]:
    return {"count": await skills_service.count_skills(session)}


@router.post(
    "/sync",
    summary="Sync the taxonomy into the database",
)
async def sync_taxonomy(session: SessionDep, user: AdminUser) -> dict[str, int | str]:
    """Add new taxonomy entries and update existing ones.

    Admin-only and idempotent: it only ever adds or corrects catalogue rows, it
    never touches the skills a candidate has claimed.
    """
    count = await skills_service.sync_taxonomy(session)
    await session.commit()
    return {"synced": count, "total": await skills_service.count_skills(session)}