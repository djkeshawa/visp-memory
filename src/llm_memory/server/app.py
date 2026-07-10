"""
FastAPI Server Entry Point
"""

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from llm_memory.core.clock import utc_now

try:
    from fastapi import Depends, FastAPI, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
    from fastapi.staticfiles import StaticFiles
except ImportError:
    raise ImportError("FastAPI not installed. Run: pip install llm-memory[api]")

from llm_memory import __version__
from llm_memory.config import load_config
from llm_memory.core.arcadedb_storage import ArcadeDbStorage
from llm_memory.core.neo4j_storage import Neo4jStorage
from llm_memory.core.reporting import MemoryIntelligenceReporter
from llm_memory.core.storage import LocalStorage
from llm_memory.recall.graph import GraphRecall
from llm_memory.server.auth import UserContext, get_current_user
from llm_memory.server.authorization import (
    can_access_scoped_record,
    require_repo_scope_access,
)
from llm_memory.server.routers import (
    ai,
    diagnostics,
    intents,
    memories,
    platform,
    quality,
    relationships,
    repositories,
    sessions,
    teams,
)
from llm_memory.server.schemas import (
    GraphNeighborsRequest,
    GraphPathRequest,
    GraphRecallResponse,
    GraphTraceRequest,
    GraphWhyRelevantRequest,
    MemoryIntelligenceReportResponse,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

config = load_config()


def describe_embedding_connection_error(provider: str, error: Exception) -> tuple[str, str]:
    """Return a non-secret error class and user-facing connection message."""
    status_code = getattr(error, "status_code", None)
    provider_label = provider.replace("-", " ").title()
    if status_code == 401:
        return (
            "HTTP 401",
            f"{provider_label} rejected the configured credentials; check the API key.",
        )
    if status_code == 403:
        return (
            "HTTP 403",
            f"{provider_label} denied access for the configured credentials.",
        )

    return (
        error.__class__.__name__,
        f"{provider} embedding driver failed to connect.",
    )


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
    provider, _ = get_server_embedding_runtime(config)
    return provider


def get_server_embedding_runtime(config):
    """Build the server embedding provider and a non-secret connection status."""
    if (
        config.embedding.provider == "sentence-transformers"
        and "LLM_MEMORY_EMBEDDING_PROVIDER" not in os.environ
    ):
        logger.info(
            "Server storage is using text fallback until an embedding provider is explicitly "
            "configured with LLM_MEMORY_EMBEDDING_PROVIDER."
        )
        return None, {
            "embedding_driver_status": "not_configured",
            "embedding_driver_connected": False,
            "embedding_last_checked_at": utc_now().isoformat(),
            "embedding_status_message": (
                "No server embedding driver is configured; using text fallback."
            ),
        }

    try:
        from llm_memory.core.embeddings import get_embedding_provider

        provider = get_embedding_provider(config.embedding, verify=True)
        provider_name = getattr(provider, "provider_name", None)
        if provider_name in {"noop", "none"}:
            configured_provider = config.embedding.provider
            status = "disabled" if configured_provider in {"noop", "none"} else "fallback"
            message = (
                "Embeddings are disabled."
                if status == "disabled"
                else "No embedding driver connected; using noop embeddings."
            )
            return provider, {
                "embedding_driver_status": status,
                "embedding_driver_connected": False,
                "embedding_last_checked_at": utc_now().isoformat(),
                "embedding_status_message": message,
            }

        return provider, {
            "embedding_driver_status": "connected",
            "embedding_driver_connected": True,
            "embedding_last_checked_at": utc_now().isoformat(),
            "embedding_status_message": "Embedding driver connected.",
        }
    except Exception as e:
        error_name, error_message = describe_embedding_connection_error(
            config.embedding.provider, e
        )
        logger.warning(
            "Failed to initialize configured embedding provider %r for server storage; "
            "falling back to noop embeddings: %s",
            config.embedding.provider,
            e,
        )
        try:
            from llm_memory.core.embeddings import NoOpProvider

            return NoOpProvider(), {
                "embedding_driver_status": "failed",
                "embedding_driver_connected": False,
                "embedding_last_checked_at": utc_now().isoformat(),
                "embedding_status_message": f"{error_message} Using noop embeddings.",
                "embedding_connection_error": error_name,
            }
        except Exception:
            return None, {
                "embedding_driver_status": "failed",
                "embedding_driver_connected": False,
                "embedding_last_checked_at": utc_now().isoformat(),
                "embedding_status_message": error_message,
                "embedding_connection_error": error_name,
            }


def get_server_embedding_fn(config):
    """Build the configured embedding function for API/server storage."""
    provider = get_server_embedding_provider(config)
    return provider.embed if provider is not None else None


def get_runtime_status(config, embedding_provider=None, embedding_status=None):
    """Return non-secret runtime configuration for readiness and dashboard views."""
    effective_provider = getattr(embedding_provider, "provider_name", None)
    effective_model = getattr(embedding_provider, "model", None)
    if embedding_status is None:
        if effective_provider in {"noop", "none"}:
            embedding_status = {
                "embedding_driver_status": "disabled",
                "embedding_driver_connected": False,
            }
        elif embedding_provider is None:
            embedding_status = {
                "embedding_driver_status": "not_configured",
                "embedding_driver_connected": False,
            }
        else:
            embedding_status = {
                "embedding_driver_status": "connected",
                "embedding_driver_connected": True,
            }
    status = {
        "storage_backend": config.storage.backend,
        "storage_mode": config.storage.mode,
        "vector_db": config.storage.vector_db,
        "embedding_provider": config.embedding.provider,
        "embedding_effective_provider": effective_provider,
        "embedding_model": effective_model or config.embedding.model,
        "auth_enabled": config.server.auth_enabled,
        "repo_id": config.repo_id,
    }
    status.update(embedding_status)
    return status


def initialize_storage(config, embedding_fn=None, embedding_provider=None):
    """Initialize the configured server storage backend."""
    if config.storage.backend == "neo4j":
        deadline = time.monotonic() + config.storage.connect_timeout_seconds
        last_error = None
        while True:
            try:
                storage = Neo4jStorage(
                    uri=config.storage.neo4j_uri,
                    user=config.storage.neo4j_user,
                    password=config.storage.neo4j_password,
                    embedding_fn=embedding_fn,
                    embedding_dimension=getattr(embedding_provider, "dimension", None),
                )
                logger.info("Initialized Neo4j storage")
                return storage, "neo4j"
            except Exception as error:
                last_error = error
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                logger.warning(
                    "Neo4j connection failed; retrying in %.1f seconds (%s)",
                    min(1.0, remaining),
                    error.__class__.__name__,
                )
                time.sleep(min(1.0, remaining))

        if config.storage.allow_fallback:
            logger.error(
                "Neo4j unavailable after bounded retries; starting in explicit SQLite "
                "fallback mode (%s)",
                last_error.__class__.__name__,
            )
            fallback = LocalStorage(config.storage.data_dir, embedding_fn=embedding_fn)
            return fallback, "sqlite-fallback"

        raise RuntimeError("Neo4j storage is unavailable and fallback is disabled") from last_error

    if config.storage.backend == "arcadedb":
        storage = ArcadeDbStorage(
            config.storage.data_dir,
            embedding_fn=embedding_fn,
            embedding_dimension=getattr(embedding_provider, "dimension", None),
        )
        logger.info("Initialized ArcadeDB Storage")
        return storage, "arcadedb"

    return LocalStorage(config.storage.data_dir, embedding_fn=embedding_fn), "sqlite"


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: release storage resources on shutdown.

    The Neo4j driver (and other backends) hold connection pools that must be
    closed to avoid leaking connections when the server stops.
    """
    yield
    storage = getattr(app.state, "storage", None)
    if storage is not None:
        try:
            storage.close()
        except Exception as e:  # pragma: no cover - defensive: shutdown must not raise
            logger.error(f"Error closing storage on shutdown: {e}")


# Initialize App
app = FastAPI(
    title="LLM Central Memory Server",
    description="Shared memory server for multi-repo context",
    version=__version__,
    lifespan=lifespan,
)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    """Attach a correlation ID and record sanitized request timing metadata."""
    supplied_request_id = request.headers.get("X-Request-ID", "")
    request_id = supplied_request_id if 0 < len(supplied_request_id) <= 128 else uuid.uuid4().hex
    started_at = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "Unhandled request error request_id=%s method=%s path=%s",
            request_id,
            request.method,
            request.url.path,
        )
        response = JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "request_id": request_id},
        )

    duration_ms = (time.perf_counter() - started_at) * 1000
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request_complete request_id=%s method=%s path=%s status=%s duration_ms=%.2f",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_options["allow_origins"],
    allow_credentials=cors_options["allow_credentials"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Storage
embedding_provider, embedding_runtime_status = get_server_embedding_runtime(config)
embedding_fn = embedding_provider.embed if embedding_provider is not None else None
storage, effective_storage_backend = initialize_storage(config, embedding_fn, embedding_provider)

# Save storage to app state for access in routers
app.state.storage = storage
app.state.storage_backend = effective_storage_backend
app.state.embedding_provider = embedding_provider
app.state.embedding_runtime_status = embedding_runtime_status

# Include Routers
app.include_router(memories.router)
app.include_router(intents.router)
app.include_router(ai.router)
app.include_router(quality.router)
app.include_router(platform.router)
app.include_router(repositories.router)
app.include_router(teams.router)
app.include_router(relationships.router)
app.include_router(sessions.router)
app.include_router(diagnostics.router)


def _get_scoped_stats(repo_id: str | None, user: UserContext) -> dict:
    """Calculate status statistics without crossing tenant boundaries."""
    if user.is_admin:
        return app.state.storage.get_stats(repo_id=repo_id)

    memories = [
        memory
        for memory in app.state.storage.list_memories(repo_id=repo_id)
        if can_access_scoped_record(app.state.storage, memory, user, scope_field="metadata")
    ]
    intents = [
        intent
        for intent in app.state.storage.list_intents(repo_id=repo_id)
        if can_access_scoped_record(app.state.storage, intent, user, scope_field="context")
    ]
    memories_by_layer: dict[str, int] = {}
    for memory in memories:
        layer = memory.get("layer", "unknown")
        memories_by_layer[layer] = memories_by_layer.get(layer, 0) + 1
    return {
        "total_memories": len(memories),
        "total_intents": len(intents),
        "memories_by_layer": memories_by_layer,
    }


@app.get("/", tags=["system"])
async def root(
    repo_id: str = None,
    user: UserContext = Depends(get_current_user),
):
    """Return authenticated, tenant-scoped system status and statistics."""
    target_repo_id = repo_id or config.repo_id
    require_repo_scope_access(app.state.storage, target_repo_id, user)
    try:
        stats = _get_scoped_stats(target_repo_id, user)
    except Exception as e:
        logger.error(f"Failed to get stats: {e}")
        stats = {}

    response = {
        "status": "online",
        "version": __version__,
        "timestamp": utc_now().isoformat(),
        "stats": stats,
    }
    runtime = get_runtime_status(config, embedding_provider, embedding_runtime_status)
    runtime["storage_backend"] = app.state.storage_backend
    response.update(runtime)
    response.update(stats)
    return response


@app.get("/healthz", tags=["system"])
async def healthz():
    """Unauthenticated liveness probe."""
    return {"status": "ok"}


@app.get("/readyz", tags=["system"])
async def readyz():
    """Unauthenticated readiness probe for release and first-run checks."""
    storage_ready = True
    try:
        app.state.storage.get_stats()
    except Exception as e:
        storage_ready = False
        logger.error("Storage readiness check failed: %s", e)

    dashboard_static_available = STATIC_DIR.exists() and STATIC_DIR.is_dir()
    ready = storage_ready and dashboard_static_available
    payload = {
        "status": "ready" if ready else "not_ready",
        "checks": {
            "storage": "ok" if storage_ready else "failed",
            "dashboard": "ok" if dashboard_static_available else "failed",
        },
    }

    return JSONResponse(status_code=200 if ready else 503, content=payload)


def _filter_graph_recall_result(result, user: UserContext):
    """Apply scoped-memory visibility after graph recall traversal."""
    visible_ids = set()
    filtered_nodes = []
    for node in result["nodes"]:
        memory = app.state.storage.get_memory(node["id"])
        if memory and can_access_scoped_record(
            app.state.storage, memory, user, scope_field="metadata"
        ):
            visible_ids.add(node["id"])
            filtered_nodes.append(node)

    result["nodes"] = filtered_nodes
    result["edges"] = [
        edge
        for edge in result["edges"]
        if edge["source_id"] in visible_ids and edge["target_id"] in visible_ids
    ]
    return result


def _require_graph_repo_access(repo_id: str | None, user: UserContext) -> str | None:
    graph_repo_id = repo_id or config.repo_id
    require_repo_scope_access(app.state.storage, graph_repo_id, user)
    return graph_repo_id


@app.post("/graph-recall/trace", response_model=GraphRecallResponse, tags=["graph-recall"])
async def graph_recall_trace(
    payload: GraphTraceRequest, user: UserContext = Depends(get_current_user)
):
    """Return an agent-optimized evidence-backed recall subgraph for a query."""
    graph_repo_id = _require_graph_repo_access(payload.repo_id, user)
    result = GraphRecall(app.state.storage).trace(
        query=payload.query,
        repo_id=graph_repo_id,
        depth=payload.depth,
        token_budget=payload.token_budget,
        limit=payload.limit,
        relationship_filter=payload.relationship_filter,
    )
    return _filter_graph_recall_result(result, user)


@app.post("/graph-recall/neighbors", response_model=GraphRecallResponse, tags=["graph-recall"])
async def graph_recall_neighbors(
    payload: GraphNeighborsRequest, user: UserContext = Depends(get_current_user)
):
    """Return a compact relationship neighborhood for a memory."""
    graph_repo_id = _require_graph_repo_access(payload.repo_id, user)
    result = GraphRecall(app.state.storage).neighbors(
        memory_id=payload.memory_id,
        relationship_filter=payload.relationship_filter,
        repo_id=graph_repo_id,
        depth=payload.depth,
        token_budget=payload.token_budget,
        limit=payload.limit,
    )
    return _filter_graph_recall_result(result, user)


@app.post("/graph-recall/path", response_model=GraphRecallResponse, tags=["graph-recall"])
async def graph_recall_path(
    payload: GraphPathRequest, user: UserContext = Depends(get_current_user)
):
    """Return the shortest evidence-backed relationship path between memories."""
    graph_repo_id = _require_graph_repo_access(payload.repo_id, user)
    result = GraphRecall(app.state.storage).path(
        source_id=payload.source_id,
        target_id=payload.target_id,
        repo_id=graph_repo_id,
        max_hops=payload.max_hops,
        token_budget=payload.token_budget,
    )
    return _filter_graph_recall_result(result, user)


@app.post("/graph-recall/why-relevant", response_model=GraphRecallResponse, tags=["graph-recall"])
async def graph_recall_why_relevant(
    payload: GraphWhyRelevantRequest, user: UserContext = Depends(get_current_user)
):
    """Explain why a memory is relevant to a query through relationship evidence."""
    graph_repo_id = _require_graph_repo_access(payload.repo_id, user)
    result = GraphRecall(app.state.storage).why_relevant(
        query=payload.query,
        memory_id=payload.memory_id,
        repo_id=graph_repo_id,
        depth=payload.depth,
        token_budget=payload.token_budget,
        limit=payload.limit,
    )
    return _filter_graph_recall_result(result, user)


@app.get(
    "/reports/memory-intelligence",
    response_model=MemoryIntelligenceReportResponse,
    tags=["reports"],
)
async def memory_intelligence_report(
    repo_id: str = None,
    limit: int = 10,
    user: UserContext = Depends(get_current_user),
):
    """Return deterministic memory intelligence report JSON."""
    report_repo_id = _require_graph_repo_access(repo_id, user)
    return MemoryIntelligenceReporter(app.state.storage).generate(
        repo_id=report_repo_id,
        limit=limit,
    )


@app.get("/reports/memory-intelligence/text", tags=["reports"])
async def memory_intelligence_report_text(
    repo_id: str = None,
    limit: int = 10,
    user: UserContext = Depends(get_current_user),
):
    """Return deterministic memory intelligence report text."""
    report_repo_id = _require_graph_repo_access(repo_id, user)
    report = MemoryIntelligenceReporter(app.state.storage).generate(
        repo_id=report_repo_id,
        limit=limit,
    )
    return Response(
        MemoryIntelligenceReporter.format_text(report),
        media_type="text/plain",
    )


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


@app.get("/auth", include_in_schema=False)
async def redirect_authentication_page():
    """Keep the focused dashboard authentication screen reachable without its base path."""
    return RedirectResponse(url="/dashboard/auth")


@app.get("/settings", include_in_schema=False)
async def redirect_settings_page():
    """Redirect the common bare settings URL to the bundled dashboard route."""
    return RedirectResponse(url="/dashboard/settings")


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
