from __future__ import annotations

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings


def _normalize_database_url(raw_url: str) -> str:
    url = make_url(raw_url)
    if url.drivername == "postgresql":
        return url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)
    return url.render_as_string(hide_password=False)


def get_database_url() -> str:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured.")
    return _normalize_database_url(settings.database_url)


def create_engine() -> AsyncEngine:
    return create_async_engine(get_database_url(), future=True, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine | None = None) -> async_sessionmaker[AsyncSession]:
    bound_engine = engine or create_engine()
    return async_sessionmaker(bound_engine, expire_on_commit=False)
