"""Configurable scoring weight profiles (PRD section 11).

Recruiters and admins must be able to change how much each signal matters.
Weights are validated to sum to 1.0 on write and stored as JSON so new
components can be introduced without a schema migration.

Resolution order used by the matching service:

1. an explicit ``weights`` object in the request,
2. the ``weight_profile`` named in the request or stored on the job,
3. the profile flagged ``is_default``,
4. the environment defaults from :class:`app.core.config.Settings`.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models import ScoringWeight
from app.schemas.matching import ScoringWeights
from app.utils.jsonio import dumps, loads_dict

logger = get_logger(__name__)

__all__ = [
    "DEFAULT_PROFILE_NAME",
    "create_profile",
    "delete_profile",
    "default_weights",
    "ensure_default_profile",
    "list_profiles",
    "resolve_weights",
    "weights_for_profile",
]

DEFAULT_PROFILE_NAME = "Default"


def default_weights() -> ScoringWeights:
    """Weights from environment configuration."""
    return ScoringWeights(
        required_skills=settings.w_required_skills,
        experience=settings.w_experience,
        education=settings.w_education,
        preferred_skills=settings.w_preferred_skills,
        semantic=settings.w_semantic,
        certifications=settings.w_certifications,
    )


def _to_schema(row: ScoringWeight) -> ScoringWeights:
    """Read a stored payload, filling gaps from the defaults.

    A profile written before a component existed stays valid instead of
    failing validation on the sum.
    """
    payload = loads_dict(row.payload)
    base = default_weights().model_dump()
    merged = {key: payload.get(key, base[key]) for key in base if isinstance(payload.get(key, base[key]), (int, float))}
    total = sum(merged.values())
    if total <= 0:  # pragma: no cover - guarded by the validator on write
        return default_weights()
    if abs(total - 1.0) > 1e-3:
        merged = {key: value / total for key, value in merged.items()}
    return ScoringWeights(**merged)


def weights_for_profile(row: ScoringWeight | None) -> ScoringWeights:
    """Effective weights for a stored profile.

    Public so API handlers do not have to reach into :func:`_to_schema`. ``None``
    falls back to the environment defaults.
    """
    return _to_schema(row) if row is not None else default_weights()


async def list_profiles(session: AsyncSession) -> list[ScoringWeight]:
    stmt = select(ScoringWeight).order_by(ScoringWeight.is_default.desc(), ScoringWeight.name)
    return list((await session.execute(stmt)).scalars().all())


async def get_profile(session: AsyncSession, name: str) -> ScoringWeight | None:
    stmt = select(ScoringWeight).where(ScoringWeight.name == name)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_default_profile(session: AsyncSession) -> ScoringWeight | None:
    stmt = select(ScoringWeight).where(ScoringWeight.is_default.is_(True)).limit(1)
    return (await session.execute(stmt)).scalars().first()


async def ensure_default_profile(session: AsyncSession) -> ScoringWeight:
    """Idempotently create the default profile from environment settings."""
    existing = await get_profile(session, DEFAULT_PROFILE_NAME)
    if existing:
        return existing
    row = ScoringWeight(
        name=DEFAULT_PROFILE_NAME,
        description="Balanced default derived from the environment configuration.",
        payload=dumps(default_weights().model_dump()),
        is_default=True,
    )
    session.add(row)
    await session.flush()
    logger.info("Created default scoring weight profile")
    return row


async def create_profile(
    session: AsyncSession,
    *,
    name: str,
    weights: ScoringWeights,
    description: str | None = None,
    is_default: bool = False,
    created_by: int | None = None,
) -> ScoringWeight:
    if await get_profile(session, name):
        raise ValueError(f"A weight profile named '{name}' already exists")

    row = ScoringWeight(
        name=name,
        description=description,
        payload=dumps(weights.model_dump()),
        is_default=is_default,
        created_by=created_by,
    )
    session.add(row)
    await session.flush()
    if is_default:
        # Exactly one default at a time.
        await session.execute(
            update(ScoringWeight)
            .where(ScoringWeight.id != row.id, ScoringWeight.is_default.is_(True))
            .values(is_default=False)
        )
    logger.info("Created scoring weight profile %r (default=%s)", name, is_default)
    return row


async def delete_profile(session: AsyncSession, profile_id: int) -> bool:
    row = await session.get(ScoringWeight, profile_id)
    if row is None:
        return False
    if row.is_default:
        raise ValueError("The default weight profile cannot be deleted")
    await session.delete(row)
    await session.flush()
    return True


async def resolve_weights(
    session: AsyncSession,
    *,
    explicit: ScoringWeights | None = None,
    profile_name: str | None = None,
) -> ScoringWeights:
    """Resolve the effective weights for one analysis run."""
    if explicit is not None:
        return explicit
    if profile_name:
        row = await get_profile(session, profile_name)
        if row is None:
            raise ValueError(f"Unknown weight profile '{profile_name}'")
        return _to_schema(row)
    default_row = await get_default_profile(session)
    if default_row is not None:
        return _to_schema(default_row)
    return default_weights()
