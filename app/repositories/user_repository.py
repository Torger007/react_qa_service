from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import UserORM
from app.services.user_service import StoredUser


def _dt_to_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _stored_to_orm(user: StoredUser) -> dict[str, object]:
    return {
        "username": user.username,
        "password_hash": user.password_hash,
        "role": user.role,
        "is_active": user.is_active,
        "last_login_at": datetime.fromisoformat(user.last_login_at) if user.last_login_at else None,
        "last_failed_login_at": datetime.fromisoformat(user.last_failed_login_at) if user.last_failed_login_at else None,
        "failed_login_attempts": user.failed_login_attempts,
        "locked_until": datetime.fromisoformat(user.locked_until) if user.locked_until else None,
        "token_version": user.token_version,
        "created_at": datetime.fromisoformat(user.created_at),
        "updated_at": datetime.fromisoformat(user.updated_at),
    }


def _orm_to_stored(user: UserORM) -> StoredUser:
    return StoredUser(
        username=user.username,
        password_hash=user.password_hash,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at.isoformat(),
        updated_at=user.updated_at.isoformat(),
        last_login_at=_dt_to_iso(user.last_login_at),
        last_failed_login_at=_dt_to_iso(user.last_failed_login_at),
        failed_login_attempts=user.failed_login_attempts,
        locked_until=_dt_to_iso(user.locked_until),
        token_version=user.token_version,
    )


class PostgresUserRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_user(self, username: str) -> StoredUser | None:
        async with self._session_factory() as session:
            user = await session.scalar(select(UserORM).where(UserORM.username == username))
            return _orm_to_stored(user) if user is not None else None

    async def list_users(self) -> list[StoredUser]:
        async with self._session_factory() as session:
            rows = await session.scalars(select(UserORM).order_by(UserORM.username.asc()))
            return [_orm_to_stored(item) for item in rows.all()]

    async def create_user(self, user: StoredUser) -> StoredUser:
        async with self._session_factory() as session:
            orm_user = UserORM(**_stored_to_orm(user))
            session.add(orm_user)
            await session.commit()
            await session.refresh(orm_user)
            return _orm_to_stored(orm_user)

    async def update_user(self, user: StoredUser) -> StoredUser:
        async with self._session_factory() as session:
            orm_user = await session.scalar(select(UserORM).where(UserORM.username == user.username))
            if orm_user is None:
                raise LookupError("User not found")
            for key, value in _stored_to_orm(user).items():
                setattr(orm_user, key, value)
            await session.commit()
            await session.refresh(orm_user)
            return _orm_to_stored(orm_user)

    async def delete_user(self, username: str) -> None:
        async with self._session_factory() as session:
            orm_user = await session.scalar(select(UserORM).where(UserORM.username == username))
            if orm_user is None:
                raise LookupError("User not found")
            await session.delete(orm_user)
            await session.commit()
