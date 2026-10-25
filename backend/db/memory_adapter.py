"""In-memory repository used for local development and tests.

It mirrors the Supabase/pgvector adapter's semantics (document-scoped search,
embedding lineage, status telemetry) so the HTTP layer can be exercised without
cloud credentials. It is not a production persistence layer.
"""
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from backend.db.base import DocumentRepository


def cosine_similarity(left: List[float], right: List[float]) -> float:
    """Compute cosine similarity, clamped to [0, 1] for citation scores."""
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0.0 or norm_right == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_left * norm_right)))


class InMemoryRepository(DocumentRepository):
    def __init__(self) -> None:
        self.documents: Dict[str, Dict[str, Any]] = {}
        self.chunks: Dict[str, List[Dict[str, Any]]] = {}
        self.embeddings: Dict[str, List[List[float]]] = {}

    async def health_check(self) -> bool:
        return True

    async def fail_stale_documents(self, older_than_minutes: int) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)
        stale = [
            doc
            for doc in self.documents.values()
            if doc["status"] in ("queued", "parsing", "chunking", "embedding")
            and doc.get("updated_at", doc["created_at"]) < cutoff
        ]
        for doc in stale:
            doc["status"] = "failed"
            doc["error_message"] = "Indexing was interrupted. Please re-upload the document."
            doc["updated_at"] = datetime.now(timezone.utc)
        return len(stale)

    async def create_document(
        self,
        document_id: str,
        name: str,
        mime_type: str,
        size_bytes: int,
        storage_path: str,
        status: str = "queued",
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        doc = {
            "id": document_id,
            "name": name,
            "mime_type": mime_type,
            "size_bytes": size_bytes,
            "storage_path": storage_path,
            "status": status,
            "progress_percentage": 0,
            "total_pages": 0,
            "total_chunks": 0,
            "chunks_processed": 0,
            "embedding_model": None,
            "embedding_dimension": None,
            "error_message": None,
            "created_at": now,
            "updated_at": now,
        }
        self.documents[document_id] = doc
        return dict(doc)

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
        doc = self.documents.get(document_id)
        if not doc:
            return {}
        doc["status"] = status
        if progress_percentage is not None:
            doc["progress_percentage"] = progress_percentage
        if total_pages is not None:
            doc["total_pages"] = total_pages
        if total_chunks is not None:
            doc["total_chunks"] = total_chunks
        if chunks_processed is not None:
            doc["chunks_processed"] = chunks_processed
        if error_message is not None:
            doc["error_message"] = error_message
        if status in ("queued", "parsing", "chunking", "embedding", "ready"):
            doc["error_message"] = None
        doc["updated_at"] = datetime.now(timezone.utc)
        return dict(doc)

    async def get_document(self, document_id: str) -> Optional[Dict[str, Any]]:
        doc = self.documents.get(document_id)
        return dict(doc) if doc else None

    async def list_documents(self, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
        ordered = sorted(self.documents.values(), key=lambda d: d["created_at"], reverse=True)
        return [dict(doc) for doc in ordered[offset : offset + limit]]

    async def delete_document(self, document_id: str) -> bool:
        if document_id not in self.documents:
            return False
        del self.documents[document_id]
        self.chunks.pop(document_id, None)
        self.embeddings.pop(document_id, None)
        return True

    async def insert_chunks(
        self,
        document_id: str,
        chunks: List[Dict[str, Any]],
        embeddings: List[List[float]],
        embedding_model: str,
        embedding_dimension: int,
    ) -> int:
        doc = self.documents.get(document_id)
        if not doc:
            return 0
        self.chunks[document_id] = [dict(chunk) for chunk in chunks]
        self.embeddings[document_id] = [list(vector) for vector in embeddings]
        doc["embedding_model"] = embedding_model
        doc["embedding_dimension"] = embedding_dimension
        doc["total_chunks"] = len(chunks)
        doc["chunks_processed"] = len(chunks)
        doc["updated_at"] = datetime.now(timezone.utc)
        return len(chunks)

    async def scoped_similarity_search(
        self,
        document_id: str,
        query_embedding: List[float],
        k: int = 5,
        threshold: float = 0.2,
    ) -> List[Tuple[Dict[str, Any], float]]:
        doc_chunks = self.chunks.get(document_id, [])
        doc_embeddings = self.embeddings.get(document_id, [])
        if not doc_chunks or not doc_embeddings:
            return []

        scored = [
            (chunk, cosine_similarity(query_embedding, embedding))
            for chunk, embedding in zip(doc_chunks, doc_embeddings)
        ]
        scored = [item for item in scored if item[1] >= threshold]
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:k]
