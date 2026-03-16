from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.core.vector_store import ScoredChunk
from app.core.vector_types import DocumentChunk
from app.models.qa_schemas import AgentTraceResponse, ToolCallTrace
from app.models.schemas import ChatMessage
from app.services.document_agent_service import (
    DocumentAgentResult,
    DocumentAgentService,
    PlannerDecision,
)
from app.services.query_rewrite_service import QueryRewriteService
from app.services.reranker import Reranker
from app.services.summary_service import SummaryResult
from tests.conftest import FakeRedis


class FakeEmbeddings:
    async def embed(self, texts):
        return [[float(len(text)), 1.0] for text in texts]


class FakePlanner:
    def __init__(self, decisions):
        self._decisions = list(decisions)
        self.calls = 0

    async def ainvoke(self, state):
        self.calls += 1
        if self._decisions:
            current = self._decisions.pop(0)
            if callable(current):
                return current(state)
            return current
        return PlannerDecision(action="respond", query=None, summary="直接回答。")


class FakeAnswerModel:
    def __init__(self, answer: str):
        self.answer = answer
        self.calls = 0

    async def ainvoke(self, state):
        self.calls += 1
        return self.answer


class FakeSummaryService:
    def __init__(self, answer: str = "这是总结结果。"):
        self.answer = answer
        self.calls = 0
        self.last_chunks: list[DocumentChunk] = []

    async def summarize(self, *, question, chunks):
        self.calls += 1
        self.last_chunks = list(chunks)
        return SummaryResult(answer=self.answer, partial_summaries=[f"摘要: {question}"])


class FakeVectorStore:
    def __init__(self, responses, listed_chunks=None):
        self._responses = list(responses)
        self._listed_chunks = listed_chunks or {}
        self.calls = 0

    async def similarity_search(self, *, query_vector, top_k, filters=None):  # noqa: ARG002
        self.calls += 1
        if self._responses:
            return self._responses.pop(0)
        return []

    async def list_chunks(self, *, doc_id=None, filters=None):  # noqa: ARG002
        if doc_id is None:
            return []
        return list(self._listed_chunks.get(doc_id, []))


class FixedQueryRewriteService(QueryRewriteService):
    def __init__(self, queries):
        self._queries = list(queries)

    async def rewrite(self, *, question: str) -> list[str]:  # noqa: ARG002
        return list(self._queries)


class IdentityReranker(Reranker):
    async def rerank(self, *, query: str, chunks, top_k: int):  # noqa: ARG002
        return list(chunks)[:top_k]


def _chunk(doc_id: str, text: str, score: float, *, order: int = 0) -> ScoredChunk:
    return ScoredChunk(
        chunk=DocumentChunk(
            doc_id=doc_id,
            chunk_id=f"{doc_id}-{order}",
            text=text,
            metadata={"title": doc_id, "order": order},
        ),
        score=score,
    )


def _doc_chunk(doc_id: str, text: str, *, order: int) -> DocumentChunk:
    return DocumentChunk(
        doc_id=doc_id,
        chunk_id=f"{doc_id}-{order}",
        text=text,
        metadata={"title": doc_id, "order": order, "section_title": "Overview"},
    )


@pytest.mark.anyio
async def test_document_agent_retrieves_then_answers():
    redis = FakeRedis()
    planner = FakePlanner(
        [
            PlannerDecision(action="retrieve_documents", query="合同风险", summary="先检索相关文档。"),
            PlannerDecision(action="respond", query=None, summary="已有足够依据，可以回答。"),
        ]
    )
    answer_model = FakeAnswerModel("这是基于检索结果的回答。")
    vector_store = FakeVectorStore([[_chunk("doc-1", "合同存在延期交付风险。", 0.91)]])
    service = DocumentAgentService(
        redis=redis,
        embeddings=FakeEmbeddings(),
        vector_store=vector_store,
        planner=planner,
        answer_model=answer_model,
        query_rewrite_service=FixedQueryRewriteService(["合同风险"]),
        reranker=IdentityReranker(),
    )

    result = await service.answer(
        subject="admin",
        session_id=uuid4(),
        message="请总结合同风险",
        top_k=4,
        doc_filters=None,
    )

    assert result.answer == "这是基于检索结果的回答。"
    assert len(result.citations) == 1
    assert result.agent.status == "response"
    assert result.agent.task_type == "qa"
    assert any(tool.name == "retrieve_documents" for tool in result.agent.tool_calls)
    assert len(result.history) == 2


@pytest.mark.anyio
async def test_document_agent_can_answer_without_retrieval():
    redis = FakeRedis()
    planner = FakePlanner([PlannerDecision(action="respond", query=None, summary="无需检索，直接回答。")])
    answer_model = FakeAnswerModel("这是直接回答。")
    service = DocumentAgentService(
        redis=redis,
        embeddings=FakeEmbeddings(),
        vector_store=FakeVectorStore([]),
        planner=planner,
        answer_model=answer_model,
        query_rewrite_service=FixedQueryRewriteService(["你好"]),
        reranker=IdentityReranker(),
    )

    result = await service.answer(
        subject="admin",
        session_id=uuid4(),
        message="你好",
        top_k=4,
        doc_filters=None,
    )

    assert result.answer == "这是直接回答。"
    assert result.citations == []
    assert result.agent.tool_calls == []
    assert result.agent.task_type == "qa"


@pytest.mark.anyio
async def test_document_agent_fills_missing_summary_from_planner():
    redis = FakeRedis()
    planner = FakePlanner([PlannerDecision(action="respond", query=None)])
    answer_model = FakeAnswerModel("这是直接回答。")
    service = DocumentAgentService(
        redis=redis,
        embeddings=FakeEmbeddings(),
        vector_store=FakeVectorStore([]),
        planner=planner,
        answer_model=answer_model,
        query_rewrite_service=FixedQueryRewriteService(["请直接回答"]),
        reranker=IdentityReranker(),
    )

    result = await service.answer(
        subject="admin",
        session_id=uuid4(),
        message="请直接回答",
        top_k=4,
        doc_filters=None,
    )

    assert result.answer == "这是直接回答。"
    assert any("可以直接生成回答" in step.summary for step in result.agent.steps)


@pytest.mark.anyio
async def test_document_agent_retries_once_when_no_results():
    redis = FakeRedis()

    def _decision(state):
        if state.get("loop_count", 0) < 2 and not state.get("retrieved_chunks"):
            return PlannerDecision(action="retrieve_documents", query="再次检索", summary="当前无结果，再检索一次。")
        return PlannerDecision(action="respond", query=None, summary="未检索到结果，给出降级回答。")

    planner = FakePlanner([_decision, _decision, _decision])
    answer_model = FakeAnswerModel("未检索到足够依据。")
    vector_store = FakeVectorStore([[], []])
    service = DocumentAgentService(
        redis=redis,
        embeddings=FakeEmbeddings(),
        vector_store=vector_store,
        planner=planner,
        answer_model=answer_model,
        query_rewrite_service=FixedQueryRewriteService(["再次检索"]),
        reranker=IdentityReranker(),
    )

    result = await service.answer(
        subject="admin",
        session_id=uuid4(),
        message="查询不存在的信息",
        top_k=4,
        doc_filters=None,
    )

    assert result.answer == "未检索到足够依据。"
    assert vector_store.calls == 2
    assert len(result.agent.tool_calls) == 2
    assert result.agent.status == "response"


@pytest.mark.anyio
async def test_document_agent_routes_summary_requests_to_summary_service():
    redis = FakeRedis()
    summary_service = FakeSummaryService(answer="一、文档概览\n这是 summary 结果。")
    listed_chunks = {
        "doc-1": [
            _doc_chunk("doc-1", "第一部分内容", order=0),
            _doc_chunk("doc-1", "第二部分内容", order=1),
        ]
    }
    vector_store = FakeVectorStore(
        [[_chunk("doc-1", "第一部分内容", 0.95, order=0)]],
        listed_chunks=listed_chunks,
    )
    service = DocumentAgentService(
        redis=redis,
        embeddings=FakeEmbeddings(),
        vector_store=vector_store,
        planner=FakePlanner([]),
        answer_model=FakeAnswerModel("不应走到这里"),
        summary_service=summary_service,
        query_rewrite_service=FixedQueryRewriteService(["请总结全文"]),
        reranker=IdentityReranker(),
    )

    result = await service.answer(
        subject="admin",
        session_id=uuid4(),
        message="请总结全文",
        top_k=4,
        doc_filters=None,
    )

    assert result.answer.startswith("一、文档概览")
    assert result.agent.task_type == "summary"
    assert result.agent.retrieval_summary
    assert result.agent.rerank_summary
    assert result.agent.summary_phase
    assert summary_service.calls == 1
    assert [chunk.chunk_id for chunk in summary_service.last_chunks] == ["doc-1-0", "doc-1-1"]
    assert [tool.name for tool in result.agent.tool_calls] == ["retrieve_documents", "rerank_results", "summarize_document"]


@pytest.mark.anyio
async def test_document_agent_merges_adjacent_retrieval_results():
    redis = FakeRedis()
    planner = FakePlanner(
        [
            PlannerDecision(action="retrieve_documents", query="query", summary="retrieve"),
            PlannerDecision(action="respond", query=None, summary="respond"),
        ]
    )
    vector_store = FakeVectorStore(
        [[
            _chunk("doc-1", "第一段", 0.95, order=0),
            _chunk("doc-1", "第二段", 0.93, order=1),
            _chunk("doc-2", "第三段", 0.80, order=0),
        ]]
    )
    service = DocumentAgentService(
        redis=redis,
        embeddings=FakeEmbeddings(),
        vector_store=vector_store,
        planner=planner,
        answer_model=FakeAnswerModel("ok"),
        query_rewrite_service=FixedQueryRewriteService(["问答"]),
        reranker=IdentityReranker(),
    )

    result = await service.answer(
        subject="admin",
        session_id=uuid4(),
        message="问答",
        top_k=4,
        doc_filters=None,
    )

    assert len(result.citations) == 2
    assert "第一段" in result.citations[0].snippet
    assert "第二段" in result.citations[0].snippet
    assert result.agent.retrieval_summary
    assert result.agent.rerank_summary


@pytest.mark.anyio
async def test_document_agent_uses_multi_query_and_rerank_tools():
    redis = FakeRedis()

    class ReverseReranker(Reranker):
        async def rerank(self, *, query: str, chunks, top_k: int):  # noqa: ARG002
            return list(sorted(chunks, key=lambda item: item.chunk.doc_id, reverse=True))[:top_k]

    vector_store = FakeVectorStore(
        [
            [_chunk("doc-a", "A内容", 0.70, order=0)],
            [_chunk("doc-b", "B内容", 0.65, order=0)],
        ]
    )
    service = DocumentAgentService(
        redis=redis,
        embeddings=FakeEmbeddings(),
        vector_store=vector_store,
        planner=FakePlanner(
            [
                PlannerDecision(action="retrieve_documents", query="合同风险", summary="retrieve"),
                PlannerDecision(action="respond", query=None, summary="respond"),
            ]
        ),
        answer_model=FakeAnswerModel("ok"),
        query_rewrite_service=FixedQueryRewriteService(["合同风险", "风险条款"]),
        reranker=ReverseReranker(),
    )

    result = await service.answer(
        subject="admin",
        session_id=uuid4(),
        message="合同风险",
        top_k=2,
        doc_filters=None,
    )

    tool_names = [tool.name for tool in result.agent.tool_calls]
    assert "rewrite_query" in tool_names
    assert "retrieve_documents" in tool_names
    assert "rerank_results" in tool_names
    assert result.agent.retrieval_summary
    assert result.agent.rerank_summary


def test_agent_trace_is_structured_without_chain_of_thought():
    trace = AgentTraceResponse(
        run_id="run-1",
        status="response",
        task_type="qa",
        retrieval_summary="summary",
        rerank_summary="rerank",
        summary_phase=None,
        rewritten_queries=["query-a"],
        steps=[],
        tool_calls=[
            ToolCallTrace(
                name="retrieve_documents",
                status="completed",
                input="query=test",
                output="检索到 1 条片段。",
                latency_ms=12,
            )
        ],
    )

    dumped = trace.model_dump()
    assert set(dumped) == {
        "run_id",
        "status",
        "task_type",
        "retrieval_summary",
        "rerank_summary",
        "summary_phase",
        "rewritten_queries",
        "steps",
        "tool_calls",
    }
    assert "chain_of_thought" not in dumped


def test_qa_endpoint_returns_agent_trace(client, admin_token):
    class EndpointAgentService:
        async def answer(self, *, subject, session_id, message, top_k, doc_filters):  # noqa: ARG002
            return DocumentAgentResult(
                answer="接口回答",
                history=[
                    ChatMessage(role="user", content=message),
                    ChatMessage(role="assistant", content="接口回答"),
                ],
                citations=[],
                agent=AgentTraceResponse(
                    run_id="run-endpoint",
                    status="response",
                    task_type="qa",
                    retrieval_summary="原始召回 1 条片段，整理后保留 1 条。",
                    rerank_summary="已完成重排。",
                    summary_phase=None,
                    rewritten_queries=["测试"],
                    steps=[],
                    tool_calls=[
                        ToolCallTrace(
                            name="retrieve_documents",
                            status="completed",
                            input="query=测试",
                            output="检索到 1 条片段。",
                            latency_ms=7,
                        )
                    ],
                ),
            )

    client.app.state.document_agent_service = EndpointAgentService()

    resp = client.post(
        "/api/v1/chat/qa",
        json={"message": "测试 agent", "top_k": 4},
        headers={"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert UUID(data["session_id"])
    assert data["answer"] == "接口回答"
    assert data["agent"]["status"] == "response"
    assert data["agent"]["task_type"] == "qa"
    assert data["agent"]["retrieval_summary"]
    assert data["agent"]["rerank_summary"]
    assert data["agent"]["rewritten_queries"] == ["测试"]
    assert data["agent"]["tool_calls"][0]["name"] == "retrieve_documents"
