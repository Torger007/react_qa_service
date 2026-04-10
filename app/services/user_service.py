from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.core.config import settings
from app.core.redis_client import key


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


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

    @property
    def is_admin(self) -> bool:
        return self.role.strip().lower() == "admin"


class UserService:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    @staticmethod
    def _user_key(username: str) -> str:
        return key("auth", "users", username)

    @staticmethod
    def _user_index_key() -> str:
        return key("auth", "users", "all")

    async def bootstrap(self) -> None:
        for demo_user in settings.demo_users():
            existing = await self.get_user(demo_user.username)
            if existing is None:
                await self.create_user(
                    username=demo_user.username,
                    password=demo_user.password,
                    role=demo_user.role,
                )

    async def get_user(self, username: str) -> StoredUser | None:
        payload = await self._redis.get(self._user_key(username))
        if not payload:
            return None
        return StoredUser.model_validate_json(payload)

    async def list_users(self) -> list[StoredUser]:
        usernames = sorted(await self._redis.smembers(self._user_index_key()))
        users: list[StoredUser] = []
        for username in usernames:
            user = await self.get_user(username)
            if user is not None:
                users.append(user)
        return users

    async def create_user(self, *, username: str, password: str, role: str = "user") -> StoredUser:
        existing = await self.get_user(username)
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
        await self._save_user(user)
        return user

    async def update_user(
        self,
        username: str,
        *,
        password: str | None = None,
        role: str | None = None,
        is_active: bool | None = None,
    ) -> StoredUser:
        user = await self.get_user(username)
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
        await self._save_user(updated_user)
        return updated_user

    async def record_login(self, username: str) -> None:
        user = await self.get_user(username)
        if user is None:
            return
        updated = user.model_copy(update={"last_login_at": _utc_now(), "updated_at": _utc_now()})
        await self._save_user(updated)

    async def authenticate(self, username: str, password: str) -> StoredUser | None:
        user = await self.get_user(username)
        if user is None or not user.is_active:
            return None
        if not verify_password(password, user.password_hash):
            return None
        await self.record_login(username)
        return await self.get_user(username)

    async def _save_user(self, user: StoredUser) -> None:
        await self._redis.set(self._user_key(user.username), user.model_dump_json())
        await self._redis.sadd(self._user_index_key(), user.username)


class UserSummary(BaseModel):
    username: str
    role: str
    is_active: bool
    created_at: str
    updated_at: str
    last_login_at: str | None = None

    @classmethod
    def from_stored_user(cls, user: StoredUser) -> "UserSummary":
        return cls(
            username=user.username,
            role=user.role,
            is_active=user.is_active,
            created_at=user.created_at,
            updated_at=user.updated_at,
            last_login_at=user.last_login_at,
        )
