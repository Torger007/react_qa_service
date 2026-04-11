"""initial postgres auth and chat schema

Revision ID: 20260411_000001
Revises:
Create Date: 2026-04-11 19:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260411_000001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failed_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )
    op.create_index("ix_users_role", "users", ["role"], unique=False)
    op.create_index("ix_users_is_active", "users", ["is_active"], unique=False)

    op.create_table(
        "auth_refresh_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("jti", sa.String(length=128), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("device_label", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_auth_refresh_sessions_user_id_users"),
        sa.UniqueConstraint("jti", name="uq_auth_refresh_sessions_jti"),
    )
    op.create_index("ix_auth_refresh_sessions_user_id", "auth_refresh_sessions", ["user_id"], unique=False)
    op.create_index("ix_auth_refresh_sessions_expires_at", "auth_refresh_sessions", ["expires_at"], unique=False)
    op.create_index("ix_auth_refresh_sessions_revoked_at", "auth_refresh_sessions", ["revoked_at"], unique=False)

    op.create_table(
        "auth_audit_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("username_snapshot", sa.String(length=128), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_username_snapshot", sa.String(length=128), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False, server_default="success"),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("details_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_auth_audit_logs_user_id_users"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], name="fk_auth_audit_logs_actor_user_id_users"),
    )
    op.create_index("ix_auth_audit_logs_event_type", "auth_audit_logs", ["event_type"], unique=False)
    op.create_index("ix_auth_audit_logs_user_id", "auth_audit_logs", ["user_id"], unique=False)
    op.create_index("ix_auth_audit_logs_actor_user_id", "auth_audit_logs", ["actor_user_id"], unique=False)
    op.create_index("ix_auth_audit_logs_created_at", "auth_audit_logs", ["created_at"], unique=False)

    op.create_table(
        "chat_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("last_message_preview", sa.Text(), nullable=False, server_default=""),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], name="fk_chat_sessions_owner_user_id_users"),
    )
    op.create_index("ix_chat_sessions_owner_user_id", "chat_sessions", ["owner_user_id"], unique=False)
    op.create_index("ix_chat_sessions_updated_at", "chat_sessions", ["updated_at"], unique=False)

    op.create_table(
        "chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], name="fk_chat_messages_session_id_chat_sessions"),
    )
    op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"], unique=False)
    op.create_index("ix_chat_messages_created_at", "chat_messages", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_chat_messages_created_at", table_name="chat_messages")
    op.drop_index("ix_chat_messages_session_id", table_name="chat_messages")
    op.drop_table("chat_messages")

    op.drop_index("ix_chat_sessions_updated_at", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_owner_user_id", table_name="chat_sessions")
    op.drop_table("chat_sessions")

    op.drop_index("ix_auth_audit_logs_created_at", table_name="auth_audit_logs")
    op.drop_index("ix_auth_audit_logs_actor_user_id", table_name="auth_audit_logs")
    op.drop_index("ix_auth_audit_logs_user_id", table_name="auth_audit_logs")
    op.drop_index("ix_auth_audit_logs_event_type", table_name="auth_audit_logs")
    op.drop_table("auth_audit_logs")

    op.drop_index("ix_auth_refresh_sessions_revoked_at", table_name="auth_refresh_sessions")
    op.drop_index("ix_auth_refresh_sessions_expires_at", table_name="auth_refresh_sessions")
    op.drop_index("ix_auth_refresh_sessions_user_id", table_name="auth_refresh_sessions")
    op.drop_table("auth_refresh_sessions")

    op.drop_index("ix_users_is_active", table_name="users")
    op.drop_index("ix_users_role", table_name="users")
    op.drop_table("users")
