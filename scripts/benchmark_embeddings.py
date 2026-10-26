"""Benchmark synthetic chunks; optionally verify cosine compatibility with PyTorch.

Examples:
    python scripts/benchmark_embeddings.py --batch-size 4
    python scripts/benchmark_embeddings.py --compare-torch

The optional comparison requires requirements-torch.txt and cached legacy model
weights. No uploaded documents or credentials are read into the benchmark corpus.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from backend.core.config import settings  # noqa: E402
from backend.services.embeddings import OnnxEmbeddings  # noqa: E402

CORPUS = [
    "A cat is resting on a mat while a kitten sleeps on the rug.",
    "Postgres stores document vectors in pgvector tables for semantic search.",
    "Water boils at one hundred degrees Celsius at standard atmospheric pressure.",
    "A ripe banana has a yellow peel and is a fruit.",
    "The demonstration meeting is on Friday. The project code name is Mango.",
    "Mars is a rocky planet in our solar system, beyond Earth.",
    "Authentication and per-user authorization protect private uploaded documents.",
    "Solar panels convert sunlight into electricity using photovoltaic cells.",
    "  Résumé details\nand Unicode text are preserved in the document.  ",
    "Long passages need consistent token truncation. " * 100,
]
QUERIES = ["Where is the kitten sleeping?", "How are document vectors stored?",
           "What is the project code name?", "How do solar panels generate electricity?"]


def normalized(vectors):
    array = np.asarray(vectors)
    return array / np.maximum(np.linalg.norm(array, axis=1, keepdims=True), 1e-12)


def run(args):
    settings.EMBEDDING_BATCH_SIZE = args.batch_size
    started = time.perf_counter()
    model = OnnxEmbeddings()
    load_seconds = time.perf_counter() - started
    texts = [(CORPUS[index % 8] + " ") * 12 for index in range(args.chunks)]
    model.embed_query("Warm up inference")
    started = time.perf_counter()
    vectors = model.embed_documents(texts)
    seconds = time.perf_counter() - started
    result = {
        "backend": "onnx-int8", "batch_size": args.batch_size,
        "cpu_threads": settings.EMBEDDING_CPU_THREADS, "chunks": len(vectors),
        "model_load_seconds": round(load_seconds, 3),
        "embedding_seconds": round(seconds, 3),
        "chunks_per_second": round(len(vectors) / seconds, 2),
        "torch_imported_by_onnx": "torch" in sys.modules,
    }
    if args.compare_torch:
        import torch
        from langchain_huggingface import HuggingFaceEmbeddings

        torch.set_num_threads(settings.EMBEDDING_CPU_THREADS)
        reference = HuggingFaceEmbeddings(
            model_name=settings.EMBEDDING_MODEL_NAME,
            model_kwargs={"device": "cpu", "local_files_only": True},
        )
        actual = normalized(model.embed_documents(CORPUS))
        expected = normalized(reference.embed_documents(CORPUS))
        min_cosine = float(np.min(np.sum(actual * expected, axis=1)))
        query_vectors = normalized(model.embed_documents(QUERIES))
        reference_queries = normalized(reference.embed_documents(QUERIES))
        matches = int(np.sum(np.argmax(query_vectors @ expected.T, axis=1) ==
                             np.argmax(reference_queries @ expected.T, axis=1)))
        reference.embed_query("Warm up inference")
        started = time.perf_counter()
        for offset in range(0, len(texts), args.batch_size):
            reference.embed_documents(texts[offset : offset + args.batch_size])
        legacy_seconds = time.perf_counter() - started
        passed = min_cosine >= 0.98 and matches == len(QUERIES)
        result.update({
            "minimum_cosine_to_legacy": round(min_cosine, 6),
            "legacy_index_query_top1_matches": f"{matches}/{len(QUERIES)}",
            "legacy_embedding_seconds": round(legacy_seconds, 3),
            "speedup_vs_single_thread_torch": round(legacy_seconds / seconds, 2),
            "compatibility_passed": passed,
        })
    print(json.dumps(result, indent=2))
    return 0 if result.get("compatibility_passed", True) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks", type=int, default=64)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--compare-torch", action="store_true")
    args = parser.parse_args()
    if args.chunks < 1 or args.batch_size < 1:
        parser.error("chunks and batch-size must be positive")
    raise SystemExit(run(args))
