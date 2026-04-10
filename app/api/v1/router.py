from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import auth, chat, docs, feedback, ops, qa

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(qa.router, prefix="/chat", tags=["qa"])
api_router.include_router(docs.router, prefix="/docs", tags=["docs"])
api_router.include_router(feedback.router, prefix="/feedback", tags=["feedback"])
api_router.include_router(ops.router, prefix="/ops", tags=["ops"])
