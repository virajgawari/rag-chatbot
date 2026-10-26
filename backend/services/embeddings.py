"""CPU-only MiniLM inference without importing PyTorch at runtime."""
import os
import threading
from typing import List

import numpy as np

from backend.core.config import settings

MINILM_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def mean_pool(token_embeddings: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
    """Match MiniLM's attention-masked mean pooling, then normalize for cosine."""
    mask = attention_mask[..., None].astype(np.float32)
    pooled = (token_embeddings * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1e-9)
    return pooled / np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)


class OnnxEmbeddings:
    backend = "onnx"

    def __init__(self) -> None:
        if settings.EMBEDDING_MODEL_NAME != MINILM_MODEL or settings.EMBEDDING_DIMENSION != 384:
            raise ValueError("The ONNX adapter supports only all-MiniLM-L6-v2 / 384 dimensions.")

        # Keep tokenizer and inference thread pools within a small CPU allocation.
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        from huggingface_hub import hf_hub_download
        from onnxruntime import ExecutionMode, InferenceSession, SessionOptions
        from tokenizers import Tokenizer

        download = dict(repo_id=MINILM_MODEL, revision=settings.EMBEDDING_MODEL_REVISION)
        model_path = hf_hub_download(filename=settings.EMBEDDING_ONNX_FILE, **download)
        tokenizer_path = hf_hub_download(filename="tokenizer.json", **download)
        self.tokenizer = Tokenizer.from_file(tokenizer_path)
        # SentenceTransformers MiniLM truncates at 256, not tokenizer_config's 512.
        self.tokenizer.enable_truncation(max_length=256)
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        options = SessionOptions()
        options.intra_op_num_threads = settings.EMBEDDING_CPU_THREADS
        options.inter_op_num_threads = 1
        options.execution_mode = ExecutionMode.ORT_SEQUENTIAL
        self.session = InferenceSession(model_path, sess_options=options, providers=["CPUExecutionProvider"])
        self.input_names = {item.name for item in self.session.get_inputs()}
        # Serialize document and query inference to bound peak memory.
        self._lock = threading.Lock()

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        result: List[List[float]] = []
        with self._lock:
            for start in range(0, len(texts), settings.EMBEDDING_BATCH_SIZE):
                batch = texts[start : start + settings.EMBEDDING_BATCH_SIZE]
                encoded = self.tokenizer.encode_batch([text.replace("\n", " ").strip() for text in batch])
                inputs = {
                    "input_ids": np.asarray([item.ids for item in encoded], dtype=np.int64),
                    "attention_mask": np.asarray([item.attention_mask for item in encoded], dtype=np.int64),
                    "token_type_ids": np.asarray([item.type_ids for item in encoded], dtype=np.int64),
                }
                outputs = self.session.run(None, {key: value for key, value in inputs.items() if key in self.input_names})
                vectors = mean_pool(outputs[0], inputs["attention_mask"])
                if vectors.shape != (len(batch), 384) or not np.isfinite(vectors).all():
                    raise ValueError("Invalid embedding model output.")
                result.extend(vectors.tolist())
        return result

    def embed_query(self, text: str) -> List[float]:
        return self.embed_documents([text])[0]


def create_embeddings():
    if settings.EMBEDDING_BACKEND == "onnx":
        return OnnxEmbeddings()
    # Optional rollback path; install requirements-torch.txt before using this.
    import torch
    from langchain_huggingface import HuggingFaceEmbeddings

    torch.set_num_threads(settings.EMBEDDING_CPU_THREADS)
    return HuggingFaceEmbeddings(model_name=settings.EMBEDDING_MODEL_NAME)
