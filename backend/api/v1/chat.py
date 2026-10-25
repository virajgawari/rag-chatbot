"""Chat endpoints: document-scoped JSON answers and SSE streaming."""
from fastapi import APIRouter, Request, status
from fastapi.responses import StreamingResponse

from backend.models.schemas import ChatRequest, ChatResponse

router = APIRouter(prefix="/chat", tags=["Chat"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Content-Encoding": "identity",
}


@router.post("", response_model=ChatResponse, status_code=status.HTTP_200_OK)
async def chat_document(request: Request, body: ChatRequest) -> ChatResponse:
    """Answer a question using only passages from ``body.document_id``."""
    return await request.app.state.chat_service.chat(body)


@router.post("/stream", status_code=status.HTTP_200_OK)
async def stream_chat_document(request: Request, body: ChatRequest) -> StreamingResponse:
    """Stream a grounded answer as Server-Sent Events.

    Retrieval and validation run before the stream starts, so unknown or
    not-ready documents return ordinary JSON error responses (404/409) instead
    of a 200 stream that fails mid-flight.
    """
    chat_service = request.app.state.chat_service
    prepared = await chat_service.prepare(body)
    if not prepared.short_circuited:
        # Missing provider configuration becomes a JSON 503, not a 200 stream
        # that fails after the first frame.
        chat_service.ensure_generation_available()
    return StreamingResponse(
        chat_service.stream_prepared(prepared),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
