"""Abstract repository for document metadata and document-scoped vector retrieval."""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple


class DocumentRepository(ABC):
    @abstractmethod
    async def create_document(
        self,
        document_id: str,
        name: str,
        mime_type: str,
        size_bytes: int,
        storage_path: str,
        status: str = "queued",
    ) -> Dict[str, Any]:
        """Record initial document metadata."""

    @abstractmethod
    async def update_status(
        self,
        document_id: str,
        status: str,
        progress_percentage: Optional[int] = None,
        total_pages: Optional[int] = None,
        total_chunks: Optional[int] = None,
        chunks_processed: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update ingestion status and progress telemetry."""

    @abstractmethod
    async def get_document(self, document_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a single document record by ID."""

    @abstractmethod
    async def list_documents(self, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
        """List documents, newest first, with pagination."""

    @abstractmethod
    async def delete_document(self, document_id: str) -> bool:
        """Delete a document record and its cascading chunks."""

    @abstractmethod
    async def insert_chunks(
        self,
        document_id: str,
        chunks: List[Dict[str, Any]],
        embeddings: List[List[float]],
        embedding_model: str,
        embedding_dimension: int,
    ) -> int:
        """Atomically replace a document's chunks and record embedding lineage."""

    @abstractmethod
    async def scoped_similarity_search(
        self,
        document_id: str,
        query_embedding: List[float],
        k: int = 5,
        threshold: float = 0.2,
    ) -> List[Tuple[Dict[str, Any], float]]:
        """Cosine similarity search strictly scoped to one document."""

    @abstractmethod
    async def fail_stale_documents(self, older_than_minutes: int) -> int:
        """Mark documents stuck mid-ingestion as failed (e.g. after a restart)."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Verify database connectivity and required schema."""
