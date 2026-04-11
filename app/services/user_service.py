from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.core.config import DemoUser, settings
from app.core.redis_client import key

if TYPE_CHECKING:
    from app.repositories.user_repository import PostgresUserRepository


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _utc_now_dt() -> datetime:
    return datetime.now(tz=timezone.utc)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def hash_password(password: str, *, iterations: int = 200_000) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations_text, salt_hex, digest_hex = password_hash.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    iterations = int(iterations_text)
    salt = bytes.fromhex(salt_hex)
    expected = bytes.fromhex(digest_hex)
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


class StoredUser(BaseModel):
    username: str
    password_hash: str = Field(repr=False)
    role: str = "user"
    is_active: bool = True
    created_at: str
    updated_at: str
    last_login_at: str | None = None
    last_failed_login_at: str | None = None
    failed_login_attempts: int = 0
    locked_until: str | None = None
    token_version: int = 0

    @property
    def is_admin(self) -> bool:
        return self.role.strip().lower() == "admin"

    @property
    def is_locked(self) -> bool:
        locked_until = _parse_datetime(self.locked_until)
        return locked_until is not None and locked_until > _utc_now_dt()


class AuthenticationError(Exception):
    def __init__(self, detail: str, *, status_code: int = 401) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class UserService:
    def __init__(
        self,
        redis: Redis,
        *,
        postgres_repo: PostgresUserRepository | None = None,
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
    def _user_key(username: str) -> str:
        return key("auth", "users", username)

    @staticmethod
    def _user_index_key() -> str:
        return key("auth", "users", "all")

    async def bootstrap(self) -> None:
        bootstrap_users = settings.demo_users()
        for demo_user in bootstrap_users:
            existing = await self._get_user_primary(demo_user.username)
            if existing is None and self._dual_write_enabled:
                existing = await self._get_user_secondary(demo_user.username)
            if existing is None:
                await self.create_user(
                    username=demo_user.username,
                    password=demo_user.password,
                    role=demo_user.role,
                )
            else:
                # Keep the configured bootstrap admin aligned with the current environment.
                await self.update_user(
                    demo_user.username,
                    password=demo_user.password,
                    role=demo_user.role,
                    is_active=True,
                )

        await self._cleanup_legacy_default_admin(bootstrap_users)

    async def get_user(self, username: str) -> StoredUser | None:
        if self._should_read_from_postgres():
            return await self._get_user_postgres(username)
        return await self._get_user_redis(username)

    async def list_users(self) -> list[StoredUser]:
        if self._should_read_from_postgres():
            return await self._list_users_postgres()
        return await self._list_users_redis()

    async def create_user(self, *, username: str, password: str, role: str = "user") -> StoredUser:
        existing = await self._get_user_primary(username)
        if existing is not None:
            raise ValueError("User already exists")

        now = _utc_now()
        user = StoredUser(
            username=username,
            password_hash=hash_password(password),
            role=role,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        return await self._persist_new_user(user)

    async def update_user(
        self,
        username: str,
        *,
        password: str | None = None,
        role: str | None = None,
        is_active: bool | None = None,
    ) -> StoredUser:
        user = await self._get_user_primary(username)
        if user is None:
            raise LookupError("User not found")

        updates: dict[str, Any] = {"updated_at": _utc_now()}
        if password is not None:
            updates["password_hash"] = hash_password(password)
        if role is not None:
            updates["role"] = role
        if is_active is not None:
            updates["is_active"] = is_active

        updated_user = user.model_copy(update=updates)
        return await self._save_existing_user(updated_user)

    async def delete_user(self, username: str) -> None:
        user = await self._get_user_primary(username)
        if user is None:
            raise LookupError("User not found")
        await self._delete_user_primary(username)
        await self._mirror_delete(username)

    async def record_login(self, username: str) -> None:
        user = await self._get_user_primary(username)
        if user is None:
            return
        now = _utc_now()
        updated = user.model_copy(
            update={
                "last_login_at": now,
                "last_failed_login_at": None,
                "failed_login_attempts": 0,
                "locked_until": None,
                "updated_at": now,
            }
        )
        await self._save_existing_user(updated)

    async def record_failed_login(self, username: str) -> StoredUser | None:
        user = await self._get_user_primary(username)
        if user is None:
            return None

        failed_attempts = user.failed_login_attempts + 1
        now_dt = _utc_now_dt()
        updates: dict[str, Any] = {
            "failed_login_attempts": failed_attempts,
            "last_failed_login_at": now_dt.isoformat(),
            "updated_at": now_dt.isoformat(),
        }
        if failed_attempts >= settings.login_max_failures:
            updates["locked_until"] = (now_dt + timedelta(seconds=settings.login_lockout_seconds)).isoformat()
        updated = user.model_copy(update=updates)
        return await self._save_existing_user(updated)

    async def authenticate(self, username: str, password: str) -> StoredUser:
        user = await self.get_user(username)
        if user is None or not user.is_active:
            raise AuthenticationError("Invalid credentials")

        if user.is_locked:
            raise AuthenticationError(f"Account is locked until {user.locked_until}", status_code=423)

        if not verify_password(password, user.password_hash):
            updated_user = await self.record_failed_login(username)
            if updated_user is not None and updated_user.is_locked:
                raise AuthenticationError(
                    f"Account is locked until {updated_user.locked_until}",
                    status_code=423,
                )
            raise AuthenticationError("Invalid credentials")

        await self.record_login(username)
        authenticated_user = await self.get_user(username)
        if authenticated_user is None:
            raise AuthenticationError("Invalid credentials")
        return authenticated_user

    async def increment_token_version(self, username: str) -> StoredUser:
        user = await self._get_user_primary(username)
        if user is None:
            raise LookupError("User not found")
        updated = user.model_copy(
            update={
                "token_version": user.token_version + 1,
                "updated_at": _utc_now(),
            }
        )
        return await self._save_existing_user(updated)

    async def _save_user(self, user: StoredUser) -> None:
        await self._save_user_redis(user)

    async def _get_user_redis(self, username: str) -> StoredUser | None:
        payload = await self._redis.get(self._user_key(username))
        if not payload:
            return None
        return StoredUser.model_validate_json(payload)

    async def _list_users_redis(self) -> list[StoredUser]:
        usernames = sorted(await self._redis.smembers(self._user_index_key()))
        users: list[StoredUser] = []
        for username in usernames:
            user = await self._get_user_redis(str(username))
            if user is not None:
                users.append(user)
        return users

    async def _save_user_redis(self, user: StoredUser) -> StoredUser:
        await self._redis.set(self._user_key(user.username), user.model_dump_json())
        await self._redis.sadd(self._user_index_key(), user.username)
        return user

    async def _delete_user_redis(self, username: str) -> None:
        await self._redis.delete(self._user_key(username))
        await self._redis.srem(self._user_index_key(), username)

    async def _get_user_postgres(self, username: str) -> StoredUser | None:
        if self._postgres_repo is None:
            return None
        return await self._postgres_repo.get_user(username)

    async def _list_users_postgres(self) -> list[StoredUser]:
        if self._postgres_repo is None:
            return []
        return await self._postgres_repo.list_users()

    async def _save_user_postgres(self, user: StoredUser, *, is_new: bool) -> StoredUser:
        if self._postgres_repo is None:
            return user
        if is_new:
            return await self._postgres_repo.create_user(user)
        return await self._postgres_repo.update_user(user)

    async def _delete_user_postgres(self, username: str) -> None:
        if self._postgres_repo is None:
            return
        await self._postgres_repo.delete_user(username)

    async def _get_user_primary(self, username: str) -> StoredUser | None:
        if self._should_write_to_postgres():
            return await self._get_user_postgres(username)
        return await self._get_user_redis(username)

    async def _get_user_secondary(self, username: str) -> StoredUser | None:
        if self._should_write_to_postgres():
            return await self._get_user_redis(username)
        return await self._get_user_postgres(username)

    async def _persist_new_user(self, user: StoredUser) -> StoredUser:
        if self._should_write_to_postgres():
            stored = await self._save_user_postgres(user, is_new=True)
            await self._mirror_save(stored, is_new=True)
            return stored
        stored = await self._save_user_redis(user)
        await self._mirror_save(stored, is_new=True)
        return stored

    async def _save_existing_user(self, user: StoredUser) -> StoredUser:
        if self._should_write_to_postgres():
            stored = await self._save_user_postgres(user, is_new=False)
            await self._mirror_save(stored, is_new=False)
            return stored
        stored = await self._save_user_redis(user)
        await self._mirror_save(stored, is_new=False)
        return stored

    async def _delete_user_primary(self, username: str) -> None:
        if self._should_write_to_postgres():
            await self._delete_user_postgres(username)
            return
        await self._delete_user_redis(username)

    async def _mirror_save(self, user: StoredUser, *, is_new: bool) -> None:
        if not self._dual_write_enabled:
            return
        if self._should_write_to_postgres():
            await self._save_user_redis(user)
            return
        if self._postgres_repo is not None:
            existing = await self._get_user_postgres(user.username)
            if existing is None:
                await self._save_user_postgres(user, is_new=True)
            else:
                await self._save_user_postgres(user, is_new=False)

    async def _mirror_delete(self, username: str) -> None:
        if not self._dual_write_enabled:
            return
        if self._should_write_to_postgres():
            await self._delete_user_redis(username)
            return
        if self._postgres_repo is not None:
            existing = await self._get_user_postgres(username)
            if existing is not None:
                await self._delete_user_postgres(username)

    def _should_write_to_postgres(self) -> bool:
        return self._storage_backend == "postgres" and self._postgres_repo is not None

    def _should_read_from_postgres(self) -> bool:
        if self._read_backend == "postgres" and self._postgres_repo is not None:
            return True
        return self._storage_backend == "postgres" and self._postgres_repo is not None

    async def _cleanup_legacy_default_admin(self, bootstrap_users: list[DemoUser]) -> None:
        configured_usernames = {user.username for user in bootstrap_users}
        if "admin" in configured_usernames:
            return
        legacy_admin = await self.get_user("admin")
        if legacy_admin is None:
            return
        if legacy_admin.role.strip().lower() != "admin":
            return
        # If the configured bootstrap admin changed away from the old default admin user,
        # remove the legacy admin account to avoid leaving a stale admin/admin login behind.
        await self.delete_user("admin")


class UserSummary(BaseModel):
    username: str
    role: str
    is_active: bool
    created_at: str
    updated_at: str
    last_login_at: str | None = None
    last_failed_login_at: str | None = None
    failed_login_attempts: int = 0
    locked_until: str | None = None

    @classmethod
    def from_stored_user(cls, user: StoredUser) -> "UserSummary":
        return cls(
            username=user.username,
            role=user.role,
            is_active=user.is_active,
            created_at=user.created_at,
            updated_at=user.updated_at,
            last_login_at=user.last_login_at,
            last_failed_login_at=user.last_failed_login_at,
            failed_login_attempts=user.failed_login_attempts,
            locked_until=user.locked_until,
        )
