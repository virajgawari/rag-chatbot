"""Live Supabase Postgres/pgvector repository test.

Requires a disposable database (never production):

    TEST_DATABASE_URL=postgresql://.../docchat_test python -m pytest -m integration

The database name must contain "test". Migrations are applied idempotently, then
a document is inserted, scoped-searched, checked for cross-document isolation,
and deleted.
"""
import os
import sys
import uuid
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.db.supabase_pgvector import SupabasePgVectorRepository  # noqa: E402

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.anyio,
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set"),
]


async def test_pgvector_roundtrip_and_isolation():
    import asyncpg

    admin = await asyncpg.connect(TEST_DATABASE_URL, timeout=30.0)
    try:
        database_name = await admin.fetchval("SELECT current_database();")
        if "test" not in database_name.lower():
            pytest.fail(
                f"Refusing to run against database '{database_name}': "
                "TEST_DATABASE_URL must point at a disposable test database."
            )
        for migration in sorted((PROJECT_ROOT / "migrations").glob("*.sql")):
            await admin.execute(migration.read_text(encoding="utf-8"))
    finally:
        await admin.close()

    repository = SupabasePgVectorRepository(TEST_DATABASE_URL, pool_min_size=1, pool_max_size=2)
    await repository.connect()
    document_a = str(uuid.uuid4())
    document_b = str(uuid.uuid4())
    try:
        assert await repository.health_check() is True

        for document_id, label in ((document_a, "alpha"), (document_b, "beta")):
            await repository.create_document(
                document_id=document_id,
                name=f"{label}.txt",
                mime_type="text/plain",
                size_bytes=10,
                storage_path=f"{document_id}/{label}.txt",
            )
            chunks = [
                {
                    "chunk_index": 0,
                    "page": 1,
                    "content": f"{label.upper()}_SECRET content.",
                    "metadata": {"doc_id": document_id, "filename": f"{label}.txt"},
                }
            ]
            vector = [0.0] * 384
            vector[0] = 1.0
            if label == "beta":
                vector[1] = 1.0
            await repository.insert_chunks(
                document_id=document_id,
                chunks=chunks,
                embeddings=[vector],
                embedding_model="sentence-transformers/all-MiniLM-L6-v2",
                embedding_dimension=384,
            )

        hits = await repository.scoped_similarity_search(
            document_id=document_a,
            query_embedding=[1.0] + [0.0] * 383,
            k=10,
            threshold=0.0,
        )
        assert hits
        assert all(chunk["metadata"]["doc_id"] == document_a for chunk, _ in hits)
        assert all("BETA_SECRET" not in chunk["content"] for chunk, _ in hits)

        status = await repository.update_status(document_a, "ready", progress_percentage=100)
        assert status["status"] == "ready"

        document = await repository.get_document(document_a)
        assert document["embedding_model"] == "sentence-transformers/all-MiniLM-L6-v2"
        assert document["embedding_dimension"] == 384

        assert await repository.delete_document(document_a) is True
        assert await repository.get_document(document_a) is None
    finally:
        await repository.delete_document(document_b)
        await repository.disconnect()
