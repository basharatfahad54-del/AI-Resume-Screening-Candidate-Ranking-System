"""Liveness and readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import SessionDep
from app.core.config import settings
from app.core.logging import get_logger
from app.schemas.assistant import HealthResponse

logger = get_logger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Service health")
async def health(session: SessionDep) -> HealthResponse:
    """Unauthenticated so a load balancer can call it.

    Reports degraded rather than failing when a dependency is down: the database
    check distinguishes "process is up but cannot serve queries" from "process is
    gone", and a hard 500 here would make a restart loop look like a fix.
    """
    database = "ok"
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - health must never raise
        database = f"error: {type(exc).__name__}"
        logger.error("Health check database failure: %s", exc)

    return HealthResponse(
        status="ok" if database == "ok" else "degraded",
        version=settings.app_name,
        environment=settings.environment,
        database=database,
        embedding_provider=settings.embedding_provider,
        llm_provider=settings.llm_provider,
    )