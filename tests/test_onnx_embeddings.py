"""Offline tests for pooling, bounded batches, and invalid model outputs."""
import threading
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from backend.core.config import Settings, settings
from backend.services.embeddings import OnnxEmbeddings, mean_pool


def test_mean_pool_ignores_padding_and_normalizes():
    tokens = np.asarray([[[3, 0], [0, 4], [999, 999]]], dtype=np.float32)
    result = mean_pool(tokens, np.asarray([[1, 1, 0]]))
    np.testing.assert_allclose(result, [[0.6, 0.8]], atol=1e-6)
    assert np.isfinite(mean_pool(tokens, np.zeros((1, 3)))).all()


class FakeTokenizer:
    def __init__(self):
        self.texts = []

    def encode_batch(self, texts):
        self.texts.extend(texts)
        return [SimpleNamespace(ids=[len(text), 0], attention_mask=[1, 0], type_ids=[0, 0]) for text in texts]


class FakeSession:
    def __init__(self):
        self.calls = []

    def run(self, outputs, inputs):
        self.calls.append(inputs)
        hidden = np.zeros((*inputs["input_ids"].shape, 384), dtype=np.float32)
        hidden[:, :, 0] = inputs["input_ids"]
        hidden[:, :, 1] = 1
        return [hidden]


def fake_model():
    model = OnnxEmbeddings.__new__(OnnxEmbeddings)
    model.tokenizer = FakeTokenizer()
    model.session = FakeSession()
    model.input_names = {"input_ids", "attention_mask"}
    model._lock = threading.Lock()
    return model


def test_batches_are_bounded_and_order_and_dimensions_are_preserved(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_BATCH_SIZE", 2)
    model = fake_model()
    result = model.embed_documents(["one\nline", "two", "third"])
    assert model.tokenizer.texts == ["one line", "two", "third"]
    assert [len(call["input_ids"]) for call in model.session.calls] == [2, 1]
    assert all(call["input_ids"].dtype == np.int64 for call in model.session.calls)
    assert all("token_type_ids" not in call for call in model.session.calls)
    assert len(result) == 3 and all(len(vector) == 384 for vector in result)
    for text, vector in zip(["one line", "two", "third"], result):
        expected = np.zeros(384)
        expected[:2] = [len(text), 1]
        expected /= np.linalg.norm(expected)
        np.testing.assert_allclose(vector, expected, atol=1e-6)
    np.testing.assert_allclose(model.embed_query("two"), result[1])


def test_empty_input_does_not_run_inference():
    model = fake_model()
    assert model.embed_documents([]) == []
    assert model.session.calls == []


@pytest.mark.parametrize("bad_output", [np.zeros((1, 2, 12)), np.full((1, 2, 384), np.nan)])
def test_invalid_model_outputs_are_rejected(bad_output):
    model = fake_model()
    model.session.run = lambda *_: [bad_output]
    with pytest.raises(ValueError, match="Invalid embedding"):
        model.embed_query("hello")


@pytest.mark.parametrize("field", ["EMBEDDING_BATCH_SIZE", "EMBEDDING_CPU_THREADS", "INDEXING_PROGRESS_INTERVAL_SECONDS"])
def test_non_positive_limits_are_rejected(field):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: 0})


def test_onnx_refuses_a_different_model(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_MODEL_NAME", "a-different-model")
    with pytest.raises(ValueError, match="supports only"):
        OnnxEmbeddings()
