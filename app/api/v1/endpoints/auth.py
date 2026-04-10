from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.core.security import create_access_token
from app.models.request import LoginRequest, PasswordChangeRequest, UserCreateRequest, UserUpdateRequest
from app.models.response import TokenResponse, UserResponse
from app.services.user_service import UserService, UserSummary, verify_password

router = APIRouter()


class TokenRequest(BaseModel):
    """OAuth2PasswordBearer expects a token endpoint; we accept JSON for simplicity."""

    username: str
    password: str


def _user_service(request: Request) -> UserService:
    return request.app.state.user_service


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


async def _issue_token(user_service: UserService, username: str, password: str) -> TokenResponse:
    user = await user_service.authenticate(username, password)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return TokenResponse(access_token=create_access_token(subject=user.username, role=user.role))


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login and get JWT",
)
async def login(request: Request, payload: LoginRequest) -> TokenResponse:
    return await _issue_token(_user_service(request), payload.username, payload.password)


@router.post(
    "/token",
    response_model=TokenResponse,
    summary="OAuth2 compatible token endpoint (JSON)",
)
async def token(request: Request, payload: TokenRequest) -> TokenResponse:
    return await _issue_token(_user_service(request), payload.username, payload.password)


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
    return UserResponse.model_validate(UserSummary.from_stored_user(user).model_dump())


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
