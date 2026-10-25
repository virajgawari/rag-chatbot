# DocChat FastAPI backend — container image.
#
# Build from the repository root so the Python package, migrations, and parser
# module are available:
#   docker build -f backend/Dockerfile -t docchat-api .
#
# The frontend is deployed separately (for example on Vercel); this image is
# intended for a container host because sentence-transformers/torch and the
# MiniLM model exceed serverless bundle limits.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    HF_HOME=/app/.cache/huggingface \
    TRANSFORMERS_CACHE=/app/.cache/huggingface

WORKDIR /app

# CPU-only torch keeps the image far smaller than the default CUDA wheels.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend ./backend
COPY migrations ./migrations
COPY scripts ./scripts

# Bake the exact embedding model into the image so container starts are fast
# and the model version is reproducible. Indexing and querying both use it.
ARG EMBEDDING_MODEL_NAME=sentence-transformers/all-MiniLM-L6-v2
ENV EMBEDDING_MODEL_NAME=${EMBEDDING_MODEL_NAME}
RUN python -c "import os; from sentence_transformers import SentenceTransformer; SentenceTransformer(os.environ['EMBEDDING_MODEL_NAME'])"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import os, sys, urllib.request; port = os.environ.get('PORT', '8000'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=4).status == 200 else 1)"

# Honor the platform-provided PORT (Render sets PORT=10000; local Docker
# defaults to 8000). One worker: each worker loads its own embedding model.
CMD uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1
