from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.redis_client import close_redis, create_redis
from app.middleware.auth import AuthMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.validation import RequestValidationMiddleware
from app.services.chat_service import ChatService
from app.core.llm_client import OpenAIChatClient, OpenAIEmbeddingsClient
from app.core.vector_store import RedisVectorStore
from app.services.document_agent_service import DocumentAgentService
from app.services.qa_service import QAService
from app.services.react_engine import ReActEngine

logger = logging.getLogger(__name__)


def build_document_agent_service(*, redis, llm_client, embeddings_client, vector_store) -> DocumentAgentService:
    return DocumentAgentService(
        redis=redis,
        llm=llm_client,
        embeddings=embeddings_client,
        vector_store=vector_store,
    )


def _startup_config_warnings() -> list[str]:
    warnings: list[str] = []
    if settings.jwt_secret == "change-me":
        warnings.append("jwt_secret is still using the default placeholder value.")
    if settings.demo_username == "admin" and settings.demo_password == "admin":
        warnings.append("demo credentials are still using admin/admin.")
    if not settings.openai_api_key:
        warnings.append("openai_api_key is empty; LLM-backed routes will fail.")
    return warnings


def _validate_runtime_configuration() -> None:
    warnings = _startup_config_warnings()
    is_production = settings.deployment_env.lower() == "production"
    if is_production and warnings:
        raise RuntimeError(
            "Unsafe production configuration detected: " + "; ".join(warnings)
        )
    for warning in warnings:
        logger.warning("Startup configuration warning: %s", warning)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _validate_runtime_configuration()
    app.state.redis = await create_redis()
    app.state.chat_service = ChatService(react_engine=ReActEngine())
    # RAG QA components
    redis = app.state.redis
    app.state.llm_client = OpenAIChatClient()
    app.state.embeddings_client = OpenAIEmbeddingsClient()
    app.state.vector_store = RedisVectorStore(redis=redis)
    app.state.qa_service = QAService(
        redis=redis,
        llm=app.state.llm_client,
        embeddings=app.state.embeddings_client,
        vector_store=app.state.vector_store,
    )
    app.state.document_agent_service = build_document_agent_service(
        redis=redis,
        llm_client=app.state.llm_client,
        embeddings_client=app.state.embeddings_client,
        vector_store=app.state.vector_store,
    )
    try:
        yield
    finally:
        await close_redis(app.state.redis)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        openapi_url="/openapi.json",
        docs_url="/swagger",
        redoc_url=None,
        lifespan=lifespan,
    )

    app.add_middleware(RequestValidationMiddleware)
    app.add_middleware(
        AuthMiddleware,
        public_paths={
            "/swagger",
            "/openapi.json",
            f"{settings.api_v1_prefix}/auth/login",
            f"{settings.api_v1_prefix}/auth/token",
        },
    )
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
