from __future__ import annotations

from typing import Any, cast
from uuid import UUID, uuid4

from redis.asyncio import Redis

from app.core.config import settings
from app.core.redis_client import key
from app.models.schemas import ChatMessage


def _history_key(session_id: UUID) -> str:
    return key("sess", str(session_id), "history")


def _max_messages() -> int:
    # 10 rounds => 10 user+assistant pairs => 20 messages
    return max(2, settings.max_rounds * 2)


def new_session_id() -> UUID:
    return uuid4()


async def append_message(r: Redis, session_id: UUID, msg: ChatMessage) -> None:
    k = _history_key(session_id)
    ra = cast(Any, r)
    await ra.lpush(k, msg.model_dump_json())
    await ra.ltrim(k, 0, _max_messages() - 1)
    await ra.expire(k, settings.session_ttl_seconds)


async def get_history(r: Redis, session_id: UUID) -> list[ChatMessage]:
    k = _history_key(session_id)
    items = await cast(Any, r).lrange(k, 0, _max_messages() - 1)
    out: list[ChatMessage] = []
    for raw in reversed(items):  # newest->oldest => chronological
        try:
            out.append(ChatMessage.model_validate_json(raw))
        except Exception:
            continue
    return out


async def clear_history(r: Redis, session_id: UUID) -> None:
    await r.delete(_history_key(session_id))
