from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.redis_client import require_redis
from app.core.security import oauth2_scheme
from app.models.request import ChatRequest
from app.models.response import ChatResponse
from app.services.chat_service import ChatService, _is_sensitive_action
from app.services.session_manager import new_session_id

router = APIRouter()


def _subject_from_state(request: Request) -> str:
    sub = getattr(request.state, "subject", None)
    if isinstance(sub, str) and sub:
        return sub
    return "unknown"


@router.post(
    "/",
    response_model=ChatResponse,
    summary="Chat with ReAct QA service",
    dependencies=[Depends(oauth2_scheme)],
)
async def chat(request: Request, payload: ChatRequest) -> JSONResponse | ChatResponse:
    redis = await require_redis(getattr(request.app.state, "redis", None))
    chat_service: ChatService = request.app.state.chat_service

    session_id: UUID = payload.session_id or new_session_id()
    subject = _subject_from_state(request)

    if (
        payload.action
        and _is_sensitive_action(payload.action)
        and subject != settings.demo_username
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions",
        )

    outcome = await chat_service.chat(
        redis=redis,
        subject=subject,
        session_id=session_id,
        message=payload.message,
        action=payload.action,
        action_input=payload.action_input,
        confirm_token=payload.confirm_token,
    )

    if outcome.confirm_challenge is not None:
        return JSONResponse(status_code=202, content=outcome.confirm_challenge.model_dump())

    return ChatResponse(session_id=session_id, answer=outcome.answer, history=outcome.history)
