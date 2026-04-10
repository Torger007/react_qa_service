from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from redis.asyncio import Redis

from app.core.redis_client import key
from app.core.config import settings
from app.models.feedback_schemas import FeedbackRequest


class FeedbackService:
    def __init__(self, *, redis: Redis) -> None:
        self._redis = redis

    async def submit(self, payload: FeedbackRequest, *, subject: str) -> str:
        feedback_id = str(uuid4())
        feedback_key = key("feedback", feedback_id)
        index_key = key("feedback", "index")
        session_key = key("feedback", "session", str(payload.session_id))
        record = {
            "feedback_id": feedback_id,
            "subject": subject,
            "session_id": str(payload.session_id),
            "run_id": payload.run_id,
            "turn_id": payload.turn_id,
            "task_type": payload.task_type,
            "feedback": payload.feedback,
            "question": payload.question,
            "answer": payload.answer,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        pipe = self._redis.pipeline(transaction=False)
        pipe.set(
            feedback_key,
            json.dumps(record, ensure_ascii=False),
            ex=settings.feedback_ttl_seconds,
        )
        pipe.sadd(index_key, feedback_key)
        pipe.sadd(session_key, feedback_key)
        pipe.expire(index_key, settings.feedback_ttl_seconds)
        pipe.expire(session_key, settings.feedback_ttl_seconds)
        await pipe.execute()
        return feedback_id
