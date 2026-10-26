"""Pydantic API contracts for DocChat."""
from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# -----------------------------------------------------------------------------
# Health
# -----------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str = "healthy"
    version: str
    embedding_model: str
    embedding_dimension: int
    database_backend: str
    storage_backend: str
    groq_configured: bool
    embedding_backend: str = "unknown"


# -----------------------------------------------------------------------------
# Documents
# -----------------------------------------------------------------------------

DocumentStatusType = Literal["queued", "parsing", "chunking", "embedding", "ready", "failed"]


class DocumentUploadResponse(BaseModel):
    document_id: str
    name: str
    status: DocumentStatusType
    size_bytes: int
    created_at: datetime


class DocumentStatusResponse(BaseModel):
    document_id: str
    name: str
    status: DocumentStatusType
    progress_percentage: int
    total_pages: int
    total_chunks: int
    chunks_processed: int
    error_message: Optional[str] = None
    created_at: datetime
    elapsed_seconds: Optional[float] = None
    estimated_remaining_seconds: Optional[float] = None


class DocumentListItem(BaseModel):
    document_id: str
    name: str
    status: DocumentStatusType
    size_bytes: int
    total_pages: int
    total_chunks: int
    created_at: datetime
    progress_percentage: int = 0
    chunks_processed: int = 0
    elapsed_seconds: Optional[float] = None
    estimated_remaining_seconds: Optional[float] = None


# -----------------------------------------------------------------------------
# Chat & citations
# -----------------------------------------------------------------------------

class SourceReference(BaseModel):
    filename: str = Field(description="Original filename the passage came from")
    page: int = Field(ge=1, description="1-based page number")
    snippet: str = Field(description="Verbatim extracted text passage")
    chunk_index: int = Field(ge=0, description="Sequential chunk index within the document")
    score: float = Field(ge=0.0, le=1.0, description="Cosine similarity score")


class ChatRequest(BaseModel):
    document_id: UUID = Field(description="Target document ID; retrieval is scoped to it")
    message: str = Field(..., min_length=1, max_length=4000, description="Natural-language question")
    k: int = Field(default=5, ge=1, le=20, description="Top K passages to retrieve")
    similarity_threshold: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="Minimum cosine similarity cutoff"
    )

    @field_validator("message")
    @classmethod
    def _message_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("message must not be blank")
        return stripped


class ChatResponse(BaseModel):
    answer: str
    sources: List[SourceReference]
    document_id: str
    short_circuited: bool = Field(
        description="True when no passage met the similarity threshold and the LLM was not called"
    )


class SSETokenEvent(BaseModel):
    type: Literal["token"] = "token"
    value: str


class SSESourcesEvent(BaseModel):
    type: Literal["sources"] = "sources"
    value: List[SourceReference]
