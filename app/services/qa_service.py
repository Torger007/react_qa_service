from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from redis.asyncio import Redis

from app.core.llm_client import EmbeddingsClient, LLMClient
from app.core.vector_store import RedisVectorStore
from app.models.qa_schemas import QACitation
from app.models.schemas import ChatMessage
from app.services.session_manager import append_message, get_history


@dataclass(frozen=True)
class QAResult:
    answer: str
    history: list[ChatMessage]
    citations: list[QACitation]


class QAService:
    """
    RAG-style QA pipeline that reuses existing session history in Redis.
    """

    def __init__(
        self,
        *,
        redis: Redis,
        llm: LLMClient,
        embeddings: EmbeddingsClient,
        vector_store: RedisVectorStore,
    ):
        self._redis = redis
        self._llm = llm
        self._emb = embeddings
        self._vs = vector_store

    async def answer(
        self,
        *,
        subject: str,
        session_id: UUID,
        message: str,
        top_k: int,
        doc_filters: dict[str, Any] | None,
    ) -> QAResult:
        history = await get_history(self._redis, session_id)

        user_msg = ChatMessage(role="user", content=message)
        await append_message(self._redis, session_id, user_msg, subject=subject, title_seed=message)

        [query_vec] = await self._emb.embed([message])
        scored = await self._vs.similarity_search(
            query_vector=query_vec,
            top_k=top_k,
            filters=doc_filters,
        )

        citations: list[QACitation] = [
            QACitation(
                doc_id=it.chunk.doc_id,
                snippet=it.chunk.text,
                score=it.score,
                metadata=it.chunk.metadata,
            )
            for it in scored
        ]

        ctx_blocks: list[str] = []
        for idx, it in enumerate(scored):
            md = it.chunk.metadata or {}
            title = md.get("title") or md.get("name") or f"Doc {it.chunk.doc_id}"
            ctx_blocks.append(
                f"[{idx + 1}] {title} (score={it.score:.3f})\n{it.chunk.text}".strip()
            )
        context_text = "\n\n".join(ctx_blocks) if ctx_blocks else "无检索到的文档片段。"

        history_snippets = [f"{m.role}: {m.content}" for m in history[-6:]]
        history_text = "\n".join(history_snippets) if history_snippets else "（无历史对话）"

        system_prompt = (
            "你是一个为企业/项目文档提供专业解答的智能助手。\n"
            "必须严格依据提供的文档上下文回答问题，无法从上下文中得到的信息要明确说明不知道，"
            "不要编造细节。优先引用编号片段中的关键信息进行总结，并用自然、简洁的中文回答。\n"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"用户身份: {subject}\n\n"
                    f"历史对话:\n{history_text}\n\n"
                    f"检索到的文档片段:\n{context_text}\n\n"
                    f"当前问题: {message}\n\n"
                    "请基于上述文档片段进行回答，并在需要时指出参考了哪些编号的片段。"
                ),
            },
        ]

        answer_text = await self._llm.chat(messages=messages)

        assistant_msg = ChatMessage(role="assistant", content=answer_text)
        await append_message(self._redis, session_id, assistant_msg, subject=subject)
        history2 = await get_history(self._redis, session_id)

        return QAResult(answer=answer_text, history=history2, citations=citations)

