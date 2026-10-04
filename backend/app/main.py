"""Application entry point.

Wires configuration, middleware, error handling and the versioned API together.
Everything environment-specific is read from :mod:`app.core.config`, so the same
image runs in development and production without code changes.
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import api_router
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.core.rate_limit import RateLimitMiddleware
from app.db.session import AsyncSessionLocal, create_all, dispose_engine
from app.services import auth as auth_service
from app.services import skills as skills_service
from app.services import weights as weights_service
from app.utils.storage import ensure_storage_dirs

logger = get_logger(__name__)

DESCRIPTION = """
Decision support for resume screening.

* Every score is explainable: the evidence rows behind a number are returned
  with the number.
* Nothing is decided automatically. Shortlisting, rejection and hiring stay
  with a human reviewer.
* Demographic attributes are never inferred, stored or used in scoring.
"""

#: Values shipped in ``config.py``. Usable for local development, unsafe anywhere else.
_INSECURE_DEFAULTS = {
    "SECRET_KEY": "change-me-in-production",
    "ADMIN_PASSWORD": "ChangeMe123!",
}


def _warn_about_insecure_defaults() -> None:
    """Refuse to let a production deploy look safe while it is not.

    Checked at startup rather than at request time so the warning lands in the
    deploy log where an operator will actually see it.
    """
    if settings.environment == "development":
        return
    if settings.secret_key == _INSECURE_DEFAULTS["SECRET_KEY"]:
        logger.error(
            "SECRET_KEY is still the example value. Every issued token is forgeable "
            "until you set SECRET_KEY."
        )
    if settings.admin_password == _INSECURE_DEFAULTS["ADMIN_PASSWORD"]:
        logger.error(
            "ADMIN_PASSWORD is still the example value. Change it before the first "
            "administrator signs in."
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    ensure_storage_dirs()
    logger.info("Starting %s in %s mode", settings.app_name, settings.environment)
    _warn_about_insecure_defaults()

    if settings.environment == "development":
        # Alembic owns the schema everywhere else. Auto-create is a developer
        # convenience only, so a production deploy can never silently mutate
        # the shape of a live database.
        await create_all()

    # Reference data is seeded in every environment, not just development. These
    # are rows, not schema, and without them the application is unusable: there
    # are no canonical skills to match candidates against, no weights to rank
    # them with, and on a fresh database nobody can sign in at all - public
    # registration only ever creates recruiters, so an operator locked out of
    # /api/admin has no way back in.
    async with AsyncSessionLocal() as session:
        await skills_service.sync_taxonomy(session)
        await weights_service.ensure_default_profile(session)
        created_admin = await auth_service.bootstrap_admin(session)
        await session.commit()
    if created_admin:
        logger.warning(
            "Bootstrap admin %s created from configuration. Change this password "
            "immediately and set ADMIN_PASSWORD in the environment.",
            settings.admin_email,
        )
    logger.info("Reference data ready (taxonomy, default weights, admin account)")

    yield

    await dispose_engine()
    logger.info("Shutdown complete")


def docs_enabled(environment: str, override: bool | None = None) -> bool:
    """Whether to serve the interactive API documentation.

    Off in staging and production by default: the OpenAPI schema is a complete
    inventory of routes, parameters and error shapes, which is reconnaissance
    for no operational benefit once the client is written. ``override`` wins so a
    team that wants docs in production can ask for them explicitly.
    """
    if override is not None:
        return override
    return environment in {"development", "test"}


_DOCS_ENABLED = docs_enabled(settings.environment, settings.expose_api_docs)

app = FastAPI(
    title=settings.app_name,
    description=DESCRIPTION,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if _DOCS_ENABLED else None,
    redoc_url="/redoc" if _DOCS_ENABLED else None,
    openapi_url="/openapi.json" if _DOCS_ENABLED else None,
)
if not _DOCS_ENABLED:
    logger.info(
        "Interactive API docs are disabled (environment=%s, expose_api_docs=%s)",
        settings.environment,
        settings.expose_api_docs,
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-Request-ID"],
)
app.add_middleware(
    RateLimitMiddleware,
    exempt_paths=("/api/health", "/health", "/docs", "/openapi.json"),
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Apply conservative security headers to every response.

    nginx sets these too for the SPA, but the API is also run directly in
    development and behind other proxies, so it must not depend on the edge to
    apply them. HSTS is only meaningful once TLS terminates in front of the app,
    so it is limited to non-development environments.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if settings.environment != "development":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Attach a request id and log one line per request.

    The id is echoed in the ``X-Request-ID`` header so a user-reported failure
    can be traced to an exact log entry.
    """
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "Unhandled error on %s %s (request_id=%s)", request.method, request.url.path, request_id
        )
        raise
    duration_ms = int((time.perf_counter() - started) * 1000)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "%s %s -> %s in %sms (request_id=%s)",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        request_id,
    )
    return response


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Uniform error body.

    Every failure carries the request id so a screenshot from a user is enough
    to find the server-side trace.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "request_id": getattr(request.state, "request_id", None),
        },
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": "Request validation failed.",
            "errors": [
                {"loc": [str(part) for part in item.get("loc", [])], "msg": item.get("msg")}
                for item in exc.errors()
            ],
            "request_id": getattr(request.state, "request_id", None),
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception (request_id=%s)", getattr(request.state, "request_id", None))
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "An unexpected error occurred. The incident has been logged.",
            "request_id": getattr(request.state, "request_id", None),
        },
    )


app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "version": "1.0.0",
        "docs": "/docs",
        "api": settings.api_v1_prefix,
        "disclaimer": (
            "Screening decision support only. Scores do not make hiring decisions; "
            "a human reviewer decides."
        ),
    }