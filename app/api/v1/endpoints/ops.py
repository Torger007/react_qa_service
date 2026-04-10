from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.config import settings

router = APIRouter()


def _config_warnings() -> list[str]:
    warnings: list[str] = []
    if settings.jwt_secret == "change-me":
        warnings.append("jwt_secret is using the default placeholder value.")
    if settings.uses_default_demo_credentials():
        warnings.append("bootstrap credentials are still using the default admin/admin pair.")
    if not settings.openai_api_key:
        warnings.append("openai_api_key is empty.")
    return warnings


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.deployment_env,
    }


@router.get("/readiness", summary="Readiness probe")
async def readiness(request: Request) -> dict[str, object]:
    redis = getattr(request.app.state, "redis", None)
    redis_ok = False
    if redis is not None:
        try:
            redis_ok = bool(await redis.ping())
        except Exception:
            redis_ok = False

    warnings = _config_warnings()
    ready = redis_ok and bool(getattr(request.app.state, "vector_store", None))
    return {
        "status": "ready" if ready else "degraded",
        "environment": settings.deployment_env,
        "checks": {
            "redis": "ok" if redis_ok else "error",
            "vector_store": "ok" if getattr(request.app.state, "vector_store", None) else "missing",
        },
        "warnings": warnings,
    }
