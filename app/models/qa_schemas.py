from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.schemas import ChatMessage


class QACitation(BaseModel):
    """
    Single retrieved document chunk used to answer a question.
    """

    doc_id: str = Field(..., description="Identifier of the source document")
    snippet: str = Field(..., description="Relevant excerpt from the document")
    score: float = Field(
        ...,
        ge=0.0,
        description="Retrieval relevance score (higher means more relevant)",
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="Optional metadata such as title, section, tags, source_path",
    )


class QARequest(BaseModel):
    """
    Request payload for RAG-style document QA.
    """

    message: str = Field(
        min_length=1,
        max_length=8000,
        description="Natural language question from the user",
    )
    session_id: UUID | None = Field(
        default=None,
        description="Optional conversation session identifier; a new one is created when omitted",
    )
    top_k: int = Field(
        default=4,
        ge=1,
        le=20,
        description="Maximum number of document chunks to retrieve for context",
    )
    doc_filters: dict[str, Any] | None = Field(
        default=None,
        description="Optional metadata filters (e.g. tags, document type)",
    )


class QAResponse(BaseModel):
    """
    Response payload for RAG-style document QA.
    """

    session_id: UUID = Field(description="Conversation session identifier")
    answer: str = Field(description="Final natural language answer")
    history: list[ChatMessage] = Field(
        default_factory=list,
        description="Updated conversation history (including the latest exchange)",
    )
    citations: list[QACitation] = Field(
        default_factory=list,
        description="Document snippets that were used to ground the answer",
    )
    agent: "AgentTraceResponse | None" = Field(
        default=None,
        description="Optional structured agent trace for UI state and tool display",
    )


class AgentStep(BaseModel):
    stage: Literal["thinking", "acting", "response", "error"]
    title: str
    summary: str


class ToolCallTrace(BaseModel):
    name: str
    status: Literal["running", "completed", "error"]
    input: str
    output: str
    latency_ms: int = Field(ge=0)


class AgentTraceResponse(BaseModel):
    run_id: str
    status: Literal["thinking", "acting", "response", "error"]
    task_type: Literal["qa", "summary"] = "qa"
    retrieval_summary: str | None = None
    rerank_summary: str | None = None
    summary_phase: str | None = None
    rewritten_queries: list[str] = Field(default_factory=list)
    steps: list[AgentStep] = Field(default_factory=list)
    tool_calls: list[ToolCallTrace] = Field(default_factory=list)


class DocIndexRequest(BaseModel):
    """
    Request payload for indexing a new document into the vector store.
    """

    doc_id: str | None = Field(
        default=None,
        description="Optional logical document id; generated when omitted",
    )
    text: str = Field(
        min_length=1,
        max_length=200_000,
        description="Raw document text content to index",
    )
    metadata: dict[str, Any] | None = Field(
        default=None,
        description="Optional document-level metadata (title, tags, source_path, etc.)",
    )


class DocIndexResponse(BaseModel):
    doc_id: str
    chunks_indexed: int


class DocInfoResponse(BaseModel):
    doc_id: str
    metadata: dict[str, Any] | None = None
    chunk_count: int = 0

