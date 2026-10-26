# DocChat Backend Runbook

FastAPI service exposing the repaired RAG pipeline to the React UI. Business
rules live in `backend/services/`; HTTP concerns live in `backend/api/` and
`backend/main.py`. The UI in `frontend/` is wired to this API through
`frontend/src/api.ts` (real upload progress, SSE chat, citations).

**Nothing has been deployed.** For Vercel/container deployment see
[deployment.md](deployment.md).

---

## 1. Prerequisites

- Python **3.11+** (developed and verified on 3.13).
- A Supabase project with Postgres + pgvector and Storage for persistent mode.
- A Groq API key with access to a chat model (default `openai/gpt-oss-120b`).
- Node 18+ only if you also run the frontend.

## 2. Install

From the repository root:

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r backend/requirements-dev.txt   # runtime + test tools
# or, runtime only:
pip install -r backend/requirements.txt
```

Run everything from the repository root so `backend.main` and the `backend`
package resolve consistently.

## 3. Configure

```bash
cp .env.example .env
```

Fill in `GROQ_API_KEY`. Add `DATABASE_URL`, `SUPABASE_URL`,
`SUPABASE_SERVICE_ROLE_KEY`, and `SUPABASE_STORAGE_BUCKET` for persistent mode.
`.env` is git-ignored and must never be committed, pasted into chat, or shipped
to the browser.

Two modes:

| Mode | Configuration | Persistence | Use |
| --- | --- | --- | --- |
| Local scratch | no `DATABASE_URL`, no `SUPABASE_URL` | in-memory DB, `storage/documents/` files | UI development, tests |
| Supabase | all Supabase variables set | Postgres/pgvector + private bucket | staging/production |

The local scratch repository implements the same scoped-search semantics but
loses all data on restart. It is not production storage.

### Supabase connection notes

- Prefer the Supabase **session pooler** (port 5432) for asyncpg, or the
  transaction pooler (port 6543) with `DATABASE_STATEMENT_CACHE_SIZE=0`
  (default), which disables prepared-statement caching for PgBouncer.
- Keep `sslmode=require` in `DATABASE_URL`.
- Direct `db.<ref>.supabase.co` is IPv6-only in many projects and may not
  resolve on IPv4-only machines or container hosts. Use the dashboard's
  **Session pooler** host instead; it carries the same database and password.
- The service-role key is used for Storage only; the DB is accessed with the
  Postgres connection string.

## 4. Migrations

Versioned SQL lives in `migrations/` and is applied once by a tracked runner.

```bash
python scripts/migrate.py --status   # applied / pending
python scripts/migrate.py            # apply pending in order
```

| Version | Purpose |
| --- | --- |
| `001_initial_schema` | pgvector extension, `documents`, `document_chunks`, HNSW cosine index, updated-at trigger |
| `002_embedding_lineage` | `embedding_model`, `embedding_dimension`, `chunks_processed`, unique `(document_id, chunk_index)`, listing/status indexes |

The runner creates `schema_migrations(version, checksum, applied_at)`, applies
each file in a transaction, and warns if an already-applied file's checksum
changes (it does not silently re-run). The migrations are idempotent, so a
database initialized by hand can be adopted safely.

`/health` returns `503 degraded` until the vector extension and both tables
exist. Migrations do not import local files; documents enter the persistent
index only when uploaded or explicitly re-embedded with
`scripts/reembed_documents.py`.

## 5. Private storage bucket

```bash
python scripts/setup_storage.py --verify-only   # inspect only
python scripts/setup_storage.py                 # create or fix
```

The script creates bucket `documents` with `public=false`, the 20 MiB size
limit, and PDF/DOCX/TXT MIME types. If the bucket exists but is public, it
switches it to private and re-verifies. The API never returns public/signed
URLs.

## 6. Run

```bash
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

# second terminal
cd frontend
npm install
npm run dev
```

- App: `http://127.0.0.1:5173` (Vite proxies `/api` to port 8000)
- API docs: `http://127.0.0.1:8000/docs`
- Health: `http://127.0.0.1:8000/health`
- Frontend dev origins are allowlisted by default. Add deployed origins to
  `CORS_ORIGINS` (comma-separated) or set `CORS_ORIGIN_REGEX` for Vercel
  previews.

Optional quick smoke test:

```bash
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/api/v1/documents/upload \
  -F "file=@/path/to/document.pdf"
# then poll
curl http://127.0.0.1:8000/api/v1/documents/<document_id>/status
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"document_id":"<document_id>","message":"What is this document about?"}'
```

Full request/response examples: [api-contract.md](api-contract.md).

## 7. Tests

```bash
python -m pytest -q                          # 22 passed, 1 skipped (fast fakes)
python -m pytest -m slow -q                  # real MiniLM 384-d semantic check
python -m pytest -m integration -q           # live pgvector (skipped without TEST_DATABASE_URL)
```

The suite was also run against the live Supabase project: migrations 001/002,
the private bucket, a real upload/index/chat/citation flow, and the guarded
pgvector test (on a disposable `docchat_test_ci` database) all passed.

A disposable database is required for the live repository test:

```bash
TEST_DATABASE_URL='postgresql://.../docchat_test?sslmode=require' \
  python -m pytest -m integration -q
```

The test refuses to run unless the database name contains `test`; it applies
migrations and inserts/deletes only its own rows. **Never point it at
production.**

What the fast suite covers: health, upload validation (type, magic bytes,
DOCX container, size cap, sanitized names), full upload → parse → chunk →
embed → store → status flow, multi-page PDF page preservation, scoped chat and
citations with filename/page/snippet, cross-document isolation, low-similarity
short-circuit (JSON and SSE, LLM not called), SSE token/sources/done framing,
pre-stream 404/409, sanitized SSE errors, embedding-lineage mismatch, UUID and
blank-message validation, pagination, delete cascade, CORS allowlisting, and
stale-ingestion recovery.

## 8. Changing the embedding model

Vectors from different models must never be mixed. The active model is recorded
on each document (`embedding_model`, `embedding_dimension`) when its chunks are
written. Chat returns `409 embedding_model_mismatch` for any document indexed
with a different model.

### Same dimension (384) — e.g. another MiniLM variant

The ONNX adapter is deliberately limited to the verified MiniLM model. For
another model, supply a validated adapter or install `requirements-torch.txt`
and use `EMBEDDING_BACKEND=torch` in a separately built legacy image.

1. Set `EMBEDDING_MODEL_NAME` in the server environment and restart the API.
2. Chat against existing documents now fails with `409` (by design).
3. Preview the work:
   ```bash
   python scripts/reembed_documents.py --dry-run
   ```
4. Run during a maintenance window (stop API writers or accept that a worker's
   per-process lock does not coordinate across processes):
   ```bash
   python scripts/reembed_documents.py
   ```
   Each document is re-downloaded from storage, re-parsed, re-chunked,
   re-embedded, and its chunks are replaced in a single transaction together
   with the new lineage. A failure leaves the previous chunks intact and marks
   the document `failed`.
5. Verify: `GET /api/v1/documents/{id}/status` is `ready` with the new
   `total_chunks`; query the document and confirm citations.

Use `--document-id <uuid>` to re-embed one document, or `--all` to force every
ready document.

### Different dimension (e.g. 768)

This needs DDL before re-embedding:

1. Stop API writers.
2. Add a new migration (next version, e.g. `003_embedding_768.sql`) that drops
   `idx_chunks_embedding_hnsw`, alters `document_chunks.embedding` to
   `vector(768)`, and recreates the HNSW index with the same operator class.
3. Apply it with `python scripts/migrate.py`.
4. Set `EMBEDDING_DIMENSION=768` and the new `EMBEDDING_MODEL_NAME`; restart.
5. Run `python scripts/reembed_documents.py --all` (dry-run first). Until a
   document is re-embedded, its old 384-d vectors cannot be inserted into the
   new column; the script re-embeds it from the stored original.
6. Re-run the live pgvector test on a disposable DB and verify chat citations.

## 9. Operational notes and known limitations

- **Auth is not implemented.** Scoping stops cross-document retrieval, but
  anyone with a document UUID can query it, and `GET /documents` lists all
  documents. Do not expose this service to untrusted users yet.
- **BackgroundTasks are in-process.** Indexing runs after the upload response
  in the same worker. A restart mid-ingest leaves a document in a non-terminal
  state; on startup the service marks ingestions older than
  `STALE_INGESTION_MINUTES` (default 30) as `failed` so clients stop polling.
  Re-upload to retry. Move to a durable job queue before scaling out.
- **Each worker loads the embedding model.** The default is a ~23 MB quantized
  ONNX artifact, without PyTorch/Transformers at runtime. It retains MiniLM's
  256-token truncation and masked mean pooling, with normalized cosine vectors.
  Use one worker, one inference thread and batches of 4 on small instances.
  Ingestion is serialized and progress DB writes are throttled to 3 seconds.
- **Performance rollouts:** benchmark representative PDFs and verify semantic
  retrieval before switching runtimes. Quantization changes vectors slightly;
  dimension equality alone is not proof of compatibility. The model name and
  dimension remain unchanged only for this verified MiniLM conversion. Do not
  switch to an unrelated embedding model without re-indexing.
- **Groq model availability varies by key.** If generation returns `502` with
  `model_not_found` in the server log, list models with
  `GET https://api.groq.com/openai/v1/models` and set `GROQ_MODEL` to an
  available chat model. The default is `openai/gpt-oss-120b`, verified against
  the development key (the older `llama-3.3-70b-versatile` was unavailable).
- **No request rate limiting** is implemented; put the API behind a gateway or
  add middleware before public exposure.
- **Listing is intentionally global** for the demo; it returns metadata only,
  never chunks, embeddings, or storage URLs.
- **Only uploaded files are stored.** The application does not read a local
  document folder or a pre-existing vector collection.
- Logs are the place for failure detail. Client errors are generic; never log
  request bodies that could contain document text or keys.
- **Prompt injection is not fully solved.** Uploaded text can contain
  instructions aimed at the model. Retrieval is scoped and the prompt forbids
  outside knowledge, but answers should be treated as untrusted content; add
  output review before acting on generated instructions.

## 10. Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `/health` is `503 degraded` | Migrations not applied, pgvector missing, or DB unreachable. Run `python scripts/migrate.py --status`. |
| Startup fails while connecting to Postgres | `DATABASE_URL` is wrong/unreachable or the database password is missing. Use the Supabase Session pooler URI and keep `?sslmode=require`. Placeholder template values are now ignored rather than used. |
| Upload `503 storage_unavailable` | Bucket missing/private misconfigured or Supabase URL/key wrong. Run `setup_storage.py --verify-only`. |
| Upload stuck in `parsing`/`embedding` | Worker restarted or crashed; check logs, wait for stale recovery, re-upload. |
| Chat `409 embedding_model_mismatch` | Document indexed with another model; run `reembed_documents.py`. |
| Chat `502` / `model_not_found` | `GROQ_MODEL` not enabled for the key; pick an available model. |
| `prepared statement ... does not exist` on port 6543 | Set `DATABASE_STATEMENT_CACHE_SIZE=0`. |
| CORS error from a deployed UI | Add its exact origin to `CORS_ORIGINS`; don't use `https://*.vercel.app` as an origin. |
| First startup is slow | Local development downloads the pinned ONNX model once. Docker bakes it at build time and loads offline. |
