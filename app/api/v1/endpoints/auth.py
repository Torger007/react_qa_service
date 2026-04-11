from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.core.security import create_access_token
from app.models.request import (
    LoginRequest,
    LogoutRequest,
    PasswordChangeRequest,
    RefreshTokenRequest,
    UserBulkDeleteRequest,
    UserCreateRequest,
    UserUpdateRequest,
)
from app.models.response import AuditLogResponse, TokenResponse, UserBulkDeleteResponse, UserResponse
from app.services.audit_log_service import AuditLogService
from app.services.token_session_service import TokenSessionService
from app.services.user_service import AuthenticationError, StoredUser, UserService, UserSummary, verify_password

router = APIRouter()


class TokenRequest(BaseModel):
    """OAuth2PasswordBearer expects a token endpoint; we accept JSON for simplicity."""

    username: str
    password: str


def _user_service(request: Request) -> UserService:
    return request.app.state.user_service


def _token_session_service(request: Request) -> TokenSessionService:
    return request.app.state.auth_session_service


def _audit_log_service(request: Request) -> AuditLogService:
    return request.app.state.audit_log_service


def _request_ip(request: Request) -> str | None:
    client = getattr(request, "client", None)
    return client.host if client is not None else None


def _subject_from_state(request: Request) -> str:
    sub = getattr(request.state, "subject", None)
    if isinstance(sub, str) and sub:
        return sub
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthenticated request")


def _role_from_state(request: Request) -> str:
    role = getattr(request.state, "role", None)
    if isinstance(role, str) and role:
        return role
    return "user"


def _require_admin(request: Request) -> None:
    if _role_from_state(request).strip().lower() != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")


async def _build_token_response(request: Request, user: StoredUser) -> TokenResponse:
    refresh_token = await _token_session_service(request).issue_refresh_token(user)
    return TokenResponse(
        access_token=create_access_token(
            subject=user.username,
            role=user.role,
            token_version=user.token_version,
        ),
        refresh_token=refresh_token,
    )


async def _issue_token(request: Request, username: str, password: str) -> TokenResponse:
    try:
        user = await _user_service(request).authenticate(username, password)
    except AuthenticationError as err:
        await _audit_log_service(request).append(
            event_type="login_failed",
            username=username,
            outcome="failure",
            ip_address=_request_ip(request),
            details={"reason": err.detail},
        )
        raise HTTPException(status_code=err.status_code, detail=err.detail) from err
    await _audit_log_service(request).append(
        event_type="login_succeeded",
        username=user.username,
        actor_username=user.username,
        outcome="success",
        ip_address=_request_ip(request),
    )
    return await _build_token_response(request, user)


async def _invalidate_user_sessions(request: Request, username: str) -> None:
    await _token_session_service(request).revoke_all_user_sessions(username)
    await _user_service(request).increment_token_version(username)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login and get JWT",
)
async def login(request: Request, payload: LoginRequest) -> TokenResponse:
    return await _issue_token(request, payload.username, payload.password)


@router.post(
    "/token",
    response_model=TokenResponse,
    summary="OAuth2 compatible token endpoint (JSON)",
)
async def token(request: Request, payload: TokenRequest) -> TokenResponse:
    return await _issue_token(request, payload.username, payload.password)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh access token and rotate refresh token",
)
async def refresh(request: Request, payload: RefreshTokenRequest) -> TokenResponse:
    token_service = _token_session_service(request)
    try:
        claims = await token_service.validate_refresh_token(payload.refresh_token)
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(err)) from err

    user = await _user_service(request).get_user(claims.subject)
    if user is None or not user.is_active:
        await _audit_log_service(request).append(
            event_type="refresh_failed",
            username=claims.subject,
            outcome="failure",
            ip_address=_request_ip(request),
            details={"reason": "User not found or inactive"},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    if user.token_version != claims.token_version:
        await _audit_log_service(request).append(
            event_type="refresh_failed",
            username=user.username,
            actor_username=user.username,
            outcome="failure",
            ip_address=_request_ip(request),
            details={"reason": "Refresh token has been invalidated"},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token has been invalidated")

    rotated_refresh = await token_service.rotate_refresh_token(payload.refresh_token, user)
    await _audit_log_service(request).append(
        event_type="refresh_succeeded",
        username=user.username,
        actor_username=user.username,
        outcome="success",
        ip_address=_request_ip(request),
    )
    return TokenResponse(
        access_token=create_access_token(
            subject=user.username,
            role=user.role,
            token_version=user.token_version,
        ),
        refresh_token=rotated_refresh,
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Logout current session",
)
async def logout(request: Request, payload: LogoutRequest) -> None:
    token_service = _token_session_service(request)
    username = _subject_from_state(request)
    claims = getattr(request.state, "token_claims", None)
    if claims is not None:
        await token_service.revoke_access_token(claims)
    try:
        await token_service.revoke_refresh_token(payload.refresh_token)
    finally:
        await _audit_log_service(request).append(
            event_type="logout",
            username=username,
            actor_username=username,
            outcome="success",
            ip_address=_request_ip(request),
        )


@router.post(
    "/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Logout all sessions for current user",
)
async def logout_all(request: Request) -> None:
    username = _subject_from_state(request)
    claims = getattr(request.state, "token_claims", None)
    if claims is not None:
        await _token_session_service(request).revoke_access_token(claims)
    await _invalidate_user_sessions(request, username)
    await _audit_log_service(request).append(
        event_type="logout_all",
        username=username,
        actor_username=username,
        outcome="success",
        ip_address=_request_ip(request),
    )


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a normal user account",
)
async def register(request: Request, payload: UserCreateRequest) -> UserResponse:
    try:
        user = await _user_service(request).create_user(
            username=payload.username,
            password=payload.password,
            role="user",
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(err)) from err
    await _audit_log_service(request).append(
        event_type="user_registered",
        username=user.username,
        actor_username=user.username,
        outcome="success",
        ip_address=_request_ip(request),
    )
    return UserResponse.model_validate(UserSummary.from_stored_user(user).model_dump())


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user profile",
)
async def me(request: Request) -> UserResponse:
    user = await _user_service(request).get_user(_subject_from_state(request))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return UserResponse.model_validate(UserSummary.from_stored_user(user).model_dump())


@router.get(
    "/users",
    response_model=list[UserResponse],
    summary="List users",
)
async def list_users(request: Request) -> list[UserResponse]:
    _require_admin(request)
    users = await _user_service(request).list_users()
    return [UserResponse.model_validate(UserSummary.from_stored_user(user).model_dump()) for user in users]


@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create user",
)
async def create_user(request: Request, payload: UserCreateRequest) -> UserResponse:
    _require_admin(request)
    try:
        user = await _user_service(request).create_user(
            username=payload.username,
            password=payload.password,
            role=payload.role,
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(err)) from err
    await _audit_log_service(request).append(
        event_type="user_created",
        username=user.username,
        actor_username=_subject_from_state(request),
        outcome="success",
        ip_address=_request_ip(request),
        details={"role": user.role},
    )
    return UserResponse.model_validate(UserSummary.from_stored_user(user).model_dump())


@router.patch(
    "/users/{username}",
    response_model=UserResponse,
    summary="Update user",
)
async def update_user(request: Request, username: str, payload: UserUpdateRequest) -> UserResponse:
    _require_admin(request)
    try:
        user = await _user_service(request).update_user(
            username,
            password=payload.password,
            role=payload.role,
            is_active=payload.is_active,
        )
    except LookupError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err
    if payload.password is not None or payload.is_active is False:
        await _invalidate_user_sessions(request, username)
        user = await _user_service(request).get_user(username) or user
    event_type = "user_updated"
    if payload.is_active is False:
        event_type = "user_disabled"
    elif payload.password is not None:
        event_type = "user_password_reset"
    await _audit_log_service(request).append(
        event_type=event_type,
        username=username,
        actor_username=_subject_from_state(request),
        outcome="success",
        ip_address=_request_ip(request),
        details={
            "role": user.role,
            "is_active": user.is_active,
            "password_changed": payload.password is not None,
        },
    )
    return UserResponse.model_validate(UserSummary.from_stored_user(user).model_dump())


@router.post(
    "/users/bulk-delete",
    response_model=UserBulkDeleteResponse,
    summary="Delete multiple users",
)
async def bulk_delete_users(request: Request, payload: UserBulkDeleteRequest) -> UserBulkDeleteResponse:
    _require_admin(request)
    current_admin = _subject_from_state(request)
    normalized_usernames = list(dict.fromkeys(username.strip() for username in payload.usernames if username.strip()))
    if current_admin in normalized_usernames:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete the current admin user")

    deleted_usernames: list[str] = []
    not_found_usernames: list[str] = []

    service = _user_service(request)
    token_service = _token_session_service(request)
    for username in normalized_usernames:
        try:
            await token_service.revoke_all_user_sessions(username)
            await service.delete_user(username)
            deleted_usernames.append(username)
        except LookupError:
            not_found_usernames.append(username)

    await _audit_log_service(request).append(
        event_type="users_bulk_deleted",
        actor_username=current_admin,
        outcome="success",
        ip_address=_request_ip(request),
        details={
            "deleted_usernames": deleted_usernames,
            "not_found_usernames": not_found_usernames,
        },
    )

    return UserBulkDeleteResponse(
        deleted_usernames=deleted_usernames,
        not_found_usernames=not_found_usernames,
    )


@router.delete(
    "/users/{username}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete user",
)
async def delete_user(request: Request, username: str) -> None:
    _require_admin(request)
    if username == _subject_from_state(request):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete the current admin user")
    try:
        await _token_session_service(request).revoke_all_user_sessions(username)
        await _user_service(request).delete_user(username)
    except LookupError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err
    await _audit_log_service(request).append(
        event_type="user_deleted",
        username=username,
        actor_username=_subject_from_state(request),
        outcome="success",
        ip_address=_request_ip(request),
    )


@router.post(
    "/me/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change current user password",
)
async def change_password(request: Request, payload: PasswordChangeRequest) -> None:
    service = _user_service(request)
    user = await service.get_user(_subject_from_state(request))
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect")
    await service.update_user(user.username, password=payload.new_password)
    await _invalidate_user_sessions(request, user.username)
    await _audit_log_service(request).append(
        event_type="password_changed",
        username=user.username,
        actor_username=user.username,
        outcome="success",
        ip_address=_request_ip(request),
    )


@router.get(
    "/audit-logs",
    response_model=list[AuditLogResponse],
    summary="List authentication audit logs",
)
async def list_audit_logs(
    request: Request,
    limit: int = 100,
    username: str | None = None,
) -> list[AuditLogResponse]:
    _require_admin(request)
    entries = await _audit_log_service(request).list_entries(limit=limit, username=username)
    return [AuditLogResponse.model_validate(entry.model_dump()) for entry in entries]
