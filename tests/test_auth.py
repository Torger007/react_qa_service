from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

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
    assert data["access_token"].count(".") == 2


def test_auth_failure_invalid_credentials(client):
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "wrong"},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"


def test_public_register_success(client):
    username = f"public_user_{uuid4().hex[:8]}"
    resp = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "publicpass123", "role": "admin"},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["username"] == username
    assert data["role"] == "user"


def test_auth_failure_expired_token(client):
    from app.core.config import settings

    now = datetime.now(tz=timezone.utc)
    token = jwt.encode(
        {
            "sub": "admin",
            "role": "admin",
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


def test_auth_failure_insufficient_permissions(client, user_token):
    resp = client.post(
        "/api/v1/chat/",
        json={"message": "do sensitive", "action": "delete", "action_input": {"id": "1"}},
        headers={"Authorization": f"Bearer {user_token}", "Content-Type": "application/json"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Insufficient permissions"


def test_get_current_user_profile(client, admin_token):
    resp = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["username"] == "admin"
    assert data["role"] == "admin"
    assert data["is_active"] is True


def test_admin_can_create_and_list_users(client, admin_token):
    username = f"qa_user_{uuid4().hex[:8]}"
    create_resp = client.post(
        "/api/v1/auth/users",
        json={"username": username, "password": "alice1234", "role": "user"},
        headers={"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"},
    )
    assert create_resp.status_code == 201
    assert create_resp.json()["username"] == username

    list_resp = client.get(
        "/api/v1/auth/users",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert list_resp.status_code == 200
    usernames = [item["username"] for item in list_resp.json()]
    assert "admin" in usernames
    assert username in usernames


def test_non_admin_cannot_create_users(client, user_token):
    username = f"qa_blocked_{uuid4().hex[:8]}"
    resp = client.post(
        "/api/v1/auth/users",
        json={"username": username, "password": "alice1234", "role": "user"},
        headers={"Authorization": f"Bearer {user_token}", "Content-Type": "application/json"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Admin privileges required"


def test_user_can_change_own_password(client, admin_token):
    username = f"qa_password_{uuid4().hex[:8]}"
    create_resp = client.post(
        "/api/v1/auth/users",
        json={"username": username, "password": "alice1234", "role": "user"},
        headers={"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"},
    )
    assert create_resp.status_code == 201

    login_resp = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "alice1234"},
        headers={"Content-Type": "application/json"},
    )
    token = login_resp.json()["access_token"]

    change_resp = client.post(
        "/api/v1/auth/me/password",
        json={"current_password": "alice1234", "new_password": "newpass123"},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    assert change_resp.status_code == 204

    relogin_resp = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "newpass123"},
        headers={"Content-Type": "application/json"},
    )
    assert relogin_resp.status_code == 200


def test_admin_can_disable_user(client, admin_token):
    username = f"qa_disabled_{uuid4().hex[:8]}"
    create_resp = client.post(
        "/api/v1/auth/users",
        json={"username": username, "password": "bob12345", "role": "user"},
        headers={"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"},
    )
    assert create_resp.status_code == 201

    disable_resp = client.patch(
        f"/api/v1/auth/users/{username}",
        json={"is_active": False},
        headers={"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"},
    )
    assert disable_resp.status_code == 200
    assert disable_resp.json()["is_active"] is False

    login_resp = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "bob12345"},
        headers={"Content-Type": "application/json"},
    )
    assert login_resp.status_code == 401
