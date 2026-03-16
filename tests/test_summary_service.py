from __future__ import annotations

import pytest

from app.core.vector_types import DocumentChunk
from app.services.summary_service import SummaryService


@pytest.mark.anyio
async def test_summary_service_normalizes_output_into_fixed_sections():
    service = SummaryService(llm=None)
    chunks = [
        DocumentChunk(doc_id="doc-1", chunk_id="doc-1-0", text="项目目标是优化检索质量。", metadata={}),
        DocumentChunk(doc_id="doc-1", chunk_id="doc-1-1", text="当前存在风险：上下文重复。", metadata={}),
    ]

    result = await service.summarize(question="请总结全文", chunks=chunks)

    assert "一、文档概览" in result.answer
    assert "二、关键要点" in result.answer
    assert "三、风险与问题" in result.answer
    assert "四、下一步建议" in result.answer
