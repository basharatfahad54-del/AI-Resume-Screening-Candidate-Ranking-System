"""Assistant, dashboard and health endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.api.deps import RecruiterUser, SessionDep, client_ip, user_agent
from app.core.logging import get_logger
from app.schemas.assistant import AssistantQuery, AssistantResponse, DashboardResponse
from app.services import assistant as assistant_service
from app.services import audit as audit_service
from app.services import dashboard as dashboard_service

logger = get_logger(__name__)
router = APIRouter(tags=["assistant"])


@router.post(
    "/assistant/query",
    response_model=AssistantResponse,
    summary="Ask a screening question",
)
async def query_assistant(
    payload: AssistantQuery, request: Request, session: SessionDep, user: RecruiterUser
) -> AssistantResponse:
    """Answer from the stored screening data.

    Read-only by construction: the assistant cannot shortlist, delete or rescore
    anyone, and its numbers come from the same stored matches the ranking page
    shows, so the two can never disagree.
    """
    try:
        response = await assistant_service.answer(
            session, payload, user_id=user.id
        )
    except assistant_service.AssistantError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    await audit_service.audit(
        session,
        action=audit_service.ACTION_ASSISTANT_QUERY,
        user_id=user.id,
        entity_type="conversation",
        entity_id=response.conversation_id,
        detail={"intent": response.intent, "mode": response.mode, "job_id": payload.job_id},
        ip_address=client_ip(request),
        user_agent=user_agent(request),
    )
    await session.commit()
    return response


@router.get("/assistant/status", summary="Which assistant capabilities are available")
async def assistant_capabilities(session: SessionDep, user: RecruiterUser) -> dict[str, object]:
    return assistant_service.assistant_status()


@router.get(
    "/dashboard",
    response_model=DashboardResponse,
    summary="Aggregated screening metrics",
)
async def dashboard(
    session: SessionDep,
    user: RecruiterUser,
    job_id: Annotated[int | None, Query(description="Narrow to one job")] = None,
    recent_limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> DashboardResponse:
    """Aggregates only - no candidate identities, no free-text PII.

    The panels are counts, averages and skill frequencies, which is enough to
    plan a shortlist without exposing who applied.
    """
    return await dashboard_service.dashboard_overview(
        session, job_id=job_id, recent_limit=recent_limit
    )