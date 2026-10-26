"""Re-embed documents whose vectors were produced by a different model.

Use this after changing EMBEDDING_MODEL_NAME (same 384 dimensions). It
re-downloads each stored original, re-parses, re-chunks, re-embeds, and replaces
that document's chunks atomically, then records the new model lineage.

Usage:
    python scripts/reembed_documents.py --dry-run
    python scripts/reembed_documents.py
    python scripts/reembed_documents.py --document-id <uuid>
    python scripts/reembed_documents.py --all       # force every ready document

Changing vector *dimensions* requires a schema migration first; see
docs/backend-runbook.md ("Changing the embedding model").

Stop API writers (or run during maintenance): per-process ingest locks do not
coordinate across multiple API workers.
"""
import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.core.config import settings  # noqa: E402
from backend.db.supabase_pgvector import SupabasePgVectorRepository  # noqa: E402
from backend.services.document_service import DocumentService  # noqa: E402
from backend.storage.local_storage import LocalStorage  # noqa: E402
from backend.storage.supabase_storage import SupabaseStorage  # noqa: E402

PAGE_SIZE = 100


async def _list_all_documents(repository) -> list[dict]:
    documents: list[dict] = []
    offset = 0
    while True:
        page = await repository.list_documents(limit=PAGE_SIZE, offset=offset)
        documents.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return documents


async def _run(args) -> int:
    if not settings.DATABASE_URL:
        print("ERROR: DATABASE_URL is not set; re-embedding requires the persistent database.")
        return 2

    repository = SupabasePgVectorRepository(
        database_url=settings.DATABASE_URL,
        pool_min_size=1,
        pool_max_size=2,
        statement_timeout_ms=settings.DATABASE_STATEMENT_TIMEOUT_MS,
    )
    await repository.connect()

    if settings.SUPABASE_URL and settings.SUPABASE_SERVICE_ROLE_KEY:
        storage = SupabaseStorage(
            supabase_url=settings.SUPABASE_URL,
            service_role_key=settings.SUPABASE_SERVICE_ROLE_KEY,
            bucket=settings.SUPABASE_STORAGE_BUCKET,
        )
    else:
        storage = LocalStorage(base_dir=settings.LOCAL_STORAGE_DIR)

    from backend.services.embeddings import create_embeddings

    print(f"Loading embedding model '{settings.EMBEDDING_MODEL_NAME}' ...")
    embeddings = await asyncio.to_thread(create_embeddings)

    service = DocumentService(
        repository=repository,
        storage=storage,
        embeddings_model=embeddings,
        embedding_model_name=settings.EMBEDDING_MODEL_NAME,
        embedding_dimension=settings.EMBEDDING_DIMENSION,
    )

    try:
        if args.document_id:
            target = await repository.get_document(args.document_id)
            if not target:
                print(f"ERROR: document {args.document_id} not found.")
                return 1
            targets = [target]
        else:
            documents = await _list_all_documents(repository)
            targets = [
                doc
                for doc in documents
                if doc["status"] == "ready"
                and (
                    args.all
                    or doc.get("embedding_model") != settings.EMBEDDING_MODEL_NAME
                    or doc.get("embedding_dimension") != settings.EMBEDDING_DIMENSION
                )
            ]

        if not targets:
            print("No documents require re-embedding.")
            return 0

        print(f"Documents to re-embed: {len(targets)}")
        failures = 0
        for doc in targets:
            document_id = str(doc["id"])
            print(
                f"  - {doc['name']} ({document_id}) "
                f"current={doc.get('embedding_model') or 'none'}/{doc.get('embedding_dimension') or '-'}",
                end="",
            )
            if args.dry_run:
                print(" [dry-run]")
                continue
            result = await service.reindex_document(document_id)
            if result["status"] == "ready":
                print(f" -> ready ({result['total_chunks']} chunks)")
            else:
                failures += 1
                print(" -> FAILED (see server logs)")
        return 1 if failures else 0
    finally:
        await repository.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-embed DocChat documents with the active model")
    parser.add_argument("--dry-run", action="store_true", help="list targets without changing data")
    parser.add_argument("--all", action="store_true", help="re-embed every ready document")
    parser.add_argument("--document-id", help="re-embed one document UUID")
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
