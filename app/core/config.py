from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ReAct QA Service"
    api_v1_prefix: str = "/api/v1"
    deployment_env: str = "development"

    # Security
    jwt_secret: str = Field(default="change-me", repr=False)
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 60 * 60

    # Simple demo user (replace with real IAM)
    demo_username: str = "admin"
    demo_password: str = Field(default="admin", repr=False)

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    redis_prefix: str = "react-qa"
    session_ttl_seconds: int = 60 * 60 * 24
    feedback_ttl_seconds: int = 60 * 60 * 24 * 30
    max_rounds: int = 10

    # LLM / RAG
    openai_api_key: str = Field(default="", repr=False)
    openai_base_url: str | None = None
    llm_model: str = "qwen3.5-plus"
    llm_temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    llm_timeout_seconds: int = Field(default=30, ge=5, le=300)
    summary_timeout_seconds: int = Field(default=90, ge=10, le=600)
    summary_max_parallelism: int = Field(default=4, ge=1, le=8)
    summary_single_pass_chars: int = Field(default=12000, ge=2000, le=40000)
    summary_max_chunks: int = Field(default=16, ge=4, le=64)
    summary_group_size: int = Field(default=4, ge=1, le=16)
    embedding_model: str = "text-embedding-v4"
    embedding_batch_size: int = Field(default=10, ge=1, le=128)
    max_upload_file_bytes: int = 5 * 1024 * 1024
    rerank_enabled: bool = True
    multi_query_enabled: bool = True
    multi_query_count: int = Field(default=3, ge=1, le=6)
    retrieval_candidate_multiplier: int = Field(default=3, ge=1, le=8)

    # Frontend / CORS
    cors_allow_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )

    # Rate limit
    rate_limit_rps: int = 5

    # Sensitive operation confirmation
    confirm_ttl_seconds: int = 60


settings = Settings()
