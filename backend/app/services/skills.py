"""Skill catalogue: persists the canonical taxonomy into the database.

The taxonomy in :mod:`app.nlp.skill_taxonomy` is the source of truth. This
service mirrors it into the ``skills`` table so skills are referencable,
joinable and countable, without ever making the database the source of truth for
normalisation (which would let a bad manual edit break matching).
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Skill, SkillCategory
from app.nlp.skill_taxonomy import (
    SKILL_TAXONOMY,
    SkillCategoryLiteral,
    normalize_skill,
    normalize_text,
)
from app.utils.jsonio import dumps

logger = get_logger(__name__)

__all__ = [
    "count_skills",
    "get_or_create_skill",
    "get_skills_by_names",
    "list_skills",
    "sync_taxonomy",
]

_VALID_CATEGORIES = {item.value for item in SkillCategory}


def _category(value: SkillCategoryLiteral | str | None) -> SkillCategory:
    """Map a taxonomy category string onto the ORM enum, defaulting safely."""
    if value and value in _VALID_CATEGORIES:
        return SkillCategory(value)
    return SkillCategory.other


async def sync_taxonomy(session: AsyncSession) -> int:
    """Insert/refresh every taxonomy skill. Returns the number written.

    Safe to run on every startup: existing rows are updated in place so a
    taxonomy change propagates without wiping candidate attribution.
    """
    existing = {
        row.normalized_name: row for row in (await session.execute(select(Skill))).scalars().all()
    }
    written = 0
    for definition in SKILL_TAXONOMY:
        aliases = list(dict.fromkeys(definition.all_surface_forms))
        row = existing.get(definition.normalized_name)
        if row is None:
            row = Skill(normalized_name=definition.normalized_name)
            session.add(row)
        row.name = definition.name
        row.category = _category(definition.category)
        row.aliases = dumps(aliases)
        row.description = definition.description or None
        written += 1

    await session.flush()
    logger.info("Synchronised %s taxonomy skills into the catalogue", written)
    return written


async def get_or_create_skill(session: AsyncSession, raw_term: str) -> Skill:
    """Resolve a raw skill string to a catalogue row, creating it if needed.

    This is the only path that can introduce a skill outside the taxonomy,
    which is intentional: a job may legitimately require a tool the taxonomy
    has never seen, and silently dropping it would weaken every score.
    """
    normalized, category, display = normalize_skill(raw_term)
    stmt = select(Skill).where(Skill.normalized_name == normalized)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is not None:
        return row

    definition = next(
        (item for item in SKILL_TAXONOMY if item.normalized_name == normalized), None
    )
    row = Skill(
        name=(display or raw_term).strip()[:120],
        normalized_name=normalized,
        category=_category(definition.category if definition else category),
        aliases=dumps([raw_term.strip()]) if definition is None else dumps(list(definition.all_surface_forms)),
        description=definition.description if definition else None,
    )
    session.add(row)
    await session.flush()
    return row


async def get_skills_by_names(session: AsyncSession, names: list[str]) -> dict[str, Skill]:
    """Bulk-resolve raw skill strings to catalogue rows."""
    if not names:
        return {}
    wanted = {normalize_skill(name)[0] for name in names if name and name.strip()}
    if not wanted:
        return {}
    stmt = select(Skill).where(Skill.normalized_name.in_(wanted))
    found = {row.normalized_name: row for row in (await session.execute(stmt)).scalars().all()}
    for name in names:
        key = normalize_skill(name)[0]
        if key not in found:
            found[key] = await get_or_create_skill(session, name)
    return found


async def list_skills(
    session: AsyncSession,
    *,
    search: str | None = None,
    category: str | None = None,
    limit: int = 100,
) -> list[Skill]:
    stmt = select(Skill)
    if category:
        stmt = stmt.where(Skill.category == _category(category))
    if search:
        needle = f"%{normalize_text(search)}%"
        stmt = stmt.where(Skill.name.ilike(needle) | Skill.normalized_name.ilike(needle))
    stmt = stmt.order_by(Skill.name).limit(min(limit, 500))
    return list((await session.execute(stmt)).scalars().all())


async def count_skills(session: AsyncSession) -> int:
    return int((await session.execute(select(func.count(Skill.id)))).scalar_one() or 0)
