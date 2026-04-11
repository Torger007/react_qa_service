from __future__ import annotations

from dataclasses import replace
from typing import Any

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

    if _combined_text_length(left, right) > 2400:
        return False

    left_kind = _metadata_str(left.chunk.metadata, "chunk_kind")
    right_kind = _metadata_str(right.chunk.metadata, "chunk_kind")
    if left_kind == "table" or right_kind == "table":
        return left_kind == right_kind and _same_structure(left.chunk.metadata, right.chunk.metadata)

    if _same_section(left.chunk.metadata, right.chunk.metadata):
        return True

    if _same_heading_path(left.chunk.metadata, right.chunk.metadata):
        return True

    if _same_page(left.chunk.metadata, right.chunk.metadata) and _same_kind(left_kind, right_kind):
        return True

    if _has_missing_section(left.chunk.metadata, right.chunk.metadata) and _same_kind(left_kind, right_kind):
        return True

    return False


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
    if not merged_metadata.get("section_title"):
        merged_metadata["section_title"] = right.chunk.metadata.get("section_title")
    if not merged_metadata.get("heading_path"):
        merged_metadata["heading_path"] = right.chunk.metadata.get("heading_path")
    if not merged_metadata.get("page"):
        merged_metadata["page"] = right.chunk.metadata.get("page")
    if not merged_metadata.get("chunk_kind"):
        merged_metadata["chunk_kind"] = right.chunk.metadata.get("chunk_kind")

    merged_chunk = DocumentChunk(
        doc_id=left.chunk.doc_id,
        chunk_id=f"{left.chunk.chunk_id}+{right.chunk.chunk_id}",
        text=merged_text,
        metadata=merged_metadata,
    )
    return replace(left, chunk=merged_chunk, score=max(left.score, right.score))


def _metadata_str(metadata: dict[str, Any], key: str) -> str:
    value = metadata.get(key)
    return str(value).strip().lower() if value is not None else ""


def _same_section(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_section = _metadata_str(left, "section_title")
    right_section = _metadata_str(right, "section_title")
    return bool(left_section) and left_section == right_section


def _same_heading_path(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_path = left.get("heading_path")
    right_path = right.get("heading_path")
    if not isinstance(left_path, list) or not isinstance(right_path, list):
        return False
    return left_path == right_path and bool(left_path)


def _same_page(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return left.get("page") is not None and left.get("page") == right.get("page")


def _same_kind(left_kind: str, right_kind: str) -> bool:
    if not left_kind or not right_kind:
        return True
    return left_kind == right_kind


def _has_missing_section(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return not _metadata_str(left, "section_title") or not _metadata_str(right, "section_title")


def _same_structure(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return _same_heading_path(left, right) or _same_page(left, right) or _same_section(left, right)


def _combined_text_length(left: ScoredChunk, right: ScoredChunk) -> int:
    return len(left.chunk.text.strip()) + len(right.chunk.text.strip())
