from __future__ import annotations

import pytest

from app.core.vector_store import ScoredChunk
from app.core.vector_types import DocumentChunk
from app.services.reranker import Reranker


class FakeRerankLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    async def chat(self, *, messages, model=None, timeout_seconds=None) -> str:  # noqa: ARG002
        self.calls += 1
        return self.response


def _chunk(doc_id: str, chunk_id: str, text: str, score: float, *, section_title: str = "") -> ScoredChunk:
    return ScoredChunk(
        chunk=DocumentChunk(
            doc_id=doc_id,
            chunk_id=chunk_id,
            text=text,
            metadata={"section_title": section_title, "order": 0},
        ),
        score=score,
    )


@pytest.mark.anyio
async def test_reranker_uses_llm_order_when_available():
    reranker = Reranker(llm=FakeRerankLLM('{"ranked_ids":["C2","C1"]}'))
    chunks = [
        _chunk("doc-a", "a-1", "合同背景与项目概况。", 0.95, section_title="背景"),
        _chunk("doc-a", "a-2", "本节明确列出付款风险、违约责任和追责路径。", 0.60, section_title="风险"),
    ]

    ranked = await reranker.rerank(query="合同有哪些风险", chunks=chunks, top_k=2)

    assert [item.chunk.chunk_id for item in ranked] == ["a-2", "a-1"]


@pytest.mark.anyio
async def test_reranker_falls_back_when_llm_output_is_invalid():
    reranker = Reranker(llm=FakeRerankLLM("not-json"))
    chunks = [
        _chunk("doc-a", "a-1", "合同风险包括付款风险和履约风险。", 0.70, section_title="风险"),
        _chunk("doc-a", "a-2", "项目背景与范围说明。", 0.90, section_title="背景"),
    ]

    ranked = await reranker.rerank(query="合同风险", chunks=chunks, top_k=2)

    assert ranked[0].chunk.chunk_id == "a-1"
