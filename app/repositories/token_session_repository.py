from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AuthRefreshSessionORM, UserORM
from app.services.token_session_service import RefreshSessionRecord


def _dt_to_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _record_to_orm(record: RefreshSessionRecord, *, user_id) -> dict[str, object]:
    return {
        "user_id": user_id,
        "jti": record.jti,
        "token_version": record.token_version,
        "issued_at": datetime.fromtimestamp(record.issued_at, tz=timezone.utc),
        "expires_at": datetime.fromtimestamp(record.exp, tz=timezone.utc),
        "revoked_at": datetime.fromisoformat(record.revoked_at) if record.revoked_at else None,
    }


def _orm_to_record(item: AuthRefreshSessionORM, username: str, role: str) -> RefreshSessionRecord:
    return RefreshSessionRecord(
        username=username,
        role=role,
        jti=item.jti,
        token_version=item.token_version,
        issued_at=int(item.issued_at.timestamp()),
        exp=int(item.expires_at.timestamp()),
        revoked_at=_dt_to_iso(item.revoked_at),
    )


class PostgresTokenSessionRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_session(self, record: RefreshSessionRecord) -> RefreshSessionRecord:
        async with self._session_factory() as session:
            user = await session.scalar(select(UserORM).where(UserORM.username == record.username))
            if user is None:
                raise LookupError("User not found")
            orm_item = AuthRefreshSessionORM(**_record_to_orm(record, user_id=user.id))
            session.add(orm_item)
            await session.commit()
            await session.refresh(orm_item)
            return _orm_to_record(orm_item, user.username, user.role)

    async def get_active_session(self, jti: str) -> RefreshSessionRecord | None:
        async with self._session_factory() as session:
            row = await session.execute(
                select(AuthRefreshSessionORM, UserORM)
                .join(UserORM, UserORM.id == AuthRefreshSessionORM.user_id)
                .where(AuthRefreshSessionORM.jti == jti)
            )
            result = row.first()
            if result is None:
                return None
            orm_item, user = result
            if orm_item.revoked_at is not None:
                return None
            if orm_item.expires_at <= datetime.now(tz=timezone.utc):
                return None
            return _orm_to_record(orm_item, user.username, user.role)

    async def revoke_session(self, jti: str) -> None:
        async with self._session_factory() as session:
            orm_item = await session.scalar(select(AuthRefreshSessionORM).where(AuthRefreshSessionORM.jti == jti))
            if orm_item is None:
                return
            if orm_item.revoked_at is None:
                orm_item.revoked_at = datetime.now(tz=timezone.utc)
                await session.commit()

    async def revoke_all_for_username(self, username: str) -> None:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(AuthRefreshSessionORM)
                .join(UserORM, UserORM.id == AuthRefreshSessionORM.user_id)
                .where(UserORM.username == username, AuthRefreshSessionORM.revoked_at.is_(None))
            )
            items = rows.all()
            if not items:
                return
            now = datetime.now(tz=timezone.utc)
            for item in items:
                item.revoked_at = now
            await session.commit()
