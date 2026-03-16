from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.redis_client import require_redis
from app.core.security import oauth2_scheme
from app.models.feedback_schemas import FeedbackRequest, FeedbackResponse
from app.services.feedback_service import FeedbackService

router = APIRouter()
logger = logging.getLogger(__name__)


def _subject_from_state(request: Request) -> str:
    sub = getattr(request.state, "subject", None)
    if isinstance(sub, str) and sub:
        return sub
    return "unknown"


@router.post(
    "",
    response_model=FeedbackResponse,
    summary="Store explicit user feedback for a QA turn",
    dependencies=[Depends(oauth2_scheme)],
)
async def submit_feedback(request: Request, payload: FeedbackRequest) -> FeedbackResponse:
    redis = await require_redis(getattr(request.app.state, "redis", None))
    service = FeedbackService(redis=redis)
    subject = _subject_from_state(request)
    try:
        feedback_id = await service.submit(payload, subject=subject)
    except Exception as exc:  # pragma: no cover - defensive wrapper
        logger.exception("Feedback submit failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"feedback 存储失败: {exc.__class__.__name__}: {exc}",
        ) from exc

    return FeedbackResponse(feedback_id=feedback_id, stored=True)
