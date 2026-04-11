from __future__ import annotations

from typing import Any, cast
from uuid import UUID, uuid4

from redis.asyncio import Redis

from app.core.config import settings
from app.core.redis_client import key
from app.models.schemas import ChatMessage, SessionMetadata


def _history_key(session_id: UUID) -> str:
    return key("sess", str(session_id), "history")


def _meta_key(session_id: UUID) -> str:
    return key("sess", str(session_id), "meta")


def _user_sessions_key(subject: str) -> str:
    return key("sess", "user", subject, "all")


def _max_messages() -> int:
    # 10 rounds => 10 user+assistant pairs => 20 messages
    return max(2, settings.max_rounds * 2)


def new_session_id() -> UUID:
    return uuid4()


def _preview_text(content: str, limit: int = 120) -> str:
    normalized = " ".join(content.split()).strip()
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit].rstrip()}..."


async def get_session_metadata(r: Redis, session_id: UUID) -> SessionMetadata | None:
    raw = await r.get(_meta_key(session_id))
    if not raw:
        return None
    try:
        return SessionMetadata.model_validate_json(raw)
    except Exception:
        return None


async def ensure_session_owner(
    r: Redis,
    *,
    subject: str,
    session_id: UUID,
    title_seed: str | None = None,
) -> SessionMetadata:
    existing = await get_session_metadata(r, session_id)
    if existing is not None:
        if existing.owner_username != subject:
            raise PermissionError("Session does not belong to the current user")
        return existing

    now = ChatMessage(role="system", content="session bootstrap").ts
    metadata = SessionMetadata(
        session_id=session_id,
        owner_username=subject,
        title=_preview_text(title_seed or "新对话", limit=40),
        created_at=now,
        updated_at=now,
        last_message_preview="",
        message_count=0,
    )
    await _save_session_metadata(r, metadata)
    return metadata


async def append_message(
    r: Redis,
    session_id: UUID,
    msg: ChatMessage,
    *,
    subject: str | None = None,
    title_seed: str | None = None,
) -> None:
    k = _history_key(session_id)
    ra = cast(Any, r)
    await ra.lpush(k, msg.model_dump_json())
    await ra.ltrim(k, 0, _max_messages() - 1)
    await ra.expire(k, settings.session_ttl_seconds)
    if subject:
        existing = await ensure_session_owner(r, subject=subject, session_id=session_id, title_seed=title_seed)
        updated = existing.model_copy(
            update={
                "updated_at": msg.ts,
                "last_message_preview": _preview_text(msg.content),
                "message_count": existing.message_count + 1,
                "title": existing.title or _preview_text(title_seed or msg.content, limit=40),
            }
        )
        await _save_session_metadata(r, updated)


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


async def list_sessions(r: Redis, subject: str) -> list[SessionMetadata]:
    session_ids = sorted(await cast(Any, r).smembers(_user_sessions_key(subject)))
    sessions: list[SessionMetadata] = []
    for raw_session_id in session_ids:
        try:
            session_id = UUID(str(raw_session_id))
        except ValueError:
            continue
        metadata = await get_session_metadata(r, session_id)
        if metadata is None or metadata.owner_username != subject:
            continue
        sessions.append(metadata)
    sessions.sort(key=lambda item: item.updated_at, reverse=True)
    return sessions


async def delete_session(r: Redis, *, subject: str, session_id: UUID) -> None:
    metadata = await get_session_metadata(r, session_id)
    if metadata is None:
        raise LookupError("Session not found")
    if metadata.owner_username != subject:
        raise PermissionError("Session does not belong to the current user")
    await r.delete(_history_key(session_id))
    await r.delete(_meta_key(session_id))
    await cast(Any, r).srem(_user_sessions_key(subject), str(session_id))


async def _save_session_metadata(r: Redis, metadata: SessionMetadata) -> None:
    meta_key = _meta_key(metadata.session_id)
    user_index_key = _user_sessions_key(metadata.owner_username)
    ra = cast(Any, r)
    await r.set(meta_key, metadata.model_dump_json())
    await r.expire(meta_key, settings.session_ttl_seconds)
    await ra.sadd(user_index_key, str(metadata.session_id))
