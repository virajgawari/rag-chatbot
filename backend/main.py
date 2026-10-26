"""FastAPI application entrypoint for DocChat."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.api.v1.router import api_router
from backend.core.config import settings
from backend.core.errors import AppError
from backend.db.memory_adapter import InMemoryRepository
from backend.db.supabase_pgvector import SupabasePgVectorRepository
from backend.services.chat_service import ChatService
from backend.services.document_service import DocumentService
from backend.storage.local_storage import LocalStorage
from backend.storage.supabase_storage import SupabaseStorage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("docchat")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s (%s)", settings.PROJECT_NAME, settings.VERSION, settings.ENVIRONMENT)

    if settings.ENVIRONMENT.lower() in {"production", "prod"} and not settings.DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is required in production. The in-memory repository is "
            "development-only and loses all documents on restart."
        )

    if bool(settings.SUPABASE_URL) != bool(settings.SUPABASE_SERVICE_ROLE_KEY):
        logger.warning(
            "Supabase Storage needs both SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY; "
            "falling back to local filesystem storage."
        )

    if settings.SUPABASE_URL and settings.SUPABASE_SERVICE_ROLE_KEY:
        logger.info("Storage backend: Supabase Storage bucket '%s'", settings.SUPABASE_STORAGE_BUCKET)
        storage = SupabaseStorage(
            supabase_url=settings.SUPABASE_URL,
            service_role_key=settings.SUPABASE_SERVICE_ROLE_KEY,
            bucket=settings.SUPABASE_STORAGE_BUCKET,
        )
    else:
        logger.info("Storage backend: local filesystem at %s", settings.LOCAL_STORAGE_DIR)
        storage = LocalStorage(base_dir=settings.LOCAL_STORAGE_DIR)

    if settings.DATABASE_URL:
        logger.info("Database backend: Supabase Postgres (pgvector)")
        repository = SupabasePgVectorRepository(
            database_url=settings.DATABASE_URL,
            pool_min_size=settings.DATABASE_POOL_MIN_SIZE,
            pool_max_size=settings.DATABASE_POOL_MAX_SIZE,
            statement_timeout_ms=settings.DATABASE_STATEMENT_TIMEOUT_MS,
            statement_cache_size=settings.DATABASE_STATEMENT_CACHE_SIZE,
        )
        try:
            await repository.connect()
        except Exception as exc:  # noqa: BLE001 - log a safe, actionable startup error
            logger.error(
                "Could not connect to the configured Postgres database (%s). "
                "Check DATABASE_URL and that the Supabase project is reachable.",
                type(exc).__name__,
            )
            raise RuntimeError("Database connection failed; see server logs.") from exc
        if not await repository.health_check():
            logger.warning(
                "Database is reachable but the schema is not ready. "
                "Run 'python scripts/migrate.py' before uploading documents."
            )
        else:
            try:
                stale = await repository.fail_stale_documents(settings.STALE_INGESTION_MINUTES)
                if stale:
                    logger.warning("Marked %s interrupted document(s) as failed.", stale)
            except Exception as exc:  # noqa: BLE001 - recovery must not block startup
                logger.warning("Could not reconcile stale documents: %s", exc)
    else:
        logger.warning(
            "DATABASE_URL is not set: using the in-memory repository. "
            "Data is lost on restart and is not production-safe."
        )
        repository = InMemoryRepository()

    from backend.services.embeddings import create_embeddings

    logger.info("Loading embedding model '%s' (%s)", settings.EMBEDDING_MODEL_NAME, settings.EMBEDDING_BACKEND)
    embeddings = await asyncio.to_thread(create_embeddings)
    if settings.EMBEDDING_DIMENSION != 384:
        logger.warning(
            "EMBEDDING_DIMENSION is %s but migrations declare vector(384); "
            "a dimension migration and full re-embedding are required.",
            settings.EMBEDDING_DIMENSION,
        )

    app.state.storage = storage
    app.state.repository = repository
    app.state.embeddings = embeddings
    app.state.document_service = DocumentService(
        repository=repository,
        storage=storage,
        embeddings_model=embeddings,
        embedding_model_name=settings.EMBEDDING_MODEL_NAME,
        embedding_dimension=settings.EMBEDDING_DIMENSION,
    )
    app.state.chat_service = ChatService(
        repository=repository,
        embeddings_model=embeddings,
        embedding_model_name=settings.EMBEDDING_MODEL_NAME,
        embedding_dimension=settings.EMBEDDING_DIMENSION,
    )

    yield

    logger.info("Shutting down")
    if hasattr(repository, "disconnect"):
        await repository.disconnect()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=(
        "Document-grounded question answering API. Every chat request is scoped "
        "to exactly one document and returns citations with filename, page and snippet."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_origin_regex=settings.CORS_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Accept", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
    max_age=600,
)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    if exc.status_code >= 500:
        logger.error("%s on %s: %s", exc.code, request.url.path, exc.message)
    else:
        logger.info("%s on %s: %s", exc.code, request.url.path, exc.message)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message, "code": exc.code},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "An internal server error occurred. Please try again later.",
            "code": "internal_error",
        },
    )


app.include_router(api_router, prefix=settings.API_V1_STR)


@app.get("/health", include_in_schema=False)
async def root_health(request: Request, response: Response):
    from backend.api.v1.health import health_check

    return await health_check(request, response)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000)
