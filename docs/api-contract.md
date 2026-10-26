# DocChat API Contract (v1)

**Base URL (local):** `http://127.0.0.1:8000`
**API prefix:** `/api/v1`
**Content types:** JSON, except uploads (`multipart/form-data`) and SSE (`text/event-stream`)
**Backend version:** 2.0.0

This document is the authoritative wire contract for the backend. The React UI
is wired to these endpoints through `frontend/src/api.ts`; the mapping notes at
the end describe the client-side field names and SSE handling.

---

## 1. Security model and scope

- **No authentication is implemented yet.** Document scoping is an isolation
  mechanism, not authentication. Any caller who knows a document UUID can read
  it, and `GET /documents` lists all documents in the database. Add real auth
  before exposing this service to untrusted users (Phase 3+).
- Every chat retrieval executes with `WHERE document_id = $1`; there is no
  global vector search endpoint. Cross-document leakage is covered by tests.
- `GROQ_API_KEY`, `SUPABASE_SERVICE_ROLE_KEY` and `DATABASE_URL` are read only
  from backend environment variables. They never appear in responses, logs, or
  the frontend bundle.
- Original files live in a **private** Supabase Storage bucket. The API never
  returns public or signed URLs; it only returns metadata and extracted snippets.
- CORS uses an explicit origin allowlist (`CORS_ORIGINS`), no credentials and no
  cookies. `CORS_ORIGIN_REGEX` can allow changing preview hostnames.

## 2. Limits

| Limit | Value | Enforced by |
| --- | --- | --- |
| Upload size | 20 MiB (`MAX_UPLOAD_SIZE_BYTES`) | streamed read + `Content-Length` pre-check |
| File types | `.pdf`, `.docx`, `.txt` | extension + magic bytes / zip container |
| Chat message | 1–4000 characters, non-blank | Pydantic |
| Top-K | 1–20 (default 5) | Pydantic |
| Similarity threshold | 0.0–1.0 (default 0.2) | Pydantic + SQL |
| List page | `limit` 1–100 (default 20), `offset` ≥ 0 | Query validation |
| Generation timeout | 30 s (`GROQ_TIMEOUT_SECONDS`) | `asyncio.timeout` |
| DB statement timeout | 15 s (`DATABASE_STATEMENT_TIMEOUT_MS`) | asyncpg |

## 3. Error envelope

All non-stream JSON errors use the same shape. `code` is stable and safe to
branch on; `detail` is a client-safe message with no stack traces, SQL, keys or
internal paths.

```json
{
  "detail": "Document '…' not found.",
  "code": "document_not_found"
}
```

| HTTP | `code` examples | Meaning |
| --- | --- | --- |
| 400 | `unsupported_file_type`, `invalid_document` | Rejected upload/content |
| 404 | `document_not_found` | Unknown UUID |
| 409 | `document_not_ready`, `embedding_model_mismatch` | Not indexed yet / needs re-embedding |
| 413 | `file_too_large` | Over the upload cap |
| 422 | FastAPI validation `detail[]` | Invalid JSON/UUID/blank message/limit |
| 502 | `generation_failed` | Upstream model failed |
| 503 | `generation_unavailable`, `storage_unavailable` | Missing Groq key / storage down |
| 504 | `generation_timeout` | Model exceeded the timeout |

---

## 4. Health

### `GET /health` and `GET /api/v1/health`

```json
{
  "status": "healthy",
  "version": "2.0.0",
  "embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
  "embedding_dimension": 384,
  "database_backend": "supabase_pgvector",
  "storage_backend": "supabase_storage",
  "groq_configured": true,
  "embedding_backend": "onnx"
}
```

- `200` when the repository is reachable **and** the pgvector schema exists.
- `503` with `"status": "degraded"` when the schema/extension is missing or the
  DB is unreachable. It never returns secret values.

## 5. Upload

### `POST /api/v1/documents/upload`

`multipart/form-data` with one field named `file`.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/documents/upload \
  -F "file=@/path/to/document.pdf"
```

`202 Accepted`:

```json
{
  "document_id": "430ce0f5-95b6-4e71-b1ff-62dcceb3d532",
  "name": "policy.pdf",
  "status": "queued",
  "size_bytes": 2476,
  "created_at": "2026-10-26T00:16:48.123456+00:00"
}
```

The raw file is written to storage first, then metadata is inserted; if the DB
insert fails the stored object is deleted. Indexing continues in a background
task. Poll the status endpoint until `ready` or `failed`.

Filename sanitization removes directory components and unsafe characters.
Spoofed content (non-PDF bytes with `.pdf`, zip without `word/document.xml`,
binary `.txt`) is rejected.

## 6. Status

### `GET /api/v1/documents/{document_id}/status`

```json
{
  "document_id": "430ce0f5-95b6-4e71-b1ff-62dcceb3d532",
  "name": "policy.pdf",
  "status": "embedding",
  "progress_percentage": 78,
  "total_pages": 12,
  "total_chunks": 140,
  "chunks_processed": 112,
  "elapsed_seconds": 90.5,
  "estimated_remaining_seconds": 22.6,
  "error_message": null,
  "created_at": "2026-10-26T00:16:48.123456+00:00"
}
```

Statuses: `queued` → `parsing` → `chunking` → `embedding` → `ready`, or
`failed`. `progress_percentage` is monotonic for a run. Failures store a
generic, client-safe `error_message`; details are logged server-side.

Timing fields are additive and nullable. `elapsed_seconds` includes queue time.
The remaining-time estimate is available only while this worker is embedding,
after at least 4 chunks have been persisted; it is not a completion guarantee.
Progress writes occur about every 3 seconds (or after the last batch), not on
every chunk. The frontend keeps polling large documents instead of reporting a
false failure after ten minutes. A restart can still interrupt background work.

## 7. List

### `GET /api/v1/documents?limit=20&offset=0`

Returns an array, newest first:

```json
[
  {
    "document_id": "430ce0f5-95b6-4e71-b1ff-62dcceb3d532",
    "name": "policy.pdf",
    "status": "ready",
    "size_bytes": 2476,
    "total_pages": 12,
    "total_chunks": 140,
    "created_at": "2026-10-26T00:16:48.123456+00:00"
  }
]
```

This is a global operational listing (see the auth note). It is intentionally
not a search endpoint and never includes chunks or vectors.
Each item also includes `progress_percentage`, `chunks_processed`,
`elapsed_seconds`, and `estimated_remaining_seconds` so indexing remains visible
after refreshing or switching documents.

## 8. Delete

### `DELETE /api/v1/documents/{document_id}`

`204 No Content` on success, `404 document_not_found` otherwise. The document
row, its chunks (FK cascade) and the stored original are removed.

## 9. Chat (JSON)

### `POST /api/v1/chat`

```json
{
  "document_id": "430ce0f5-95b6-4e71-b1ff-62dcceb3d532",
  "message": "What is the Phoenix project budget?",
  "k": 5,
  "similarity_threshold": 0.2
}
```

`k` and `similarity_threshold` are optional.

```json
{
  "answer": "The Phoenix project budget is **$50,000**.",
  "sources": [
    {
      "filename": "phase2_smoke.pdf",
      "page": 1,
      "snippet": "The Phoenix project budget is 50,000 dollars.",
      "chunk_index": 0,
      "score": 0.785
    }
  ],
  "document_id": "430ce0f5-95b6-4e71-b1ff-62dcceb3d532",
  "short_circuited": false
}
```

- Retrieval is scoped to exactly `document_id`; `page` is always a 1-based
  integer, `filename` is the original upload name, `snippet` is verbatim text,
  and `score` is cosine similarity in `[0, 1]`.
- If no chunk meets the threshold, the LLM is **not called**:
  `short_circuited: true`, `answer` equals `NO_ANSWER_MESSAGE`, `sources: []`.
- Overview questions (`summarize`, `key takeaways`, `main points`, `overview`,
  `what should I know`, and similar) retrieve their top passages without the
  threshold when `similarity_threshold` is omitted, because such wording is
  often semantically distant from every individual passage. An explicit
  threshold is always honoured, and the strict prompt still refuses to answer
  when the passages do not support it.
- `404` unknown document, `409` not ready, `409` embedding-model mismatch,
  `503` missing `GROQ_API_KEY`, `502` upstream failure, `504` timeout.

## 10. Chat (SSE)

### `POST /api/v1/chat/stream`

Same JSON body as `/chat`. Use `fetch` + `ReadableStream` (not `EventSource`,
which cannot POST). `Accept: text/event-stream` is recommended.

Retrieval and validation happen **before** the response starts, so unknown or
not-ready documents return normal JSON `404`/`409` responses rather than a
broken `200` stream.

Success: `200`, `Content-Type: text/event-stream; charset=utf-8`,
`Cache-Control: no-cache, no-transform`.

```text
event: token
data: {"type": "token", "value": "The "}

event: token
data: {"type": "token", "value": "budget"}

event: sources
data: {"type": "sources", "value": [{"filename": "phase2_smoke.pdf", "page": 1, "snippet": "…", "chunk_index": 0, "score": 0.785}]}

event: done
data: {"type": "done", "finished": true}
```

- `token` events arrive incrementally; concatenate `value` in order.
- `sources` is emitted once after the final token (empty array for a
  short-circuit, which emits one `token` event containing `NO_ANSWER_MESSAGE`).
- On a mid-stream model failure: `event: error` with a generic
  `{ "type": "error", "code": "generation_failed" | "generation_timeout", "message": "…" }`
  followed by the end of the stream. Internal details and keys are never sent.
- A client disconnect cancels the generator; no server-side history is stored.

## 11. Frontend integration

`frontend/src/api.ts` is the only HTTP boundary. It maps snake_case API fields
to the UI's camelCase types:

- `document_id` → `documentId`;
- status strings (`queued`/`parsing`/`chunking`/`embedding`) → the real progress
  bar and "Indexing document…" label; `ready` → chat screen; `failed` → inline
  error with retry;
- citations expose `filename`, `page`, `snippet`, `chunkIndex`, and `score`;
  the UI groups multiple retrieved chunks from the same page into one page chip
  and shows the verbatim snippets in an expandable card;
- `/chat/stream` is consumed with `fetch` + `response.body.getReader()` and an
  `AbortController`; the stop button aborts the request and drops an empty
  partial answer;
- non-stream errors use `response.status` plus the stable `code` field; mid-stream
  failures arrive as an `error` event and render a Retry link against the
  original question;
- local development calls `/api/v1` through the Vite proxy. On Vercel,
  `VITE_API_BASE_URL` points at the deployed backend (the client normalizes the
  trailing `/api/v1`).

No mock functions, fake timers, hardcoded citation pages, or canned answers
remain in `frontend/src/`.

## 12. CORS

Default allowlist (from `CORS_ORIGINS`): `http://localhost:5173`,
`http://127.0.0.1:5173`, `http://localhost:5183`, `http://127.0.0.1:5183`,
`http://localhost:3000`, `http://127.0.0.1:3000`. Wildcards are not used;
`https://*.vercel.app` is not a valid exact origin. Set
`CORS_ORIGIN_REGEX=https://.*\.vercel\.app` for previews if needed.
`allow_credentials` is `false`; allowed methods are `GET`, `POST`, `DELETE`,
`OPTIONS`, and allowed request headers are `Content-Type`, `Accept`,
`X-Request-ID`.
