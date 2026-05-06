"""
FastAPI Server Entry Point
"""

import logging
from datetime import datetime
from pathlib import Path

try:
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
except ImportError:
    raise ImportError("FastAPI not installed. Run: pip install llm-memory[api]")

from llm_memory.config import load_config
from llm_memory.core.neo4j_storage import Neo4jStorage
from llm_memory.core.storage import LocalStorage
from llm_memory.server.routers import intents, memories, relationships, repositories, teams

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

config = load_config()

# Initialize App
app = FastAPI(
    title="LLM Central Memory Server",
    description="Shared memory server for multi-repo context",
    version="0.2.0"
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Storage
if config.storage.backend == "neo4j":
    try:
        storage = Neo4jStorage()
        logger.info("Initialized Neo4j Storage")
    except Exception as e:
        logger.error(f"Failed to initialize Neo4j, falling back to SQLite: {e}")
        storage = LocalStorage(config.storage.data_dir)
else:
    storage = LocalStorage(config.storage.data_dir)

# Save storage to app state for access in routers
app.state.storage = storage

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
async def root():
    """System status and stats."""
    try:
        stats = app.state.storage.get_stats()
    except Exception as e:
        logger.error(f"Failed to get stats: {e}")
        stats = {}

    response = {
        "status": "online",
        "version": "0.2.0",
        "timestamp": datetime.now().isoformat(),
        "storage_backend": config.storage.backend,
        "stats": stats
    }
    response.update(stats)
    return response

# Mount static files for dashboard (if available)
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists() and STATIC_DIR.is_dir():
    # Mount static assets
    if (STATIC_DIR / "_next" / "static").exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR / "_next" / "static")), name="static")
    if (STATIC_DIR / "_next").exists():
        app.mount("/_next", StaticFiles(directory=str(STATIC_DIR / "_next")), name="next")

    # Serve dashboard at /dashboard and root
    @app.get("/dashboard/{full_path:path}")
    async def serve_dashboard_path(full_path: str):
        """Serve dashboard files."""
        file_path = STATIC_DIR / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        # Fallback to index.html for SPA routing
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/dashboard")
    async def serve_dashboard():
        """Serve dashboard index."""
        return FileResponse(STATIC_DIR / "index.html")

    logger.info(f"Dashboard mounted at /dashboard from {STATIC_DIR}")
else:
    logger.warning(f"Dashboard static files not found at {STATIC_DIR}. Dashboard will not be available.")
    logger.info("To build the dashboard, run: python build_frontend.py")
