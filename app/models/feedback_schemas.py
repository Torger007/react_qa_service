from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class FeedbackRequest(BaseModel):
    session_id: UUID
    run_id: str | None = Field(default=None, max_length=200)
    turn_id: str | None = Field(default=None, max_length=200)
    task_type: Literal["qa", "summary"] = "qa"
    feedback: Literal["up", "down"]
    question: str = Field(min_length=1, max_length=8000)
    answer: str = Field(min_length=1, max_length=20000)


class FeedbackResponse(BaseModel):
    feedback_id: str
    stored: bool = True
