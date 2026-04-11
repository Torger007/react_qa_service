from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AuthAuditLogORM, UserORM
from app.services.audit_log_service import AuditLogEntry


def _parse_iso_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _orm_to_entry(item: AuthAuditLogORM) -> AuditLogEntry:
    return AuditLogEntry(
        event_type=item.event_type,
        username=item.username_snapshot,
        actor_username=item.actor_username_snapshot,
        outcome=item.outcome,
        created_at=item.created_at.isoformat(),
        ip_address=item.ip_address,
        details=item.details_json or {},
    )


class PostgresAuditLogRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def append(self, entry: AuditLogEntry) -> AuditLogEntry:
        async with self._session_factory() as session:
            user_id = await session.scalar(select(UserORM.id).where(UserORM.username == entry.username)) if entry.username else None
            actor_user_id = (
                await session.scalar(select(UserORM.id).where(UserORM.username == entry.actor_username))
                if entry.actor_username
                else None
            )
            orm_item = AuthAuditLogORM(
                event_type=entry.event_type,
                user_id=user_id,
                username_snapshot=entry.username,
                actor_user_id=actor_user_id,
                actor_username_snapshot=entry.actor_username,
                outcome=entry.outcome,
                ip_address=entry.ip_address,
                details_json=entry.details,
                created_at=_parse_iso_datetime(entry.created_at),
            )
            session.add(orm_item)
            await session.commit()
            await session.refresh(orm_item)
            return _orm_to_entry(orm_item)

    async def list_entries(self, *, limit: int, username: str | None = None) -> list[AuditLogEntry]:
        async with self._session_factory() as session:
            stmt = select(AuthAuditLogORM).order_by(AuthAuditLogORM.created_at.desc()).limit(limit)
            if username:
                stmt = stmt.where(
                    or_(
                        AuthAuditLogORM.username_snapshot == username,
                        AuthAuditLogORM.actor_username_snapshot == username,
                    )
                )
            rows = await session.scalars(stmt)
            return [_orm_to_entry(item) for item in rows.all()]
