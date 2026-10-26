# DocChat FastAPI backend — container image.
#
# Build from the repository root so the Python package, migrations, and parser
# module are available:
#   docker build -f backend/Dockerfile -t docchat-api .
#
# The frontend is deployed separately (for example on Vercel); this image is
# intended for a long-lived container host with a baked, quantized ONNX model.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    HF_HOME=/app/.cache/huggingface \
    HF_HUB_DISABLE_XET=1 \
    TOKENIZERS_PARALLELISM=false \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend ./backend
COPY migrations ./migrations
COPY scripts ./scripts

# Bake and validate the pinned INT8 model; neither PyTorch nor transformers is
# installed. Indexing and querying share the same tokenizer/pooling/runtime.
ARG EMBEDDING_MODEL_NAME=sentence-transformers/all-MiniLM-L6-v2
ENV EMBEDDING_MODEL_NAME=${EMBEDDING_MODEL_NAME}
RUN python -c "from backend.services.embeddings import create_embeddings; model = create_embeddings(); assert len(model.embed_query('Build verification')) == 384"
ENV HF_HUB_OFFLINE=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import os, sys, urllib.request; port = os.environ.get('PORT', '8000'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=4).status == 200 else 1)"

# Honor the platform-provided PORT (Render sets PORT=10000; local Docker
# defaults to 8000). One worker: each worker loads its own embedding model.
CMD uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1
