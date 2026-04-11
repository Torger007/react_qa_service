from __future__ import annotations

import asyncio

from app.services.user_service import UserService
from tests.conftest import FakeRedis


class FakePostgresUserRepository:
    def __init__(self) -> None:
        self._users: dict[str, object] = {}

    async def get_user(self, username: str):
        return self._users.get(username)

    async def list_users(self):
        return [self._users[key] for key in sorted(self._users)]

    async def create_user(self, user):
        self._users[user.username] = user
        return user

    async def update_user(self, user):
        if user.username not in self._users:
            raise LookupError("User not found")
        self._users[user.username] = user
        return user

    async def delete_user(self, username: str):
        if username not in self._users:
            raise LookupError("User not found")
        del self._users[username]


def test_user_service_dual_writes_to_postgres_when_redis_is_primary():
    redis = FakeRedis()
    postgres_repo = FakePostgresUserRepository()
    service = UserService(
        redis,
        postgres_repo=postgres_repo,
        storage_backend="redis",
        read_backend="redis",
        dual_write_enabled=True,
    )

    created = asyncio.run(service.create_user(username="alice", password="Password123", role="user"))

    redis_user = asyncio.run(service.get_user("alice"))
    postgres_user = asyncio.run(postgres_repo.get_user("alice"))

    assert created.username == "alice"
    assert redis_user is not None and redis_user.username == "alice"
    assert postgres_user is not None and postgres_user.username == "alice"


def test_user_service_can_read_from_postgres_backend():
    redis = FakeRedis()
    postgres_repo = FakePostgresUserRepository()
    primary_service = UserService(
        redis,
        postgres_repo=postgres_repo,
        storage_backend="redis",
        read_backend="redis",
        dual_write_enabled=True,
    )
    asyncio.run(primary_service.create_user(username="bob", password="Password123", role="admin"))

    postgres_read_service = UserService(
        redis,
        postgres_repo=postgres_repo,
        storage_backend="redis",
        read_backend="postgres",
        dual_write_enabled=True,
    )

    user = asyncio.run(postgres_read_service.get_user("bob"))

    assert user is not None
    assert user.username == "bob"
    assert user.role == "admin"


def test_user_service_dual_delete_keeps_backends_in_sync():
    redis = FakeRedis()
    postgres_repo = FakePostgresUserRepository()
    service = UserService(
        redis,
        postgres_repo=postgres_repo,
        storage_backend="redis",
        read_backend="redis",
        dual_write_enabled=True,
    )

    asyncio.run(service.create_user(username="carol", password="Password123", role="user"))
    asyncio.run(service.delete_user("carol"))

    assert asyncio.run(service.get_user("carol")) is None
    assert asyncio.run(postgres_repo.get_user("carol")) is None


def test_bootstrap_uses_primary_backend_even_when_read_backend_is_postgres(monkeypatch):
    from app.core.config import DemoUser, settings

    redis = FakeRedis()
    postgres_repo = FakePostgresUserRepository()
    seed_service = UserService(
        redis,
        postgres_repo=postgres_repo,
        storage_backend="redis",
        read_backend="redis",
        dual_write_enabled=False,
    )
    asyncio.run(seed_service.create_user(username="bootstrap_admin", password="Secret123", role="admin"))

    service = UserService(
        redis,
        postgres_repo=postgres_repo,
        storage_backend="redis",
        read_backend="postgres",
        dual_write_enabled=True,
    )
    monkeypatch.setattr(
        type(settings),
        "demo_users",
        lambda self: [DemoUser(username="bootstrap_admin", password="Secret123", role="admin")],
    )

    asyncio.run(service.bootstrap())

    assert asyncio.run(service.get_user("bootstrap_admin")) is not None
    assert asyncio.run(postgres_repo.get_user("bootstrap_admin")) is not None
