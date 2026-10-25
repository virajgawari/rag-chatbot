"""Document lifecycle service: validation, storage, parsing, chunking, embedding."""
import asyncio
import logging
import math
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_text_splitters import RecursiveCharacterTextSplitter

from backend.core.config import settings
from backend.core.errors import (
    DocumentNotFoundError,
    FileTooLargeError,
    InvalidDocumentError,
    UnsupportedFileTypeError,
)
from backend.core.security import sanitize_filename, validate_file_content
from backend.db.base import DocumentRepository
from backend.models.schemas import (
    DocumentListItem,
    DocumentStatusResponse,
    DocumentUploadResponse,
)
from backend.services.parsing import extract_raw_pages
from backend.storage.base import BaseStorage

logger = logging.getLogger(__name__)

_CHUNK_READ_BYTES = 1024 * 1024  # 1 MiB read window while enforcing the upload cap
_SAFE_PIPELINE_ERROR = "Document processing failed ({stage}). Please retry the upload."


class DocumentService:
    def __init__(
        self,
        repository: DocumentRepository,
        storage: BaseStorage,
        embeddings_model: Any,
        embedding_model_name: str = settings.EMBEDDING_MODEL_NAME,
        embedding_dimension: int = settings.EMBEDDING_DIMENSION,
    ) -> None:
        self.repository = repository
        self.storage = storage
        self.embeddings_model = embeddings_model
        self.embedding_model_name = embedding_model_name
        self.embedding_dimension = embedding_dimension
        self._ingest_locks: Dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    async def _read_with_cap(self, file) -> bytes:
        """Read an UploadFile in bounded chunks so oversized uploads cannot exhaust memory."""
        chunks: List[bytes] = []
        total = 0
        while True:
            chunk = await file.read(_CHUNK_READ_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > settings.MAX_UPLOAD_SIZE_BYTES:
                raise FileTooLargeError(
                    f"File exceeds the maximum upload size of "
                    f"{settings.MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)} MB."
                )
            chunks.append(chunk)
        return b"".join(chunks)

    async def upload_document(self, file) -> DocumentUploadResponse:
        if not file.filename:
            raise InvalidDocumentError("A file name is required.")

        clean_name = sanitize_filename(file.filename)
        if len(clean_name) > settings.MAX_FILENAME_LENGTH:
            raise InvalidDocumentError(
                f"File name is too long (maximum {settings.MAX_FILENAME_LENGTH} characters)."
            )

        suffix = Path(clean_name).suffix.lower()
        if suffix not in settings.ALLOWED_EXTENSIONS:
            allowed = ", ".join(sorted(settings.ALLOWED_EXTENSIONS))
            raise UnsupportedFileTypeError(
                f"Unsupported file type '{suffix or 'unknown'}'. Allowed formats: {allowed}."
            )

        data = await self._read_with_cap(file)
        if not data:
            raise InvalidDocumentError("The uploaded file is empty.")

        is_valid, error_msg = validate_file_content(data, clean_name)
        if not is_valid:
            raise InvalidDocumentError(error_msg)

        document_id = str(uuid.uuid4())
        destination_path = f"{document_id}/{clean_name}"
        mime_type = file.content_type or "application/octet-stream"

        storage_path = await self.storage.upload(destination_path, data, mime_type)
        try:
            doc_record = await self.repository.create_document(
                document_id=document_id,
                name=clean_name,
                mime_type=mime_type,
                size_bytes=len(data),
                storage_path=storage_path,
                status="queued",
            )
        except Exception:
            # Never leave an untracked object behind if the metadata insert fails.
            try:
                await self.storage.delete(storage_path)
            except Exception:  # noqa: BLE001 - best-effort cleanup
                logger.warning("Failed to clean up storage object %s after DB failure", storage_path)
            raise

        return DocumentUploadResponse(
            document_id=document_id,
            name=clean_name,
            status="queued",
            size_bytes=len(data),
            created_at=doc_record["created_at"],
        )

    # ------------------------------------------------------------------
    # Ingestion pipeline
    # ------------------------------------------------------------------

    def _lock_for(self, document_id: str) -> asyncio.Lock:
        lock = self._ingest_locks.get(document_id)
        if lock is None:
            lock = asyncio.Lock()
            self._ingest_locks[document_id] = lock
        return lock

    async def process_document_pipeline(self, document_id: str) -> None:
        """Run ingestion for a document. Safe to invoke from a background task."""
        async with self._lock_for(document_id):
            doc = await self.repository.get_document(document_id)
            if not doc:
                logger.error("Cannot process document %s: record not found", document_id)
                return
            await self._ingest(doc)

    async def reindex_document(self, document_id: str) -> Dict[str, Any]:
        """Re-parse, re-chunk and re-embed an already-stored document.

        Used by the embedding-model migration: chunks are replaced atomically and
        the document's embedding lineage is updated in the same transaction.
        """
        async with self._lock_for(document_id):
            doc = await self.repository.get_document(document_id)
            if not doc:
                raise DocumentNotFoundError(f"Document '{document_id}' not found.")
            await self._ingest(doc)
            updated = await self.repository.get_document(document_id)
            return {
                "document_id": document_id,
                "name": doc["name"],
                "status": updated["status"] if updated else "failed",
                "embedding_model": updated.get("embedding_model") if updated else None,
                "total_chunks": updated.get("total_chunks", 0) if updated else 0,
            }

    async def _ingest(self, doc: Dict[str, Any]) -> None:
        document_id = str(doc["id"])
        stage = "initialization"
        temp_path: Optional[Path] = None

        try:
            await self.repository.update_status(
                document_id, status="parsing", progress_percentage=10, chunks_processed=0
            )

            raw_bytes = await self.storage.download(doc["storage_path"])
            suffix = Path(doc["name"]).suffix.lower()
            stage = "parsing"
            with tempfile.NamedTemporaryFile(
                prefix=f"docchat_{document_id}_", suffix=suffix, delete=False
            ) as handle:
                handle.write(raw_bytes)
                temp_path = Path(handle.name)

            pages = await asyncio.to_thread(extract_raw_pages, temp_path)
            if not pages:
                raise ValueError("no extractable text found")

            stage = "chunking"
            await self.repository.update_status(
                document_id,
                status="chunking",
                progress_percentage=35,
                total_pages=len(pages),
            )

            splitter = RecursiveCharacterTextSplitter(
                chunk_size=settings.DEFAULT_CHUNK_SIZE,
                chunk_overlap=settings.DEFAULT_CHUNK_OVERLAP,
                separators=["\n\n", "\n", " ", ""],
            )

            chunks: List[Dict[str, Any]] = []
            chunk_counter = 0
            for page_number, page_text in pages:
                for chunk_text in splitter.split_text(page_text):
                    if not chunk_text.strip():
                        continue
                    chunks.append(
                        {
                            "chunk_index": chunk_counter,
                            "page": page_number,
                            "content": chunk_text,
                            "metadata": {
                                "doc_id": document_id,
                                "filename": doc["name"],
                                "page": page_number,
                                "chunk_index": chunk_counter,
                                "source": doc["name"],
                            },
                        }
                    )
                    chunk_counter += 1

            if not chunks:
                raise ValueError("text splitting produced no chunks")

            stage = "embedding"
            await self.repository.update_status(
                document_id,
                status="embedding",
                progress_percentage=50,
                total_chunks=len(chunks),
                chunks_processed=0,
            )

            embeddings: List[List[float]] = []
            batch_size = max(1, settings.EMBEDDING_BATCH_SIZE)
            total_batches = math.ceil(len(chunks) / batch_size)

            for batch_number in range(total_batches):
                batch = chunks[batch_number * batch_size : (batch_number + 1) * batch_size]
                try:
                    async with asyncio.timeout(settings.EMBEDDING_TIMEOUT_SECONDS):
                        vectors = await asyncio.to_thread(
                            self.embeddings_model.embed_documents,
                            [item["content"] for item in batch],
                        )
                except TimeoutError:
                    raise ValueError("embedding batch timed out") from None
                for vector in vectors:
                    if len(vector) != self.embedding_dimension:
                        raise ValueError(
                            f"embedding model returned {len(vector)} dimensions, "
                            f"expected {self.embedding_dimension}"
                        )
                embeddings.extend(vectors)
                processed = len(embeddings)
                progress = 50 + int(45 * processed / len(chunks))
                await self.repository.update_status(
                    document_id,
                    status="embedding",
                    progress_percentage=min(progress, 95),
                    chunks_processed=processed,
                )

            stage = "storing"
            await self.repository.insert_chunks(
                document_id=document_id,
                chunks=chunks,
                embeddings=embeddings,
                embedding_model=self.embedding_model_name,
                embedding_dimension=self.embedding_dimension,
            )
            await self.repository.update_status(
                document_id,
                status="ready",
                progress_percentage=100,
                chunks_processed=len(chunks),
                error_message=None,
            )
            logger.info(
                "Indexed document %s (%s chunks, model=%s)",
                document_id,
                len(chunks),
                self.embedding_model_name,
            )

        except Exception as exc:  # noqa: BLE001 - persisted as a client-safe status
            logger.exception("Ingestion failed for document %s during %s: %s", document_id, stage, exc)
            await self.repository.update_status(
                document_id,
                status="failed",
                error_message=_SAFE_PIPELINE_ERROR.format(stage=stage),
            )
        finally:
            if temp_path is not None and temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    logger.warning("Could not remove temporary file %s", temp_path)

    # ------------------------------------------------------------------
    # Read models
    # ------------------------------------------------------------------

    async def get_document_status(self, document_id: str) -> DocumentStatusResponse:
        doc = await self.repository.get_document(document_id)
        if not doc:
            raise DocumentNotFoundError(f"Document '{document_id}' not found.")

        return DocumentStatusResponse(
            document_id=str(doc["id"]),
            name=doc["name"],
            status=doc["status"],
            progress_percentage=doc.get("progress_percentage", 0),
            total_pages=doc.get("total_pages", 0),
            total_chunks=doc.get("total_chunks", 0),
            chunks_processed=doc.get("chunks_processed", 0),
            error_message=doc.get("error_message"),
            created_at=doc["created_at"],
        )

    async def list_documents(self, limit: int = 20, offset: int = 0) -> List[DocumentListItem]:
        docs = await self.repository.list_documents(limit=limit, offset=offset)
        return [
            DocumentListItem(
                document_id=str(doc["id"]),
                name=doc["name"],
                status=doc["status"],
                size_bytes=doc["size_bytes"],
                total_pages=doc.get("total_pages", 0),
                total_chunks=doc.get("total_chunks", 0),
                created_at=doc["created_at"],
            )
            for doc in docs
        ]

    async def delete_document(self, document_id: str) -> bool:
        doc = await self.repository.get_document(document_id)
        if not doc:
            raise DocumentNotFoundError(f"Document '{document_id}' not found.")

        deleted = await self.repository.delete_document(document_id)
        try:
            await self.storage.delete(doc["storage_path"])
        except Exception as exc:  # noqa: BLE001 - metadata deletion already succeeded
            logger.warning("Failed to delete storage object %s: %s", doc["storage_path"], exc)
        return deleted
