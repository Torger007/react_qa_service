from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import HTTPException
from jose import JWTError
from pydantic import BaseModel
from redis.asyncio import Redis

from app.core.redis_client import key
from app.core.security import AccessTokenClaims, create_refresh_token, decode_refresh_token
from app.services.user_service import StoredUser

if TYPE_CHECKING:
    from app.repositories.token_session_repository import PostgresTokenSessionRepository


def _utc_timestamp() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp())


class RefreshSessionRecord(BaseModel):
    username: str
    role: str = "user"
    jti: str
    token_version: int
    issued_at: int
    exp: int
    revoked_at: str | None = None


class TokenSessionService:
    def __init__(
        self,
        redis: Redis,
        *,
        postgres_repo: PostgresTokenSessionRepository | None = None,
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
    def _refresh_key(jti: str) -> str:
        return key("auth", "refresh", jti)

    @staticmethod
    def _user_refresh_index(username: str) -> str:
        return key("auth", "refresh", "user", username)

    @staticmethod
    def _revoked_access_key(jti: str) -> str:
        return key("auth", "revoked", "access", jti)

    async def issue_refresh_token(self, user: StoredUser) -> str:
        token = create_refresh_token(user.username, user.role, token_version=user.token_version)
        claims = decode_refresh_token(token)
        record = RefreshSessionRecord(
            username=user.username,
            role=user.role,
            jti=claims.jti,
            token_version=user.token_version,
            issued_at=_utc_timestamp(),
            exp=claims.exp,
        )
        await self._save_refresh_primary(record)
        await self._mirror_save(record, is_new=True)
        return token

    async def revoke_refresh_token(self, token: str) -> None:
        claims = decode_refresh_token(token)
        await self._revoke_refresh_primary(claims.subject, claims.jti)
        await self._mirror_revoke(claims.subject, claims.jti)

    async def validate_refresh_token(self, token: str) -> AccessTokenClaims:
        try:
            claims = decode_refresh_token(token)
        except (JWTError, HTTPException) as err:
            raise ValueError("Invalid refresh token") from err

        record = await self._get_refresh_active_record(claims.jti)
        if record is None:
            raise ValueError("Refresh token is no longer active")
        if record.username != claims.subject:
            raise ValueError("Refresh token subject mismatch")
        if int(record.token_version) != claims.token_version:
            raise ValueError("Refresh token has been invalidated")
        return claims

    async def rotate_refresh_token(self, token: str, user: StoredUser) -> str:
        await self.revoke_refresh_token(token)
        return await self.issue_refresh_token(user)

    async def revoke_access_token(self, claims: AccessTokenClaims) -> None:
        ttl = max(1, claims.exp - _utc_timestamp())
        await self._redis.set(self._revoked_access_key(claims.jti), "1", ex=ttl)

    async def is_access_token_revoked(self, jti: str) -> bool:
        return await self._redis.get(self._revoked_access_key(jti)) is not None

    async def revoke_all_user_sessions(self, username: str) -> None:
        await self._revoke_all_primary(username)
        await self._mirror_revoke_all(username)

    async def _save_refresh_redis(self, record: RefreshSessionRecord) -> RefreshSessionRecord:
        ttl = max(1, record.exp - _utc_timestamp())
        payload = {
            "username": record.username,
            "role": record.role,
            "token_version": record.token_version,
            "exp": record.exp,
            "issued_at": record.issued_at,
            "revoked_at": record.revoked_at,
        }
        await self._redis.set(self._refresh_key(record.jti), json.dumps(payload), ex=ttl)
        await self._redis.sadd(self._user_refresh_index(record.username), record.jti)
        return record

    async def _get_refresh_redis(self, jti: str) -> RefreshSessionRecord | None:
        raw = await self._redis.get(self._refresh_key(jti))
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return RefreshSessionRecord(
            username=str(payload.get("username", "")),
            role=str(payload.get("role", "user")),
            jti=jti,
            token_version=int(payload.get("token_version", 0)),
            issued_at=int(payload.get("issued_at", payload.get("exp", 0))),
            exp=int(payload.get("exp", 0)),
            revoked_at=payload.get("revoked_at"),
        )

    async def _revoke_refresh_redis(self, username: str, jti: str) -> None:
        await self._redis.delete(self._refresh_key(jti))
        await self._redis.srem(self._user_refresh_index(username), jti)

    async def _revoke_all_redis(self, username: str) -> None:
        refresh_ids = await self._redis.smembers(self._user_refresh_index(username))
        for jti in refresh_ids:
            await self._redis.delete(self._refresh_key(str(jti)))
        await self._redis.delete(self._user_refresh_index(username))

    async def _save_refresh_postgres(self, record: RefreshSessionRecord, *, is_new: bool) -> RefreshSessionRecord:
        if self._postgres_repo is None:
            return record
        if is_new:
            return await self._postgres_repo.create_session(record)
        await self._postgres_repo.revoke_session(record.jti)
        return record

    async def _get_refresh_postgres(self, jti: str) -> RefreshSessionRecord | None:
        if self._postgres_repo is None:
            return None
        return await self._postgres_repo.get_active_session(jti)

    async def _revoke_refresh_postgres(self, jti: str) -> None:
        if self._postgres_repo is None:
            return
        await self._postgres_repo.revoke_session(jti)

    async def _revoke_all_postgres(self, username: str) -> None:
        if self._postgres_repo is None:
            return
        await self._postgres_repo.revoke_all_for_username(username)

    async def _save_refresh_primary(self, record: RefreshSessionRecord) -> RefreshSessionRecord:
        if self._should_write_to_postgres():
            return await self._save_refresh_postgres(record, is_new=True)
        return await self._save_refresh_redis(record)

    async def _get_refresh_active_record(self, jti: str) -> RefreshSessionRecord | None:
        if self._should_read_from_postgres():
            return await self._get_refresh_postgres(jti)
        return await self._get_refresh_redis(jti)

    async def _revoke_refresh_primary(self, username: str, jti: str) -> None:
        if self._should_write_to_postgres():
            await self._revoke_refresh_postgres(jti)
            return
        await self._revoke_refresh_redis(username, jti)

    async def _revoke_all_primary(self, username: str) -> None:
        if self._should_write_to_postgres():
            await self._revoke_all_postgres(username)
            return
        await self._revoke_all_redis(username)

    async def _mirror_save(self, record: RefreshSessionRecord, *, is_new: bool) -> None:
        if not self._dual_write_enabled:
            return
        if self._should_write_to_postgres():
            await self._save_refresh_redis(record)
            return
        if self._postgres_repo is not None:
            await self._save_refresh_postgres(record, is_new=is_new)

    async def _mirror_revoke(self, username: str, jti: str) -> None:
        if not self._dual_write_enabled:
            return
        if self._should_write_to_postgres():
            await self._revoke_refresh_redis(username, jti)
            return
        await self._revoke_refresh_postgres(jti)

    async def _mirror_revoke_all(self, username: str) -> None:
        if not self._dual_write_enabled:
            return
        if self._should_write_to_postgres():
            await self._revoke_all_redis(username)
            return
        await self._revoke_all_postgres(username)

    def _should_write_to_postgres(self) -> bool:
        return self._storage_backend == "postgres" and self._postgres_repo is not None

    def _should_read_from_postgres(self) -> bool:
        if self._read_backend == "postgres" and self._postgres_repo is not None:
            return True
        return self._storage_backend == "postgres" and self._postgres_repo is not None
