from __future__ import annotations

import asyncio

from app.services.audit_log_service import AuditLogService
from tests.conftest import FakeRedis


class FakePostgresAuditLogRepository:
    def __init__(self) -> None:
        self._entries: list[object] = []

    async def append(self, entry):
        self._entries.insert(0, entry)
        return entry

    async def list_entries(self, *, limit: int, username: str | None = None):
        entries = self._entries
        if username:
            entries = [
                entry
                for entry in entries
                if entry.username == username or entry.actor_username == username
            ]
        return entries[:limit]


def test_audit_log_service_dual_writes_to_postgres_when_redis_is_primary():
    redis = FakeRedis()
    postgres_repo = FakePostgresAuditLogRepository()
    service = AuditLogService(
        redis,
        postgres_repo=postgres_repo,
        storage_backend="redis",
        read_backend="redis",
        dual_write_enabled=True,
    )

    asyncio.run(service.append(event_type="login_succeeded", username="alice", actor_username="alice"))

    redis_entries = asyncio.run(service.list_entries(limit=10))
    assert len(redis_entries) == 1
    assert len(postgres_repo._entries) == 1


def test_audit_log_service_can_read_from_postgres_backend():
    redis = FakeRedis()
    postgres_repo = FakePostgresAuditLogRepository()
    primary = AuditLogService(
        redis,
        postgres_repo=postgres_repo,
        storage_backend="redis",
        read_backend="redis",
        dual_write_enabled=True,
    )
    asyncio.run(primary.append(event_type="user_created", username="bob", actor_username="admin"))

    postgres_reader = AuditLogService(
        redis,
        postgres_repo=postgres_repo,
        storage_backend="redis",
        read_backend="postgres",
        dual_write_enabled=True,
    )

    entries = asyncio.run(postgres_reader.list_entries(limit=10, username="bob"))

    assert len(entries) == 1
    assert entries[0].event_type == "user_created"
