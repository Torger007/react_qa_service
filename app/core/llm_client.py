from __future__ import annotations

from typing import Any, Iterable

from openai import OpenAI

from app.core.config import settings


class LLMClient:
    """
    Thin abstraction over chat-completion style LLMs.
    """

    async def chat(self, *, messages: list[dict[str, Any]]) -> str:  # pragma: no cover - interface
        raise NotImplementedError


class OpenAIChatClient(LLMClient):
    """
    OpenAI-compatible chat client using the official `openai` SDK.
    """

    def __init__(self) -> None:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        self._client = OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
        )

    async def chat(self, *, messages: list[dict[str, Any]]) -> str:
        # OpenAI SDK is synchronous; run in thread to avoid blocking event loop.
        import anyio

        def _call() -> str:
            resp = self._client.chat.completions.create(
                model=settings.llm_model,
                messages=messages,
                temperature=settings.llm_temperature,
            )
            content = resp.choices[0].message.content
            return content or ""

        return await anyio.to_thread.run_sync(_call)


class EmbeddingsClient:
    """
    Simple embedding client interface.
    """

    async def embed(self, texts: Iterable[str]) -> list[list[float]]:  # pragma: no cover - interface
        raise NotImplementedError


class OpenAIEmbeddingsClient(EmbeddingsClient):
    """
    OpenAI embeddings client using the embeddings API.
    """

    def __init__(self) -> None:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        self._client = OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
        )

    async def embed(self, texts: Iterable[str]) -> list[list[float]]:
        import anyio

        texts_list = list(texts)
        if not texts_list:
            return []
        batch_size = settings.embedding_batch_size
        if batch_size <= 0:
            raise RuntimeError("EMBEDDING_BATCH_SIZE must be positive")

        def _call() -> list[list[float]]:
            out: list[list[float]] = []
            for start in range(0, len(texts_list), batch_size):
                batch = texts_list[start : start + batch_size]
                resp = self._client.embeddings.create(
                    model=settings.embedding_model,
                    input=batch,
                )
                out.extend([item.embedding for item in resp.data])
            return out

        return await anyio.to_thread.run_sync(_call)

