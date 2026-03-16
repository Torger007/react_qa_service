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
    max_rounds: int = 10

    # LLM / RAG
    openai_api_key: str = Field(default="", repr=False)
    openai_base_url: str | None = None
    llm_model: str = "qwen3.5-plus"
    llm_temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    embedding_model: str = "text-embedding-v4"
    embedding_batch_size: int = Field(default=10, ge=1, le=128)
    max_upload_file_bytes: int = 5 * 1024 * 1024
    rerank_enabled: bool = True
    multi_query_enabled: bool = True
    multi_query_count: int = Field(default=3, ge=1, le=6)
    retrieval_candidate_multiplier: int = Field(default=3, ge=1, le=8)

    # Rate limit
    rate_limit_rps: int = 5

    # Sensitive operation confirmation
    confirm_ttl_seconds: int = 60


settings = Settings()
