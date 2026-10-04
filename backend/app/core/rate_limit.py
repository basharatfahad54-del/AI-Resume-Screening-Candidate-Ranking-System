"""A small in-process rate limiter.

Deliberately not Redis: the deployment target is a single API container, and a
shared store would add an operational dependency for a limit that exists to blunt
credential stuffing on the login endpoint. If this ever runs multi-replica, swap
:class:`RateLimiter` for a Redis-backed implementation of the same two methods.

The limiter fails open. A limiter outage must not become an outage.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class RateLimiter:
    """Fixed-window counter with lazy eviction of idle keys."""

    def __init__(self, limit: int, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._last_sweep = time.monotonic()

    def check(self, key: str) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)``."""
        now = time.monotonic()
        with self._lock:
            self._maybe_sweep(now)
            bucket = self._hits[key]
            while bucket and now - bucket[0] >= self.window:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False, max(1, int(self.window - (now - bucket[0])) + 1)
            bucket.append(now)
            return True, 0

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)

    def _maybe_sweep(self, now: float) -> None:
        """Drop fully expired windows so memory tracks active clients only."""
        if now - self._last_sweep < self.window:
            return
        self._last_sweep = now
        for key in [k for k, bucket in self._hits.items() if not bucket or now - bucket[-1] >= self.window]:
            del self._hits[key]


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Applies the limiter to authentication routes only.

    Limiting every endpoint would penalise a recruiter paging through a ranking;
    the abuse vector that matters is repeated credential submission.
    """

    def __init__(self, app, *, limit: int | None = None, exempt_paths: tuple[str, ...] = ()) -> None:
        super().__init__(app)
        self.limit = limit or settings.rate_limit_per_minute
        self.limiter = RateLimiter(self.limit)
        self.exempt = exempt_paths

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not settings.rate_limit_enabled or request.method not in {"POST", "PUT", "PATCH"}:
            return await call_next(request)

        path = request.url.path
        if any(path.endswith(suffix) for suffix in self.exempt):
            return await call_next(request)

        client = request.client.host if request.client else "unknown"
        allowed, retry_after = self.limiter.check(f"{client}:{path}")
        if not allowed:
            logger.warning("Rate limit hit for %s on %s", client, path)
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "detail": "Too many requests. Please wait before trying again.",
                    "retry_after_seconds": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)


__all__ = ["RateLimiter", "RateLimitMiddleware"]