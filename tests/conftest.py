from __future__ import annotations

from collections import defaultdict

import pytest
from fastapi.testclient import TestClient


class FakeRedis:
    """
    Async Redis minimal subset used by the service.

    This keeps unit tests hermetic (no external Redis required).
    """

    def __init__(self) -> None:
        self._kv: dict[str, str] = {}
        self._lists: dict[str, list[str]] = defaultdict(list)
        self._counters: dict[str, int] = defaultdict(int)
        self._sets: dict[str, set[str]] = defaultdict(set)
        self._expirations: dict[str, int] = {}

    async def ping(self) -> bool:  # pragma: no cover
        return True

    async def aclose(self) -> None:  # pragma: no cover
        return None

    async def get(self, key: str) -> str | None:
        return self._kv.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self._kv[key] = value
        if ex is not None:
            self._expirations[key] = ex
        return True

    async def delete(self, key: str) -> int:
        n = 0
        if key in self._kv:
            del self._kv[key]
            n += 1
        if key in self._lists:
            del self._lists[key]
            n += 1
        if key in self._counters:
            del self._counters[key]
            n += 1
        if key in self._sets:
            del self._sets[key]
            n += 1
        return n

    async def incr(self, key: str) -> int:
        self._counters[key] += 1
        return self._counters[key]

    async def expire(self, key: str, seconds: int) -> bool:  # noqa: ARG002
        self._expirations[key] = seconds
        return True

    async def lpush(self, key: str, value: str) -> int:
        self._lists[key].insert(0, value)
        return len(self._lists[key])

    async def ltrim(self, key: str, start: int, stop: int) -> bool:
        self._lists[key] = self._lists[key][start : stop + 1]
        return True

    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        items = self._lists.get(key, [])
        return items[start : stop + 1]

    async def sadd(self, key: str, *values: str) -> int:
        before = len(self._sets[key])
        self._sets[key].update(values)
        return len(self._sets[key]) - before

    async def smembers(self, key: str) -> set[str]:
        return set(self._sets.get(key, set()))

    def pipeline(self, transaction: bool = False):  # noqa: ARG002
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, tuple[object, ...]]] = []

    def set(self, *args: object, **kwargs: object):
        self._ops.append(("set", args + (kwargs.get("ex"),)))
        return self

    def sadd(self, *args: object):
        self._ops.append(("sadd", args))
        return self

    def expire(self, *args: object):
        self._ops.append(("expire", args))
        return self

    async def execute(self) -> list[object]:
        results: list[object] = []
        for op, args in self._ops:
            method = getattr(self._redis, op)
            results.append(await method(*args))
        self._ops.clear()
        return results


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    # Patch Redis lifecycle to avoid real network access.
    from app.core import redis_client
    from app.core.config import settings
    from app.models.qa_schemas import AgentTraceResponse
    from app.services.document_agent_service import DocumentAgentResult
    from app.main import create_app

    fake = FakeRedis()

    async def _create_redis():
        return fake

    async def _close_redis(_r):
        return None

    class FakeEmbeddingsClient:
        async def embed(self, texts):
            return [[float(len(text or "")), 1.0] for text in texts]

    class FakeChatClient:
        async def chat(self, *, messages):  # noqa: ARG002
            return "stub chat response"

    class DummyAgentService:
        async def answer(self, *, subject, session_id, message, top_k, doc_filters):  # noqa: ARG002
            from app.models.schemas import ChatMessage

            return DocumentAgentResult(
                answer=f"dummy answer for {message}",
                history=[
                    ChatMessage(role="user", content=message),
                    ChatMessage(role="assistant", content=f"dummy answer for {message}"),
                ],
                citations=[],
                agent=AgentTraceResponse(
                    run_id="dummy-run",
                    status="response",
                    task_type="qa",
                    steps=[],
                    tool_calls=[],
                ),
            )

    def _build_document_agent_service(*, redis, llm_client, embeddings_client, vector_store):  # noqa: ARG001
        return DummyAgentService()

    monkeypatch.setattr(redis_client, "create_redis", _create_redis)
    monkeypatch.setattr(redis_client, "close_redis", _close_redis)
    monkeypatch.setattr(settings, "rate_limit_rps", 10_000)
    monkeypatch.setattr("app.main.OpenAIChatClient", FakeChatClient)
    monkeypatch.setattr("app.main.OpenAIEmbeddingsClient", FakeEmbeddingsClient)
    monkeypatch.setattr("app.main.build_document_agent_service", _build_document_agent_service)

    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def admin_token() -> str:
    from app.core.config import settings
    from app.core.security import create_access_token

    return create_access_token(subject=settings.demo_username, role="admin")


@pytest.fixture()
def user_token() -> str:
    # A non-admin subject (cannot pass sensitive action authorization).
    from app.core.security import create_access_token

    return create_access_token(subject="user", role="user")
