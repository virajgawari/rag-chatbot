"""Supabase PostgreSQL + pgvector repository implemented with asyncpg."""
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

from backend.db.base import DocumentRepository

logger = logging.getLogger(__name__)


class SupabasePgVectorRepository(DocumentRepository):
    def __init__(
        self,
        database_url: str,
        pool_min_size: int = 1,
        pool_max_size: int = 10,
        statement_timeout_ms: int = 15_000,
        statement_cache_size: int = 0,
    ) -> None:
        self.database_url = database_url
        self.pool_min_size = pool_min_size
        self.pool_max_size = pool_max_size
        self.statement_timeout_ms = statement_timeout_ms
        self.statement_cache_size = statement_cache_size
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                dsn=self.database_url,
                min_size=self.pool_min_size,
                max_size=self.pool_max_size,
                timeout=30.0,
                command_timeout=self.statement_timeout_ms / 1000,
                statement_cache_size=self.statement_cache_size,
                server_settings={
                    "application_name": "docchat-api",
                    "statement_timeout": str(self.statement_timeout_ms),
                },
            )

    async def disconnect(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            await self.connect()
        assert self._pool is not None
        return self._pool

    async def health_check(self) -> bool:
        """Connectivity plus required schema (vector extension and tables)."""
        try:
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT
                        EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') AS has_vector,
                        to_regclass('public.documents') IS NOT NULL AS has_documents,
                        to_regclass('public.document_chunks') IS NOT NULL AS has_chunks,
                        EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_schema = 'public'
                              AND table_name = 'documents'
                              AND column_name = 'embedding_model'
                        ) AS has_lineage;
                    """
                )
            if not row or not (
                row["has_vector"]
                and row["has_documents"]
                and row["has_chunks"]
                and row["has_lineage"]
            ):
                logger.warning(
                    "Database schema incomplete (vector=%s, documents=%s, chunks=%s, lineage=%s). "
                    "Apply migrations before serving traffic.",
                    row["has_vector"] if row else None,
                    row["has_documents"] if row else None,
                    row["has_chunks"] if row else None,
                    row["has_lineage"] if row else None,
                )
                return False
            return True
        except Exception as exc:  # noqa: BLE001 - health checks must never raise
            logger.warning("Database health check failed: %s", exc)
            return False

    async def create_document(
        self,
        document_id: str,
        name: str,
        mime_type: str,
        size_bytes: int,
        storage_path: str,
        status: str = "queued",
    ) -> Dict[str, Any]:
        pool = await self._ensure_pool()
        row = await pool.fetchrow(
            """
            INSERT INTO documents (id, name, mime_type, size_bytes, storage_path, status, progress_percentage)
            VALUES ($1::uuid, $2, $3, $4, $5, $6, 0)
            RETURNING id, name, mime_type, size_bytes, storage_path, status,
                      progress_percentage, total_pages, total_chunks, chunks_processed,
                      embedding_model, embedding_dimension, error_message, created_at;
            """,
            document_id,
            name,
            mime_type,
            size_bytes,
            storage_path,
            status,
        )
        return dict(row)

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
        pool = await self._ensure_pool()
        row = await pool.fetchrow(
            """
            UPDATE documents
            SET status = $2,
                progress_percentage = COALESCE($3, progress_percentage),
                total_pages = COALESCE($4, total_pages),
                total_chunks = COALESCE($5, total_chunks),
                chunks_processed = COALESCE($6, chunks_processed),
                error_message = $7,
                updated_at = timezone('utc'::text, now())
            WHERE id = $1::uuid
            RETURNING id, name, status, progress_percentage, total_pages, total_chunks,
                      chunks_processed, error_message, created_at;
            """,
            document_id,
            status,
            progress_percentage,
            total_pages,
            total_chunks,
            chunks_processed,
            error_message,
        )
        return dict(row) if row else {}

    async def get_document(self, document_id: str) -> Optional[Dict[str, Any]]:
        pool = await self._ensure_pool()
        row = await pool.fetchrow(
            """
            SELECT id, name, mime_type, size_bytes, storage_path, status, progress_percentage,
                   total_pages, total_chunks, chunks_processed, embedding_model, embedding_dimension,
                   error_message, created_at
            FROM documents
            WHERE id = $1::uuid;
            """,
            document_id,
        )
        return dict(row) if row else None

    async def list_documents(self, limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
        pool = await self._ensure_pool()
        rows = await pool.fetch(
            """
            SELECT id, name, mime_type, size_bytes, status, progress_percentage,
                   total_pages, total_chunks, created_at
            FROM documents
            ORDER BY created_at DESC
            LIMIT $1 OFFSET $2;
            """,
            limit,
            offset,
        )
        return [dict(row) for row in rows]

    async def delete_document(self, document_id: str) -> bool:
        pool = await self._ensure_pool()
        row = await pool.fetchrow(
            "DELETE FROM documents WHERE id = $1::uuid RETURNING id;",
            document_id,
        )
        return bool(row)

    async def insert_chunks(
        self,
        document_id: str,
        chunks: List[Dict[str, Any]],
        embeddings: List[List[float]],
        embedding_model: str,
        embedding_dimension: int,
    ) -> int:
        if len(chunks) != len(embeddings):
            raise ValueError("chunk and embedding counts do not match")
        for vector in embeddings:
            if len(vector) != embedding_dimension:
                raise ValueError(
                    f"embedding dimension mismatch: expected {embedding_dimension}, got {len(vector)}"
                )

        pool = await self._ensure_pool()
        records = [
            (
                document_id,
                chunk["chunk_index"],
                chunk.get("page", 1),
                chunk["content"],
                json.dumps(chunk.get("metadata", {})),
                f"[{','.join(map(str, vector))}]",
            )
            for chunk, vector in zip(chunks, embeddings)
        ]

        async with pool.acquire() as conn:
            async with conn.transaction():
                # Replacing chunks and their lineage is atomic: a failed reindex
                # leaves the previous index intact.
                await conn.execute("DELETE FROM document_chunks WHERE document_id = $1::uuid;", document_id)
                await conn.executemany(
                    """
                    INSERT INTO document_chunks
                        (document_id, chunk_index, page_number, content, metadata, embedding)
                    VALUES ($1::uuid, $2, $3, $4, $5::jsonb, $6::vector);
                    """,
                    records,
                )
                await conn.execute(
                    """
                    UPDATE documents
                    SET embedding_model = $2,
                        embedding_dimension = $3,
                        total_chunks = $4,
                        chunks_processed = $4,
                        updated_at = timezone('utc'::text, now())
                    WHERE id = $1::uuid;
                    """,
                    document_id,
                    embedding_model,
                    embedding_dimension,
                    len(records),
                )
        return len(records)

    async def fail_stale_documents(self, older_than_minutes: int) -> int:
        """Fail documents whose in-process ingestion task died (e.g. a restart)."""
        pool = await self._ensure_pool()
        result = await pool.execute(
            """
            UPDATE documents
            SET status = 'failed',
                error_message = 'Indexing was interrupted. Please re-upload the document.',
                updated_at = timezone('utc'::text, now())
            WHERE status IN ('queued', 'parsing', 'chunking', 'embedding')
              AND updated_at < timezone('utc'::text, now()) - ($1 * interval '1 minute');
            """,
            older_than_minutes,
        )
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0

    async def scoped_similarity_search(
        self,
        document_id: str,
        query_embedding: List[float],
        k: int = 5,
        threshold: float = 0.2,
    ) -> List[Tuple[Dict[str, Any], float]]:
        """Cosine search constrained to one document and a similarity floor."""
        pool = await self._ensure_pool()
        emb_str = f"[{','.join(map(str, query_embedding))}]"
        rows = await pool.fetch(
            """
            SELECT id, document_id, chunk_index, page_number, content, metadata,
                   1 - (embedding <=> $2::vector) AS similarity
            FROM document_chunks
            WHERE document_id = $1::uuid
              AND 1 - (embedding <=> $2::vector) >= $4
            ORDER BY embedding <=> $2::vector
            LIMIT $3;
            """,
            document_id,
            emb_str,
            k,
            threshold,
        )
        results: List[Tuple[Dict[str, Any], float]] = []
        for row in rows:
            metadata = row["metadata"]
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except ValueError:
                    metadata = {}
            results.append(
                (
                    {
                        "id": str(row["id"]),
                        "document_id": str(row["document_id"]),
                        "chunk_index": row["chunk_index"],
                        "page": row["page_number"],
                        "content": row["content"],
                        "metadata": metadata or {},
                    },
                    max(0.0, min(1.0, float(row["similarity"]))),
                )
            )
        return results
