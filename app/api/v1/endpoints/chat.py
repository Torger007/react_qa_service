from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.core.redis_client import require_redis
from app.core.security import oauth2_scheme
from app.models.request import ChatRequest
from app.models.response import ChatResponse, SessionDetailResponse, SessionSummaryResponse
from app.services.chat_service import ChatService, _is_sensitive_action
from app.services.session_manager import (
    delete_session,
    ensure_session_owner,
    get_history,
    get_session_metadata,
    list_sessions,
    new_session_id,
)

router = APIRouter()


def _subject_from_state(request: Request) -> str:
    sub = getattr(request.state, "subject", None)
    if isinstance(sub, str) and sub:
        return sub
    return "unknown"


def _is_admin_from_state(request: Request) -> bool:
    role = getattr(request.state, "role", None)
    return isinstance(role, str) and role.strip().lower() == "admin"


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
    try:
        await ensure_session_owner(redis, subject=subject, session_id=session_id, title_seed=payload.message)
    except PermissionError as err:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(err)) from err

    if (
        payload.action
        and _is_sensitive_action(payload.action)
        and not _is_admin_from_state(request)
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


@router.get(
    "/sessions",
    response_model=list[SessionSummaryResponse],
    summary="List current user's chat sessions",
    dependencies=[Depends(oauth2_scheme)],
)
async def get_sessions(request: Request) -> list[SessionSummaryResponse]:
    redis = await require_redis(getattr(request.app.state, "redis", None))
    subject = _subject_from_state(request)
    sessions = await list_sessions(redis, subject)
    return [
        SessionSummaryResponse(
            session_id=item.session_id,
            title=item.title,
            created_at=item.created_at.isoformat(),
            updated_at=item.updated_at.isoformat(),
            last_message_preview=item.last_message_preview,
            message_count=item.message_count,
        )
        for item in sessions
    ]


@router.get(
    "/sessions/{session_id}",
    response_model=SessionDetailResponse,
    summary="Get current user's chat session detail",
    dependencies=[Depends(oauth2_scheme)],
)
async def get_session_detail(request: Request, session_id: UUID) -> SessionDetailResponse:
    redis = await require_redis(getattr(request.app.state, "redis", None))
    subject = _subject_from_state(request)
    metadata = await get_session_metadata(redis, session_id)
    if metadata is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if metadata.owner_username != subject:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Session does not belong to the current user")
    history = await get_history(redis, session_id)
    return SessionDetailResponse(
        session_id=metadata.session_id,
        title=metadata.title,
        created_at=metadata.created_at.isoformat(),
        updated_at=metadata.updated_at.isoformat(),
        last_message_preview=metadata.last_message_preview,
        message_count=metadata.message_count,
        history=history,
    )


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete current user's chat session",
    dependencies=[Depends(oauth2_scheme)],
)
async def remove_session(request: Request, session_id: UUID) -> None:
    redis = await require_redis(getattr(request.app.state, "redis", None))
    subject = _subject_from_state(request)
    try:
        await delete_session(redis, subject=subject, session_id=session_id)
    except LookupError as err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err)) from err
    except PermissionError as err:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(err)) from err
