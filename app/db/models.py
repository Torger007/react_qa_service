from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class UserORM(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failed_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    refresh_sessions: Mapped[list["AuthRefreshSessionORM"]] = relationship(back_populates="user")
    audit_logs: Mapped[list["AuthAuditLogORM"]] = relationship(
        back_populates="user",
        foreign_keys="AuthAuditLogORM.user_id",
    )
    actor_audit_logs: Mapped[list["AuthAuditLogORM"]] = relationship(
        back_populates="actor_user",
        foreign_keys="AuthAuditLogORM.actor_user_id",
    )
    chat_sessions: Mapped[list["ChatSessionORM"]] = relationship(back_populates="owner_user")


class AuthRefreshSessionORM(Base):
    __tablename__ = "auth_refresh_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    jti: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    device_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user: Mapped[UserORM] = relationship(back_populates="refresh_sessions")

    __table_args__ = (
        Index("ix_auth_refresh_sessions_user_id", "user_id"),
        Index("ix_auth_refresh_sessions_expires_at", "expires_at"),
        Index("ix_auth_refresh_sessions_revoked_at", "revoked_at"),
    )


class AuthAuditLogORM(Base):
    __tablename__ = "auth_audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    username_snapshot: Mapped[str | None] = mapped_column(String(128), nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    actor_username_snapshot: Mapped[str | None] = mapped_column(String(128), nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, default="success")
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    user: Mapped[UserORM | None] = relationship(back_populates="audit_logs", foreign_keys=[user_id])
    actor_user: Mapped[UserORM | None] = relationship(back_populates="actor_audit_logs", foreign_keys=[actor_user_id])

    __table_args__ = (
        Index("ix_auth_audit_logs_event_type", "event_type"),
        Index("ix_auth_audit_logs_user_id", "user_id"),
        Index("ix_auth_audit_logs_actor_user_id", "actor_user_id"),
        Index("ix_auth_audit_logs_created_at", "created_at"),
    )


class ChatSessionORM(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    last_message_preview: Mapped[str] = mapped_column(Text, nullable=False, default="")
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    owner_user: Mapped[UserORM] = relationship(back_populates="chat_sessions")
    messages: Mapped[list["ChatMessageORM"]] = relationship(back_populates="session")

    __table_args__ = (
        Index("ix_chat_sessions_owner_user_id", "owner_user_id"),
        Index("ix_chat_sessions_updated_at", "updated_at"),
    )


class ChatMessageORM(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chat_sessions.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    session: Mapped[ChatSessionORM] = relationship(back_populates="messages")

    __table_args__ = (
        Index("ix_chat_messages_session_id", "session_id"),
        Index("ix_chat_messages_created_at", "created_at"),
    )
