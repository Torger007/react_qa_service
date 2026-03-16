from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.core.vector_types import DocumentChunk

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_ORDERED_LIST_RE = re.compile(r"^\s*(\d+[\.\)])\s+")
_UNORDERED_LIST_RE = re.compile(r"^\s*[-*+]\s+")
_TABLE_RE = re.compile(r"\s+\|\s+")
_PAGE_MARKER_RE = re.compile(r"\f")


@dataclass(frozen=True)
class StructuredBlock:
    text: str
    kind: str
    page: int
    section_title: str | None
    heading_path: list[str]


def make_chunks_from_text(
    *,
    doc_id: str | None,
    text: str,
    metadata: dict[str, Any] | None = None,
    chunk_size: int = 800,
    chunk_overlap: int = 200,
) -> list[DocumentChunk]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if chunk_overlap < 0:
        raise ValueError("chunk_overlap must be non-negative")
    if not text.strip():
        return []

    resolved_doc_id = doc_id or str(uuid4())
    doc_metadata = dict(metadata or {})
    blocks = _split_into_blocks(text)

    chunks: list[DocumentChunk] = []
    pending_blocks: list[StructuredBlock] = []
    current_length = 0
    order = 0

    for block in blocks:
        if pending_blocks and _should_flush_before_append(pending_blocks[-1], block):
            built, order = _build_chunk_group(
                doc_id=resolved_doc_id,
                doc_metadata=doc_metadata,
                blocks=pending_blocks,
                start_order=order,
            )
            chunks.extend(built)
            pending_blocks = []
            current_length = 0

        if len(block.text) > chunk_size:
            if pending_blocks:
                built, order = _build_chunk_group(
                    doc_id=resolved_doc_id,
                    doc_metadata=doc_metadata,
                    blocks=pending_blocks,
                    start_order=order,
                )
                chunks.extend(built)
                pending_blocks = []
                current_length = 0
            built, order = _split_oversized_block(
                doc_id=resolved_doc_id,
                doc_metadata=doc_metadata,
                block=block,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                start_order=order,
            )
            chunks.extend(built)
            continue

        projected = current_length + len(block.text) + (2 if pending_blocks else 0)
        if pending_blocks and projected > chunk_size:
            built, order = _build_chunk_group(
                doc_id=resolved_doc_id,
                doc_metadata=doc_metadata,
                blocks=pending_blocks,
                start_order=order,
            )
            chunks.extend(built)
            pending_blocks = []
            current_length = 0

        pending_blocks.append(block)
        current_length += len(block.text) + (2 if current_length else 0)

    if pending_blocks:
        built, order = _build_chunk_group(
            doc_id=resolved_doc_id,
            doc_metadata=doc_metadata,
            blocks=pending_blocks,
            start_order=order,
        )
        chunks.extend(built)

    return chunks


def _split_into_blocks(text: str) -> list[StructuredBlock]:
    pages = _split_pages(text)
    blocks: list[StructuredBlock] = []
    heading_stack: list[str] = []

    for page_number, page_text in pages:
        paragraph_lines: list[str] = []
        for raw_line in page_text.splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()

            heading = _parse_heading(stripped)
            if heading:
                if paragraph_lines:
                    blocks.append(_make_block(paragraph_lines, page_number, heading_stack))
                    paragraph_lines = []
                level, title = heading
                heading_stack = heading_stack[: level - 1]
                heading_stack.append(title)
                continue

            if not stripped:
                if paragraph_lines:
                    blocks.append(_make_block(paragraph_lines, page_number, heading_stack))
                    paragraph_lines = []
                continue

            if _is_list_item(stripped) or _is_table_row(stripped):
                if paragraph_lines and _infer_kind(paragraph_lines) != _infer_kind([stripped]):
                    blocks.append(_make_block(paragraph_lines, page_number, heading_stack))
                    paragraph_lines = []

            paragraph_lines.append(stripped)

        if paragraph_lines:
            blocks.append(_make_block(paragraph_lines, page_number, heading_stack))

    return [block for block in blocks if block.text.strip()]


def _split_pages(text: str) -> list[tuple[int, str]]:
    if not _PAGE_MARKER_RE.search(text):
        return [(1, text)]

    parts = [part.strip("\n") for part in _PAGE_MARKER_RE.split(text)]
    return [(index + 1, part) for index, part in enumerate(parts) if part.strip()]


def _parse_heading(line: str) -> tuple[int, str] | None:
    if not line:
        return None
    match = _HEADING_RE.match(line)
    if match:
        return len(match.group(1)), match.group(2).strip()
    if len(line) <= 80 and line.endswith(":"):
        return 2, line[:-1].strip()
    return None


def _is_list_item(line: str) -> bool:
    return bool(_ORDERED_LIST_RE.match(line) or _UNORDERED_LIST_RE.match(line))


def _is_table_row(line: str) -> bool:
    return bool(_TABLE_RE.search(line))


def _infer_kind(lines: list[str]) -> str:
    if lines and all(_is_table_row(line) for line in lines):
        return "table"
    if lines and all(_is_list_item(line) for line in lines):
        return "list"
    return "paragraph"


def _make_block(lines: list[str], page: int, heading_path: list[str]) -> StructuredBlock:
    kind = _infer_kind(lines)
    text = "\n".join(lines).strip()
    return StructuredBlock(
        text=text,
        kind=kind,
        page=page,
        section_title=heading_path[-1] if heading_path else None,
        heading_path=list(heading_path),
    )


def _should_flush_before_append(current: StructuredBlock, incoming: StructuredBlock) -> bool:
    if current.page != incoming.page:
        return True
    if current.heading_path != incoming.heading_path:
        return True
    if current.kind != incoming.kind and {current.kind, incoming.kind} & {"list", "table"}:
        return True
    return False


def _build_chunk_group(
    *,
    doc_id: str,
    doc_metadata: dict[str, Any],
    blocks: list[StructuredBlock],
    start_order: int,
) -> tuple[list[DocumentChunk], int]:
    text = "\n\n".join(block.text for block in blocks).strip()
    if not text:
        return [], start_order

    primary = blocks[0]
    merged_metadata = {
        **doc_metadata,
        "section_title": primary.section_title,
        "order": start_order,
        "page": primary.page,
        "heading_path": primary.heading_path,
        "chunk_kind": primary.kind,
    }
    chunk = DocumentChunk(
        doc_id=doc_id,
        chunk_id=f"{doc_id}-{start_order}",
        text=text,
        metadata=merged_metadata,
    )
    return [chunk], start_order + 1


def _split_oversized_block(
    *,
    doc_id: str,
    doc_metadata: dict[str, Any],
    block: StructuredBlock,
    chunk_size: int,
    chunk_overlap: int,
    start_order: int,
) -> tuple[list[DocumentChunk], int]:
    chunks: list[DocumentChunk] = []
    start = 0
    order = start_order
    text = block.text

    while start < len(text):
        end = min(len(text), start + chunk_size)
        snippet = text[start:end].strip()
        if snippet:
            chunks.append(
                DocumentChunk(
                    doc_id=doc_id,
                    chunk_id=f"{doc_id}-{order}",
                    text=snippet,
                    metadata={
                        **doc_metadata,
                        "section_title": block.section_title,
                        "order": order,
                        "page": block.page,
                        "heading_path": block.heading_path,
                        "chunk_kind": block.kind,
                    },
                )
            )
            order += 1
        if end >= len(text):
            break
        start = max(start + 1, end - chunk_overlap)

    return chunks, order
