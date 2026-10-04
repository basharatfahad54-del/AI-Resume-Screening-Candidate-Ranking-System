"""Shared FastAPI dependencies: session, authentication, roles, pagination.

Routes stay thin because everything that needs a decision lives here: an
unauthenticated request, a wrong role or an out-of-range page must be rejected
in one place rather than in twenty handlers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Callable

from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import decode_token
from app.db.session import get_db
from app.models import User, UserRole
from app.services import auth as auth_service
from app.utils.storage import ensure_storage_dirs

logger = get_logger(__name__)

# ``auto_error=False`` so a missing header produces our JSON 401 rather than
# FastAPI's default body, keeping error responses uniform.
_bearer = HTTPBearer(auto_error=False, description="JWT access token")

SessionDep = Annotated[AsyncSession, Depends(get_db)]


@dataclass(frozen=True, slots=True)
class Pagination:
    """Validated ``?page=&page_size=`` pair plus the derived SQL window."""

    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


def pagination(
    page: Annotated[int, Query(ge=1, description="1-based page number")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 20,
) -> Pagination:
    return Pagination(page=page, page_size=page_size)


PaginationDep = Annotated[Pagination, Depends(pagination)]


def client_ip(request: Request) -> str | None:
    """Best-effort client address for audit records.

    ``X-Forwarded-For`` is only consulted when the app is behind a proxy it
    controls; blindly trusting it would let a caller forge the audit trail.
    """
    if request.client is not None:
        return request.client.host
    return request.headers.get("x-forwarded-for", "").split(",")[0].strip() or None


def user_agent(request: Request) -> str | None:
    return (request.headers.get("user-agent") or "")[:300] or None


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #
async def get_current_user(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> User:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except PyJWTError as exc:
        # Expired, tampered or wrong-type token: all are simply "not valid",
        # and the reason is not echoed back to the caller.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token is invalid or has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed token."
        )

    user = await session.get(User, int(user_id))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is not active."
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: UserRole) -> Callable[..., User]:
    """Dependency factory restricting an endpoint to the given roles."""

    allowed = set(roles)

    async def _dependency(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"This action requires one of: "
                    f"{', '.join(sorted(role.value for role in allowed))}."
                ),
            )
        return user

    return _dependency


#: Admins only - user management, audit logs, taxonomy, weight profiles.
AdminUser = Annotated[User, Depends(require_roles(UserRole.admin))]
#: Anyone who may work with candidate data. A hiring manager sits in the same
#: tier as a recruiter: they can run and review matches for their roles, but
#: changing shared configuration (weights, users, retention) stays with an admin.
RecruiterUser = Annotated[
    User,
    Depends(require_roles(UserRole.admin, UserRole.recruiter, UserRole.hiring_manager)),
]


# --------------------------------------------------------------------------- #
# Startup helpers
# --------------------------------------------------------------------------- #
async def prepare_storage() -> None:
    """Create the upload directories.

    Failures are logged rather than raised: the app must still start to report
    a clear health status when storage is misconfigured.
    """
    try:
        ensure_storage_dirs()
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not prepare storage directories: %s", exc)


__all__ = [
    "AdminUser",
    "CurrentUser",
    "Pagination",
    "PaginationDep",
    "RecruiterUser",
    "SessionDep",
    "client_ip",
    "get_current_user",
    "pagination",
    "prepare_storage",
    "require_roles",
    "settings",
    "user_agent",
]