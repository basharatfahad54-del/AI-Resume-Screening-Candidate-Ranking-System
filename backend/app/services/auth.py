"""Authentication, token lifecycle and bootstrap (PRD section 5.1)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    token_fingerprint,
    verify_password,
)
from app.models import RefreshToken, User, UserRole
from app.schemas.auth import TokenPair
from app.services import audit as audit_service

logger = get_logger(__name__)

__all__ = [
    "authenticate",
    "bootstrap_admin",
    "create_user",
    "issue_tokens",
    "logout",
    "refresh_access_token",
    "revoke_all_tokens",
]


class AuthError(Exception):
    """Authentication failed. Deliberately vague to the caller."""


def _expires_at(days: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=days)


def _is_expired(value: datetime) -> bool:
    """Timezone-safe expiry check.

    SQLite does not persist timezone information, so a value loaded from a
    SQLite database comes back naive. Comparing that directly against an
    aware ``now()`` raises ``TypeError``, so the value is normalised first.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value < datetime.now(timezone.utc)


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    stmt = select(User).where(User.email == email.strip().lower())
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def create_user(
    session: AsyncSession,
    *,
    name: str,
    email: str,
    password: str,
    role: UserRole = UserRole.recruiter,
) -> User:
    """Create a user. Raises ``ValueError`` when the email is already taken."""
    normalized_email = email.strip().lower()
    if await get_user_by_email(session, normalized_email):
        raise ValueError("An account with this email already exists")

    # The first account to exist becomes the administrator, so a fresh
    # deployment is never left without an owner.
    existing_users = int(
        (await session.execute(select(User.id).limit(1))).first() is not None
    )
    user = User(
        name=name.strip(),
        email=normalized_email,
        password_hash=hash_password(password),
        role=UserRole.admin if not existing_users else role,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    logger.info("Created user id=%s role=%s", user.id, user.role.value)
    return user


async def bootstrap_admin(session: AsyncSession) -> User | None:
    """Create the configured administrator if no user exists yet."""
    if (await session.execute(select(User.id).limit(1))).first() is not None:
        return None
    email = settings.admin_email.strip().lower()
    if await get_user_by_email(session, email):
        return None
    logger.info("No users found - bootstrapping administrator %s", email)
    admin = User(
        name="Administrator",
        email=email,
        password_hash=hash_password(settings.admin_password),
        role=UserRole.admin,
        is_active=True,
    )
    session.add(admin)
    await session.flush()
    return admin


def issue_tokens(user: User) -> TokenPair:
    """Create an access/refresh pair. The refresh token is *not* persisted here."""
    return TokenPair(
        access_token=create_access_token(user.id, role=user.role.value),
        refresh_token=create_refresh_token(user.id),
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,
    )


async def authenticate(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> tuple[User, TokenPair]:
    """Verify credentials and return the user with a fresh token pair."""
    user = await get_user_by_email(session, email)
    if user is None or not verify_password(password, user.password_hash):
        # Audit the attempt without revealing which half was wrong.
        await audit_service.audit(
            session,
            action=audit_service.ACTION_LOGIN_FAILED,
            entity_type="user",
            entity_id=email,
            detail={"reason": "invalid_credentials"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        raise AuthError("Incorrect email or password")

    if not user.is_active:
        await audit_service.audit(
            session,
            action=audit_service.ACTION_LOGIN_FAILED,
            user_id=user.id,
            entity_type="user",
            entity_id=user.id,
            detail={"reason": "inactive"},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        raise AuthError("This account has been deactivated")

    pair = issue_tokens(user)
    await _store_refresh_token(session, user.id, pair.refresh_token)
    await audit_service.audit(
        session,
        action=audit_service.ACTION_LOGIN,
        user_id=user.id,
        entity_type="user",
        entity_id=user.id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return user, pair


async def _store_refresh_token(session: AsyncSession, user_id: int, token: str) -> RefreshToken:
    """Persist a refresh token as a revocable, non-replayable identifier.

    Only the SHA-256 fingerprint is stored (see
    :func:`app.core.security.token_fingerprint`): the row proves a token was
    issued and lets us revoke it, but reading the table does not yield a usable
    session. The column is unique and indexed, so rotation stays a single-row
    lookup.
    """
    row = RefreshToken(
        user_id=user_id,
        token=token_fingerprint(token),
        expires_at=_expires_at(settings.refresh_token_expire_days),
        revoked=False,
    )
    session.add(row)
    await session.flush()
    return row


async def refresh_access_token(session: AsyncSession, token: str) -> tuple[User, TokenPair]:
    """Rotate a refresh token and mint a new pair."""
    try:
        payload = decode_token(token, "refresh")
    except jwt.PyJWTError as exc:
        raise AuthError("Refresh token is invalid or expired") from exc

    subject = payload.get("sub")
    try:
        user_id = int(subject)
    except (TypeError, ValueError) as exc:
        raise AuthError("Refresh token is invalid") from exc

    stored = (
        await session.execute(
            select(RefreshToken).where(
                RefreshToken.token == token_fingerprint(token),
                RefreshToken.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if stored is None or stored.revoked or _is_expired(stored.expires_at):
        raise AuthError("Refresh token has been revoked")

    user = await get_user(session, user_id)
    if user is None or not user.is_active:
        raise AuthError("Account is no longer active")

    # Rotate: the presented token is revoked and replaced.
    stored.revoked = True
    pair = issue_tokens(user)
    await _store_refresh_token(session, user.id, pair.refresh_token)
    await audit_service.audit(
        session,
        action=audit_service.ACTION_TOKEN_REFRESH,
        user_id=user.id,
        entity_type="user",
        entity_id=user.id,
    )
    return user, pair


async def logout(session: AsyncSession, user_id: int, token: str | None = None) -> bool:
    """Revoke one refresh token, or every token for the user."""
    if token:
        result = await session.execute(
            select(RefreshToken).where(
                RefreshToken.token == token_fingerprint(token),
                RefreshToken.user_id == user_id,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return False
        row.revoked = True
    else:
        await _revoke_user_tokens(session, user_id)
    await audit_service.audit(
        session,
        action=audit_service.ACTION_LOGOUT,
        user_id=user_id,
        entity_type="user",
        entity_id=user_id,
        detail={"scope": "single" if token else "all"},
    )
    await session.flush()
    return True


async def revoke_all_tokens(session: AsyncSession, user_id: int) -> int:
    return await _revoke_user_tokens(session, user_id)


async def _revoke_user_tokens(session: AsyncSession, user_id: int) -> int:
    rows = list(
        (
            await session.execute(
                select(RefreshToken).where(
                    RefreshToken.user_id == user_id, RefreshToken.revoked.is_(False)
                )
            )
        ).scalars().all()
    )
    for row in rows:
        row.revoked = True
    if rows:
        await session.flush()
    return len(rows)
