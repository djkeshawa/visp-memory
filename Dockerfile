# Multi-stage build for LLM Memory with Dashboard

# Stage 1: Build Next.js dashboard
FROM node:20.19.4-alpine3.22 AS frontend-builder

WORKDIR /app/dashboard
COPY llm-memory-dashboard/package*.json ./
RUN npm ci
COPY llm-memory-dashboard/ ./
RUN npm run export

# Stage 2: Build Python package
FROM python:3.11.13-slim-bookworm AS python-builder

WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ ./src/
COPY --from=frontend-builder /app/dashboard/out ./src/llm_memory/server/static/

# Install build dependencies
RUN pip install --no-cache-dir build && \
    python -m build

# Stage 3: Final runtime image
FROM python:3.11.13-slim-bookworm

# Install runtime dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user and writable data directory
RUN useradd -m -u 1000 llmuser && \
    mkdir -p /data && \
    chown -R llmuser:llmuser /data

WORKDIR /app

# Copy built wheel from builder
COPY --from=python-builder /app/dist/*.whl ./

# Install the runtime server profile from lock-derived constraints. Keep local
# transformers, Torch, and ChromaDB out of the production image.
ARG LLM_MEMORY_EXTRAS=api,mcp,arcadedb,neo4j,openai,anthropic,ollama
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir "uv==0.11.7" && \
    uv export --frozen --no-dev --no-emit-project --no-hashes \
      --extra api --extra mcp --extra arcadedb --extra neo4j --extra openai --extra anthropic --extra ollama \
      --output-file constraints.txt && \
    pip install --no-cache-dir --constraint constraints.txt "$(ls *.whl)[${LLM_MEMORY_EXTRAS}]" && \
    rm constraints.txt pyproject.toml uv.lock && \
    rm *.whl

# Switch to non-root user
USER llmuser

# Set environment variables
ENV LLM_MEMORY_STORAGE_DATA_DIR=/data
ENV LLM_MEMORY_EMBEDDING_PROVIDER=auto
ENV PYTHONUNBUFFERED=1

# Expose API port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/readyz').raise_for_status()" || exit 1

# Start the server
CMD ["uvicorn", "llm_memory.server.app:app", "--host", "0.0.0.0", "--port", "8000"]
