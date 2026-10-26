"""Configuration for the DocChat FastAPI backend.

Every credential is read from backend environment variables (or the project
``.env`` file). Nothing here is ever sent to the browser.
"""
from pathlib import Path
from typing import Annotated, List, Literal, Optional, Set

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Values copied from .env.example must never be treated as real credentials.
_PLACEHOLDER_MARKERS = (
    "your-project-ref",
    "your_password",
    "your_region",
    "your_service_role_key",
    "your_groq_api_key",
    "postgres.project_ref",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    PROJECT_NAME: str = "DocChat API"
    VERSION: str = "2.0.0"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: str = "development"

    # CORS: explicit origins only. Credentials are not used (no cookies), so a
    # wildcard origin is safe but discouraged; a regex is available for Vercel
    # preview deployments whose hostnames change.
    CORS_ORIGINS: Annotated[List[str], NoDecode] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5183",
        "http://127.0.0.1:5183",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    CORS_ORIGIN_REGEX: Optional[str] = None

    # Upload limits
    MAX_UPLOAD_SIZE_BYTES: int = 20 * 1024 * 1024  # 20 MB
    MAX_FILENAME_LENGTH: int = 200
    ALLOWED_EXTENSIONS: Annotated[Set[str], NoDecode] = {".pdf", ".docx", ".txt"}

    # Embedding configuration. The pgvector column is vector(384); changing the
    # model requires the documented re-embedding migration.
    EMBEDDING_MODEL_NAME: str = "sentence-transformers/all-MiniLM-L6-v2"
    EMBEDDING_DIMENSION: int = 384
    EMBEDDING_BACKEND: Literal["onnx", "torch"] = "onnx"
    EMBEDDING_MODEL_REVISION: str = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
    EMBEDDING_ONNX_FILE: str = "onnx/model_quint8_avx2.onnx"
    EMBEDDING_CPU_THREADS: int = 1
    EMBEDDING_BATCH_SIZE: int = 4
    EMBEDDING_TIMEOUT_SECONDS: float = 120.0
    INDEXING_PROGRESS_INTERVAL_SECONDS: float = 3.0
    DEFAULT_CHUNK_SIZE: int = 1000
    DEFAULT_CHUNK_OVERLAP: int = 200
    # Documents left mid-pipeline after a restart are marked failed after this long.
    STALE_INGESTION_MINUTES: int = 30

    # Retrieval
    SIMILARITY_THRESHOLD: float = 0.2
    DEFAULT_TOP_K: int = 5
    MAX_TOP_K: int = 20
    NO_ANSWER_MESSAGE: str = "I cannot find relevant information in the given document."

    # Groq Cloud API (server-only secret)
    GROQ_API_KEY: Optional[str] = None
    GROQ_MODEL: str = "openai/gpt-oss-120b"
    GROQ_TIMEOUT_SECONDS: float = 30.0
    GROQ_TEMPERATURE: float = 0.0

    # Supabase Postgres / Storage (server-only secrets)
    DATABASE_URL: Optional[str] = None
    DATABASE_POOL_MIN_SIZE: int = 1
    DATABASE_POOL_MAX_SIZE: int = 10
    DATABASE_STATEMENT_TIMEOUT_MS: int = 15_000
    # 0 is required for PgBouncer transaction pooling (Supabase port 6543).
    DATABASE_STATEMENT_CACHE_SIZE: int = 0
    SUPABASE_URL: Optional[str] = None
    SUPABASE_SERVICE_ROLE_KEY: Optional[str] = None
    SUPABASE_STORAGE_BUCKET: str = "documents"

    # Local development storage (used when Supabase Storage is not configured)
    LOCAL_STORAGE_DIR: Path = PROJECT_ROOT / "storage" / "documents"

    # Listing pagination
    DOCUMENT_LIST_DEFAULT_LIMIT: int = 20
    DOCUMENT_LIST_MAX_LIMIT: int = 100

    @field_validator(
        "DATABASE_URL",
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "GROQ_API_KEY",
        mode="after",
    )
    @classmethod
    def _placeholder_is_unset(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        lowered = cleaned.lower()
        if any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
            return None
        return cleaned

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, value):
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [item.strip().rstrip("/") for item in value.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip().rstrip("/") for item in value if str(item).strip()]
        return value

    @field_validator("CORS_ORIGIN_REGEX", mode="before")
    @classmethod
    def _blank_regex_is_none(cls, value):
        # An empty regex would match every origin in Starlette; treat it as unset.
        if value is None or not str(value).strip():
            return None
        return str(value).strip()

    @field_validator("ALLOWED_EXTENSIONS", mode="before")
    @classmethod
    def _split_extensions(cls, value):
        if isinstance(value, str):
            return {
                (item if item.startswith(".") else f".{item}").strip().lower()
                for item in value.split(",")
                if item.strip()
            }
        if isinstance(value, (list, tuple, set)):
            return {
                (str(item) if str(item).startswith(".") else f".{item}").strip().lower()
                for item in value
                if str(item).strip()
            }
        return value

    @field_validator("EMBEDDING_DIMENSION")
    @classmethod
    def _positive_dimension(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("EMBEDDING_DIMENSION must be positive")
        return value

    @field_validator("EMBEDDING_CPU_THREADS", "EMBEDDING_BATCH_SIZE")
    @classmethod
    def _positive_count(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Embedding thread and batch counts must be positive")
        return value

    @field_validator("EMBEDDING_TIMEOUT_SECONDS", "GROQ_TIMEOUT_SECONDS", "INDEXING_PROGRESS_INTERVAL_SECONDS")
    @classmethod
    def _positive_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("Timeout values must be positive")
        return value


settings = Settings()
