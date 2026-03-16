from __future__ import annotations

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


def build_document_agent_service(*, redis, llm_client, embeddings_client, vector_store) -> DocumentAgentService:
    return DocumentAgentService(
        redis=redis,
        llm=llm_client,
        embeddings=embeddings_client,
        vector_store=vector_store,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
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
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
