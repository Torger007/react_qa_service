from __future__ import annotations

import anyio
import pytest

from app.core.vector_types import DocumentChunk
from app.services.summary_service import SummaryService


@pytest.mark.anyio
async def test_summary_service_normalizes_output_into_fixed_sections():
    service = SummaryService(llm=None)
    chunks = [
        DocumentChunk(doc_id="doc-1", chunk_id="doc-1-0", text="Document goal is to improve retrieval quality.", metadata={}),
        DocumentChunk(doc_id="doc-1", chunk_id="doc-1-1", text="Current risk is duplicated context across chunks.", metadata={}),
    ]

    result = await service.summarize(question="Please summarize the document", chunks=chunks)

    assert "一、文档概览" in result.answer
    assert "二、关键要点" in result.answer
    assert "三、风险与问题" in result.answer
    assert "四、下一步建议" in result.answer


class SlowSummaryLLM:
    async def chat(self, *, messages, model=None, timeout_seconds=None):  # noqa: ARG002
        await anyio.sleep(60)
        return "should timeout"


class RecordingSummaryLLM:
    def __init__(self) -> None:
        self.calls = 0
        self.timeouts: list[int | None] = []

    async def chat(self, *, messages, model=None, timeout_seconds=None):  # noqa: ARG002
        self.calls += 1
        self.timeouts.append(timeout_seconds)
        return (
            "一、文档概览\n"
            "这是单次 LLM 总结。\n\n"
            "二、关键要点\n"
            "- 关键点 A\n\n"
            "三、风险与问题\n"
            "- 风险点 A\n\n"
            "四、下一步建议\n"
            "- 建议 A"
        )


@pytest.mark.anyio
async def test_summary_service_falls_back_when_llm_times_out(monkeypatch):
    monkeypatch.setattr("app.services.summary_service.settings.summary_timeout_seconds", 1)
    service = SummaryService(llm=SlowSummaryLLM())
    chunks = [
        DocumentChunk(doc_id="doc-1", chunk_id="doc-1-0", text="Document goal is to improve retrieval quality.", metadata={}),
    ]

    result = await service.summarize(question="Please summarize the document", chunks=chunks)

    assert "一、文档概览" in result.answer


@pytest.mark.anyio
async def test_summary_service_uses_single_pass_llm_for_small_documents(monkeypatch):
    monkeypatch.setattr("app.services.summary_service.settings.summary_single_pass_chars", 20000)
    llm = RecordingSummaryLLM()
    service = SummaryService(llm=llm)
    chunks = [
        DocumentChunk(doc_id="doc-1", chunk_id="doc-1-0", text="Short content for direct summary.", metadata={}),
        DocumentChunk(doc_id="doc-1", chunk_id="doc-1-1", text="Another short content block.", metadata={}),
    ]

    result = await service.summarize(question="Please summarize the document", chunks=chunks)

    assert llm.calls == 1
    assert llm.timeouts == [90]
    assert "这是单次 LLM 总结" in result.answer
