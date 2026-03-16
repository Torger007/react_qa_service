from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.llm_client import LLMClient
from app.core.vector_types import DocumentChunk
from app.prompts.summary_prompts import (
    SUMMARY_MAP_SYSTEM_PROMPT,
    SUMMARY_REDUCE_SYSTEM_PROMPT,
    build_map_prompt,
    build_reduce_prompt,
)


class SummaryLLM(Protocol):
    async def chat(self, *, messages: list[dict[str, str]]) -> str: ...


@dataclass(frozen=True)
class SummaryResult:
    answer: str
    partial_summaries: list[str]


class SummaryService:
    def __init__(self, *, llm: LLMClient | SummaryLLM | None = None, group_size: int = 4) -> None:
        self._llm = llm
        self._group_size = max(1, group_size)

    async def summarize(self, *, question: str, chunks: list[DocumentChunk]) -> SummaryResult:
        if not chunks:
            return SummaryResult(
                answer="未找到可用于总结的文档内容，因此暂时无法生成全文总结。",
                partial_summaries=[],
            )

        groups = [chunks[index : index + self._group_size] for index in range(0, len(chunks), self._group_size)]
        partial_summaries: list[str] = []
        for group in groups:
            group_text = "\n\n".join(chunk.text for chunk in group if chunk.text.strip())
            if not group_text:
                continue
            partial_summaries.append(await self._summarize_group(question=question, content=group_text))

        if not partial_summaries:
            return SummaryResult(
                answer="未找到可用于总结的有效文本内容。",
                partial_summaries=[],
            )

        if len(partial_summaries) == 1:
            final_answer = partial_summaries[0]
        else:
            final_answer = await self._reduce_summaries(question=question, partial_summaries=partial_summaries)

        return SummaryResult(
            answer=self._normalize_summary_output(final_answer, partial_summaries=partial_summaries),
            partial_summaries=partial_summaries,
        )

    async def _summarize_group(self, *, question: str, content: str) -> str:
        if self._llm is None:
            return self._fallback_group_summary(content)

        return await self._llm.chat(
            messages=[
                {"role": "system", "content": SUMMARY_MAP_SYSTEM_PROMPT},
                {"role": "user", "content": build_map_prompt(question=question, content=content)},
            ]
        )

    async def _reduce_summaries(self, *, question: str, partial_summaries: list[str]) -> str:
        if self._llm is None:
            return self._fallback_reduce_summary(partial_summaries)

        return await self._llm.chat(
            messages=[
                {"role": "system", "content": SUMMARY_REDUCE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_reduce_prompt(question=question, partial_summaries=partial_summaries),
                },
            ]
        )

    @staticmethod
    def _fallback_group_summary(content: str) -> str:
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        bullets = lines[:5]
        summary_lines = ["一、文档概览", "该部分主要覆盖以下内容：", "", "二、关键要点"]
        summary_lines.extend(f"- {line}" for line in bullets[:3] or ["未提取到明确要点。"])
        summary_lines.extend(["", "三、风险与问题", "- 需结合原文进一步确认潜在风险。", "", "四、下一步建议", "- 建议结合完整文档继续核对。"])
        return "\n".join(summary_lines)

    @staticmethod
    def _fallback_reduce_summary(partial_summaries: list[str]) -> str:
        merged = "\n\n".join(partial_summaries[:3])
        return (
            "一、文档概览\n"
            "该文档已完成分段总结，核心内容如下。\n\n"
            "二、关键要点\n"
            f"{merged}\n\n"
            "三、风险与问题\n"
            "- 以上结果基于已抽取的文本片段，可能仍需回看原文核对细节。\n\n"
            "四、下一步建议\n"
            "- 如需更细的章节总结，可继续指定具体章节。"
        )

    @staticmethod
    def _normalize_summary_output(raw: str, *, partial_summaries: list[str]) -> str:
        text = (raw or "").strip()
        headers = ("一、文档概览", "二、关键要点", "三、风险与问题", "四、下一步建议")
        if text and all(header in text for header in headers):
            return text

        lines = [line.strip(" -\t") for line in text.splitlines() if line.strip()]
        fallback_lines = [
            line.strip(" -\t")
            for summary in partial_summaries
            for line in summary.splitlines()
            if line.strip()
        ]
        source_lines = lines or fallback_lines

        overview = source_lines[0] if source_lines else "已完成文档级总结，但当前内容较为简略。"
        key_points = source_lines[:3] or ["未提取到明确要点。"]
        risks = [line for line in source_lines if "风险" in line or "问题" in line][:2]
        if not risks:
            risks = ["未在当前文档片段中识别出明确风险，建议结合原文复核。"]
        next_steps = [line for line in source_lines if "建议" in line or "下一步" in line][:2]
        if not next_steps:
            next_steps = ["如需继续深入，可指定章节或问题做进一步分析。"]

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
