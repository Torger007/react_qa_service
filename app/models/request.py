from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    session_id: UUID | None = None

    # Optional tool/action request. If provided and considered sensitive, requires confirmation.
    action: str | None = Field(default=None, max_length=128)
    action_input: dict[str, Any] | None = None

    # Sensitive operation confirmation token (second step)
    confirm_token: str | None = Field(default=None, max_length=256)
