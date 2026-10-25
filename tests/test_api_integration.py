"""Integration tests for the DocChat FastAPI backend.

Covers upload -> parse -> chunk -> embed -> store -> retrieve -> answer -> cite,
plus strict document scoping, SSE streaming, validation, and safe errors.
"""
import io
import json
import zipfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend.core.config import settings
from backend.services.chat_service import ChatService

pytestmark = pytest.mark.anyio


def client_for(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def upload_text(client: AsyncClient, name: str, content: str, mime: str = "text/plain"):
    return await client.post(
        "/api/v1/documents/upload",
        files={"file": (name, content.encode("utf-8"), mime)},
    )


async def index(workspace, document_id: str) -> None:
    await workspace["document_service"].process_document_pipeline(document_id)


def parse_sse(body: str) -> list[dict]:
    events = []
    for block in body.strip().split("\n\n"):
        if not block.strip():
            continue
        event_name = None
        data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = line.split(":", 1)[1].strip()
        if event_name and data is not None:
            events.append({"event": event_name, "data": json.loads(data)})
    return events


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

async def test_health_endpoints(workspace):
    async with client_for(workspace["app"]) as client:
        for path in ("/health", "/api/v1/health"):
            response = await client.get(path)
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "healthy"
            assert body["embedding_dimension"] == 384
            assert body["embedding_model"] == settings.EMBEDDING_MODEL_NAME
            assert body["database_backend"] == "in_memory"
            assert isinstance(body["groq_configured"], bool)


# ---------------------------------------------------------------------------
# Upload validation
# ---------------------------------------------------------------------------

async def test_upload_rejects_unsupported_empty_and_spoofed_files(workspace):
    async with client_for(workspace["app"]) as client:
        response = await client.post(
            "/api/v1/documents/upload",
            files={"file": ("malware.exe", b"MZ\x90\x00", "application/octet-stream")},
        )
        assert response.status_code == 400
        assert response.json()["code"] == "unsupported_file_type"

        response = await upload_text(client, "empty.txt", "")
        assert response.status_code == 400
        assert response.json()["code"] == "invalid_document"

        response = await client.post(
            "/api/v1/documents/upload",
            files={"file": ("fake.pdf", b"not a pdf", "application/pdf")},
        )
        assert response.status_code == 400
        assert response.json()["code"] == "invalid_document"

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("readme.txt", "not a docx")
        response = await client.post(
            "/api/v1/documents/upload",
            files={
                "file": (
                    "fake.docx",
                    buffer.getvalue(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        assert response.status_code == 400


async def test_upload_enforces_size_limit(workspace, monkeypatch):
    monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_BYTES", 2048)
    async with client_for(workspace["app"]) as client:
        response = await upload_text(client, "large.txt", "a" * 5000)
        assert response.status_code == 413
        assert response.json()["code"] == "file_too_large"


async def test_upload_sanitizes_filename(workspace):
    async with client_for(workspace["app"]) as client:
        response = await client.post(
            "/api/v1/documents/upload",
            files={"file": ("../../etc/passwd.txt", b"harmless text", "text/plain")},
        )
        assert response.status_code == 202
        assert "/" not in response.json()["name"]
        assert "\\" not in response.json()["name"]


# ---------------------------------------------------------------------------
# Full pipeline and citations
# ---------------------------------------------------------------------------

async def test_full_ingestion_and_grounded_citation(workspace):
    async with client_for(workspace["app"]) as client:
        content = (
            "DocChat Technical Specification\n\n"
            "The DocChat system was engineered by Team Phoenix in 2026.\n\n"
            "Chunks retain 1-based page numbers and stable document identifiers.\n"
        )
        upload = await upload_text(client, "phoenix_specs.txt", content)
        assert upload.status_code == 202
        document_id = upload.json()["document_id"]
        assert upload.json()["status"] == "queued"

        await index(workspace, document_id)

        status_response = await client.get(f"/api/v1/documents/{document_id}/status")
        assert status_response.status_code == 200
        status = status_response.json()
        assert status["status"] == "ready"
        assert status["progress_percentage"] == 100
        assert status["total_chunks"] >= 1
        assert status["chunks_processed"] == status["total_chunks"]
        assert status["total_pages"] == 1
        assert status["error_message"] is None

        chat = await client.post(
            "/api/v1/chat",
            json={"document_id": document_id, "message": "Who engineered DocChat?", "similarity_threshold": 0.0},
        )
        assert chat.status_code == 200
        body = chat.json()
        assert body["short_circuited"] is False
        assert "Team Phoenix" in body["answer"]
        assert body["document_id"] == document_id

        source = body["sources"][0]
        assert source["filename"] == "phoenix_specs.txt"
        assert source["page"] == 1
        assert "Team Phoenix" in source["snippet"]
        assert source["chunk_index"] == 0
        assert 0.0 <= source["score"] <= 1.0

        listing = await client.get("/api/v1/documents")
        assert listing.status_code == 200
        assert any(item["document_id"] == document_id for item in listing.json())


async def test_pdf_page_numbers_are_preserved(workspace):
    reportlab = pytest.importorskip("reportlab.pdfgen.canvas")
    buffer = io.BytesIO()
    pdf = reportlab.Canvas(buffer)
    pdf.drawString(72, 720, "Page one: the Phoenix protocol budget is 50,000 dollars.")
    pdf.showPage()
    pdf.drawString(72, 720, "Page two: the retention period is seven years.")
    pdf.showPage()
    pdf.save()

    async with client_for(workspace["app"]) as client:
        response = await client.post(
            "/api/v1/documents/upload",
            files={"file": ("two_pages.pdf", buffer.getvalue(), "application/pdf")},
        )
        assert response.status_code == 202
        document_id = response.json()["document_id"]
        await index(workspace, document_id)

        status = (await client.get(f"/api/v1/documents/{document_id}/status")).json()
        assert status["total_pages"] == 2

        chat = await client.post(
            "/api/v1/chat",
            json={"document_id": document_id, "message": "What is the budget and retention?", "similarity_threshold": 0.0},
        )
        assert chat.status_code == 200
        sources = chat.json()["sources"]
        assert {source["page"] for source in sources} == {1, 2}
        assert all(source["filename"] == "two_pages.pdf" for source in sources)


# ---------------------------------------------------------------------------
# Strict document scoping
# ---------------------------------------------------------------------------

async def test_chat_is_strictly_scoped_to_requested_document(workspace):
    async with client_for(workspace["app"]) as client:
        alpha = await upload_text(
            client, "alpha.txt", "ALPHA_SECRET: The Alpha project budget is 50,000 dollars."
        )
        beta = await upload_text(
            client, "beta.txt", "BETA_SECRET: The Beta project budget is 90,000 dollars."
        )
        alpha_id = alpha.json()["document_id"]
        beta_id = beta.json()["document_id"]
        await index(workspace, alpha_id)
        await index(workspace, beta_id)

        response = await client.post(
            "/api/v1/chat",
            json={"document_id": alpha_id, "message": "What is the project budget?", "similarity_threshold": 0.0},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["sources"], "expected at least one citation from the scoped document"
        for source in body["sources"]:
            assert source["filename"] == "alpha.txt"
            assert "BETA_SECRET" not in source["snippet"]
            assert "BETA_SECRET" not in body["answer"]

        # Repository-level scoping check: beta's embedding must never see alpha.
        vector = workspace["embeddings"].embed_query("project budget")
        beta_hits = await workspace["repository"].scoped_similarity_search(
            document_id=beta_id, query_embedding=vector, k=10, threshold=0.0
        )
        assert beta_hits
        assert all(chunk["metadata"]["doc_id"] == beta_id for chunk, _ in beta_hits)
        assert all("ALPHA_SECRET" not in chunk["content"] for chunk, _ in beta_hits)


async def test_low_similarity_short_circuits_without_calling_llm(workspace):
    async with client_for(workspace["app"]) as client:
        upload = await upload_text(client, "finance.txt", "Quarterly revenue was 12 million dollars.")
        document_id = upload.json()["document_id"]
        await index(workspace, document_id)

        response = await client.post(
            "/api/v1/chat",
            json={
                "document_id": document_id,
                "message": "How do I bake a chocolate cake?",
                "similarity_threshold": 1.0,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["short_circuited"] is True
        assert body["answer"] == settings.NO_ANSWER_MESSAGE
        assert body["sources"] == []
        assert workspace["llm"].requests == []

        streamed = await client.post(
            "/api/v1/chat/stream",
            json={
                "document_id": document_id,
                "message": "How do I bake a chocolate cake?",
                "similarity_threshold": 1.0,
            },
        )
        assert streamed.status_code == 200
        events = parse_sse(streamed.text)
        assert [event["event"] for event in events] == ["token", "sources", "done"]
        assert events[0]["data"]["value"] == settings.NO_ANSWER_MESSAGE
        assert events[1]["data"]["value"] == []
        assert workspace["llm"].requests == [], "SSE short-circuit must not call the LLM"


# ---------------------------------------------------------------------------
# SSE streaming
# ---------------------------------------------------------------------------

async def test_sse_stream_emits_tokens_sources_and_done(workspace):
    async with client_for(workspace["app"]) as client:
        upload = await upload_text(client, "sse_notes.txt", "DocChat streams answers using text/event-stream.")
        document_id = upload.json()["document_id"]
        await index(workspace, document_id)

        response = await client.post(
            "/api/v1/chat/stream",
            json={"document_id": document_id, "message": "What format does streaming use?", "similarity_threshold": 0.0},
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache, no-transform"

        events = parse_sse(response.text)
        assert [event["event"] for event in events] == ["token"] * len(events[:-2]) + ["sources", "done"]
        answer = "".join(event["data"]["value"] for event in events if event["event"] == "token")
        assert "Team Phoenix" in answer
        sources = next(event["data"]["value"] for event in events if event["event"] == "sources")
        assert sources[0]["filename"] == "sse_notes.txt"
        assert sources[0]["page"] == 1
        assert sources[0]["snippet"]
        assert events[-1]["data"] == {"type": "done", "finished": True}


async def test_sse_validates_document_before_streaming(workspace):
    async with client_for(workspace["app"]) as client:
        missing = await client.post(
            "/api/v1/chat/stream",
            json={"document_id": "00000000-0000-4000-8000-000000000000", "message": "Hello?"},
        )
        assert missing.status_code == 404
        assert missing.headers["content-type"].startswith("application/json")
        assert missing.json()["code"] == "document_not_found"

        created = await workspace["repository"].create_document(
            document_id="11111111-1111-4111-8111-111111111111",
            name="still_indexing.txt",
            mime_type="text/plain",
            size_bytes=10,
            storage_path="nothing.txt",
        )
        assert created["status"] == "queued"
        not_ready = await client.post(
            "/api/v1/chat/stream",
            json={"document_id": created["id"], "message": "Hello?"},
        )
        assert not_ready.status_code == 409
        assert not_ready.json()["code"] == "document_not_ready"


async def test_sse_error_event_is_sanitized(workspace, exploding_llm):
    workspace["app"].state.chat_service = ChatService(
        repository=workspace["repository"],
        embeddings_model=workspace["embeddings"],
        llm_client=exploding_llm,
    )

    async with client_for(workspace["app"]) as client:
        upload = await upload_text(client, "boom.txt", "The answer source is here.")
        document_id = upload.json()["document_id"]
        await index(workspace, document_id)

        response = await client.post(
            "/api/v1/chat/stream",
            json={"document_id": document_id, "message": "Where is the source?", "similarity_threshold": 0.0},
        )
        assert response.status_code == 200
        events = parse_sse(response.text)
        assert events[-1]["event"] == "error"
        message = events[-1]["data"]["message"]
        assert "TEST_FAKE_PROVIDER_TOKEN" not in message
        assert "generation" in events[-1]["data"]["code"]


# ---------------------------------------------------------------------------
# Embedding lineage and input validation
# ---------------------------------------------------------------------------

async def test_embedding_model_mismatch_requires_reindex(workspace):
    async with client_for(workspace["app"]) as client:
        upload = await upload_text(client, "mismatch.txt", "Document indexed with an older model.")
        document_id = upload.json()["document_id"]
        await index(workspace, document_id)

        workspace["repository"].documents[document_id]["embedding_model"] = "old/incompatible-model"

        response = await client.post(
            "/api/v1/chat",
            json={"document_id": document_id, "message": "What is this?"},
        )
        assert response.status_code == 409
        assert response.json()["code"] == "embedding_model_mismatch"


async def test_invalid_document_id_is_rejected(workspace):
    async with client_for(workspace["app"]) as client:
        response = await client.post(
            "/api/v1/chat", json={"document_id": "not-a-uuid", "message": "Hello"}
        )
        assert response.status_code == 422

        response = await client.get("/api/v1/documents/not-a-uuid/status")
        assert response.status_code == 422


async def test_blank_chat_message_is_rejected(workspace):
    async with client_for(workspace["app"]) as client:
        response = await client.post(
            "/api/v1/chat",
            json={"document_id": "00000000-0000-4000-8000-000000000000", "message": "   "},
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Listing, deletion, CORS
# ---------------------------------------------------------------------------

async def test_listing_pagination(workspace):
    async with client_for(workspace["app"]) as client:
        ids = []
        for index_number in range(3):
            response = await upload_text(client, f"page_{index_number}.txt", f"Document {index_number}")
            ids.append(response.json()["document_id"])

        first = (await client.get("/api/v1/documents?limit=2&offset=0")).json()
        second = (await client.get("/api/v1/documents?limit=2&offset=2")).json()
        assert len(first) == 2
        assert len(second) == 1
        assert {item["document_id"] for item in first + second} == set(ids)

        invalid = await client.get("/api/v1/documents?limit=1000")
        assert invalid.status_code == 422


async def test_delete_removes_record_chunks_and_file(workspace):
    async with client_for(workspace["app"]) as client:
        upload = await upload_text(client, "delete_me.txt", "Temporary content")
        document_id = upload.json()["document_id"]
        await index(workspace, document_id)

        stored = list(Path(workspace["tmp_dir"]).rglob("delete_me.txt"))
        assert stored

        response = await client.delete(f"/api/v1/documents/{document_id}")
        assert response.status_code == 204
        assert not Path(stored[0]).exists()

        status = await client.get(f"/api/v1/documents/{document_id}/status")
        assert status.status_code == 404
        assert workspace["repository"].chunks.get(document_id) is None


async def test_cors_allowlist(workspace):
    async with client_for(workspace["app"]) as client:
        allowed = await client.options(
            "/api/v1/chat",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert allowed.status_code == 200
        assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
        assert "access-control-allow-credentials" not in allowed.headers

        denied = await client.options(
            "/api/v1/chat",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert "access-control-allow-origin" not in denied.headers


async def test_stale_ingestion_recovery(workspace):
    from datetime import datetime, timedelta, timezone

    repository = workspace["repository"]
    document = await repository.create_document(
        document_id="22222222-2222-4222-8222-222222222222",
        name="stuck.txt",
        mime_type="text/plain",
        size_bytes=10,
        storage_path="stuck.txt",
    )
    await repository.update_status(document["id"], status="parsing", progress_percentage=10)
    repository.documents[document["id"]]["updated_at"] = datetime.now(timezone.utc) - timedelta(hours=2)

    assert await repository.fail_stale_documents(30) == 1
    recovered = await repository.get_document(document["id"])
    assert recovered["status"] == "failed"
    assert "interrupted" in recovered["error_message"].lower()


def test_blank_cors_regex_is_disabled_and_origins_parse():
    from backend.core.config import Settings

    config = Settings(CORS_ORIGIN_REGEX="  ", CORS_ORIGINS="https://a.example, https://b.example/")
    assert config.CORS_ORIGIN_REGEX is None
    assert config.CORS_ORIGINS == ["https://a.example", "https://b.example"]


async def test_stream_without_provider_config_returns_json_503(workspace, monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", None)
    workspace["app"].state.chat_service = ChatService(
        repository=workspace["repository"],
        embeddings_model=workspace["embeddings"],
        llm_client=None,
    )
    async with client_for(workspace["app"]) as client:
        upload = await upload_text(client, "provider.txt", "The provider is not configured.")
        document_id = upload.json()["document_id"]
        await index(workspace, document_id)

        response = await client.post(
            "/api/v1/chat/stream",
            json={"document_id": document_id, "message": "What is configured?", "similarity_threshold": 0.0},
        )
        assert response.status_code == 503
        assert response.headers["content-type"].startswith("application/json")
        assert response.json()["code"] == "generation_unavailable"


async def test_overview_question_retrieves_without_threshold(workspace):
    async with client_for(workspace["app"]) as client:
        upload = await upload_text(
            client,
            "overview.txt",
            "The Phoenix budget is 50,000 dollars. Records are retained for seven years.",
        )
        document_id = upload.json()["document_id"]
        await index(workspace, document_id)

        response = await client.post(
            "/api/v1/chat",
            json={"document_id": document_id, "message": "Summarize this document"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["short_circuited"] is False
        assert body["sources"]
        assert workspace["llm"].requests

        explicit = await client.post(
            "/api/v1/chat",
            json={
                "document_id": document_id,
                "message": "Summarize this document",
                "similarity_threshold": 1.0,
            },
        )
        assert explicit.json()["short_circuited"] is True


def test_template_placeholders_are_not_treated_as_credentials():
    from backend.core.config import Settings

    config = Settings(
        DATABASE_URL=(
            "postgresql://postgres.project_ref:your_password@"
            "aws-0-region.pooler.supabase.com:6543/postgres?sslmode=require"
        ),
        SUPABASE_URL="https://your-project-ref.supabase.co",
        SUPABASE_SERVICE_ROLE_KEY="your_service_role_key_here",
        GROQ_API_KEY="your_groq_api_key_here",
    )
    assert config.DATABASE_URL is None
    assert config.SUPABASE_URL is None
    assert config.SUPABASE_SERVICE_ROLE_KEY is None
    assert config.GROQ_API_KEY is None
