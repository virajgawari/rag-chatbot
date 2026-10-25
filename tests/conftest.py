"""Shared test fixtures for the DocChat API suite."""
import hashlib
import shutil
import sys
import tempfile
from pathlib import Path
from typing import List

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.db.memory_adapter import InMemoryRepository  # noqa: E402
from backend.main import app  # noqa: E402
from backend.services.chat_service import ChatService  # noqa: E402
from backend.services.document_service import DocumentService  # noqa: E402
from backend.storage.local_storage import LocalStorage  # noqa: E402


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeEmbeddings:
    """Deterministic 384-dimensional embeddings; no model download in unit tests."""

    dimension = 384

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> List[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [
            ((digest[i % len(digest)] ^ (i & 0xFF)) - 128.0) / 128.0
            for i in range(self.dimension)
        ]


class FakeLLM:
    """Async fake matching ChatGroq's ainvoke/astream shape."""

    def __init__(self, answer: str = "DocChat was engineered by Team Phoenix.") -> None:
        self.answer = answer
        self.requests: list = []

    async def ainvoke(self, prompt):
        self.requests.append(prompt)
        return AIMessage(content=self.answer)

    async def astream(self, prompt):
        self.requests.append(prompt)
        for token in self.answer.split(" "):
            yield AIMessageChunk(content=token + " ")


class ExplodingLLM:
    """Fails with a message containing a fake secret to prove errors are sanitized."""

    def __init__(self) -> None:
        self.requests: list = []

    async def ainvoke(self, prompt):
        self.requests.append(prompt)
        raise RuntimeError("provider failure with TEST_FAKE_PROVIDER_TOKEN")

    async def astream(self, prompt):
        self.requests.append(prompt)
        raise RuntimeError("provider failure with TEST_FAKE_PROVIDER_TOKEN")
        yield  # pragma: no cover


@pytest.fixture
def exploding_llm():
    return ExplodingLLM()


@pytest.fixture
def workspace():
    """Isolated storage + in-memory repository + fake services wired onto app.state."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="docchat_test_"))
    storage = LocalStorage(base_dir=tmp_dir)
    repository = InMemoryRepository()
    embeddings = FakeEmbeddings()
    llm = FakeLLM()

    app.state.storage = storage
    app.state.repository = repository
    app.state.embeddings = embeddings
    app.state.document_service = DocumentService(
        repository=repository,
        storage=storage,
        embeddings_model=embeddings,
    )
    app.state.chat_service = ChatService(
        repository=repository,
        embeddings_model=embeddings,
        llm_client=llm,
    )
    app.state.tmp_dir = tmp_dir

    yield {
        "app": app,
        "storage": storage,
        "repository": repository,
        "embeddings": embeddings,
        "llm": llm,
        "tmp_dir": tmp_dir,
        "document_service": app.state.document_service,
        "chat_service": app.state.chat_service,
    }

    shutil.rmtree(tmp_dir, ignore_errors=True)
