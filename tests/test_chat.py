from __future__ import annotations


def test_chat_normal_dialogue(client, admin_token):
    resp = client.post(
        "/api/v1/chat/",
        json={"message": "你好，介绍一下ReAct是什么？"},
        headers={"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data
    assert isinstance(data.get("answer"), str) and data["answer"]
    assert data["answer"].startswith("[ReAct]")
    assert isinstance(data.get("history"), list)


def test_chat_sensitive_confirmation_202(client, admin_token):
    resp = client.post(
        "/api/v1/chat/",
        json={"message": "我要执行删除", "action": "delete", "action_input": {"id": "123"}},
        headers={"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data.get("required") is True
    assert isinstance(data.get("confirm_token"), str) and data["confirm_token"]
    assert isinstance(data.get("expires_in_seconds"), int) and data["expires_in_seconds"] > 0
    assert "confirmation" in (data.get("reason") or "").lower()
    assert isinstance(data.get("details"), dict)


def test_chat_token_expired_failure(client):
    from datetime import datetime, timedelta, timezone

    from jose import jwt

    from app.core.config import settings

    now = datetime.now(tz=timezone.utc)
    token = jwt.encode(
        {
            "sub": "admin",
            "iat": int(now.timestamp()) - 60,
            "exp": int((now - timedelta(seconds=1)).timestamp()),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    resp = client.post(
        "/api/v1/chat/",
        json={"message": "hello"},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    assert resp.status_code == 403
    assert "Token expired" in resp.text
