from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

Role = Literal["user", "assistant", "system"]


class ChatMessage(BaseModel):
    role: Role
    content: str = Field(min_length=1, max_length=8000)
    ts: datetime = Field(default_factory=datetime.utcnow)


class SessionInfo(BaseModel):
    session_id: UUID


class SessionMetadata(BaseModel):
    session_id: UUID
    owner_username: str
    title: str
    created_at: datetime
    updated_at: datetime
    last_message_preview: str = ""
    message_count: int = 0
