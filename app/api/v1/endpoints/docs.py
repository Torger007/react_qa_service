from __future__ import annotations

import json
import logging
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status

from app.core.config import settings
from app.core.redis_client import require_redis
from app.core.security import oauth2_scheme
from app.core.vector_store import RedisVectorStore, make_chunks_from_text
from app.models.qa_schemas import DocIndexRequest, DocIndexResponse, DocInfoResponse
from app.services.document_loader import read_text_from_upload

router = APIRouter()
logger = logging.getLogger(__name__)


def _friendly_index_error(exc: Exception) -> str:
    msg = str(exc)
    lower = msg.lower()
    if "json.set" in lower or "unknown command" in lower and "json" in lower:
        return (
            "Redis JSON commands are unavailable. Please use Redis Stack (or enable RedisJSON module), "
            "then retry."
        )
    return f"Document indexing failed: {exc.__class__.__name__}: {msg}"


async def _index_text(
    *,
    request: Request,
    text: str,
    doc_id: str | None,
    metadata: dict[str, Any] | None,
) -> DocIndexResponse:
    await require_redis(getattr(request.app.state, "redis", None))

    vector_store: RedisVectorStore = request.app.state.vector_store
    resolved_doc_id = doc_id or str(uuid4())
    resolved_metadata = metadata or {}

    chunks = make_chunks_from_text(doc_id=resolved_doc_id, text=text, metadata=resolved_metadata)
    if not chunks:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Document text produced no chunks",
        )

    try:
        texts = [c.text for c in chunks]
        embeddings_client = request.app.state.embeddings_client
        vectors = await embeddings_client.embed(texts)

        await vector_store.add_chunks(embeddings=vectors, chunks=chunks)
        await vector_store.set_document_metadata(doc_id=resolved_doc_id, metadata=resolved_metadata)
        return DocIndexResponse(doc_id=resolved_doc_id, chunks_indexed=len(chunks))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Document indexing failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_friendly_index_error(exc),
        ) from exc


@router.post(
    "/index",
    response_model=DocIndexResponse,
    summary="Index a plain-text document into the QA vector store",
    dependencies=[Depends(oauth2_scheme)],
)
async def index_document(request: Request, payload: DocIndexRequest) -> DocIndexResponse:
    """
    Index a new plain-text document.

    This initial version accepts raw text in the request body; file uploads and
    rich format parsing (PDF/Markdown) can be layered on later.
    """
    return await _index_text(
        request=request,
        text=payload.text,
        doc_id=payload.doc_id,
        metadata=payload.metadata,
    )


@router.post(
    "/upload",
    response_model=DocIndexResponse,
    summary="Upload a text file and index it into the QA vector store",
    dependencies=[Depends(oauth2_scheme)],
)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    doc_id: str | None = Form(default=None),
    metadata_json: str | None = Form(default=None),
) -> DocIndexResponse:
    metadata: dict[str, Any] = {}
    if metadata_json:
        try:
            parsed = json.loads(metadata_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid metadata_json: {exc.msg}",
            ) from exc
        if not isinstance(parsed, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="metadata_json must decode to a JSON object",
            )
        metadata = parsed

    if file.filename and "filename" not in metadata:
        metadata["filename"] = file.filename

    try:
        text = await read_text_from_upload(
            upload=file,
            max_bytes=settings.max_upload_file_bytes,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("Document parsing failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Document parsing failed: {exc.__class__.__name__}: {exc}",
        ) from exc

    return await _index_text(
        request=request,
        text=text,
        doc_id=doc_id,
        metadata=metadata,
    )


@router.get(
    "/{doc_id}",
    response_model=DocInfoResponse,
    summary="Get indexed document metadata and basic stats",
    dependencies=[Depends(oauth2_scheme)],
)
async def get_document_info(request: Request, doc_id: str) -> DocInfoResponse:
    await require_redis(getattr(request.app.state, "redis", None))

    vector_store: RedisVectorStore = request.app.state.vector_store
    metadata, chunk_count = await vector_store.get_document_info(doc_id)
    if metadata is None and chunk_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )

    return DocInfoResponse(doc_id=doc_id, metadata=metadata, chunk_count=chunk_count)

