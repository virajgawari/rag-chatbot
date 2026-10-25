"""Health check endpoint."""
from fastapi import APIRouter, Request, Response, status

from backend.core.config import settings
from backend.db.memory_adapter import InMemoryRepository
from backend.db.supabase_pgvector import SupabasePgVectorRepository
from backend.models.schemas import HealthResponse
from backend.storage.local_storage import LocalStorage
from backend.storage.supabase_storage import SupabaseStorage

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthResponse)
async def health_check(request: Request, response: Response) -> HealthResponse:
    """Report connectivity and configuration without exposing any secret values."""
    app_state = request.app.state
    database_ok = await app_state.repository.health_check()

    if not database_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    repository = app_state.repository
    storage = app_state.storage
    database_backend = (
        "supabase_pgvector"
        if isinstance(repository, SupabasePgVectorRepository)
        else "in_memory"
        if isinstance(repository, InMemoryRepository)
        else type(repository).__name__
    )
    storage_backend = (
        "supabase_storage"
        if isinstance(storage, SupabaseStorage)
        else "local_filesystem"
        if isinstance(storage, LocalStorage)
        else type(storage).__name__
    )

    return HealthResponse(
        status="healthy" if database_ok else "degraded",
        version=settings.VERSION,
        embedding_model=settings.EMBEDDING_MODEL_NAME,
        embedding_dimension=settings.EMBEDDING_DIMENSION,
        database_backend=database_backend,
        storage_backend=storage_backend,
        groq_configured=bool(settings.GROQ_API_KEY),
    )
