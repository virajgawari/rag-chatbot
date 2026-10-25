# Deploying DocChat

DocChat has two deployable pieces:

| Piece | Where | Why |
| --- | --- | --- |
| `frontend/` (React + Vite) | **Vercel** | Static SPA; Vercel is a natural fit. |
| `backend/` (FastAPI) | **Container host** (Render, Railway, Fly.io, ECS, etc.) | It runs `sentence-transformers`, PyTorch, and the MiniLM model locally. That image is far larger than a Vercel serverless function allows, and the model needs a long-lived process. |

The frontend is fully Vercel-ready. The backend is container-ready (see
`backend/Dockerfile`). Deploying the backend to Vercel is not supported with the
current local-embedding design; doing so would require replacing the embedding
provider with a hosted API and re-verifying every vector, which is out of scope
for this version.

---

## 1. Prerequisites

- A Supabase project with Postgres, pgvector enabled, and Storage.
- A Groq API key.
- A container host that can run a ~2 GB image and expose HTTP.
- A Vercel account.
- The Supabase **database password** (not the publishable/secret API keys).
  The secret key is used for Storage; asyncpg still needs the Postgres
  connection string.

Do not put the Groq key, Supabase secret key, or database password in the
frontend, in `VITE_*` variables, in git, or in screenshots.

---

## 2. Supabase setup

### 2.1 Database connection

In Supabase Dashboard → **Connect**, copy the **Session pooler** URI (port
`5432`) for IPv4 compatibility. The direct `db.<ref>.supabase.co` host is
IPv6-only in many projects and will fail to resolve on IPv4-only container
hosts, so prefer the pooler. It looks like:

```text
postgresql://postgres.<project-ref>:<database-password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
```

Put it in the backend environment as `DATABASE_URL`. Keep
`DATABASE_STATEMENT_CACHE_SIZE=0` when using the transaction pooler (port
`6543`); the session pooler also works with it at the cost of some caching.

### 2.2 Migrations

Run from the repository root with the backend environment loaded:

```bash
python scripts/migrate.py --status
python scripts/migrate.py
```

This applies:

1. `001_initial_schema.sql` — pgvector, `documents`, `document_chunks`
   (`vector(384)`), HNSW cosine index, updated-at trigger.
2. `002_embedding_lineage.sql` — embedding model/dimension, chunk progress,
   unique `(document_id, chunk_index)`, listing indexes.

The runner records applied versions and checksums in `schema_migrations`. Do
not edit an applied migration; add a new numbered file.

### 2.3 Private storage bucket

Set `SUPABASE_URL` (for example `https://<project-ref>.supabase.co`) and
`SUPABASE_SERVICE_ROLE_KEY` in the backend environment, then run:

```bash
python scripts/setup_storage.py
python scripts/setup_storage.py --verify-only
```

The script creates/repairs the private `documents` bucket with a 20 MiB limit
and PDF/DOCX/TXT MIME types. Supabase Storage returns a 400 with a nested
“Bucket not found” body for a missing bucket; the script handles that shape.
The API never returns public or signed URLs.

### 2.4 Existing files

No local document folder or pre-existing vector collection is imported.
Documents enter the persistent system only when uploaded (or explicitly
re-embedded with `scripts/reembed_documents.py`).

---

## 3. Backend deployment (container)

The image:

- uses CPU-only PyTorch;
- installs `backend/requirements.txt`;
- bakes `sentence-transformers/all-MiniLM-L6-v2` into the image at
  `/app/.cache/huggingface`;
- runs one Uvicorn worker (each worker loads its own model);
- has a `/health` healthcheck.

Build and run locally:

```bash
docker build -f backend/Dockerfile -t docchat-api .
docker run --rm --env-file .env -p 8000:8000 docchat-api
curl http://127.0.0.1:8000/health
```

On a container host:

1. Create a web service from this repository, Dockerfile
   `backend/Dockerfile`, repository root as build context.
2. Set the backend environment variables (see section 5).
3. Set the health check path to `/health`.
4. Run migrations once as a release/pre-deploy command:
   `python scripts/migrate.py`.
5. Set the public backend URL, for example `https://docchat-api.onrender.com`.

Run exactly one worker per instance for this deployment size. To scale, add
instances behind the platform; each instance will load its own model copy.
Indexing is in-process today, so restarting mid-ingestion marks stale documents
failed after `STALE_INGESTION_MINUTES`; re-upload to retry. A durable job queue
is the next backend hardening step.

---

## 4. Vercel frontend deployment

1. Import the repository into Vercel.
2. Set **Root Directory** to `frontend`.
3. Framework preset: **Vite**. Build command `npm run build`; output `dist`.
4. Add the environment variable for the deployed backend:
   ```text
   VITE_API_BASE_URL=https://docchat-api.example.com/api/v1
   ```
   The client accepts either the API root or a host and normalizes it to
   `/api/v1`.
5. Deploy. `frontend/vercel.json` supplies the SPA rewrite, security headers,
   and immutable asset caching.
6. Add the resulting Vercel origin to the backend `CORS_ORIGINS` (exact origin,
   no trailing slash), or set `CORS_ORIGIN_REGEX` for preview hostnames. Example:
   ```text
   CORS_ORIGINS=https://docchat.vercel.app
   CORS_ORIGIN_REGEX=https://docchat-[a-z0-9-]+\.vercel\.app
   ```
   Do not use `https://*.vercel.app` as an exact origin.
7. Redeploy or restart the backend so it picks up CORS changes.

`VITE_API_BASE_URL` is public configuration, not a secret. Never put
`GROQ_API_KEY`, `DATABASE_URL`, or `SUPABASE_SERVICE_ROLE_KEY` in it.

### Local preview of the production build

```bash
cd frontend
npm run build
npm run preview -- --host 127.0.0.1 --port 4173
```

Set `VITE_API_BASE_URL=http://127.0.0.1:8000/api/v1` if you are not using the
Vite dev proxy.

---

## 5. Environment variables

Backend:

| Variable | Required | Notes |
| --- | --- | --- |
| `ENVIRONMENT` | yes in production | Set `production`; startup refuses to use the in-memory store. |
| `GROQ_API_KEY` | yes | Server-only. |
| `GROQ_MODEL` | no | Default `openai/gpt-oss-120b`; must be enabled for the key. |
| `DATABASE_URL` | yes for persistence | Supabase session pooler URI with `?sslmode=require`. |
| `SUPABASE_URL` | yes for Supabase Storage | `https://<ref>.supabase.co`. |
| `SUPABASE_SERVICE_ROLE_KEY` | yes for Supabase Storage | Server-only. |
| `SUPABASE_STORAGE_BUCKET` | no | Default `documents`. |
| `CORS_ORIGINS` | yes | Comma-separated exact frontend origins. |
| `CORS_ORIGIN_REGEX` | no | Optional regex for Vercel previews. |
| `EMBEDDING_MODEL_NAME` | no | Do not change without re-embedding. |
| `EMBEDDING_DIMENSION` | no | `384`; changing it requires a dimension migration and full re-embedding. |

Frontend:

| Variable | Required | Notes |
| --- | --- | --- |
| `VITE_API_BASE_URL` | yes on Vercel | Public backend URL including `/api/v1`. |
| `VITE_API_PROXY_TARGET` | local only | Dev proxy target; default `http://127.0.0.1:8000`. |

---

## 6. Post-deployment verification

1. `GET https://<backend>/health` returns `200`, `status: healthy`,
   `database_backend: supabase_pgvector`, `storage_backend: supabase_storage`,
   `embedding_dimension: 384`.
2. Upload a two-page PDF from the deployed UI. Polling reaches `ready` with the
   expected page/chunk counts.
3. Ask a question answered on page 1 and confirm the answer and Page 1 chip.
4. Ask a question answered on page 2 and confirm the Page 2 chip.
5. Expand a source chip and confirm the verbatim passage.
6. Ask an out-of-scope question and confirm the deterministic refusal with no
   sources.
7. Open browser devtools: no `GROQ_API_KEY`, Supabase secret, or database URL
   appears in JS, network responses, or console logs.
8. Confirm an unauthenticated request to the private bucket object fails.

---

## 7. Rollback

- Vercel: promote the previous deployment in the Vercel dashboard.
- Backend: redeploy the previous image/tag; migrations are additive and
  idempotent. Do not roll back a migration that dropped a column or index
  without a reviewed inverse migration.
- Storage: uploaded objects are independent of the image; rollback does not
  delete them.

---

## 8. Known limitations before public exposure

- **No authentication.** Document scoping isolates retrieval, but anyone with a
  document UUID can query it, and `GET /documents` lists all documents. Add
  auth and per-owner scoping before exposing this to untrusted users.
- **No rate limiting.** Put the API behind a gateway or add middleware.
- **In-process indexing.** Use a durable queue for multi-worker/restart-safe
  ingestion.
- **Prompt injection.** Uploaded text can contain instructions; answers should
  be treated as untrusted content.
- **Vercel cannot host this backend** with local embeddings. See the top of this
  document.
