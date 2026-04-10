from __future__ import annotations

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.security import decode_access_token


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Enforces JWT auth on API routes, except for explicitly public endpoints.
    """

    def __init__(self, app, public_paths: set[str] | None = None):
        super().__init__(app)
        self.public_paths = public_paths or set()

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        if any(path.startswith(p) for p in self.public_paths):
            return await call_next(request)

        auth = request.headers.get("authorization") or ""
        if not auth.lower().startswith("bearer "):
            return Response(status_code=401, content="Missing bearer token")
        token = auth.split(" ", 1)[1].strip()
        try:
            claims = decode_access_token(token)
        except HTTPException as e:
            return Response(status_code=e.status_code, content=str(e.detail))
        except Exception:
            return Response(status_code=401, content="Invalid token")

        request.state.subject = claims.subject
        request.state.role = claims.role
        return await call_next(request)
