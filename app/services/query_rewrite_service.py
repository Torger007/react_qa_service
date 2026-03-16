from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from app.core.llm_client import LLMClient

_SPLIT_RE = re.compile(r"[\s,，。！？;；]+")


class RewriteLLM(Protocol):
    async def chat(self, *, messages: list[dict[str, str]]) -> str: ...


@dataclass(frozen=True)
class QueryRewriteService:
    llm: LLMClient | RewriteLLM | None = None
    max_queries: int = 3

    async def rewrite(self, *, question: str) -> list[str]:
        variants = [question.strip()]
        variants.extend(self._fallback_rewrites(question))

        deduped: list[str] = []
        seen: set[str] = set()
        for item in variants:
            normalized = item.strip()
            if not normalized or normalized in seen:
                continue
            deduped.append(normalized)
            seen.add(normalized)
            if len(deduped) >= max(1, self.max_queries):
                break
        return deduped

    def _fallback_rewrites(self, question: str) -> list[str]:
        normalized = question.strip()
        stripped = re.sub(r"^(请|帮我|麻烦|请问)", "", normalized).strip()
        tokens = [token for token in _SPLIT_RE.split(stripped) if token]
        keyword_query = " ".join(tokens[:6]).strip()

        variants: list[str] = []
        if stripped and stripped != normalized:
            variants.append(stripped)
        if keyword_query and keyword_query not in {normalized, stripped}:
            variants.append(keyword_query)
        if tokens:
            focus = " ".join(tokens[-3:]).strip()
            if focus and focus not in {normalized, stripped, keyword_query}:
                variants.append(focus)
        return variants
