"""Document endpoints: upload, status, listing, and deletion."""
from typing import List
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, File, Query, Request, UploadFile, status

from backend.core.config import settings
from backend.core.errors import FileTooLargeError
from backend.models.schemas import (
    DocumentListItem,
    DocumentStatusResponse,
    DocumentUploadResponse,
)

router = APIRouter(prefix="/documents", tags=["Documents"])

_MULTIPART_OVERHEAD_BYTES = 1024 * 1024


@router.post("/upload", response_model=DocumentUploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> DocumentUploadResponse:
    """Validate, store, and queue a PDF, DOCX, or TXT file for indexing."""
    declared_length = request.headers.get("content-length")
    if declared_length and declared_length.isdigit():
        if int(declared_length) > settings.MAX_UPLOAD_SIZE_BYTES + _MULTIPART_OVERHEAD_BYTES:
            raise FileTooLargeError(
                f"File exceeds the maximum upload size of "
                f"{settings.MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)} MB."
            )

    doc_service = request.app.state.document_service
    upload_result = await doc_service.upload_document(file)
    background_tasks.add_task(doc_service.process_document_pipeline, upload_result.document_id)
    return upload_result


@router.get("/{document_id}/status", response_model=DocumentStatusResponse)
async def get_document_status(request: Request, document_id: UUID) -> DocumentStatusResponse:
    """Return real ingestion telemetry for one document."""
    return await request.app.state.document_service.get_document_status(str(document_id))


@router.get("", response_model=List[DocumentListItem])
async def list_documents(
    request: Request,
    limit: int = Query(
        default=settings.DOCUMENT_LIST_DEFAULT_LIMIT,
        ge=1,
        le=settings.DOCUMENT_LIST_MAX_LIMIT,
        description="Maximum number of documents to return",
    ),
    offset: int = Query(default=0, ge=0, description="Number of documents to skip"),
) -> List[DocumentListItem]:
    """List documents, newest first.

    This is an operational listing for the demo UI. It is not scoped to an
    authenticated user; see docs/api-contract.md for the auth limitation.
    """
    return await request.app.state.document_service.list_documents(limit=limit, offset=offset)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(request: Request, document_id: UUID) -> None:
    """Delete a document, its chunks, and the stored raw file."""
    await request.app.state.document_service.delete_document(str(document_id))
