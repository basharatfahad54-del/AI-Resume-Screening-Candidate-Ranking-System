"""Password hashing and JWT token utilities."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import bcrypt
import jwt

from app.core.config import settings

TokenType = Literal["access", "refresh"]


def token_fingerprint(token: str) -> str:
    """Return the value stored in the database for a refresh token.

    A SHA-256 digest, not the token. A database dump, a slow query log or a
    backup restored onto a laptop must not hand out working sessions: the digest
    proves a presented token is the one that was issued without storing anything
    replayable. The lookup stays O(1) because the digest is the indexed value.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    """Hash with bcrypt and return a ``str`` for the database column.

    bcrypt truncates silently past 72 bytes, so a longer password is rejected
    outright rather than accepted with a misleading prefix-only hash.
    """
    encoded = password.encode("utf-8")
    if len(encoded) > 72:
        raise ValueError("Password must be at most 72 bytes")
    salt = bcrypt.gensalt(rounds=settings.bcrypt_rounds)
    return bcrypt.hashpw(encoded, salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time comparison.

    ``bcrypt.checkpw`` requires ``bytes`` for both arguments; the stored hash
    comes back from the database as ``str``, so it must be encoded before the
    call. A malformed or legacy hash returns False rather than raising.
    """
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _create_token(subject: str, token_type: TokenType, expires_delta: timedelta, **claims: Any) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": token_type,
        "iat": now,
        "nbf": now,
        "exp": now + expires_delta,
        "jti": uuid.uuid4().hex,
        **claims,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(subject: str | int, role: str = "", expires_minutes: int | None = None) -> str:
    minutes = expires_minutes if expires_minutes is not None else settings.access_token_expire_minutes
    return _create_token(
        str(subject),
        "access",
        timedelta(minutes=minutes),
        role=role,
    )


def create_refresh_token(subject: str | int, expires_days: int | None = None) -> str:
    days = expires_days if expires_days is not None else settings.refresh_token_expire_days
    return _create_token(str(subject), "refresh", timedelta(days=days))


def decode_token(token: str, expected_type: TokenType | None = None) -> dict[str, Any]:
    """Decode and validate a JWT. Raises jwt.PyJWTError on any problem."""
    payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    if expected_type and payload.get("type") != expected_type:
        raise jwt.InvalidTokenError(f"Expected {expected_type} token, got {payload.get('type')}")
    return payload
