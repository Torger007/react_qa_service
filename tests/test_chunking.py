from __future__ import annotations

from app.core.chunking import make_chunks_from_text


def test_chunking_prefers_headings_and_paragraphs():
    text = (
        "# Overview\n"
        "This is the first paragraph.\n\n"
        "## Risks\n"
        "- Item one\n"
        "- Item two\n"
    )

    chunks = make_chunks_from_text(doc_id="doc-1", text=text, metadata={"source": "unit"})

    assert len(chunks) == 3
    assert chunks[0].metadata["section_title"] == "Overview"
    assert chunks[0].metadata["heading_path"] == ["Overview"]
    assert chunks[1].metadata["section_title"] == "Risks"
    assert chunks[1].metadata["chunk_kind"] == "list"
    assert chunks[2].metadata["section_title"] == "Next"


def test_chunking_splits_large_blocks_with_overlap_metadata():
    text = "# Section\n" + ("A" * 1200)

    chunks = make_chunks_from_text(
        doc_id="doc-2",
        text=text,
        chunk_size=400,
        chunk_overlap=50,
    )

    assert len(chunks) >= 3
    assert all(chunk.metadata["section_title"] == "Section" for chunk in chunks)
    assert [chunk.metadata["order"] for chunk in chunks] == list(range(len(chunks)))


def test_chunking_preserves_page_metadata():
    text = "# Page One\nAlpha\n\f# Page Two\nBeta"

    chunks = make_chunks_from_text(doc_id="doc-3", text=text)

    assert len(chunks) == 2
    assert chunks[0].metadata["page"] == 1
    assert chunks[1].metadata["page"] == 2


def test_chunking_keeps_table_blocks_together():
    text = "# Data\nCol A | Col B\n1 | 2\n3 | 4"

    chunks = make_chunks_from_text(doc_id="doc-4", text=text)

    assert len(chunks) == 1
    assert chunks[0].metadata["chunk_kind"] == "table"
