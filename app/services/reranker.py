from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from app.core.config import settings
from app.core.llm_client import LLMClient
from app.core.vector_store import ScoredChunk

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z0-9_]+")
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class RerankLLM(Protocol):
    async def chat(
        self,
        *,
        messages: list[dict[str, str]],
        model: str | None = None,
        timeout_seconds: int | None = None,
    ) -> str: ...


@dataclass(frozen=True)
class Reranker:
    llm: LLMClient | RerankLLM | None = None

    async def rerank(self, *, query: str, chunks: list[ScoredChunk], top_k: int) -> list[ScoredChunk]:
        if not chunks:
            return []

        ranked = sorted(
            chunks,
            key=lambda item: self._fallback_score(query=query, item=item),
            reverse=True,
        )
        llm_ranked = await self._llm_rerank(query=query, chunks=ranked, top_k=top_k)
        if llm_ranked:
            return llm_ranked[: max(1, top_k)]
        return ranked[: max(1, top_k)]

    async def _llm_rerank(self, *, query: str, chunks: list[ScoredChunk], top_k: int) -> list[ScoredChunk] | None:
        if self.llm is None or len(chunks) <= 1:
            return None

        candidate_count = min(len(chunks), max(top_k, settings.rerank_max_candidates))
        candidates = chunks[:candidate_count]
        prompt = self._build_rerank_prompt(query=query, chunks=candidates, top_k=top_k)

        try:
            raw = await self.llm.chat(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是企业文档检索重排助手。"
                            "请根据用户问题，对候选片段按相关性从高到低排序。"
                            "优先保留能直接回答问题、证据更具体、信息更完整的片段。"
                            "只输出 JSON，不要输出解释。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                model=settings.rerank_model,
                timeout_seconds=settings.rerank_timeout_seconds,
            )
        except Exception:
            return None

        ranked_ids = self._parse_ranked_ids(raw)
        if not ranked_ids:
            return None

        by_id = {self._candidate_id(index): item for index, item in enumerate(candidates, start=1)}
        ordered: list[ScoredChunk] = []
        seen: set[str] = set()
        for candidate_id in ranked_ids:
            item = by_id.get(candidate_id)
            if item is None or candidate_id in seen:
                continue
            ordered.append(item)
            seen.add(candidate_id)

        if not ordered:
            return None

        for index, item in enumerate(candidates, start=1):
            candidate_id = self._candidate_id(index)
            if candidate_id not in seen:
                ordered.append(item)

        ordered.extend(chunks[candidate_count:])
        return ordered

    @staticmethod
    def _candidate_id(index: int) -> str:
        return f"C{index}"

    @classmethod
    def _build_rerank_prompt(cls, *, query: str, chunks: list[ScoredChunk], top_k: int) -> str:
        lines = [
            f"用户问题：{query}",
            f"请从下面的候选片段中选出最相关的前 {max(1, top_k)} 条，并给出完整排序。",
            '请严格输出 JSON，例如：{"ranked_ids":["C2","C1","C3"]}',
            "",
            "候选片段：",
        ]
        for index, item in enumerate(chunks, start=1):
            section_title = str(item.chunk.metadata.get("section_title") or "").strip()
            preview = item.chunk.text.strip().replace("\r", "")[:1200]
            lines.append(
                "\n".join(
                    [
                        cls._candidate_id(index),
                        f"doc_id={item.chunk.doc_id}",
                        f"chunk_id={item.chunk.chunk_id}",
                        f"vector_score={item.score:.4f}",
                        f"section_title={section_title or '(none)'}",
                        "content:",
                        preview,
                    ]
                )
            )
            lines.append("")
        return "\n".join(lines).strip()

    @classmethod
    def _parse_ranked_ids(cls, raw: str) -> list[str]:
        text = (raw or "").strip()
        if not text:
            return []

        candidates = [text]
        candidates.extend(block.strip() for block in _JSON_BLOCK_RE.findall(text) if block.strip())

        for candidate in candidates:
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                continue

            if isinstance(payload, dict):
                ranked_ids = payload.get("ranked_ids")
                if isinstance(ranked_ids, list):
                    return [str(item).strip() for item in ranked_ids if str(item).strip()]
            if isinstance(payload, list):
                return [str(item).strip() for item in payload if str(item).strip()]
        return []

    def _fallback_score(self, *, query: str, item: ScoredChunk) -> float:
        query_tokens = set(_TOKEN_RE.findall(query.lower()))
        text_tokens = _TOKEN_RE.findall(item.chunk.text.lower())
        if not query_tokens or not text_tokens:
            return item.score

        overlap = sum(1 for token in text_tokens if token in query_tokens)
        coverage = overlap / max(1, len(query_tokens))
        density = overlap / max(1, len(text_tokens))

        section_title = str(item.chunk.metadata.get("section_title", "")).lower()
        metadata_bonus = 0.0
        if section_title and any(token in section_title for token in query_tokens):
            metadata_bonus = 0.08

        return (item.score * 0.7) + (coverage * 0.2) + (density * 0.02) + metadata_bonus
