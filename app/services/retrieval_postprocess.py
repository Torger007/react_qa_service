from __future__ import annotations

from dataclasses import replace

from app.core.vector_store import ScoredChunk
from app.core.vector_types import DocumentChunk


def postprocess_retrieved_chunks(
    chunks: list[ScoredChunk],
    *,
    max_results: int,
) -> list[ScoredChunk]:
    if not chunks:
        return []

    deduped = _dedupe_chunks(chunks)
    merged = _merge_adjacent_chunks(deduped)
    merged.sort(
        key=lambda item: (
            -item.score,
            item.chunk.doc_id,
            int(item.chunk.metadata.get("order", 0)),
        )
    )
    return merged[: max(1, max_results)]


def _dedupe_chunks(chunks: list[ScoredChunk]) -> list[ScoredChunk]:
    seen: dict[tuple[str, int, str], ScoredChunk] = {}
    for item in chunks:
        order = int(item.chunk.metadata.get("order", 0))
        text = item.chunk.text.strip()
        key = (item.chunk.doc_id, order, text)
        previous = seen.get(key)
        if previous is None or item.score > previous.score:
            seen[key] = item
    return list(seen.values())


def _merge_adjacent_chunks(chunks: list[ScoredChunk]) -> list[ScoredChunk]:
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

        previous = merged[-1]
        if _can_merge(previous, item):
            merged[-1] = _merge_pair(previous, item)
            continue
        merged.append(item)

    return merged


def _can_merge(left: ScoredChunk, right: ScoredChunk) -> bool:
    if left.chunk.doc_id != right.chunk.doc_id:
        return False
    left_order = int(left.chunk.metadata.get("order", 0))
    right_order = int(right.chunk.metadata.get("order", 0))
    if right_order != left_order + 1:
        return False
    return left.chunk.metadata.get("section_title") == right.chunk.metadata.get("section_title")


def _merge_pair(left: ScoredChunk, right: ScoredChunk) -> ScoredChunk:
    left_order = int(left.chunk.metadata.get("order", 0))
    right_order = int(right.chunk.metadata.get("order", left_order))
    merged_text = f"{left.chunk.text}\n\n{right.chunk.text}".strip()
    merged_metadata = {
        **left.chunk.metadata,
        "order": left_order,
        "start_order": left.chunk.metadata.get("start_order", left_order),
        "end_order": right.chunk.metadata.get("end_order", right_order),
        "merged_chunk_count": int(left.chunk.metadata.get("merged_chunk_count", 1))
        + int(right.chunk.metadata.get("merged_chunk_count", 1)),
    }
    merged_chunk = DocumentChunk(
        doc_id=left.chunk.doc_id,
        chunk_id=f"{left.chunk.chunk_id}+{right.chunk.chunk_id}",
        text=merged_text,
        metadata=merged_metadata,
    )
    return replace(left, chunk=merged_chunk, score=max(left.score, right.score))
