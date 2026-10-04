"""Audit logging (PRD sections 24 and 25).

Every operation that reads or mutates candidate data is recorded: who did it,
when, from where, and which entity was affected. This is what makes the system
defensible in a hiring dispute - it can show that a recruiter opened a specific
resume, and that no ranking was ever produced automatically.

Audit writes deliberately never fail the request they describe: compliance
telemetry must not be able to take the product down. Failures are logged at
error level instead.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import AuditLog
from app.utils.jsonio import dumps
from app.utils.text import mask_email

logger = get_logger(__name__)

__all__ = ["audit", "list_audit_logs"]

# Actions used across the API. Centralised so the audit trail stays greppable
# and reviewers can enumerate every privileged operation from one file.
ACTION_LOGIN = "auth.login"
ACTION_LOGIN_FAILED = "auth.login_failed"
ACTION_LOGOUT = "auth.logout"
ACTION_REGISTER = "auth.register"
ACTION_TOKEN_REFRESH = "auth.token_refresh"
ACTION_USER_UPDATE = "user.update"
ACTION_USER_DELETE = "user.delete"
ACTION_JOB_CREATE = "job.create"
ACTION_JOB_UPDATE = "job.update"
ACTION_JOB_DELETE = "job.delete"
ACTION_JOB_VIEW = "job.view"
ACTION_RESUME_UPLOAD = "resume.upload"
ACTION_RESUME_DELETE = "resume.delete"
ACTION_RESUME_VIEW = "resume.view"
ACTION_CANDIDATE_UPDATE = "candidate.update"
ACTION_CANDIDATE_DELETE = "candidate.delete"
ACTION_CANDIDATE_EXPORT = "candidate.export"
ACTION_MATCH_ANALYZE = "match.analyze"
ACTION_MATCH_RANKING_VIEW = "match.ranking_view"
ACTION_ASSISTANT_QUERY = "assistant.query"
ACTION_WEIGHTS_UPDATE = "weights.update"
ACTION_PURGE = "admin.purge"


async def audit(
    session: AsyncSession,
    *,
    action: str,
    user_id: int | None = None,
    entity_type: str | None = None,
    entity_id: int | str | None = None,
    detail: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Record one audit entry. Never raises.

    Written inside a SAVEPOINT so that a failure here rolls back only the audit
    row. A session-wide rollback would discard whatever business transaction the
    caller was in the middle of, which is a far worse outcome than a missing log
    line.
    """
    safe_detail = _sanitize(detail or {})
    entry = AuditLog(
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        detail=dumps(safe_detail),
        ip_address=(ip_address or "")[:64] or None,
        user_agent=(user_agent or "")[:300] or None,
    )
    try:
        async with session.begin_nested():
            session.add(entry)
            await session.flush()
    except Exception:  # noqa: BLE001 - telemetry must not break the request
        logger.exception("Failed to write audit log action=%s", action)


def _sanitize(detail: dict[str, Any]) -> dict[str, Any]:
    """Mask obvious secrets/PII before an audit row is persisted."""
    clean: dict[str, Any] = {}
    for key, value in detail.items():
        lowered = key.lower()
        if lowered in {"password", "token", "secret", "api_key", "access_token", "refresh_token"}:
            clean[key] = "[redacted]"
        elif lowered in {"email", "candidate_email"} and isinstance(value, str):
            clean[key] = mask_email(value)
        elif isinstance(value, dict):
            clean[key] = _sanitize(value)
        else:
            clean[key] = value
    return clean


async def list_audit_logs(
    session: AsyncSession,
    *,
    user_id: int | None = None,
    action: str | None = None,
    limit: int = 100,
) -> list[AuditLog]:
    """Recent audit entries, newest first. Used by the admin endpoint."""
    from sqlalchemy import select

    stmt = select(AuditLog)
    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    stmt = stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(min(limit, 500))
    return list((await session.execute(stmt)).scalars().all())
