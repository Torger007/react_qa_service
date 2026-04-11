from __future__ import annotations

import pytest

from app.services.query_rewrite_service import QueryRewriteService


class FakeRewriteLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    async def chat(self, *, messages, model=None, timeout_seconds=None) -> str:  # noqa: ARG002
        self.calls += 1
        return self.response


@pytest.mark.anyio
async def test_query_rewrite_uses_llm_queries_when_available():
    service = QueryRewriteService(
        llm=FakeRewriteLLM('{"queries":["合同付款风险","合同履约违约责任"]}'),
        max_queries=3,
    )

    queries = await service.rewrite(question="请帮我总结合同风险")

    assert queries == [
        "请帮我总结合同风险",
        "合同付款风险",
        "合同履约违约责任",
    ]


@pytest.mark.anyio
async def test_query_rewrite_falls_back_when_llm_output_is_invalid():
    service = QueryRewriteService(
        llm=FakeRewriteLLM("not-json"),
        max_queries=3,
    )

    queries = await service.rewrite(question="请帮我总结合同风险")

    assert queries[0] == "请帮我总结合同风险"
    assert "总结合同风险" in queries
