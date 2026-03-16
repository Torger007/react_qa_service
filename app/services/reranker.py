from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from app.core.llm_client import LLMClient
from app.core.vector_store import ScoredChunk

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z0-9_]+")


class RerankLLM(Protocol):
    async def chat(self, *, messages: list[dict[str, str]]) -> str: ...


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
        return ranked[: max(1, top_k)]

    def _fallback_score(self, *, query: str, item: ScoredChunk) -> float:
        query_tokens = set(_TOKEN_RE.findall(query.lower()))
        text_tokens = _TOKEN_RE.findall(item.chunk.text.lower())
        if not query_tokens or not text_tokens:
            return item.score

        overlap = sum(1 for token in text_tokens if token in query_tokens)
        coverage = overlap / max(1, len(query_tokens))
        density = overlap / max(1, len(text_tokens))
        metadata_bonus = 0.0
        section_title = str(item.chunk.metadata.get("section_title", "")).lower()
        if section_title and any(token in section_title for token in query_tokens):
            metadata_bonus = 0.08

        return (item.score * 0.7) + (coverage * 0.2) + (density * 0.02) + metadata_bonus
