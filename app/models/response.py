from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.schemas import ChatMessage


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    username: str
    role: str
    is_active: bool
    created_at: str
    updated_at: str
    last_login_at: str | None = None


class ChatResponse(BaseModel):
    session_id: UUID
    answer: str
    history: list[ChatMessage] = Field(default_factory=list)


class ConfirmChallengeResponse(BaseModel):
    required: bool = True
    confirm_token: str
    expires_in_seconds: int
    reason: str
    details: dict[str, Any] | None = None
