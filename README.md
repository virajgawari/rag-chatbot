# DocChat

A document-grounded RAG chat app. Upload a PDF, DOCX, or TXT, ask questions in
plain language, and get answers that cite the exact file, page, and passage the
answer came from.

Built with a React/Vite frontend, a FastAPI backend, Supabase Postgres +
pgvector, private Supabase Storage, MiniLM embeddings, and Groq for generation.

![DocChat stack](https://img.shields.io/badge/React%2018-Vite-61dafb?style=flat-square)
![FastAPI](https://img.shields.io/badge/FastAPI-Python%203.11%2B-009688?style=flat-square)
![Supabase](https://img.shields.io/badge/Supabase-Postgres%20%2B%20pgvector-3ecf8e?style=flat-square)

## Features

- Real upload progress and indexing telemetry (`queued` → `parsing` → `chunking`
  → `embedding` → `ready` / `failed`).
- Strictly document-scoped retrieval: every chat query is filtered by
  `document_id`.
- Server-Sent Events (SSE) streaming answers with a stop button.
- Page-level citations with filename, 1-based page, verbatim snippet, chunk
  index, and cosine score.
- Deterministic short-circuit for out-of-scope questions: the LLM is not called
  when no passage meets the similarity threshold.
- Private document storage with per-document delete.
- Responsive UI, keyboard-accessible controls, and a mobile document drawer.
- Versioned database migrations and a tracked migration runner.
- Local scratch mode for UI work without Supabase credentials.

## Architecture

```text
React + Vite (frontend/)
        │  upload / status / SSE chat
        ▼
FastAPI (backend/) ───────────────► Groq (generation)
        │
        ├── Supabase Postgres + pgvector (documents, chunks, embeddings)
        ├── Supabase Storage (private originals)
         └── MiniLM-L6-v2 via quantized ONNX (384-d, local CPU)
```

Business logic lives in `backend/services/`; HTTP routes live in
`backend/api/`. See [docs/architecture.md](docs/architecture.md) for the full
data flow and schema.

## Repository layout

```text
backend/
  api/v1/          FastAPI routes (health, documents, chat)
  core/            settings, errors, upload validation
  db/              pgvector repository + in-memory test adapter
  models/          Pydantic request/response schemas
  services/        ingestion, parsing, retrieval, generation
  storage/         Supabase Storage + local filesystem adapters
frontend/
  src/components/  upload, sidebar, chat, citations
  src/api.ts       the only HTTP/SSE boundary
migrations/        versioned SQL
scripts/           migrate, bucket setup, re-embedding
tests/             backend unit/API/integration tests
docs/              API contract, architecture, runbook, deployment
```

## Quick start

### Clone

```bash
git clone https://github.com/virajgawari/rag-chatbot.git
cd rag-chatbot
```

### Prerequisites

- Python **3.11+** (verified on 3.13)
- Node.js **18+**
- A Groq API key
- Optional: a Supabase project with Postgres + pgvector and Storage

### 1. Install

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r backend/requirements-dev.txt

cd frontend
npm install
cd ..
```

### 2. Configure

```bash
cp .env.example .env
```

At minimum, set `GROQ_API_KEY`. Without Supabase variables the backend runs in
scratch mode: metadata is in memory and uploaded files go to
`storage/documents/`. Data is lost on restart.

For persistent mode, set:

```dotenv
DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
SUPABASE_URL=https://<project-ref>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<server-only secret key>
SUPABASE_STORAGE_BUCKET=documents
```

`.env` is git-ignored. Never put the Groq key, Supabase secret key, or database
password in frontend code or a `VITE_*` variable.

### 3. Initialize Supabase (persistent mode only)

```bash
python scripts/migrate.py --status
python scripts/migrate.py
python scripts/setup_storage.py
python scripts/setup_storage.py --verify-only
```

The migrations enable pgvector, create `documents` and `document_chunks`
(`vector(384)`, HNSW cosine index), and add embedding lineage fields. The
storage script creates a **private** `documents` bucket with a 20 MiB limit.

### 4. Run

```bash
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

In a second terminal:

```bash
cd frontend
npm run dev
```

- App: http://127.0.0.1:5173
- API docs: http://127.0.0.1:8000/docs
- Health: http://127.0.0.1:8000/health

The Vite dev server proxies `/api` to `http://127.0.0.1:8000` by default.
Override with `VITE_API_PROXY_TARGET` in `frontend/.env.local` if needed.

## API

Base URL: `http://127.0.0.1:8000`; prefix: `/api/v1`.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Liveness and schema readiness |
| `POST` | `/api/v1/documents/upload` | Multipart upload, `202` + real UUID |
| `GET` | `/api/v1/documents/{id}/status` | Real indexing status and progress |
| `GET` | `/api/v1/documents?limit&offset` | Document metadata listing |
| `DELETE` | `/api/v1/documents/{id}` | Delete document, chunks, and stored file |
| `POST` | `/api/v1/chat` | Scoped grounded answer + citations |
| `POST` | `/api/v1/chat/stream` | Scoped SSE tokens + citations |

Full request/response examples, limits, SSE frames, and error codes are in
[docs/api-contract.md](docs/api-contract.md).

## Testing

```bash
python -m pytest -q                 # 22 passed, 1 skipped (live pgvector)
python -m pytest -m slow -q         # real MiniLM 384-d semantic check
python -m pytest -m integration -q  # live pgvector; requires TEST_DATABASE_URL
```

The fast suite covers upload validation, the full parse → chunk → embed → store
→ retrieve → answer → cite pipeline, page preservation, cross-document
isolation, threshold short-circuiting, SSE frames, delete cascade, CORS, and
safe errors.

The integration test refuses to run against a database whose name does not
contain `test`:

```bash
TEST_DATABASE_URL='postgresql://.../docchat_test?sslmode=require' \
  python -m pytest -m integration -q
```

Frontend:

```bash
cd frontend
npm run build
```

## Embeddings

Indexing and querying both use
`sentence-transformers/all-MiniLM-L6-v2` (384 dimensions), using a pinned INT8
ONNX model, the original 256-token limit, and masked mean pooling. The default
runtime does not load PyTorch. Small batches (4), one CPU inference thread, and
serialized ingestion bound memory on Render Free; progress writes are throttled
to every 3 seconds, with a final update always persisted. Each document records
its embedding model and dimension; chat returns `409 embedding_model_mismatch`
rather than mixing incompatible vectors. Re-embedding and dimension-change
procedures are documented in
[docs/backend-runbook.md](docs/backend-runbook.md#8-changing-the-embedding-model).

## Deployment

- **Frontend:** Vercel, root directory `frontend`, build `npm run build`, output
  `dist`. Set `VITE_API_BASE_URL` to the deployed backend URL including
  `/api/v1`.
- **Backend:** the FastAPI service must run as a container (or another
  long-lived Python host) for local ONNX inference and in-process indexing. Use
  `backend/Dockerfile`; deploying this backend as Vercel functions is not supported.
- Add the Vercel origin to `CORS_ORIGINS` on the backend.

Full instructions, environment variables, post-deploy checks, and rollback are
in [docs/deployment.md](docs/deployment.md).

## Security

- **No authentication yet.** Document scoping prevents cross-document retrieval,
  but it is not authorization. Anyone with a document UUID can query it, and
  `GET /documents` is a global metadata listing. Do not expose this API to
  untrusted users without auth, per-owner scoping, and rate limiting.
- Uploads are limited to 20 MiB and validated by extension, magic bytes, and
  DOCX container structure.
- Supabase Storage is private; the API never returns public or signed URLs.
- The Groq key, Supabase service credentials, and database URL are server-only.
- CORS is an explicit origin allowlist with credentials disabled.
- Indexing runs as an in-process background task today. Use a durable queue
  before scaling to multiple workers.

## License

No license has been added yet. Add one before distributing the project publicly.
