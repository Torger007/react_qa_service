from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from jose import jwt


def test_token_issue_success(client):
    from app.core.config import settings

    resp = client.post(
        "/api/v1/auth/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data.get("access_token"), str) and data["access_token"]
    assert isinstance(data.get("refresh_token"), str) and data["refresh_token"]
    assert data["access_token"].count(".") == 2


def test_refresh_rotates_refresh_token(client):
    from app.core.config import settings

    login_resp = client.post(
        "/api/v1/auth/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
        headers={"Content-Type": "application/json"},
    )
    assert login_resp.status_code == 200
    refresh_token = login_resp.json()["refresh_token"]

    refresh_resp = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
        headers={"Content-Type": "application/json"},
    )
    assert refresh_resp.status_code == 200
    data = refresh_resp.json()
    assert data["refresh_token"] != refresh_token

    old_refresh_resp = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
        headers={"Content-Type": "application/json"},
    )
    assert old_refresh_resp.status_code == 401


def test_logout_revokes_current_session(client):
    from app.core.config import settings

    login_resp = client.post(
        "/api/v1/auth/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
        headers={"Content-Type": "application/json"},
    )
    assert login_resp.status_code == 200
    access_token = login_resp.json()["access_token"]
    refresh_token = login_resp.json()["refresh_token"]

    logout_resp = client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": refresh_token},
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
    )
    assert logout_resp.status_code == 204

    me_resp = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert me_resp.status_code == 401

    refresh_resp = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
        headers={"Content-Type": "application/json"},
    )
    assert refresh_resp.status_code == 401


def test_logout_all_invalidates_existing_tokens(client):
    from app.core.config import settings

    login_resp = client.post(
        "/api/v1/auth/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
        headers={"Content-Type": "application/json"},
    )
    assert login_resp.status_code == 200
    access_token = login_resp.json()["access_token"]
    refresh_token = login_resp.json()["refresh_token"]

    logout_all_resp = client.post(
        "/api/v1/auth/logout-all",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert logout_all_resp.status_code == 204

    me_resp = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert me_resp.status_code == 401

    refresh_resp = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
        headers={"Content-Type": "application/json"},
    )
    assert refresh_resp.status_code == 401


def test_auth_failure_invalid_credentials(client):
    from app.core.config import settings

    resp = client.post(
        "/api/v1/auth/login",
        json={"username": settings.admin_username, "password": "wrong"},
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"


def test_failed_login_attempts_are_recorded_and_reset_on_success(client, admin_token):
    username = f"qa_failed_reset_{uuid4().hex[:8]}"
    create_resp = client.post(
        "/api/v1/auth/users",
        json={"username": username, "password": "Reset12345", "role": "user"},
        headers={"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"},
    )
    assert create_resp.status_code == 201

    failed_resp = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "wrong-pass"},
        headers={"Content-Type": "application/json"},
    )
    assert failed_resp.status_code == 401

    list_resp = client.get(
        "/api/v1/auth/users",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    user = next(item for item in list_resp.json() if item["username"] == username)
    assert user["failed_login_attempts"] == 1
    assert user["last_failed_login_at"] is not None

    success_resp = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "Reset12345"},
        headers={"Content-Type": "application/json"},
    )
    assert success_resp.status_code == 200

    list_resp_after = client.get(
        "/api/v1/auth/users",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    user_after = next(item for item in list_resp_after.json() if item["username"] == username)
    assert user_after["failed_login_attempts"] == 0
    assert user_after["locked_until"] is None


def test_user_is_locked_after_reaching_failure_threshold(client, admin_token):
    from app.core.config import settings

    username = f"qa_locked_{uuid4().hex[:8]}"
    create_resp = client.post(
        "/api/v1/auth/users",
        json={"username": username, "password": "Lock123456", "role": "user"},
        headers={"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"},
    )
    assert create_resp.status_code == 201

    final_resp = None
    for _ in range(settings.login_max_failures):
        final_resp = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "wrong-pass"},
            headers={"Content-Type": "application/json"},
        )
    assert final_resp is not None
    assert final_resp.status_code == 423
    assert "Account is locked until" in final_resp.json()["detail"]

    locked_login = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "Lock123456"},
        headers={"Content-Type": "application/json"},
    )
    assert locked_login.status_code == 423

    list_resp = client.get(
        "/api/v1/auth/users",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    user = next(item for item in list_resp.json() if item["username"] == username)
    assert user["failed_login_attempts"] == settings.login_max_failures
    assert user["locked_until"] is not None


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
    from app.core.config import settings

    resp = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["username"] == settings.admin_username
    assert data["role"] == "admin"
    assert data["is_active"] is True


def test_admin_can_create_and_list_users(client, admin_token):
    from app.core.config import settings

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
    assert settings.admin_username in usernames
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


def test_password_change_invalidates_existing_tokens(client, admin_token):
    username = f"qa_password_invalidate_{uuid4().hex[:8]}"
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
    access_token = login_resp.json()["access_token"]
    refresh_token = login_resp.json()["refresh_token"]

    change_resp = client.post(
        "/api/v1/auth/me/password",
        json={"current_password": "alice1234", "new_password": "alice12345"},
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
    )
    assert change_resp.status_code == 204

    me_resp = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert me_resp.status_code == 401

    refresh_resp = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
        headers={"Content-Type": "application/json"},
    )
    assert refresh_resp.status_code == 401


def test_admin_can_delete_user(client, admin_token):
    username = f"qa_deleted_{uuid4().hex[:8]}"
    create_resp = client.post(
        "/api/v1/auth/users",
        json={"username": username, "password": "delete12345", "role": "user"},
        headers={"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"},
    )
    assert create_resp.status_code == 201

    delete_resp = client.delete(
        f"/api/v1/auth/users/{username}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert delete_resp.status_code == 204

    list_resp = client.get(
        "/api/v1/auth/users",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert list_resp.status_code == 200
    usernames = [item["username"] for item in list_resp.json()]
    assert username not in usernames


def test_admin_cannot_delete_self(client, admin_token):
    from app.core.config import settings

    delete_resp = client.delete(
        f"/api/v1/auth/users/{settings.admin_username}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert delete_resp.status_code == 400
    assert delete_resp.json()["detail"] == "Cannot delete the current admin user"


def test_admin_can_bulk_delete_users(client, admin_token):
    usernames = [f"qa_bulk_{uuid4().hex[:8]}", f"qa_bulk_{uuid4().hex[:8]}"]
    for username in usernames:
        create_resp = client.post(
            "/api/v1/auth/users",
            json={"username": username, "password": "delete12345", "role": "user"},
            headers={"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"},
        )
        assert create_resp.status_code == 201

    bulk_delete_resp = client.post(
        "/api/v1/auth/users/bulk-delete",
        json={"usernames": [usernames[0], usernames[1], "missing_user_demo"]},
        headers={"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"},
    )
    assert bulk_delete_resp.status_code == 200
    data = bulk_delete_resp.json()
    assert sorted(data["deleted_usernames"]) == sorted(usernames)
    assert data["not_found_usernames"] == ["missing_user_demo"]

    list_resp = client.get(
        "/api/v1/auth/users",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert list_resp.status_code == 200
    remaining = [item["username"] for item in list_resp.json()]
    for username in usernames:
        assert username not in remaining


def test_admin_cannot_bulk_delete_self(client, admin_token):
    from app.core.config import settings

    bulk_delete_resp = client.post(
        "/api/v1/auth/users/bulk-delete",
        json={"usernames": [settings.admin_username, f"qa_bulk_{uuid4().hex[:8]}"]},
        headers={"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"},
    )
    assert bulk_delete_resp.status_code == 400
    assert bulk_delete_resp.json()["detail"] == "Cannot delete the current admin user"


def test_admin_can_view_audit_logs(client, admin_token):
    username = f"qa_audit_{uuid4().hex[:8]}"
    failed_resp = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "wrong-pass"},
        headers={"Content-Type": "application/json"},
    )
    assert failed_resp.status_code == 401

    logs_resp = client.get(
        f"/api/v1/auth/audit-logs?limit=20&username={username}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert logs_resp.status_code == 200
    logs = logs_resp.json()
    assert any(item["event_type"] == "login_failed" for item in logs)
    assert any(item["username"] == username for item in logs)
