"""Administrative endpoints: users, audit log, retention.

All routes require the ``admin`` role - these are the operations that can change
who has access, read what everyone did, or destroy data.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AdminUser, SessionDep, client_ip, user_agent
from app.core.config import settings
from app.core.logging import get_logger
from app.models import Candidate, Job, User, UserRole
from app.schemas.auth import UserRead, UserRegisterRequest
from app.services import audit as audit_service
from app.services import auth as auth_service
from app.services import resumes as resumes_service
from app.utils.storage import human_size, purge_expired_uploads

logger = get_logger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


def _client(request: Request) -> dict[str, str | None]:
    return {
        "ip_address": client_ip(request),
        "user_agent": user_agent(request),
    }


@router.get("/users", response_model=list[UserRead], summary="List users")
async def list_users(
    session: SessionDep,
    admin: AdminUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[UserRead]:
    total = int((await session.execute(select(func.count(User.id)))).scalar_one() or 0)
    users = (
        (
            await session.execute(
                select(User).order_by(User.created_at.desc()).offset(offset).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    _ = total
    return [UserRead.model_validate(user) for user in users]


@router.post(
    "/users",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a user",
)
async def create_user(
    payload: UserRegisterRequest, request: Request, session: SessionDep, admin: AdminUser
) -> UserRead:
    if await auth_service.get_user_by_email(session, payload.email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="That email is already registered."
        )
    user = await auth_service.create_user(
        session,
        name=payload.name,
        email=payload.email,
        password=payload.password,
        role=payload.role,
    )
    await audit_service.audit(
        session,
        action=audit_service.ACTION_REGISTER,
        user_id=admin.id,
        entity_type="user",
        entity_id=user.id,
        detail={"role": payload.role.value if hasattr(payload.role, "value") else payload.role},
        **_client(request),
    )
    await session.commit()
    return UserRead.model_validate(user)


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Deactivate a user and revoke their sessions",
)
async def deactivate_user(
    user_id: int, request: Request, session: SessionDep, admin: AdminUser
) -> None:
    """Deactivate rather than delete.

    Audit records reference the user, and destroying the row would leave gaps in
    the trail. Every issued session is revoked so the change takes effect
    immediately rather than at token expiry.
    """
    if user_id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate your own account.",
        )

    user = await auth_service.get_user(session, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such user.")

    user.is_active = False
    revoked = await auth_service.revoke_all_tokens(session, user.id)
    await audit_service.audit(
        session,
        action=audit_service.ACTION_USER_UPDATE,
        user_id=admin.id,
        entity_type="user",
        entity_id=user.id,
        detail={"deactivated": True, "tokens_revoked": revoked},
        **_client(request),
    )
    await session.commit()


@router.get("/audit-logs", summary="Read the audit trail")
async def audit_logs(
    session: SessionDep,
    admin: AdminUser,
    user_id: Annotated[int | None, Query(ge=1)] = None,
    action: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict[str, object]]:
    """Who did what, when, and from where.

    Emails are masked: the audit trail is read by administrators, and a log that
    leaks every candidate address is a second copy of the personal data the
    product exists to minimise.
    """
    rows = await audit_service.list_audit_logs(
        session, user_id=user_id, action=action, limit=limit
    )
    return [
        {
            "id": row.id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "user_id": row.user_id,
            "action": row.action,
            "entity_type": row.entity_type,
            "entity_id": row.entity_id,
            "detail": row.detail,
            "ip_address": row.ip_address,
        }
        for row in rows
    ]


@router.get("/storage", summary="Storage configuration and usage")
async def storage_info(session: SessionDep, admin: AdminUser) -> dict[str, object]:
    resumes_dir = settings.resumes_dir
    files = list(resumes_dir.rglob("*")) if resumes_dir.exists() else []
    documents = [item for item in files if item.is_file()]
    total_bytes = sum(item.stat().st_size for item in documents)
    return {
        "storage_dir": str(settings.storage_dir),
        "retention_days": settings.retention_days,
        "document_count": len(documents),
        "total_size": human_size(total_bytes),
        "max_upload_size_mb": settings.max_upload_size_mb,
    }


@router.post(
    "/retention/purge",
    summary="Delete documents past the retention window",
)
async def purge_storage(request: Request, session: SessionDep, admin: AdminUser) -> dict[str, object]:
    """Enforce the retention policy.

    Only orphaned files are removed - a file still referenced by a candidate
    record is never deleted by retention, because that would silently break a
    profile a recruiter is looking at.
    """
    referenced = await _referenced_upload_names(session)
    removed = purge_expired_uploads(settings.retention_days, protected_names=referenced)
    await audit_service.audit(
        session,
        action=audit_service.ACTION_PURGE,
        user_id=admin.id,
        entity_type="storage",
        detail={"removed": removed, "retention_days": settings.retention_days},
        **_client(request),
    )
    await session.commit()
    return {
        "removed": removed,
        "protected": len(referenced),
        "retention_days": settings.retention_days,
    }


async def _referenced_upload_names(session: AsyncSession) -> set[str]:
    """Stored file names still attached to a candidate or job row."""
    names: set[str] = set()
    for column in (Candidate.resume_path, Candidate.original_filename, Job.source_file):
        if column is None:
            continue
        values = await session.scalars(select(column).where(column.is_not(None)))
        names.update(value for value in values if value)
    return names


@router.post(
    "/candidates/{candidate_id}/purge",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Permanently erase a candidate",
)
async def purge_candidate(
    candidate_id: int, request: Request, session: SessionDep, admin: AdminUser
) -> None:
    """Hard deletion, for an erasure request that outranks the audit stub."""
    candidate = await session.get(Candidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such candidate.")

    await resumes_service.delete_candidate(session, candidate, hard=True)
    await audit_service.audit(
        session,
        action=audit_service.ACTION_CANDIDATE_DELETE,
        user_id=admin.id,
        entity_type="candidate",
        entity_id=candidate_id,
        detail={"hard": True},
        **_client(request),
    )
    await session.commit()


@router.get("/roles", summary="Roles the API understands")
async def roles(session: SessionDep, admin: AdminUser) -> dict[str, list[str]]:
    return {"roles": sorted(role.value for role in UserRole)}