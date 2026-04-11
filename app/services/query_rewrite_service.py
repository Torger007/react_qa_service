from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from app.core.config import settings
from app.core.llm_client import LLMClient

_SPLIT_RE = re.compile(r"[\s,，。！？;；:：/\\]+")
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_POLITE_PREFIX_RE = re.compile(r"^(?:(?:请问)|请|帮我|麻烦)+")


class RewriteLLM(Protocol):
    async def chat(
        self,
        *,
        messages: list[dict[str, str]],
        model: str | None = None,
        timeout_seconds: int | None = None,
    ) -> str: ...


@dataclass(frozen=True)
class QueryRewriteService:
    llm: LLMClient | RewriteLLM | None = None
    max_queries: int = 3

    async def rewrite(self, *, question: str) -> list[str]:
        normalized_question = question.strip()
        if not normalized_question:
            return []

        variants = [normalized_question]
        variants.extend(await self._llm_rewrites(normalized_question))
        variants.extend(self._fallback_rewrites(normalized_question))
        return self._dedupe_queries(variants)

    async def _llm_rewrites(self, question: str) -> list[str]:
        if self.llm is None or self.max_queries <= 1:
            return []

        try:
            raw = await self.llm.chat(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是企业文档检索改写助手。"
                            "请围绕用户原问题，生成更适合检索的中文查询改写。"
                            "改写应保留原意，优先补充主题词、对象词、关键约束，不要扩写成回答。"
                            "只输出 JSON，不要输出解释。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"原问题：{question}\n"
                            f"请输出不超过 {max(1, self.max_queries - 1)} 条改写查询。"
                            '格式示例：{"queries":["改写1","改写2"]}'
                        ),
                    },
                ],
                model=settings.query_rewrite_model,
                timeout_seconds=settings.query_rewrite_timeout_seconds,
            )
        except Exception:
            return []

        return self._parse_queries(raw)

    def _fallback_rewrites(self, question: str) -> list[str]:
        stripped = _POLITE_PREFIX_RE.sub("", question).strip()
        tokens = [token for token in _SPLIT_RE.split(stripped) if token]
        keyword_query = " ".join(tokens[:6]).strip()

        variants: list[str] = []
        if stripped and stripped != question:
            variants.append(stripped)
        if keyword_query and keyword_query not in {question, stripped}:
            variants.append(keyword_query)
        if tokens:
            focus = " ".join(tokens[-3:]).strip()
            if focus and focus not in {question, stripped, keyword_query}:
                variants.append(focus)
        return variants

    def _dedupe_queries(self, variants: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for item in variants:
            normalized = " ".join(item.split()).strip()
            if not normalized or normalized in seen:
                continue
            deduped.append(normalized)
            seen.add(normalized)
            if len(deduped) >= max(1, self.max_queries):
                break
        return deduped

    @classmethod
    def _parse_queries(cls, raw: str) -> list[str]:
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
                queries = payload.get("queries")
                if isinstance(queries, list):
                    return [str(item).strip() for item in queries if str(item).strip()]
            if isinstance(payload, list):
                return [str(item).strip() for item in payload if str(item).strip()]
        return []
