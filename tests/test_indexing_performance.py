"""Progress write throttling, bounded concurrency, and ingestion telemetry."""
import asyncio
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.datastructures import UploadFile

from backend.core.config import settings
from backend.services import document_service as module

pytestmark = pytest.mark.anyio


async def uploaded(workspace, text="One sentence about documents. " * 80):
    service = workspace["document_service"]
    response = await service.upload_document(UploadFile(file=io.BytesIO(text.encode()), filename="performance.txt"))
    return response.document_id


async def test_fast_batches_do_not_write_progress_for_every_chunk(workspace, monkeypatch):
    monkeypatch.setattr(settings, "DEFAULT_CHUNK_SIZE", 100)
    monkeypatch.setattr(settings, "DEFAULT_CHUNK_OVERLAP", 20)
    monkeypatch.setattr(settings, "EMBEDDING_BATCH_SIZE", 4)
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: 100.0))
    repo = workspace["repository"]
    original = repo.update_status
    repo.update_status = AsyncMock(wraps=original)
    document_id = await uploaded(workspace)
    await workspace["document_service"].process_document_pipeline(document_id)
    doc = await repo.get_document(document_id)
    assert doc["status"] == "ready"
    assert doc["total_chunks"] > 10
    assert doc["chunks_processed"] == doc["total_chunks"]
    updates = [call.kwargs for call in repo.update_status.call_args_list]
    assert [update["status"] for update in updates] == ["parsing", "chunking", "embedding", "embedding", "ready"]
    assert updates[-2]["chunks_processed"] == doc["total_chunks"]


async def test_slow_batches_report_progress_and_eta(workspace, monkeypatch):
    monkeypatch.setattr(settings, "DEFAULT_CHUNK_SIZE", 100)
    monkeypatch.setattr(settings, "DEFAULT_CHUNK_OVERLAP", 20)
    monkeypatch.setattr(settings, "EMBEDDING_BATCH_SIZE", 4)
    clock = [100.0]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    embeddings = workspace["embeddings"]
    original = embeddings.embed_documents
    batch_sizes = []

    def embed(texts):
        batch_sizes.append(len(texts))
        clock[0] += 4
        return original(texts)

    monkeypatch.setattr(embeddings, "embed_documents", embed)
    repo = workspace["repository"]
    original_update = repo.update_status
    repo.update_status = AsyncMock(wraps=original_update)
    document_id = await uploaded(workspace)
    service = workspace["document_service"]
    await service.process_document_pipeline(document_id)
    assert max(batch_sizes) == 4
    updates = [call.kwargs for call in repo.update_status.call_args_list if call.kwargs.get("chunks_processed", 0) > 0]
    assert len(updates) == len(batch_sizes) + 1
    assert document_id not in service._embedding_started

    await repo.update_status(document_id, status="embedding", chunks_processed=4, total_chunks=12)
    service._embedding_started[document_id] = clock[0] - 10
    status = await service.get_document_status(document_id)
    assert status.estimated_remaining_seconds == pytest.approx(20)
    assert status.elapsed_seconds is not None and status.elapsed_seconds >= 0
    listing = await service.list_documents(20, 0)
    assert listing[0].chunks_processed == 4
    assert listing[0].estimated_remaining_seconds == pytest.approx(20)


async def test_documents_are_not_embedded_concurrently(workspace, monkeypatch):
    service = workspace["document_service"]
    document_ids = [await uploaded(workspace), await uploaded(workspace)]
    active = 0
    maximum = 0

    async def ingest(doc):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0)
        active -= 1

    monkeypatch.setattr(service, "_ingest", ingest)
    await asyncio.gather(*(service.process_document_pipeline(document_id) for document_id in document_ids))
    assert maximum == 1


async def test_missing_vectors_fail_instead_of_marking_document_ready(workspace, monkeypatch):
    document_id = await uploaded(workspace)
    monkeypatch.setattr(workspace["embeddings"], "embed_documents", lambda _: [])
    service = workspace["document_service"]
    await service.process_document_pipeline(document_id)
    status = await service.get_document_status(document_id)
    assert status.status == "failed"
    assert "embedding" in status.error_message
    assert status.estimated_remaining_seconds is None
    assert document_id not in service._embedding_started
