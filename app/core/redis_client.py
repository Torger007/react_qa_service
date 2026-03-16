from __future__ import annotations

from typing import Any, Final, cast

from redis.asyncio import Redis

from app.core.config import settings


def _redis() -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)


async def create_redis() -> Redis:
    r = _redis()
    # `redis.asyncio` typing can be inconsistent across versions.
    await cast(Any, r).ping()
    return r


async def close_redis(r: Redis) -> None:
    await cast(Any, r).aclose()


def key(*parts: str) -> str:
    prefix: Final[str] = settings.redis_prefix
    return ":".join([prefix, *parts])


async def require_redis(r: Redis | None) -> Redis:
    if r is None:
        raise RuntimeError("Redis client not initialized")
    return r
