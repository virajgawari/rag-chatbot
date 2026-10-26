"""Verify the real embedding model used by indexing and querying.

Marked slow because it loads the real ONNX model; run with:
    python -m pytest -m slow tests/test_embedding_model.py
"""
import math

import pytest

from backend.core.config import settings

pytestmark = pytest.mark.slow


def _cosine(left, right) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    return dot / (math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right)))


def test_embedding_model_is_384_dimensional_and_semantic():
    from backend.services.embeddings import create_embeddings

    model = create_embeddings()
    related = model.embed_query("The cat is resting on the mat.")
    paraphrase = model.embed_query("A kitten is sleeping on a rug.")
    unrelated = model.embed_query("Postgres stores documents in pgvector tables.")

    assert len(related) == settings.EMBEDDING_DIMENSION == 384
    assert len(paraphrase) == 384
    assert len(unrelated) == 384
    assert _cosine(related, paraphrase) > _cosine(related, unrelated)
