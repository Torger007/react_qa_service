from __future__ import annotations

from app.core.vector_store import ScoredChunk
from app.core.vector_types import DocumentChunk
from app.services.retrieval_postprocess import postprocess_retrieved_chunks


def _scored(doc_id: str, order: int, text: str, score: float, *, section: str = "Overview") -> ScoredChunk:
    return ScoredChunk(
        chunk=DocumentChunk(
            doc_id=doc_id,
            chunk_id=f"{doc_id}-{order}",
            text=text,
            metadata={"order": order, "section_title": section},
        ),
        score=score,
    )


def test_postprocess_dedupes_and_merges_adjacent_chunks():
    chunks = [
        _scored("doc-1", 0, "alpha", 0.91),
        _scored("doc-1", 0, "alpha", 0.87),
        _scored("doc-1", 1, "beta", 0.89),
        _scored("doc-2", 0, "gamma", 0.86),
    ]

    processed = postprocess_retrieved_chunks(chunks, max_results=4)

    assert len(processed) == 2
    assert processed[0].chunk.doc_id == "doc-1"
    assert processed[0].chunk.text == "alpha\n\nbeta"
    assert processed[0].chunk.metadata["merged_chunk_count"] == 2
    assert processed[1].chunk.doc_id == "doc-2"
