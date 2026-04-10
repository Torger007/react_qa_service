from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

import anyio

from app.core.config import settings
from app.core.vector_types import DocumentChunk
from app.prompts.summary_prompts import (
    SUMMARY_MAP_SYSTEM_PROMPT,
    SUMMARY_REDUCE_SYSTEM_PROMPT,
    build_map_prompt,
    build_reduce_prompt,
)


class SummaryLLM(Protocol):
    async def chat(
        self,
        *,
        messages: list[dict[str, str]],
        model: str | None = None,
        timeout_seconds: int | None = None,
    ) -> str: ...


@dataclass(frozen=True)
class SummaryResult:
    answer: str
    partial_summaries: list[str]


class SummaryService:
    def __init__(self, *, llm: SummaryLLM | None = None, group_size: int | None = None) -> None:
        self._llm = llm
        resolved_group_size = settings.summary_group_size if group_size is None else group_size
        self._group_size = max(1, resolved_group_size)

    async def summarize(self, *, question: str, chunks: list[DocumentChunk]) -> SummaryResult:
        if not chunks:
            return SummaryResult(
                answer="未找到可用于总结的文档内容，因此暂时无法生成全文总结。",
                partial_summaries=[],
            )

        if self._llm is None:
            answer = self._build_extractive_summary(question=question, chunks=chunks)
            return SummaryResult(answer=answer, partial_summaries=[])

        combined_content = "\n\n".join(chunk.text for chunk in chunks if chunk.text.strip())
        if combined_content and len(combined_content) <= settings.summary_single_pass_chars:
            answer = await self._summarize_single_pass(question=question, content=combined_content, chunks=chunks)
            return SummaryResult(answer=answer, partial_summaries=[])

        groups = [chunks[index : index + self._group_size] for index in range(0, len(chunks), self._group_size)]
        partial_summaries = await self._summarize_groups(question=question, groups=groups)

        if not partial_summaries:
            answer = self._build_extractive_summary(question=question, chunks=chunks)
            return SummaryResult(answer=answer, partial_summaries=[])

        if len(partial_summaries) == 1:
            final_answer = partial_summaries[0]
        else:
            final_answer = await self._reduce_summaries(question=question, partial_summaries=partial_summaries)

        return SummaryResult(
            answer=self._normalize_summary_output(final_answer, chunks=chunks, partial_summaries=partial_summaries),
            partial_summaries=partial_summaries,
        )

    async def _summarize_single_pass(
        self,
        *,
        question: str,
        content: str,
        chunks: list[DocumentChunk],
    ) -> str:
        if self._llm is None:
            return self._build_extractive_summary(question=question, chunks=chunks)
        try:
            with anyio.fail_after(settings.summary_timeout_seconds):
                answer = await self._llm.chat(
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "你是文档总结助手。请基于给定文档内容输出高质量中文总结，"
                                "必须使用以下结构：一、文档概览 二、关键要点 三、风险与问题 四、下一步建议。"
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"用户请求：{question}\n\n"
                                "请直接阅读以下文档内容并输出最终总结，不要先做局部摘要。\n\n"
                                f"文档内容：\n{content}"
                            ),
                        },
                    ],
                    timeout_seconds=settings.summary_timeout_seconds,
                )
                return self._normalize_summary_output(answer, chunks=chunks, partial_summaries=[])
        except Exception:
            return self._build_extractive_summary(question=question, chunks=chunks)

    async def _summarize_groups(
        self,
        *,
        question: str,
        groups: list[list[DocumentChunk]],
    ) -> list[str]:
        group_texts = [
            "\n\n".join(chunk.text for chunk in group if chunk.text.strip())
            for group in groups
        ]
        results: list[str | None] = [None] * len(group_texts)
        limiter = anyio.CapacityLimiter(settings.summary_max_parallelism)

        async def _run(index: int, content: str) -> None:
            if not content:
                return
            async with limiter:
                results[index] = await self._summarize_group(question=question, content=content)

        async with anyio.create_task_group() as task_group:
            for index, content in enumerate(group_texts):
                task_group.start_soon(_run, index, content)

        return [item for item in results if item]

    async def _summarize_group(self, *, question: str, content: str) -> str:
        if self._llm is None:
            return self._build_extractive_summary(
                question=question,
                chunks=[DocumentChunk(doc_id="fallback", chunk_id="fallback", text=content, metadata={})],
            )
        try:
            with anyio.fail_after(settings.summary_timeout_seconds):
                return await self._llm.chat(
                    messages=[
                        {"role": "system", "content": SUMMARY_MAP_SYSTEM_PROMPT},
                        {"role": "user", "content": build_map_prompt(question=question, content=content)},
                    ],
                    timeout_seconds=settings.summary_timeout_seconds,
                )
        except Exception:
            return self._build_extractive_summary(
                question=question,
                chunks=[DocumentChunk(doc_id="fallback", chunk_id="fallback", text=content, metadata={})],
            )

    async def _reduce_summaries(self, *, question: str, partial_summaries: list[str]) -> str:
        if self._llm is None:
            return self._build_extractive_summary(
                question=question,
                chunks=[
                    DocumentChunk(doc_id="reduce", chunk_id=f"reduce-{index}", text=text, metadata={})
                    for index, text in enumerate(partial_summaries)
                ],
            )
        try:
            with anyio.fail_after(settings.summary_timeout_seconds):
                return await self._llm.chat(
                    messages=[
                        {"role": "system", "content": SUMMARY_REDUCE_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": build_reduce_prompt(question=question, partial_summaries=partial_summaries),
                        },
                    ],
                    timeout_seconds=settings.summary_timeout_seconds,
                )
        except Exception:
            return self._build_extractive_summary(
                question=question,
                chunks=[
                    DocumentChunk(doc_id="reduce", chunk_id=f"reduce-{index}", text=text, metadata={})
                    for index, text in enumerate(partial_summaries)
                ],
            )

    @classmethod
    def _build_extractive_summary(cls, *, question: str, chunks: list[DocumentChunk]) -> str:
        candidate_lines = cls._collect_candidate_lines(chunks)
        overview = cls._build_overview(question=question, chunks=chunks, candidate_lines=candidate_lines)
        key_points = cls._select_key_points(candidate_lines)
        risks = cls._select_risks(candidate_lines)
        next_steps = cls._select_next_steps(candidate_lines)

        return "\n".join(
            [
                "一、文档概览",
                overview,
                "",
                "二、关键要点",
                *(f"- {item}" for item in key_points),
                "",
                "三、风险与问题",
                *(f"- {item}" for item in risks),
                "",
                "四、下一步建议",
                *(f"- {item}" for item in next_steps),
            ]
        )

    @classmethod
    def _collect_candidate_lines(cls, chunks: list[DocumentChunk]) -> list[str]:
        candidates: list[str] = []
        seen: set[str] = set()
        for chunk in chunks:
            section_title = str(chunk.metadata.get("section_title") or "").strip()
            for raw_piece in cls._split_text_units(chunk.text):
                piece = raw_piece.strip(" -\t")
                if len(piece) < 8:
                    continue
                normalized = re.sub(r"\s+", " ", piece).strip()
                if normalized in seen:
                    continue
                seen.add(normalized)
                if section_title and section_title.lower() not in normalized.lower():
                    candidates.append(f"[{section_title}] {normalized}")
                else:
                    candidates.append(normalized)
        return candidates

    @staticmethod
    def _split_text_units(text: str) -> list[str]:
        raw_lines = [line.strip() for line in text.splitlines() if line.strip()]
        units: list[str] = []
        for line in raw_lines:
            parts = re.split(r"(?<=[。！？.!?；;])\s+|\s{2,}", line)
            for part in parts:
                stripped = part.strip()
                if stripped:
                    units.append(stripped)
        return units

    @classmethod
    def _build_overview(cls, *, question: str, chunks: list[DocumentChunk], candidate_lines: list[str]) -> str:
        section_titles: list[str] = []
        for chunk in chunks:
            title = str(chunk.metadata.get("section_title") or "").strip()
            if title and title not in section_titles:
                section_titles.append(title)

        first_line = candidate_lines[0] if candidate_lines else "文档包含若干可供总结的片段。"
        if section_titles:
            title_preview = "、".join(section_titles[:4])
            return f"围绕“{question}”，文档主要覆盖 {title_preview} 等内容，核心信息可概括为：{first_line}"
        return f"围绕“{question}”，文档核心信息可概括为：{first_line}"

    @classmethod
    def _select_key_points(cls, candidate_lines: list[str]) -> list[str]:
        scored = sorted(candidate_lines, key=cls._score_key_point, reverse=True)
        selected = cls._take_unique(scored, limit=4)
        return selected or ["未能从当前文档片段中提取出足够稳定的关键要点。"]

    @classmethod
    def _select_risks(cls, candidate_lines: list[str]) -> list[str]:
        risk_markers = ("风险", "问题", "阻塞", "限制", "不足", "异常", "失败", "依赖", "超时", "缺失")
        risk_lines = [line for line in candidate_lines if any(marker in line for marker in risk_markers)]
        selected = cls._take_unique(sorted(risk_lines, key=len, reverse=True), limit=3)
        return selected or ["当前片段中未识别到明确风险，建议结合原文重点核查异常、限制和依赖项。"]

    @classmethod
    def _select_next_steps(cls, candidate_lines: list[str]) -> list[str]:
        action_markers = ("建议", "下一步", "需要", "应当", "可以", "执行", "验证", "检查", "优化", "修复")
        next_lines = [line for line in candidate_lines if any(marker in line for marker in action_markers)]
        selected = cls._take_unique(sorted(next_lines, key=cls._score_next_step, reverse=True), limit=3)
        return selected or ["建议继续基于当前总结结果，逐项验证关键结论、风险点和后续行动。"]

    @staticmethod
    def _score_key_point(line: str) -> int:
        score = len(line)
        if line.startswith("["):
            score += 10
        important_markers = ("目标", "核心", "主要", "方案", "流程", "结论", "架构", "范围")
        score += sum(12 for marker in important_markers if marker in line)
        return score

    @staticmethod
    def _score_next_step(line: str) -> int:
        score = len(line)
        priority_markers = ("建议", "下一步", "需要", "执行", "验证", "修复", "优化")
        score += sum(10 for marker in priority_markers if marker in line)
        return score

    @staticmethod
    def _take_unique(lines: list[str], *, limit: int) -> list[str]:
        selected: list[str] = []
        seen_roots: set[str] = set()
        for line in lines:
            root = re.sub(r"^\[[^\]]+\]\s*", "", line)
            root = re.sub(r"\s+", "", root)
            if not root or root in seen_roots:
                continue
            seen_roots.add(root)
            selected.append(line)
            if len(selected) >= limit:
                break
        return selected

    @classmethod
    def _normalize_summary_output(
        cls,
        raw: str,
        *,
        chunks: list[DocumentChunk],
        partial_summaries: list[str],
    ) -> str:
        text = (raw or "").strip()
        headers = ("一、文档概览", "二、关键要点", "三、风险与问题", "四、下一步建议")
        if text and all(header in text for header in headers):
            return text

        fallback_chunks = chunks or [
            DocumentChunk(doc_id="normalize", chunk_id=f"normalize-{index}", text=summary, metadata={})
            for index, summary in enumerate(partial_summaries)
        ]
        return cls._build_extractive_summary(question="请总结文档", chunks=fallback_chunks)
