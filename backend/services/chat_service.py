"""Document-scoped retrieval and grounded generation.

The service owns retrieval and LLM interaction but knows nothing about HTTP.
Routes pre-validate by calling ``prepare`` before starting an SSE response, so
document errors are returned as normal JSON status codes rather than a broken
200 stream.
"""
import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, List, Optional

from langchain_core.prompts import ChatPromptTemplate

from backend.core.config import settings
from backend.core.errors import (
    DocumentNotFoundError,
    DocumentNotReadyError,
    EmbeddingModelMismatchError,
    GenerationFailedError,
    GenerationTimeoutError,
    GenerationUnavailableError,
)
from backend.db.base import DocumentRepository
from backend.models.schemas import ChatRequest, ChatResponse, SourceReference

logger = logging.getLogger(__name__)

# Document-level questions are often semantically distant from every individual
# passage ("key takeaways" scores near zero against a short document), so they
# retrieve the top passages without the relevance floor. The strict prompt still
# refuses to answer when the context does not support it.
_OVERVIEW_PATTERN = re.compile(
    r"\b(summar(?:y|ize|ise)|overview|key takeaways?|main (?:points|topics|ideas)|"
    r"tl;?dr|what is this (?:document|file) about|what should i know|highlights?)\b",
    re.IGNORECASE,
)

PROMPT_TEMPLATE = ChatPromptTemplate.from_template(
    """
You are a strict, factual AI Document Assistant.

Answer the user's question using ONLY the context passages below.

RULES:
1. Use only facts stated in the context. Do not use outside knowledge.
2. Do not extrapolate, speculate, or invent details.
3. If the context does not contain the answer, reply exactly:
"{no_answer}"
4. Be concise and faithful to the source text.

Context:
-------------------------
{context}
-------------------------

Question:
{question}

Answer:
"""
)


@dataclass
class PreparedChat:
    """Retrieval result, computed before any response body is streamed."""

    document_id: str
    question: str
    sources: List[SourceReference] = field(default_factory=list)
    context: Optional[str] = None
    short_circuited: bool = False


def format_sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


class ChatService:
    def __init__(
        self,
        repository: DocumentRepository,
        embeddings_model: Any,
        llm_client: Optional[Any] = None,
        embedding_model_name: str = settings.EMBEDDING_MODEL_NAME,
        embedding_dimension: int = settings.EMBEDDING_DIMENSION,
    ) -> None:
        self.repository = repository
        self.embeddings_model = embeddings_model
        self.llm_client = llm_client
        self.embedding_model_name = embedding_model_name
        self.embedding_dimension = embedding_dimension

    # ------------------------------------------------------------------
    # Preparation (retrieval + scoping)
    # ------------------------------------------------------------------

    async def prepare(self, request: ChatRequest) -> PreparedChat:
        document_id = str(request.document_id)
        doc = await self.repository.get_document(document_id)
        if not doc:
            raise DocumentNotFoundError(f"Document '{document_id}' not found.")

        if doc["status"] != "ready":
            raise DocumentNotReadyError(
                f"Document is not ready for chat (current status: {doc['status']})."
            )

        indexed_model = doc.get("embedding_model")
        indexed_dimension = doc.get("embedding_dimension")
        if (
            indexed_model != self.embedding_model_name
            or indexed_dimension != self.embedding_dimension
        ):
            logger.warning(
                "Refusing query for document %s: indexed with %s/%s, active model is %s/%s",
                document_id,
                indexed_model,
                indexed_dimension,
                self.embedding_model_name,
                self.embedding_dimension,
            )
            raise EmbeddingModelMismatchError(
                "This document was indexed with a different embedding model. "
                "Run the re-embedding migration before querying it."
            )

        query_embedding = await self._embed_query(request.message)
        threshold = (
            request.similarity_threshold
            if request.similarity_threshold is not None
            else settings.SIMILARITY_THRESHOLD
        )
        k = min(request.k or settings.DEFAULT_TOP_K, settings.MAX_TOP_K)
        is_overview = bool(_OVERVIEW_PATTERN.search(request.message))
        if is_overview and request.similarity_threshold is None:
            threshold = 0.0
            k = max(k, 8)

        scored = await self.repository.scoped_similarity_search(
            document_id=document_id,
            query_embedding=query_embedding,
            k=k,
            threshold=threshold,
        )
        qualifying = [(chunk, score) for chunk, score in scored if score >= threshold]

        if not qualifying:
            return PreparedChat(
                document_id=document_id,
                question=request.message,
                sources=[],
                context=None,
                short_circuited=True,
            )

        context_parts: List[str] = []
        sources: List[SourceReference] = []
        for index, (chunk, score) in enumerate(qualifying, start=1):
            page = int(chunk.get("page", 1))
            filename = doc["name"]
            context_parts.append(
                f"[Passage {index}] Source: {filename} (Page {page}):\n{chunk['content']}"
            )
            sources.append(
                SourceReference(
                    filename=filename,
                    page=page,
                    snippet=chunk["content"],
                    chunk_index=int(chunk["chunk_index"]),
                    score=round(float(score), 4),
                )
            )

        return PreparedChat(
            document_id=document_id,
            question=request.message,
            sources=sources,
            context="\n\n".join(context_parts),
            short_circuited=False,
        )

    async def _embed_query(self, text: str) -> List[float]:
        try:
            async with asyncio.timeout(settings.EMBEDDING_TIMEOUT_SECONDS):
                vector = await asyncio.to_thread(self.embeddings_model.embed_query, text)
        except TimeoutError as exc:
            raise GenerationTimeoutError("Embedding the question timed out.") from exc
        if len(vector) != self.embedding_dimension:
            raise EmbeddingModelMismatchError(
                "The active embedding model does not match the configured dimension."
            )
        return vector

    # ------------------------------------------------------------------
    # Generation (non-streaming)
    # ------------------------------------------------------------------

    async def chat(self, request: ChatRequest) -> ChatResponse:
        prepared = await self.prepare(request)
        if prepared.short_circuited:
            return ChatResponse(
                answer=settings.NO_ANSWER_MESSAGE,
                sources=[],
                document_id=prepared.document_id,
                short_circuited=True,
            )

        answer = await self._generate(prepared)
        return ChatResponse(
            answer=answer,
            sources=prepared.sources,
            document_id=prepared.document_id,
            short_circuited=False,
        )

    def _build_prompt(self, prepared: PreparedChat):
        return PROMPT_TEMPLATE.invoke(
            {
                "context": prepared.context or "",
                "question": prepared.question,
                "no_answer": settings.NO_ANSWER_MESSAGE,
            }
        )

    def ensure_generation_available(self) -> None:
        """Raise before a stream starts when no LLM can be constructed."""
        if self.llm_client is None and not settings.GROQ_API_KEY:
            raise GenerationUnavailableError("GROQ_API_KEY is not configured on the server.")

    def _get_llm(self):
        if self.llm_client is not None:
            return self.llm_client
        self.ensure_generation_available()
        from langchain_groq import ChatGroq

        return ChatGroq(
            model=settings.GROQ_MODEL,
            temperature=settings.GROQ_TEMPERATURE,
            groq_api_key=settings.GROQ_API_KEY,
            timeout=settings.GROQ_TIMEOUT_SECONDS,
            max_retries=1,
        )

    async def _invoke_text(self, llm: Any, prompt: Any) -> str:
        try:
            async with asyncio.timeout(settings.GROQ_TIMEOUT_SECONDS + 5):
                if hasattr(llm, "ainvoke"):
                    response = await llm.ainvoke(prompt)
                else:
                    response = await asyncio.to_thread(llm.invoke, prompt)
        except TimeoutError as exc:
            raise GenerationTimeoutError() from exc
        except Exception as exc:  # noqa: BLE001 - sanitized for the client
            logger.exception("LLM invocation failed: %s", exc)
            raise GenerationFailedError() from exc

        text = getattr(response, "content", None)
        if text is None:
            text = str(response)
        if isinstance(text, list):  # some providers return content blocks
            text = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part) for part in text
            )
        return str(text).strip()

    async def _generate(self, prepared: PreparedChat) -> str:
        llm = self._get_llm()
        prompt = self._build_prompt(prepared)
        answer = await self._invoke_text(llm, prompt)
        if not answer:
            raise GenerationFailedError("The model returned an empty answer.")
        return answer

    # ------------------------------------------------------------------
    # Generation (SSE)
    # ------------------------------------------------------------------

    async def stream(self, request: ChatRequest) -> AsyncGenerator[str, None]:
        """Yield SSE frames for an already-scoped chat request.

        Document/readiness errors propagate here only if the route skipped
        ``prepare``; the chat route always prepares first so those become JSON
        HTTP errors. LLM failures are reported as an ``error`` event.
        """
        prepared = await self.prepare(request)

        if prepared.short_circuited:
            yield format_sse("token", {"type": "token", "value": settings.NO_ANSWER_MESSAGE})
            yield format_sse("sources", {"type": "sources", "value": []})
            yield format_sse("done", {"type": "done", "finished": True})
            return

        async for frame in self.stream_prepared(prepared):
            yield frame

    async def stream_prepared(self, prepared: PreparedChat) -> AsyncGenerator[str, None]:
        if prepared.short_circuited:
            yield format_sse("token", {"type": "token", "value": settings.NO_ANSWER_MESSAGE})
            yield format_sse("sources", {"type": "sources", "value": []})
            yield format_sse("done", {"type": "done", "finished": True})
            return

        emitted = False
        try:
            llm = self._get_llm()
            prompt = self._build_prompt(prepared)
            async with asyncio.timeout(settings.GROQ_TIMEOUT_SECONDS + 5):
                async for token in self._iter_tokens(llm, prompt):
                    if not token:
                        continue
                    emitted = True
                    yield format_sse("token", {"type": "token", "value": token})

            if not emitted:
                raise GenerationFailedError("The model returned an empty answer.")

            yield format_sse(
                "sources",
                {
                    "type": "sources",
                    "value": [source.model_dump() for source in prepared.sources],
                },
            )
            yield format_sse("done", {"type": "done", "finished": True})
        except TimeoutError:
            logger.warning("Streaming generation timed out for document %s", prepared.document_id)
            yield format_sse(
                "error",
                {
                    "type": "error",
                    "code": "generation_timeout",
                    "message": "Answer generation timed out. Please retry.",
                },
            )
        except Exception as exc:  # noqa: BLE001 - never leak internals to the stream
            logger.exception("Streaming generation failed: %s", exc)
            yield format_sse(
                "error",
                {
                    "type": "error",
                    "code": "generation_failed",
                    "message": "Answer generation failed. Please retry.",
                },
            )

    async def _iter_tokens(self, llm: Any, prompt: Any) -> AsyncGenerator[str, None]:
        if hasattr(llm, "astream"):
            async for chunk in llm.astream(prompt):
                text = getattr(chunk, "content", chunk)
                if isinstance(text, list):
                    text = "".join(
                        part.get("text", "") if isinstance(part, dict) else str(part)
                        for part in text
                    )
                if text:
                    yield str(text)
            return

        # Synchronous client fallback: pull tokens on a worker thread so the
        # event loop is never blocked.
        iterator = await asyncio.to_thread(lambda: iter(llm.stream(prompt)))
        sentinel = object()
        while True:
            chunk = await asyncio.to_thread(next, iterator, sentinel)
            if chunk is sentinel:
                break
            text = getattr(chunk, "content", chunk)
            if text:
                yield str(text)
