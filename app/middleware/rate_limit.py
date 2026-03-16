from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import settings
from app.core.redis_client import key


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Fixed-window rate limit: N requests per second.

    Uses Redis for distributed enforcement. If Redis is unavailable, fails open.
    """

    def __init__(self, app):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        redis = getattr(request.app.state, "redis", None)
        if redis is None:
            return await call_next(request)

        subject = (
            getattr(request.state, "subject", None) or request.client.host
            if request.client
            else "unknown"
        )
        now = int(time.time())
        bucket = key("rl", subject, str(now))

        try:
            n = await redis.incr(bucket)
            if n == 1:
                await redis.expire(bucket, 2)
        except Exception:
            return await call_next(request)

        if n > settings.rate_limit_rps:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded", "limit_rps": settings.rate_limit_rps},
                headers={"Retry-After": "1"},
            )
        return await call_next(request)
