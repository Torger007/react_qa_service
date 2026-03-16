from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.core.config import settings
from app.core.security import create_access_token
from app.models.request import LoginRequest
from app.models.response import TokenResponse

router = APIRouter()


class TokenRequest(BaseModel):
    """OAuth2PasswordBearer expects a token endpoint; we accept JSON for simplicity."""

    username: str
    password: str


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login (demo) and get JWT",
)
async def login(payload: LoginRequest) -> TokenResponse:
    if payload.username != settings.demo_username or payload.password != settings.demo_password:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return TokenResponse(access_token=create_access_token(subject=payload.username))


@router.post(
    "/token",
    response_model=TokenResponse,
    summary="OAuth2 compatible token endpoint (JSON)",
)
async def token(payload: TokenRequest) -> TokenResponse:
    if payload.username != settings.demo_username or payload.password != settings.demo_password:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return TokenResponse(access_token=create_access_token(subject=payload.username))
