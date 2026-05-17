"""
FastAPI Server Entry Point
"""

import logging
import os
from datetime import datetime
from pathlib import Path

try:
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse, Response
    from fastapi.staticfiles import StaticFiles
except ImportError:
    raise ImportError("FastAPI not installed. Run: pip install llm-memory[api]")

from llm_memory import __version__
from llm_memory.config import load_config
from llm_memory.core.neo4j_storage import Neo4jStorage
from llm_memory.core.storage import LocalStorage
from llm_memory.server.routers import intents, memories, relationships, repositories, teams

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

config = load_config()


def get_cors_options(config):
    """Build CORS options from config, avoiding wildcard credentials."""
    origins = config.server.cors_origins
    allow_credentials = config.server.cors_allow_credentials and "*" not in origins
    return {
        "allow_origins": origins,
        "allow_credentials": allow_credentials,
    }


def get_server_embedding_provider(config):
    """Build the configured embedding provider for API/server storage."""
    if (
        config.embedding.provider == "sentence-transformers"
        and "LLM_MEMORY_EMBEDDING_PROVIDER" not in os.environ
    ):
        logger.info(
            "Server storage is using text fallback until an embedding provider is explicitly "
            "configured with LLM_MEMORY_EMBEDDING_PROVIDER."
        )
        return None

    try:
        from llm_memory.core.embeddings import get_embedding_provider

        return get_embedding_provider(config.embedding)
    except Exception as e:
        logger.warning(
            "Failed to initialize configured embedding provider %r for server storage; "
            "falling back to noop embeddings: %s",
            config.embedding.provider,
            e,
        )
        try:
            from llm_memory.core.embeddings import NoOpProvider

            return NoOpProvider()
        except Exception:
            return None


def get_server_embedding_fn(config):
    """Build the configured embedding function for API/server storage."""
    provider = get_server_embedding_provider(config)
    return provider.embed if provider is not None else None


def get_runtime_status(config, embedding_provider=None):
    """Return non-secret runtime configuration for readiness and dashboard views."""
    effective_provider = getattr(embedding_provider, "provider_name", None)
    effective_model = getattr(embedding_provider, "model", None)
    return {
        "storage_backend": config.storage.backend,
        "storage_mode": config.storage.mode,
        "vector_db": config.storage.vector_db,
        "embedding_provider": config.embedding.provider,
        "embedding_effective_provider": effective_provider or config.embedding.provider,
        "embedding_model": effective_model or config.embedding.model,
        "auth_enabled": config.server.auth_enabled,
        "repo_id": config.repo_id,
    }


cors_options = get_cors_options(config)
STATIC_DIR = Path(__file__).parent / "static"


def dashboard_file_path(full_path: str) -> Path:
    """Resolve a dashboard route to an exported static file."""
    static_root = STATIC_DIR.resolve()
    normalized_path = full_path.strip("/")
    candidates = []

    if normalized_path:
        candidates.extend(
            [
                STATIC_DIR / normalized_path,
                STATIC_DIR / f"{normalized_path}.html",
                STATIC_DIR / normalized_path / "index.html",
            ]
        )
    else:
        candidates.append(STATIC_DIR / "index.html")

    for candidate in candidates:
        resolved_candidate = candidate.resolve()
        if (
            resolved_candidate.is_relative_to(static_root)
            and resolved_candidate.exists()
            and resolved_candidate.is_file()
        ):
            return resolved_candidate

    return static_root / "index.html"


# Initialize App
app = FastAPI(
    title="LLM Central Memory Server",
    description="Shared memory server for multi-repo context",
    version=__version__,
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_options["allow_origins"],
    allow_credentials=cors_options["allow_credentials"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Storage
embedding_provider = get_server_embedding_provider(config)
embedding_fn = embedding_provider.embed if embedding_provider is not None else None
effective_storage_backend = "sqlite"
if config.storage.backend == "neo4j":
    try:
        storage = Neo4jStorage(
            uri=config.storage.neo4j_uri,
            user=config.storage.neo4j_user,
            password=config.storage.neo4j_password,
            embedding_fn=embedding_fn,
            embedding_dimension=getattr(embedding_provider, "dimension", None),
        )
        effective_storage_backend = "neo4j"
        logger.info("Initialized Neo4j Storage")
    except Exception as e:
        logger.error(f"Failed to initialize Neo4j, falling back to SQLite: {e}")
        storage = LocalStorage(config.storage.data_dir, embedding_fn=embedding_fn)
else:
    storage = LocalStorage(config.storage.data_dir, embedding_fn=embedding_fn)

# Save storage to app state for access in routers
app.state.storage = storage
app.state.storage_backend = effective_storage_backend

# Include Routers
app.include_router(memories.router)
app.include_router(intents.router)
app.include_router(repositories.router)
app.include_router(teams.router)
app.include_router(relationships.router)

# Optional routers for Phase 3.4+ (to be implemented)
try:
    from llm_memory.server.routers import analysis

    if hasattr(analysis, "router"):
        app.include_router(analysis.router)
except ImportError:
    pass


@app.get("/", tags=["system"])
async def root(repo_id: str = None):
    """System status and stats."""
    try:
        stats = app.state.storage.get_stats(repo_id=repo_id or config.repo_id)
    except Exception as e:
        logger.error(f"Failed to get stats: {e}")
        stats = {}

    response = {
        "status": "online",
        "version": __version__,
        "timestamp": datetime.now().isoformat(),
        "stats": stats,
    }
    runtime = get_runtime_status(config, embedding_provider)
    runtime["storage_backend"] = app.state.storage_backend
    response.update(runtime)
    response.update(stats)
    return response


@app.get("/healthz", tags=["system"])
async def healthz():
    """Unauthenticated liveness probe."""
    return {
        "status": "ok",
        "version": __version__,
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/readyz", tags=["system"])
async def readyz():
    """Unauthenticated readiness probe for release and first-run checks."""
    storage_ready = True
    storage_error = None

    try:
        app.state.storage.get_stats()
    except Exception as e:
        storage_ready = False
        storage_error = e.__class__.__name__
        logger.error("Storage readiness check failed: %s", e)

    dashboard_static_available = STATIC_DIR.exists() and STATIC_DIR.is_dir()
    ready = storage_ready and dashboard_static_available
    payload = {
        "status": "ready" if ready else "not_ready",
        "version": __version__,
        "storage_ready": storage_ready,
        "dashboard_static_available": dashboard_static_available,
        "timestamp": datetime.now().isoformat(),
    }
    runtime = get_runtime_status(config, embedding_provider)
    runtime["storage_backend"] = app.state.storage_backend
    payload.update(runtime)

    if storage_error:
        payload["storage_error"] = storage_error

    return JSONResponse(status_code=200 if ready else 503, content=payload)


@app.head("/favicon.ico", include_in_schema=False)
async def favicon_head():
    """Allow favicon existence checks."""
    return Response(status_code=200)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Serve the bundled dashboard icon when static assets are available."""
    for name in ("icon-light-32x32.png", "icon.svg"):
        icon_path = STATIC_DIR / name
        if icon_path.exists():
            return FileResponse(icon_path)
    return Response(status_code=204)


if STATIC_DIR.exists() and STATIC_DIR.is_dir():
    # Mount static assets
    if (STATIC_DIR / "_next" / "static").exists():
        app.mount(
            "/static",
            StaticFiles(directory=str(STATIC_DIR / "_next" / "static")),
            name="static",
        )
    if (STATIC_DIR / "_next").exists():
        app.mount("/_next", StaticFiles(directory=str(STATIC_DIR / "_next")), name="next")

    # Serve dashboard at /dashboard and root
    @app.head("/dashboard/{full_path:path}")
    async def serve_dashboard_path_head(full_path: str):
        """Allow Next.js link prefetch checks for dashboard routes."""
        return Response(status_code=200)

    @app.head("/dashboard")
    async def serve_dashboard_head():
        """Allow Next.js link prefetch checks for the dashboard index."""
        return Response(status_code=200)

    @app.get("/dashboard/{full_path:path}")
    async def serve_dashboard_path(full_path: str):
        """Serve dashboard files."""
        return FileResponse(dashboard_file_path(full_path))

    @app.get("/dashboard")
    async def serve_dashboard():
        """Serve dashboard index."""
        return FileResponse(dashboard_file_path(""))

    logger.info(f"Dashboard mounted at /dashboard from {STATIC_DIR}")
else:
    logger.warning(
        f"Dashboard static files not found at {STATIC_DIR}. Dashboard will not be available."
    )
    logger.info("To build the dashboard, run: python build_frontend.py")
