from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError

from app.core.config import settings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")


@dataclass(frozen=True)
class AccessTokenClaims:
    subject: str
    role: str = "user"
    token_type: str = "access"
    jti: str = ""
    exp: int = 0
    token_version: int = 0

    @property
    def is_admin(self) -> bool:
        return self.role.strip().lower() == "admin"


def create_access_token(subject: str, role: str = "user", *, token_version: int = 0) -> str:
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": subject,
        "role": role,
        "type": "access",
        "jti": uuid4().hex,
        "token_version": token_version,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.access_token_ttl_seconds)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(subject: str, role: str = "user", *, token_version: int = 0) -> str:
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": subject,
        "role": role,
        "type": "refresh",
        "jti": uuid4().hex,
        "token_version": token_version,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.refresh_token_ttl_seconds)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _decode_token(token: str, *, expected_type: str) -> AccessTokenClaims:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except ExpiredSignatureError as err:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from err
    except JWTError as err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from err
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token subject",
            headers={"WWW-Authenticate": "Bearer"},
        )
    role = payload.get("role", "user")
    if not isinstance(role, str) or not role:
        role = "user"
    token_type = payload.get("type", "access")
    if token_type != expected_type:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
            headers={"WWW-Authenticate": "Bearer"},
        )
    jti = payload.get("jti")
    if not isinstance(jti, str) or not jti:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token id",
            headers={"WWW-Authenticate": "Bearer"},
        )
    exp = payload.get("exp")
    token_version = payload.get("token_version", 0)
    return AccessTokenClaims(
        subject=sub,
        role=role,
        token_type=token_type,
        jti=jti,
        exp=int(exp) if isinstance(exp, (int, float)) else 0,
        token_version=int(token_version) if isinstance(token_version, (int, float)) else 0,
    )


def decode_access_token(token: str) -> AccessTokenClaims:
    return _decode_token(token, expected_type="access")


def decode_refresh_token(token: str) -> AccessTokenClaims:
    return _decode_token(token, expected_type="refresh")


def decode_subject(token: str) -> str:
    return decode_access_token(token).subject


async def get_current_subject(token: Annotated[str, Depends(oauth2_scheme)]) -> str:
    return decode_subject(token)
