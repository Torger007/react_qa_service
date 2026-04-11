from __future__ import annotations

import json
import re
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Literal, Protocol, TypedDict
from uuid import UUID, uuid4

import anyio
from pydantic import BaseModel, Field, ValidationError
from redis.asyncio import Redis

from app.core.config import settings
from app.core.llm_client import EmbeddingsClient, LLMClient
from app.core.vector_store import RedisVectorStore, ScoredChunk
from app.core.vector_types import DocumentChunk
from app.models.qa_schemas import AgentStep, AgentTraceResponse, QACitation, ToolCallTrace
from app.models.schemas import ChatMessage
from app.services.query_rewrite_service import QueryRewriteService
from app.services.reranker import Reranker
from app.services.retrieval_postprocess import postprocess_retrieved_chunks
from app.services.session_manager import append_message, get_history
from app.services.summary_service import SummaryService

TaskType = Literal["qa", "summary"]


class PlannerDecision(BaseModel):
    action: str = Field(pattern="^(retrieve_documents|respond)$")
    query: str | None = None
    summary: str = Field(default="", max_length=400)


class PlannerModel(Protocol):
    async def ainvoke(self, state: "AgentState") -> PlannerDecision: ...


class AnswerModel(Protocol):
    async def ainvoke(self, state: "AgentState") -> str: ...


class RetrievedChunk(TypedDict):
    doc_id: str
    chunk_id: str
    text: str
    score: float
    metadata: dict[str, Any] | None


class AgentState(TypedDict, total=False):
    subject: str
    session_id: str
    user_message: str
    history: list[ChatMessage]
    top_k: int
    doc_filters: dict[str, Any] | None
    messages: list[dict[str, str]]
    retrieved_chunks: list[RetrievedChunk]
    citations: list[QACitation]
    tool_traces: list[ToolCallTrace]
    steps: list[AgentStep]
    stage: str
    final_answer: str
    error: str | None
    loop_count: int
    run_id: str
    planned_action: str
    planned_query: str | None
    task_type: TaskType
    aggregated_context: str
    summary_drafts: list[str]
    retrieval_summary: str
    rerank_summary: str
    summary_phase: str | None
    rewritten_queries: list[str]
    retrieval_candidates: list[RetrievedChunk]


@dataclass(frozen=True)
class DocumentAgentResult:
    answer: str
    history: list[ChatMessage]
    citations: list[QACitation]
    agent: AgentTraceResponse


class LangChainPlanner:
    def __init__(self, model_name: str) -> None:
        from langchain_openai import ChatOpenAI

        self._model = ChatOpenAI(
            model=model_name,
            temperature=0,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            timeout=settings.llm_timeout_seconds,
        )

    async def ainvoke(self, state: AgentState) -> PlannerDecision:
        from langchain_core.messages import HumanMessage, SystemMessage

        history_text = "\n".join(
            f"{message.role}: {message.content}" for message in state.get("history", [])[-6:]
        ) or "(no prior conversation)"
        retrieval_text = "\n\n".join(
            f"[{index + 1}] {chunk['doc_id']} score={chunk['score']:.3f}\n{chunk['text']}"
            for index, chunk in enumerate(state.get("retrieved_chunks", []))
        ) or "(no retrieved context yet)"

        messages = [
            SystemMessage(
                content=(
                    "You are the planning node for a grounded document QA agent. "
                    "Return JSON only. Allowed actions: retrieve_documents or respond. "
                    'Use this exact JSON schema: {"action":"retrieve_documents|respond","query":"optional string or null","summary":"short reason"}. '
                    "Prefer retrieve_documents when evidence is insufficient."
                )
            ),
            HumanMessage(
                content=(
                    f"User: {state['subject']}\n"
                    f"Task type: {state.get('task_type', 'qa')}\n"
                    f"Question: {state['user_message']}\n"
                    f"History:\n{history_text}\n\n"
                    f"Retrieved context:\n{retrieval_text}\n\n"
                    f"Loop count: {state['loop_count']}\n"
                    f"Top K: {state['top_k']}\n"
                    "Return the planner decision as JSON."
                )
            ),
        ]
        result = await self._model.ainvoke(messages)
        return self._parse_decision(result)

    @staticmethod
    def _parse_decision(result: Any) -> PlannerDecision:
        additional_kwargs = getattr(result, "additional_kwargs", {}) or {}
        parsed = additional_kwargs.get("parsed")
        if isinstance(parsed, PlannerDecision):
            return parsed
        if isinstance(parsed, dict):
            try:
                return PlannerDecision.model_validate(parsed)
            except ValidationError:
                pass

        content = getattr(result, "content", "")
        if isinstance(content, list):
            text_parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    maybe_text = item.get("text")
                    if isinstance(maybe_text, str):
                        text_parts.append(maybe_text)
                elif isinstance(item, str):
                    text_parts.append(item)
            content = "\n".join(text_parts)
        if not isinstance(content, str):
            content = str(content or "")

        candidate = content.strip()
        if not candidate:
            raise ValueError(
                "Planner returned empty content and no usable structured payload."
            )

        try:
            return PlannerDecision.model_validate_json(candidate)
        except ValidationError:
            pass
        except Exception:
            pass

        match = re.search(r"\{.*\}", candidate, re.DOTALL)
        if match:
            try:
                payload = json.loads(match.group(0))
                return PlannerDecision.model_validate(payload)
            except (json.JSONDecodeError, ValidationError):
                pass

        lowered = candidate.lower()
        action = "retrieve_documents" if "retrieve_documents" in lowered else "respond"
        return PlannerDecision(
            action=action,
            query=None,
            summary=candidate[:400],
        )


class LangChainAnswerModel:
    def __init__(self, model_name: str) -> None:
        from langchain_openai import ChatOpenAI

        self._model = ChatOpenAI(
            model=model_name,
            temperature=settings.llm_temperature,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            timeout=settings.llm_timeout_seconds,
        )

    async def ainvoke(self, state: AgentState) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage

        history_text = "\n".join(
            f"{message.role}: {message.content}" for message in state.get("history", [])[-6:]
        ) or "（无历史对话）"
        retrieval_text = "\n\n".join(
            f"[{index + 1}] {chunk['doc_id']} (score={chunk['score']:.3f})\n{chunk['text']}"
            for index, chunk in enumerate(state.get("retrieved_chunks", []))
        ) or "（没有检索到足够依据）"

        result = await self._model.ainvoke(
            [
                SystemMessage(
                    content=(
                        "你是企业文档问答助手。"
                        "只能根据给定检索片段回答。"
                        "如果证据不足，要明确说明不确定。"
                        "请输出简洁、自然的中文答案。"
                    )
                ),
                HumanMessage(
                    content=(
                        f"用户身份: {state['subject']}\n"
                        f"历史对话:\n{history_text}\n\n"
                        f"检索片段:\n{retrieval_text}\n\n"
                        f"当前问题: {state['user_message']}\n"
                        "请给出最终答案。"
                    )
                ),
            ]
        )
        content = getattr(result, "content", "")
        return content if isinstance(content, str) else str(content or "")


class DocumentAgentService:
    def __init__(
        self,
        *,
        redis: Redis,
        embeddings: EmbeddingsClient,
        vector_store: RedisVectorStore,
        llm: LLMClient | None = None,
        planner: PlannerModel | None = None,
        answer_model: AnswerModel | None = None,
        summary_service: SummaryService | None = None,
        reranker: Reranker | None = None,
        query_rewrite_service: QueryRewriteService | None = None,
        max_loops: int = 2,
    ) -> None:
        self._redis = redis
        self._emb = embeddings
        self._vs = vector_store
        self._planner = planner or LangChainPlanner(settings.llm_model)
        self._answer_model = answer_model or LangChainAnswerModel(settings.llm_model)
        self._summary_service = summary_service or SummaryService(llm=llm)
        self._fallback_summary_service = SummaryService(llm=None)
        self._reranker = reranker or Reranker(llm=llm)
        self._query_rewrite_service = query_rewrite_service or QueryRewriteService(
            llm=llm,
            max_queries=settings.multi_query_count,
        )
        self._max_loops = max_loops
        self._graph = self._build_graph()

    def _build_graph(self):
        try:
            from langgraph.graph import END, StateGraph
        except ModuleNotFoundError:
            return _FallbackCompiledGraph(self)

        graph = StateGraph(AgentState)
        graph.add_node("prepare_context", self._prepare_context)
        graph.add_node("classify_task", self._classify_task)
        graph.add_node("plan_next_step", self._plan_next_step)
        graph.add_node("retrieve_documents", self._retrieve_documents)
        graph.add_node("rerank_results", self._rerank_results)
        graph.add_node("summarize_document", self._summarize_document)
        graph.add_node("generate_answer", self._generate_answer)
        graph.set_entry_point("prepare_context")
        graph.add_edge("prepare_context", "classify_task")
        graph.add_conditional_edges(
            "classify_task",
            self._route_after_classification,
            {
                "plan_next_step": "plan_next_step",
                "retrieve_documents": "retrieve_documents",
            },
        )
        graph.add_conditional_edges(
            "retrieve_documents",
            self._route_after_retrieval,
            {
                "rerank_results": "rerank_results",
            },
        )
        graph.add_conditional_edges(
            "rerank_results",
            self._route_after_rerank,
            {
                "plan_next_step": "plan_next_step",
                "summarize_document": "summarize_document",
            },
        )
        graph.add_conditional_edges(
            "plan_next_step",
            self._route_after_plan,
            {
                "retrieve_documents": "retrieve_documents",
                "generate_answer": "generate_answer",
            },
        )
        graph.add_edge("summarize_document", END)
        graph.add_edge("generate_answer", END)
        return graph.compile()

    async def answer(
        self,
        *,
        subject: str,
        session_id: UUID,
        message: str,
        top_k: int,
        doc_filters: dict[str, Any] | None,
    ) -> DocumentAgentResult:
        history = await get_history(self._redis, session_id)

        user_msg = ChatMessage(role="user", content=message)
        await append_message(self._redis, session_id, user_msg, subject=subject, title_seed=message)

        result = await self._graph.ainvoke(
            {
                "subject": subject,
                "session_id": str(session_id),
                "user_message": message,
                "history": history,
                "top_k": top_k,
                "doc_filters": doc_filters,
                "messages": [],
                "retrieved_chunks": [],
                "retrieval_candidates": [],
                "citations": [],
                "tool_traces": [],
                "steps": [],
                "stage": "thinking",
                "final_answer": "",
                "error": None,
                "loop_count": 0,
                "run_id": str(uuid4()),
                "planned_action": "respond",
                "planned_query": None,
                "task_type": "qa",
                "aggregated_context": "",
                "summary_drafts": [],
                "retrieval_summary": "",
                "rerank_summary": "",
                "summary_phase": None,
                "rewritten_queries": [],
            }
        )

        answer = result.get("final_answer") or "未能生成回答。"
        assistant_msg = ChatMessage(role="assistant", content=answer)
        await append_message(self._redis, session_id, assistant_msg, subject=subject)
        history2 = await get_history(self._redis, session_id)

        trace = AgentTraceResponse(
            run_id=result["run_id"],
            status=result.get("stage", "response"),  # type: ignore[arg-type]
            task_type=result.get("task_type", "qa"),
            retrieval_summary=result.get("retrieval_summary"),
            rerank_summary=result.get("rerank_summary"),
            summary_phase=result.get("summary_phase"),
            rewritten_queries=result.get("rewritten_queries", []),
            steps=result.get("steps", []),
            tool_calls=result.get("tool_traces", []),
        )
        return DocumentAgentResult(
            answer=answer,
            history=history2,
            citations=result.get("citations", []),
            agent=trace,
        )

    async def _prepare_context(self, state: AgentState) -> AgentState:
        steps = list(state.get("steps", []))
        steps.append(
            AgentStep(
                stage="thinking",
                title="准备上下文",
                summary="已加载最近会话历史，并初始化本轮智能体状态。",
            )
        )
        return {
            **state,
            "stage": "thinking",
            "steps": steps,
        }

    async def _classify_task(self, state: AgentState) -> AgentState:
        task_type = _classify_task_type(state["user_message"])
        steps = list(state.get("steps", []))
        steps.append(
            AgentStep(
                stage="thinking",
                title="识别任务类型",
                summary=(
                    "识别为全文/文档总结任务，将优先走 summary 链路。"
                    if task_type == "summary"
                    else "识别为问答任务，将优先走检索问答链路。"
                ),
            )
        )
        return {
            **state,
            "task_type": task_type,
            "steps": steps,
        }

    def _route_after_classification(self, state: AgentState) -> str:
        if state.get("task_type") == "summary":
            return "retrieve_documents"
        return "plan_next_step"

    async def _plan_next_step(self, state: AgentState) -> AgentState:
        steps = list(state.get("steps", []))
        if state.get("loop_count", 0) >= self._max_loops:
            decision = PlannerDecision(
                action="respond",
                query=None,
                summary="已达到最大检索轮次，转入最终回答。",
            )
        else:
            decision = await self._planner.ainvoke(state)
            if not decision.summary.strip():
                decision = PlannerDecision(
                    action=decision.action,
                    query=decision.query,
                    summary=(
                        "需要先检索文档片段后再回答。"
                        if decision.action == "retrieve_documents"
                        else "已有足够信息，可以直接生成回答。"
                    ),
                )

        steps.append(
            AgentStep(
                stage="thinking",
                title="规划下一步",
                summary=decision.summary,
            )
        )
        planned_query = decision.query or state["user_message"]
        return {
            **state,
            "stage": "thinking",
            "steps": steps,
            "planned_action": decision.action,
            "planned_query": planned_query,
        }

    def _route_after_plan(self, state: AgentState) -> str:
        if (
            state.get("planned_action") == "retrieve_documents"
            and state.get("loop_count", 0) < self._max_loops
        ):
            return "retrieve_documents"
        return "generate_answer"

    async def _retrieve_documents(self, state: AgentState) -> AgentState:
        query = state.get("planned_query") or state["user_message"]
        top_k = max(state["top_k"], 8) if state.get("task_type") == "summary" else state["top_k"]
        candidate_top_k = max(top_k, top_k * settings.retrieval_candidate_multiplier)
        started = perf_counter()
        queries = await self._resolve_retrieval_queries(query=query, task_type=state.get("task_type", "qa"))
        scored = await self._retrieve_multi_query_candidates(
            queries=queries,
            top_k=candidate_top_k,
            filters=state.get("doc_filters"),
        )
        latency_ms = int((perf_counter() - started) * 1000)
        retrieval_candidates = [self._to_retrieved_chunk(item) for item in scored]
        retrieval_summary = self._build_recall_summary(scored=scored, queries=queries)

        tool_traces = list(state.get("tool_traces", []))
        if len(queries) > 1:
            tool_traces.append(
                ToolCallTrace(
                    name="rewrite_query",
                    status="completed",
                    input=query,
                    output="\n".join(queries),
                    latency_ms=0,
                )
            )
        tool_traces.append(
            ToolCallTrace(
                name="retrieve_documents",
                status="completed",
                input=f"queries={len(queries)}; top_k={candidate_top_k}",
                output=retrieval_summary,
                latency_ms=latency_ms,
            )
        )

        steps = list(state.get("steps", []))
        steps.append(
            AgentStep(
                stage="acting",
                title="执行知识检索",
                summary=(
                    f"已完成多查询召回，得到 {len(retrieval_candidates)} 条候选片段。"
                    if retrieval_candidates
                    else "已执行文档检索，但未命中相关片段。"
                ),
            )
        )
        return {
            **state,
            "stage": "acting",
            "retrieval_candidates": retrieval_candidates,
            "tool_traces": tool_traces,
            "steps": steps,
            "loop_count": state.get("loop_count", 0) + 1,
            "retrieval_summary": retrieval_summary,
            "rewritten_queries": queries,
        }

    def _route_after_retrieval(self, state: AgentState) -> str:
        return "rerank_results"

    async def _rerank_results(self, state: AgentState) -> AgentState:
        query = state.get("planned_query") or state["user_message"]
        top_k = max(state["top_k"], 8) if state.get("task_type") == "summary" else state["top_k"]
        candidate_items = [
            ScoredChunk(
                chunk=DocumentChunk(
                    doc_id=item["doc_id"],
                    chunk_id=item["chunk_id"],
                    text=item["text"],
                    metadata=item.get("metadata") or {},
                ),
                score=item["score"],
            )
            for item in state.get("retrieval_candidates", [])
        ]
        reranked = await self._rerank_candidates(query=query, candidates=candidate_items, top_k=top_k)
        processed = postprocess_retrieved_chunks(reranked, max_results=top_k)
        retrieved_chunks = [self._to_retrieved_chunk(item) for item in processed]
        citations = [self._to_citation(item) for item in processed]
        rerank_summary = self._build_rerank_summary(
            candidates=candidate_items,
            reranked=reranked,
            processed=processed,
        )

        tool_traces = list(state.get("tool_traces", []))
        tool_traces.append(
            ToolCallTrace(
                name="rerank_results",
                status="completed",
                input=f"query={query}; candidates={len(candidate_items)}",
                output=rerank_summary,
                latency_ms=0,
            )
        )
        steps = list(state.get("steps", []))
        steps.append(
            AgentStep(
                stage="acting",
                title="重排检索结果",
                summary=(
                    f"已对 {len(candidate_items)} 条候选片段完成重排与聚合，保留 {len(retrieved_chunks)} 条结果。"
                    if candidate_items
                    else "没有可重排的候选片段。"
                ),
            )
        )
        return {
            **state,
            "stage": "acting",
            "retrieved_chunks": retrieved_chunks,
            "citations": citations,
            "tool_traces": tool_traces,
            "steps": steps,
            "rerank_summary": rerank_summary,
        }

    def _route_after_rerank(self, state: AgentState) -> str:
        if state.get("task_type") == "summary":
            return "summarize_document"
        return "plan_next_step"

    async def _summarize_document(self, state: AgentState) -> AgentState:
        started = perf_counter()
        summary_chunks = await self._collect_summary_chunks(state)
        used_fallback = False
        try:
            with anyio.fail_after(settings.summary_timeout_seconds + 5):
                summary_result = await self._summary_service.summarize(
                    question=state["user_message"],
                    chunks=summary_chunks,
                )
        except Exception:
            used_fallback = True
            summary_result = await self._fallback_summary_service.summarize(
                question=state["user_message"],
                chunks=summary_chunks,
            )
        latency_ms = int((perf_counter() - started) * 1000)

        citations = [self._citation_from_chunk(chunk) for chunk in summary_chunks[:8]]
        if summary_chunks and not used_fallback:
            step_summary = f"已基于 {len(summary_chunks)} 条文档片段生成总结。"
            tool_output = "已完成文档级摘要汇总。"
            summary_phase = f"mapped {len(summary_result.partial_summaries)} draft(s) and reduced to final summary"
        elif summary_chunks:
            step_summary = f"LLM 摘要超时或失败，已改用快速降级总结，基于 {len(summary_chunks)} 条片段输出结果。"
            tool_output = "LLM summary timeout/failure detected; returned fallback summary."
            summary_phase = f"fallback summary generated from {len(summary_chunks)} chunk(s)"
        else:
            step_summary = "缺少可用文档内容，返回了降级总结结果。"
            tool_output = "No usable chunks found; returned fallback summary."
            summary_phase = "fallback summary generated from 0 chunk(s)"

        steps = list(state.get("steps", []))
        steps.append(
            AgentStep(
                stage="response",
                title="生成文档总结",
                summary=step_summary,
            )
        )

        tool_traces = list(state.get("tool_traces", []))
        tool_traces.append(
            ToolCallTrace(
                name="summarize_document",
                status="completed",
                input=f"task=summary; chunks={len(summary_chunks)}",
                output=tool_output,
                latency_ms=latency_ms,
            )
        )
        return {
            **state,
            "stage": "response",
            "steps": steps,
            "citations": citations,
            "tool_traces": tool_traces,
            "summary_drafts": summary_result.partial_summaries,
            "aggregated_context": "\n\n".join(chunk.text for chunk in summary_chunks[:12]),
            "summary_phase": summary_phase,
            "final_answer": summary_result.answer,
        }

    async def _generate_answer(self, state: AgentState) -> AgentState:
        answer = await self._answer_model.ainvoke(state)
        steps = list(state.get("steps", []))
        steps.append(
            AgentStep(
                stage="response",
                title="生成最终回答",
                summary=(
                    "已基于检索片段生成最终回答。"
                    if state.get("citations")
                    else "在缺少充分检索依据的情况下生成了降级回答。"
                ),
            )
        )
        return {
            **state,
            "stage": "response",
            "steps": steps,
            "final_answer": answer,
        }

    async def _collect_summary_chunks(self, state: AgentState) -> list[DocumentChunk]:
        retrieved_chunks = state.get("retrieved_chunks", [])
        doc_filters = state.get("doc_filters") or {}
        doc_id_filter = doc_filters.get("doc_id")
        if isinstance(doc_id_filter, str) and doc_id_filter:
            chunks = await self._vs.list_chunks(doc_id=doc_id_filter)
            if chunks:
                return self._limit_summary_chunks(chunks, retrieved_chunks=retrieved_chunks)

        seen_doc_ids: list[str] = []
        for chunk in state.get("retrieved_chunks", []):
            doc_id = chunk["doc_id"]
            if doc_id not in seen_doc_ids:
                seen_doc_ids.append(doc_id)

        summary_chunks: list[DocumentChunk] = []
        for doc_id in seen_doc_ids[:3]:
            summary_chunks.extend(await self._vs.list_chunks(doc_id=doc_id))

        if summary_chunks:
            return self._limit_summary_chunks(summary_chunks, retrieved_chunks=retrieved_chunks)

        return self._limit_summary_chunks(
            [
                DocumentChunk(
                    doc_id=chunk["doc_id"],
                    chunk_id=chunk["chunk_id"],
                    text=chunk["text"],
                    metadata=chunk.get("metadata") or {},
                )
                for chunk in retrieved_chunks
            ],
            retrieved_chunks=retrieved_chunks,
        )

    @staticmethod
    def _limit_summary_chunks(
        chunks: list[DocumentChunk],
        *,
        retrieved_chunks: list[RetrievedChunk] | None = None,
    ) -> list[DocumentChunk]:
        max_chunks = settings.summary_max_chunks
        if len(chunks) <= max_chunks:
            return chunks

        ordered_chunks = sorted(
            chunks,
            key=lambda chunk: (
                chunk.doc_id,
                int(chunk.metadata.get("order", 0)),
                chunk.chunk_id,
            ),
        )
        retrieved_chunks = retrieved_chunks or []
        relevance_ranked = DocumentAgentService._rank_summary_chunks_by_relevance(
            chunks=ordered_chunks,
            retrieved_chunks=retrieved_chunks,
        )

        selected_keys: set[tuple[str, str]] = set()
        selected: list[DocumentChunk] = []
        relevant_budget = min(
            max_chunks,
            max(1, round(max_chunks * 0.6)),
            len(relevance_ranked),
        )

        for chunk in relevance_ranked[:relevant_budget]:
            key = (chunk.doc_id, chunk.chunk_id)
            if key in selected_keys:
                continue
            selected.append(chunk)
            selected_keys.add(key)

        if len(selected) < max_chunks:
            for chunk in DocumentAgentService._coverage_sample_chunks(ordered_chunks, max_chunks=max_chunks):
                key = (chunk.doc_id, chunk.chunk_id)
                if key in selected_keys:
                    continue
                selected.append(chunk)
                selected_keys.add(key)
                if len(selected) >= max_chunks:
                    break

        if len(selected) < max_chunks:
            for chunk in ordered_chunks:
                key = (chunk.doc_id, chunk.chunk_id)
                if key in selected_keys:
                    continue
                selected.append(chunk)
                selected_keys.add(key)
                if len(selected) >= max_chunks:
                    break

        return sorted(
            selected[:max_chunks],
            key=lambda chunk: (
                chunk.doc_id,
                int(chunk.metadata.get("order", 0)),
                chunk.chunk_id,
            ),
        )

    @staticmethod
    def _rank_summary_chunks_by_relevance(
        *,
        chunks: list[DocumentChunk],
        retrieved_chunks: list[RetrievedChunk],
    ) -> list[DocumentChunk]:
        if not chunks:
            return []
        if not retrieved_chunks:
            return list(chunks)

        ranked: list[tuple[float, int, DocumentChunk]] = []
        for index, chunk in enumerate(chunks):
            score = 0.0
            chunk_order = int(chunk.metadata.get("order", 0))
            chunk_section = str(chunk.metadata.get("section_title") or "")
            for retrieved in retrieved_chunks:
                if retrieved["doc_id"] != chunk.doc_id:
                    continue
                metadata = retrieved.get("metadata") or {}
                retrieved_order = int(metadata.get("order", 0))
                retrieved_section = str(metadata.get("section_title") or "")
                retrieved_score = float(retrieved.get("score", 0.0))
                distance = abs(chunk_order - retrieved_order)
                proximity = 1.0 / (1.0 + distance)
                current = retrieved_score * proximity
                if chunk.chunk_id == retrieved["chunk_id"]:
                    current += 1.0
                if chunk_section and retrieved_section and chunk_section == retrieved_section:
                    current += 0.05
                score = max(score, current)
            ranked.append((score, -index, chunk))

        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [chunk for score, _, chunk in ranked if score > 0]

    @staticmethod
    def _coverage_sample_chunks(chunks: list[DocumentChunk], *, max_chunks: int) -> list[DocumentChunk]:
        if not chunks:
            return []
        if len(chunks) <= max_chunks:
            return list(chunks)
        if max_chunks <= 1:
            return [chunks[0]]

        selected: list[DocumentChunk] = []
        last_index = len(chunks) - 1
        for position in range(max_chunks):
            index = round(position * last_index / (max_chunks - 1))
            selected.append(chunks[index])
        return selected

    async def _resolve_retrieval_queries(self, *, query: str, task_type: TaskType) -> list[str]:
        if task_type == "summary" or not settings.multi_query_enabled:
            return [query]
        return await self._query_rewrite_service.rewrite(question=query)

    async def _retrieve_multi_query_candidates(
        self,
        *,
        queries: list[str],
        top_k: int,
        filters: dict[str, Any] | None,
    ) -> list[ScoredChunk]:
        combined: dict[tuple[str, str], ScoredChunk] = {}
        for query in queries:
            [query_vec] = await self._emb.embed([query])
            scored = await self._vs.similarity_search(
                query_vector=query_vec,
                top_k=top_k,
                filters=filters,
            )
            for item in scored:
                key = (item.chunk.doc_id, item.chunk.chunk_id)
                previous = combined.get(key)
                if previous is None or item.score > previous.score:
                    combined[key] = item
        return list(combined.values())

    async def _rerank_candidates(
        self,
        *,
        query: str,
        candidates: list[ScoredChunk],
        top_k: int,
    ) -> list[ScoredChunk]:
        if not settings.rerank_enabled:
            return sorted(candidates, key=lambda item: item.score, reverse=True)[: max(1, top_k)]
        return await self._reranker.rerank(query=query, chunks=candidates, top_k=top_k)

    @staticmethod
    def _to_retrieved_chunk(item: ScoredChunk) -> RetrievedChunk:
        return {
            "doc_id": item.chunk.doc_id,
            "chunk_id": item.chunk.chunk_id,
            "text": item.chunk.text,
            "score": item.score,
            "metadata": item.chunk.metadata,
        }

    @staticmethod
    def _to_citation(item: ScoredChunk) -> QACitation:
        return QACitation(
            doc_id=item.chunk.doc_id,
            snippet=item.chunk.text,
            score=item.score,
            metadata=item.chunk.metadata,
        )

    @staticmethod
    def _citation_from_chunk(chunk: DocumentChunk) -> QACitation:
        return QACitation(
            doc_id=chunk.doc_id,
            snippet=chunk.text,
            score=1.0,
            metadata=chunk.metadata,
        )

    @staticmethod
    def _build_recall_summary(
        *,
        scored: list[ScoredChunk],
        queries: list[str],
    ) -> str:
        if not scored:
            return "未检索到相关文档片段。"
        doc_count = len({item.chunk.doc_id for item in scored})
        return (
            f"共执行 {len(queries)} 个查询，召回 {len(scored)} 条候选片段，覆盖 {doc_count} 篇文档。"
        )

    @staticmethod
    def _build_rerank_summary(
        *,
        candidates: list[ScoredChunk],
        reranked: list[ScoredChunk],
        processed: list[ScoredChunk],
    ) -> str:
        if not processed:
            return "没有可输出的重排结果。"
        doc_count = len({item.chunk.doc_id for item in processed})
        merged_count = sum(int(item.chunk.metadata.get("merged_chunk_count", 1)) for item in processed)
        return (
            f"对 {len(candidates)} 条候选完成重排，保留 {len(reranked)} 条高相关结果，"
            f"整理后输出 {len(processed)} 条，覆盖 {doc_count} 篇文档，合并上下文片段 {merged_count} 条。"
        )


class _FallbackCompiledGraph:
    def __init__(self, service: DocumentAgentService) -> None:
        self._service = service

    async def ainvoke(self, state: AgentState) -> AgentState:
        current = await self._service._prepare_context(state)
        current = await self._service._classify_task(current)
        if self._service._route_after_classification(current) == "retrieve_documents":
            current = await self._service._retrieve_documents(current)
            current = await self._service._rerank_results(current)
            current = await self._service._summarize_document(current)
            return current

        while True:
            current = await self._service._plan_next_step(current)
            route = self._service._route_after_plan(current)
            if route == "retrieve_documents":
                current = await self._service._retrieve_documents(current)
                current = await self._service._rerank_results(current)
                continue
            current = await self._service._generate_answer(current)
            return current


def _classify_task_type(message: str) -> TaskType:
    normalized = message.strip().lower()
    generic_summary_prompts = (
        "\u603b\u7ed3",
        "\u603b\u7ed3\u4e00\u4e0b",
        "\u603b\u7ed3\u4e00\u4e0b\u5427",
        "\u8bf7\u603b\u7ed3",
        "\u5e2e\u6211\u603b\u7ed3",
        "\u505a\u4e2a\u603b\u7ed3",
        "\u603b\u7ed3\u4e0b",
    )
    explicit_summary_markers = (
        "\u603b\u7ed3\u4e00\u4e0b",
        "\u603b\u7ed3\u5168\u6587",
        "\u5168\u6587\u603b\u7ed3",
        "\u603b\u7ed3\u6587\u6863",
        "\u603b\u7ed3\u6587\u6863\u5185\u5bb9",
        "\u603b\u7ed3\u8fd9\u7bc7\u6587\u6863",
        "\u603b\u7ed3\u8fd9\u7bc7\u6587\u7ae0",
        "\u6458\u8981",
        "\u6982\u89c8",
        "\u6982\u62ec\u5168\u6587",
        "\u7efc\u8ff0",
        "\u6574\u4f53\u8bb2\u4e86\u4ec0\u4e48",
        "\u8fd9\u7bc7\u6587\u6863\u8bb2\u4e86\u4ec0\u4e48",
        "\u8fd9\u7bc7\u6587\u7ae0\u8bb2\u4e86\u4ec0\u4e48",
        "summarize",
        "summary",
    )
    document_scope_markers = (
        "\u5168\u6587",
        "\u6587\u6863",
        "\u6587\u7ae0",
        "\u5185\u5bb9",
        "\u6574\u4f53",
        "\u8fd9\u4efd",
        "\u8fd9\u7bc7",
        "\u6700\u8fd1\u4e0a\u4f20",
        "\u4e0a\u9762",
        "\u4e0a\u8ff0",
        "\u672c\u6587",
    )
    summary_signals = ("\u603b\u7ed3", "\u6458\u8981", "\u6982\u62ec", "\u7efc\u8ff0")
    focused_qa_markers = (
        "\u4e3a\u4ec0\u4e48",
        "\u600e\u4e48",
        "\u662f\u5426",
        "\u80fd\u5426",
        "\u54ea\u4e9b",
        "\u54ea\u4e2a",
        "\u591a\u5c11",
        "\u4ec0\u4e48\u65f6\u5019",
        "\u4ec0\u4e48\u539f\u56e0",
        "\u98ce\u9669\u662f\u4ec0\u4e48",
    )
    focused_topic_markers = (
        "\u98ce\u9669",
        "\u5408\u540c",
        "\u6761\u6b3e",
        "\u95ee\u9898",
        "\u5efa\u8bae",
        "\u5dee\u5f02",
        "\u539f\u56e0",
    )
    if normalized in generic_summary_prompts:
        return "summary"
    if any(marker in normalized for marker in explicit_summary_markers):
        return "summary"
    if any(signal in normalized for signal in summary_signals) and any(
        scope in normalized for scope in document_scope_markers
    ):
        return "summary"
    if any(marker in normalized for marker in focused_qa_markers):
        return "qa"
    if any(signal in normalized for signal in summary_signals) and any(
        marker in normalized for marker in focused_topic_markers
    ):
        return "qa"
    return "qa"
