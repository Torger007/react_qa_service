from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.schemas import ChatMessage


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"


class UserResponse(BaseModel):
    username: str
    role: str
    is_active: bool
    created_at: str
    updated_at: str
    last_login_at: str | None = None
    last_failed_login_at: str | None = None
    failed_login_attempts: int = 0
    locked_until: str | None = None


class UserBulkDeleteResponse(BaseModel):
    deleted_usernames: list[str]
    not_found_usernames: list[str]


class AuditLogResponse(BaseModel):
    event_type: str
    username: str | None = None
    actor_username: str | None = None
    outcome: str
    created_at: str
    ip_address: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    session_id: UUID
    answer: str
    history: list[ChatMessage] = Field(default_factory=list)


class SessionSummaryResponse(BaseModel):
    session_id: UUID
    title: str
    created_at: str
    updated_at: str
    last_message_preview: str = ""
    message_count: int = 0


class SessionDetailResponse(SessionSummaryResponse):
    history: list[ChatMessage] = Field(default_factory=list)


class ConfirmChallengeResponse(BaseModel):
    required: bool = True
    confirm_token: str
    expires_in_seconds: int
    reason: str
    details: dict[str, Any] | None = None
