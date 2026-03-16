from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
from redis.asyncio import Redis

from app.core.chunking import make_chunks_from_text
from app.core.redis_client import key
from app.core.vector_types import DocumentChunk


@dataclass(frozen=True)
class ScoredChunk:
    chunk: DocumentChunk
    score: float


class RedisVectorStore:
    """
    Minimal vector store backed by Redis.

    This is intentionally simple and optimized for small/medium corpora:
    - Vectors are stored as JSON strings in Redis keys.
    - Similarity search performs a linear scan and cosine similarity in Python.
    """

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    @staticmethod
    def _chunk_key(doc_id: str, chunk_id: str) -> str:
        return key("vs", doc_id, chunk_id)

    @staticmethod
    def _index_key() -> str:
        return key("vs", "index")

    @staticmethod
    def _doc_meta_key(doc_id: str) -> str:
        return key("vs", "docmeta", doc_id)

    async def add_chunks(self, *, embeddings: Sequence[Sequence[float]], chunks: Sequence[DocumentChunk]) -> None:
        if len(embeddings) != len(chunks):
            raise ValueError("embeddings and chunks length mismatch")

        pipe = self._redis.pipeline(transaction=False)
        index_key = self._index_key()
        for emb, chunk in zip(embeddings, chunks):
            vec = list(map(float, emb))
            k = self._chunk_key(chunk.doc_id, chunk.chunk_id)
            payload = {
                "doc_id": chunk.doc_id,
                "chunk_id": chunk.chunk_id,
                "text": chunk.text,
                "metadata": chunk.metadata,
                "vector": vec,
            }
            pipe.set(k, json.dumps(payload, ensure_ascii=False))
            pipe.sadd(index_key, k)
        await pipe.execute()

    async def set_document_metadata(self, *, doc_id: str, metadata: dict[str, Any]) -> None:
        k = self._doc_meta_key(doc_id)
        payload = {"doc_id": doc_id, "metadata": metadata}
        await self._redis.set(k, json.dumps(payload, ensure_ascii=False))

    async def get_document_info(self, doc_id: str) -> tuple[dict[str, Any] | None, int]:
        meta_key = self._doc_meta_key(doc_id)
        raw = await self._redis.get(meta_key)
        metadata: dict[str, Any] | None = None
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                md = parsed.get("metadata")
                if isinstance(md, dict):
                    metadata = md

        index_key = self._index_key()
        keys = await self._redis.smembers(index_key)
        chunk_count = 0
        for k in keys:
            parts = str(k).split(":")
            if len(parts) >= 3 and parts[1] == "vs" and parts[2] == doc_id:
                chunk_count += 1
        return metadata, chunk_count

    async def similarity_search(
        self,
        *,
        query_vector: Sequence[float],
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[ScoredChunk]:
        """
        Perform cosine-similarity search over all indexed chunks.
        """
        filters = filters or {}
        index_key = self._index_key()
        keys = await self._redis.smembers(index_key)
        if not keys:
            return []

        q = np.array(list(map(float, query_vector)), dtype="float32")
        if np.linalg.norm(q) == 0:
            return []

        scored: list[ScoredChunk] = []
        for k in keys:
            raw = await self._redis.get(k)
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            md = data.get("metadata") or {}
            if not isinstance(md, dict):
                md = {}
            if any(md.get(fk) != fv for fk, fv in filters.items()):
                continue

            vec = np.array(data.get("vector", []), dtype="float32")
            if vec.size == 0 or vec.shape != q.shape:
                continue
            denom = float(np.linalg.norm(q) * np.linalg.norm(vec))
            if denom == 0.0:
                continue
            score = float(np.dot(q, vec) / denom)

            chunk = DocumentChunk(
                doc_id=str(data.get("doc_id", "")),
                chunk_id=str(data.get("chunk_id", "")),
                text=str(data.get("text", "")),
                metadata=md,
            )
            scored.append(ScoredChunk(chunk=chunk, score=score))

        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[: max(1, top_k)]

    async def list_chunks(
        self,
        *,
        doc_id: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[DocumentChunk]:
        filters = filters or {}
        index_key = self._index_key()
        keys = await self._redis.smembers(index_key)
        if not keys:
            return []

        chunks: list[DocumentChunk] = []
        for k in keys:
            raw = await self._redis.get(k)
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue

            current_doc_id = str(data.get("doc_id", ""))
            if doc_id and current_doc_id != doc_id:
                continue

            md = data.get("metadata") or {}
            if not isinstance(md, dict):
                md = {}
            if any(md.get(fk) != fv for fk, fv in filters.items()):
                continue

            chunks.append(
                DocumentChunk(
                    doc_id=current_doc_id,
                    chunk_id=str(data.get("chunk_id", "")),
                    text=str(data.get("text", "")),
                    metadata=md,
                )
            )

        chunks.sort(
            key=lambda chunk: (
                chunk.doc_id,
                int(chunk.metadata.get("order", 0)),
                chunk.chunk_id,
            )
        )
        return chunks

