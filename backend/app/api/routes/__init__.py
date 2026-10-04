"""Router aggregation.

Each module owns one resource; this file is the only place that knows the full
URL map, so adding an endpoint stays a local edit plus one ``include_router``.
"""

from fastapi import APIRouter

from app.api.routes import (
    admin,
    assistant,
    auth,
    candidates,
    health,
    jobs,
    matching,
    skills,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(jobs.router)
api_router.include_router(candidates.router)
api_router.include_router(matching.router)
api_router.include_router(skills.router)
api_router.include_router(assistant.router)
api_router.include_router(admin.router)

__all__ = ["api_router"]