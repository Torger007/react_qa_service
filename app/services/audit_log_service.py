from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.core.config import settings
from app.core.redis_client import key

if TYPE_CHECKING:
    from app.repositories.audit_log_repository import PostgresAuditLogRepository


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class AuditLogEntry(BaseModel):
    event_type: str
    username: str | None = None
    actor_username: str | None = None
    outcome: str = "success"
    created_at: str = Field(default_factory=_utc_now)
    ip_address: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class AuditLogService:
    def __init__(
        self,
        redis: Redis,
        *,
        postgres_repo: PostgresAuditLogRepository | None = None,
        storage_backend: str = "redis",
        read_backend: str = "redis",
        dual_write_enabled: bool = False,
    ) -> None:
        self._redis = redis
        self._postgres_repo = postgres_repo
        self._storage_backend = storage_backend.strip().lower()
        self._read_backend = read_backend.strip().lower()
        self._dual_write_enabled = dual_write_enabled

    @staticmethod
    def _log_key() -> str:
        return key("auth", "audit", "logs")

    async def append(
        self,
        *,
        event_type: str,
        username: str | None = None,
        actor_username: str | None = None,
        outcome: str = "success",
        ip_address: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditLogEntry:
        entry = AuditLogEntry(
            event_type=event_type,
            username=username,
            actor_username=actor_username,
            outcome=outcome,
            ip_address=ip_address,
            details=details or {},
        )
        stored = await self._append_primary(entry)
        await self._mirror_append(stored)
        return stored

    async def _append_redis(self, entry: AuditLogEntry) -> AuditLogEntry:
        await self._redis.lpush(self._log_key(), entry.model_dump_json())
        await self._redis.ltrim(self._log_key(), 0, settings.audit_log_max_entries - 1)
        return entry

    async def _append_postgres(self, entry: AuditLogEntry) -> AuditLogEntry:
        if self._postgres_repo is None:
            return entry
        return await self._postgres_repo.append(entry)

    async def list_entries(
        self,
        *,
        limit: int | None = None,
        username: str | None = None,
    ) -> list[AuditLogEntry]:
        safe_limit = max(1, min(limit or settings.audit_log_default_limit, settings.audit_log_max_entries))
        if self._should_read_from_postgres():
            return await self._list_entries_postgres(limit=safe_limit, username=username)
        return await self._list_entries_redis(limit=safe_limit, username=username)

    async def _list_entries_redis(self, *, limit: int, username: str | None = None) -> list[AuditLogEntry]:
        raw_items = await self._redis.lrange(self._log_key(), 0, limit - 1)
        entries: list[AuditLogEntry] = []
        for raw in raw_items:
            try:
                entry = AuditLogEntry.model_validate_json(raw)
            except Exception:
                continue
            if username and entry.username != username and entry.actor_username != username:
                continue
            entries.append(entry)
        return entries

    async def _list_entries_postgres(self, *, limit: int, username: str | None = None) -> list[AuditLogEntry]:
        if self._postgres_repo is None:
            return []
        return await self._postgres_repo.list_entries(limit=limit, username=username)

    async def _append_primary(self, entry: AuditLogEntry) -> AuditLogEntry:
        if self._should_write_to_postgres():
            return await self._append_postgres(entry)
        return await self._append_redis(entry)

    async def _mirror_append(self, entry: AuditLogEntry) -> None:
        if not self._dual_write_enabled:
            return
        if self._should_write_to_postgres():
            await self._append_redis(entry)
            return
        if self._postgres_repo is not None:
            await self._append_postgres(entry)

    def _should_write_to_postgres(self) -> bool:
        return self._storage_backend == "postgres" and self._postgres_repo is not None

    def _should_read_from_postgres(self) -> bool:
        if self._read_backend == "postgres" and self._postgres_repo is not None:
            return True
        return self._storage_backend == "postgres" and self._postgres_repo is not None
