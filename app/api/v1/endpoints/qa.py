from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.redis_client import require_redis
from app.core.security import oauth2_scheme
from app.models.qa_schemas import QARequest, QAResponse
from app.services.document_agent_service import DocumentAgentService
from app.services.session_manager import new_session_id

router = APIRouter()
logger = logging.getLogger(__name__)


def _subject_from_state(request: Request) -> str:
    sub = getattr(request.state, "subject", None)
    if isinstance(sub, str) and sub:
        return sub
    return "unknown"


@router.post(
    "/qa",
    response_model=QAResponse,
    summary="Document QA with RAG-style agent",
    dependencies=[Depends(oauth2_scheme)],
)
async def qa(request: Request, payload: QARequest) -> QAResponse:
    """
    Entry point for the document QA agent.

    This initial implementation only wires request/response and session handling.
    The RAG / retrieval pipeline will be implemented in later steps.
    """
    redis = await require_redis(getattr(request.app.state, "redis", None))
    _ = redis  # ensure Redis is initialized (used by QAService for history)

    agent_service: DocumentAgentService = request.app.state.document_agent_service
    session_id: UUID = payload.session_id or new_session_id()
    subject = _subject_from_state(request)
    try:
        result = await agent_service.answer(
            subject=subject,
            session_id=session_id,
            message=payload.message,
            top_k=payload.top_k,
            doc_filters=payload.doc_filters,
        )
    except Exception as exc:
        logger.exception("QA request failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"agent 执行失败: {exc.__class__.__name__}: {exc}",
        ) from exc

    return QAResponse(
        session_id=session_id,
        answer=result.answer,
        history=result.history,
        citations=result.citations,
        agent=result.agent,
    )
