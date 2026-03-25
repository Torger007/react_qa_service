from __future__ import annotations

from dataclasses import replace

from app.core.vector_store import ScoredChunk
from app.core.vector_types import DocumentChunk


def postprocess_retrieved_chunks(
    chunks: list[ScoredChunk],
    *,
    max_results: int, #最终返回的最大结果数
) -> list[ScoredChunk]:
    if not chunks:
        return []

    #去重
    deduped = _dedupe_chunks(chunks)

    #合并相邻块
    merged = _merge_adjacent_chunks(deduped)

    #排序（按分数，文档ID，排序）
    merged.sort(
        key=lambda item: (
            -item.score,
            item.chunk.doc_id,
            int(item.chunk.metadata.get("order", 0)),
        )
    )

    #返回前max_results个
    return merged[: max(1, max_results)]


def _dedupe_chunks(chunks: list[ScoredChunk]) -> list[ScoredChunk]:
    # 使用字典去重 （1.文档ID相同 2.顺序号相同，在文档中的位置相同。 3.文本内容一模一样）
    seen: dict[tuple[str, int, str], ScoredChunk] = {}
    for item in chunks:
        # 提取元数据
        order = int(item.chunk.metadata.get("order", 0))
        text = item.chunk.text.strip()
        #生成唯一key
        key = (item.chunk.doc_id, order, text)

        previous = seen.get(key)
        if previous is None or item.score > previous.score:
            seen[key] = item
    return list(seen.values())


def _merge_adjacent_chunks(chunks: list[ScoredChunk]) -> list[ScoredChunk]:
    # 先按（文档ID，顺序，分数）排序
    ordered = sorted(
        chunks,
        key=lambda item: (
            item.chunk.doc_id,
            int(item.chunk.metadata.get("order", 0)),
            -item.score,
        ),
    )
    merged: list[ScoredChunk] = []

    for item in ordered:
        if not merged:
            merged.append(item)
            continue

        #尝试和上一个合并
        previous = merged[-1]
        if _can_merge(previous, item):
            merged[-1] = _merge_pair(previous, item)
            continue
        merged.append(item) #不合并，添加为新元素

    return merged


def _can_merge(left: ScoredChunk, right: ScoredChunk) -> bool:
    #1.必须来自同一文档
    if left.chunk.doc_id != right.chunk.doc_id:
        return False
    # 2.必须是相邻的块
    left_order = int(left.chunk.metadata.get("order", 0))
    right_order = int(right.chunk.metadata.get("order", 0))
    if right_order != left_order + 1:
        return False
    #必须要有相同的章节标题
    return left.chunk.metadata.get("section_title") == right.chunk.metadata.get("section_title")


#实际合并操作
def _merge_pair(left: ScoredChunk, right: ScoredChunk) -> ScoredChunk:
    #提取顺序号
    left_order = int(left.chunk.metadata.get("order", 0))
    right_order = int(right.chunk.metadata.get("order", left_order))

    #合并文本
    merged_text = f"{left.chunk.text}\n\n{right.chunk.text}".strip()

    #合并元数据
    merged_metadata = {
        **left.chunk.metadata,
        "order": left_order,
        "start_order": left.chunk.metadata.get("start_order", left_order),
        "end_order": right.chunk.metadata.get("end_order", right_order),
        "merged_chunk_count": int(left.chunk.metadata.get("merged_chunk_count", 1))
        + int(right.chunk.metadata.get("merged_chunk_count", 1)),
    }

    #创建新块
    merged_chunk = DocumentChunk(
        doc_id=left.chunk.doc_id,
        chunk_id=f"{left.chunk.chunk_id}+{right.chunk.chunk_id}",
        text=merged_text,
        metadata=merged_metadata,
    )
    # 创建新ScoredChunk，分数取两者最大值
    return replace(left, chunk=merged_chunk, score=max(left.score, right.score))
