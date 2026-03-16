from __future__ import annotations

from uuid import UUID, uuid4


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
