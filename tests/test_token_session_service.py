from __future__ import annotations

import asyncio

from app.core.security import decode_refresh_token
from app.services.token_session_service import TokenSessionService
from app.services.user_service import StoredUser
from tests.conftest import FakeRedis


class FakePostgresTokenSessionRepository:
    def __init__(self) -> None:
        self._sessions: dict[str, object] = {}

    async def create_session(self, record):
        self._sessions[record.jti] = record
        return record

    async def get_active_session(self, jti: str):
        record = self._sessions.get(jti)
        if record is None or record.revoked_at is not None:
            return None
        return record

    async def revoke_session(self, jti: str):
        record = self._sessions.get(jti)
        if record is None:
            return
        self._sessions[jti] = record.model_copy(update={"revoked_at": "2026-04-11T00:00:00+00:00"})

    async def revoke_all_for_username(self, username: str):
        for jti, record in list(self._sessions.items()):
            if record.username == username and record.revoked_at is None:
                self._sessions[jti] = record.model_copy(update={"revoked_at": "2026-04-11T00:00:00+00:00"})


def _stored_user(username: str = "alice") -> StoredUser:
    return StoredUser(
        username=username,
        password_hash="hashed",
        role="user",
        is_active=True,
        created_at="2026-04-11T00:00:00+00:00",
        updated_at="2026-04-11T00:00:00+00:00",
        token_version=2,
    )


def test_token_session_service_dual_writes_to_postgres_when_redis_is_primary():
    redis = FakeRedis()
    postgres_repo = FakePostgresTokenSessionRepository()
    service = TokenSessionService(
        redis,
        postgres_repo=postgres_repo,
        storage_backend="redis",
        read_backend="redis",
        dual_write_enabled=True,
    )

    token = asyncio.run(service.issue_refresh_token(_stored_user()))
    claims = decode_refresh_token(token)

    assert asyncio.run(service.validate_refresh_token(token)).subject == "alice"
    assert claims.jti in postgres_repo._sessions


def test_token_session_service_can_read_from_postgres_backend():
    redis = FakeRedis()
    postgres_repo = FakePostgresTokenSessionRepository()
    primary = TokenSessionService(
        redis,
        postgres_repo=postgres_repo,
        storage_backend="redis",
        read_backend="redis",
        dual_write_enabled=True,
    )
    token = asyncio.run(primary.issue_refresh_token(_stored_user("bob")))

    postgres_reader = TokenSessionService(
        redis,
        postgres_repo=postgres_repo,
        storage_backend="redis",
        read_backend="postgres",
        dual_write_enabled=True,
    )

    claims = asyncio.run(postgres_reader.validate_refresh_token(token))

    assert claims.subject == "bob"


def test_token_session_service_revoke_all_keeps_backends_in_sync():
    redis = FakeRedis()
    postgres_repo = FakePostgresTokenSessionRepository()
    service = TokenSessionService(
        redis,
        postgres_repo=postgres_repo,
        storage_backend="redis",
        read_backend="redis",
        dual_write_enabled=True,
    )
    token = asyncio.run(service.issue_refresh_token(_stored_user("carol")))

    asyncio.run(service.revoke_all_user_sessions("carol"))

    try:
        asyncio.run(service.validate_refresh_token(token))
    except ValueError as err:
        assert "no longer active" in str(err)
    else:  # pragma: no cover
        raise AssertionError("Expected revoked refresh token to be rejected")
