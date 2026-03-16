from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class RequestValidationMiddleware(BaseHTTPMiddleware):
    """
    Lightweight request validation guardrails.

    - Enforces JSON on POST/PUT/PATCH for API routes.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH"}:
            ct = request.headers.get("content-type", "")
            normalized_ct = ct.lower()
            if "application/json" not in normalized_ct and "multipart/form-data" not in normalized_ct:
                return JSONResponse(
                    status_code=415,
                    content={"detail": "Content-Type must be application/json or multipart/form-data"},
                )
        return await call_next(request)
