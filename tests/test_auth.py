from __future__ import annotations

from datetime import datetime, timedelta, timezone

from jose import jwt


def test_token_issue_success(client):
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin"},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data.get("access_token"), str) and data["access_token"]
    # JWT format: header.payload.signature
    assert data["access_token"].count(".") == 2


def test_auth_failure_invalid_credentials(client):
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "wrong"},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"


def test_auth_failure_expired_token(client):
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
    # Auth middleware returns plain text for middleware-level failures.
    assert "Token expired" in resp.text


def test_auth_failure_insufficient_permissions(client, user_token):
    resp = client.post(
        "/api/v1/chat/",
        json={"message": "do sensitive", "action": "delete", "action_input": {"id": "1"}},
        headers={"Authorization": f"Bearer {user_token}", "Content-Type": "application/json"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Insufficient permissions"
