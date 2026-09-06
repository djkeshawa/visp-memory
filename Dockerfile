# syntax=docker/dockerfile:1

FROM node:20.19.4-alpine3.22 AS frontend-builder
WORKDIR /app/dashboard
ENV NEXT_TELEMETRY_DISABLED=1
COPY visp-memory-dashboard/package*.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY visp-memory-dashboard/ ./
RUN npm run export

# Dependency installation depends only on the lockfile and selected extras, so
# editing Python or dashboard code does not download the server stack again.
FROM python:3.11.13-slim-bookworm AS dependency-builder
WORKDIR /app
ARG VISP_MEMORY_EXTRAS=api,mcp,arcadedb,neo4j,openai,anthropic,ollama
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install "uv==0.11.7" && python -m venv /opt/venv
RUN --mount=type=cache,target=/root/.cache/uv \
    set -eu; \
    set --; \
    for extra in $(printf '%s' "$VISP_MEMORY_EXTRAS" | tr ',' ' '); do \
      set -- "$@" --extra "$extra"; \
    done; \
    uv export --frozen --no-dev --no-emit-project "$@" --output-file /tmp/requirements.txt; \
    uv pip install --python /opt/venv/bin/python --require-hashes -r /tmp/requirements.txt

FROM dependency-builder AS python-builder
COPY README.md LICENSE NOTICE ./
COPY src/ ./src/
COPY --from=frontend-builder /app/dashboard/out ./src/visp_memory/server/static/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv build --wheel --out-dir /dist && \
    uv pip install --python /opt/venv/bin/python --no-deps /dist/*.whl && \
    uv pip check --python /opt/venv/bin/python

FROM python:3.11.13-slim-bookworm AS runtime
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    useradd -m -u 1000 llmuser && \
    mkdir -p /data && chown llmuser:llmuser /data
WORKDIR /app
COPY LICENSE NOTICE ./
COPY --from=python-builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    VISP_MEMORY_STORAGE_DATA_DIR=/data \
    VISP_MEMORY_EMBEDDING_PROVIDER=auto \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER llmuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/readyz', timeout=3).close()"
CMD ["uvicorn", "visp_memory.server.app:app", "--host", "0.0.0.0", "--port", "8000"]
