"""Framework-agnostic domain errors.

Services raise these instead of importing FastAPI. The API layer maps them to
safe HTTP responses in ``backend.main``; internal details are only logged.
"""


class AppError(Exception):
    """Base class for expected, client-safe application errors."""

    status_code: int = 500
    code: str = "internal_error"
    default_message: str = "An unexpected error occurred."

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)


class DocumentNotFoundError(AppError):
    status_code = 404
    code = "document_not_found"
    default_message = "Document not found."


class DocumentNotReadyError(AppError):
    status_code = 409
    code = "document_not_ready"
    default_message = "Document is still being indexed. Try again once it is ready."


class EmbeddingModelMismatchError(AppError):
    status_code = 409
    code = "embedding_model_mismatch"
    default_message = (
        "This document was indexed with a different embedding model. "
        "It must be re-embedded before it can be queried."
    )


class UnsupportedFileTypeError(AppError):
    status_code = 400
    code = "unsupported_file_type"
    default_message = "Unsupported file type."


class InvalidDocumentError(AppError):
    status_code = 400
    code = "invalid_document"
    default_message = "The uploaded file could not be accepted."


class FileTooLargeError(AppError):
    status_code = 413
    code = "file_too_large"
    default_message = "The uploaded file is too large."


class StorageUnavailableError(AppError):
    status_code = 503
    code = "storage_unavailable"
    default_message = "File storage is temporarily unavailable."


class GenerationUnavailableError(AppError):
    status_code = 503
    code = "generation_unavailable"
    default_message = "Answer generation is not configured on this server."


class GenerationTimeoutError(AppError):
    status_code = 504
    code = "generation_timeout"
    default_message = "Answer generation timed out. Please retry."


class GenerationFailedError(AppError):
    status_code = 502
    code = "generation_failed"
    default_message = "Answer generation failed. Please retry."
