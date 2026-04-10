from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.core.config import settings
from app.core.redis_client import key
from app.models.feedback_schemas import FeedbackRequest
from app.services.feedback_service import FeedbackService
from tests.conftest import FakeRedis


def test_feedback_endpoint_persists_record(client, admin_token):
    session_id = uuid4()
    resp = client.post(
        "/api/v1/feedback",
        json={
            "session_id": str(session_id),
            "run_id": "run-1",
            "turn_id": "turn-1",
            "task_type": "qa",
            "feedback": "up",
            "question": "测试问题",
            "answer": "测试回答",
        },
        headers={"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert UUID(data["feedback_id"])
    assert data["stored"] is True


@pytest.mark.anyio
async def test_feedback_service_sets_ttl():
    redis = FakeRedis()
    service = FeedbackService(redis=redis)
    session_id = uuid4()

    feedback_id = await service.submit(
        FeedbackRequest(
            session_id=session_id,
            run_id="run-1",
            turn_id="turn-1",
            task_type="summary",
            feedback="down",
            question="summary?",
            answer="result",
        ),
        subject="tester",
    )

    assert UUID(feedback_id)
    assert redis._expirations[key("feedback", "index")] == settings.feedback_ttl_seconds
    assert redis._expirations[key("feedback", "session", str(session_id))] == settings.feedback_ttl_seconds
