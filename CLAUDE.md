# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

---

## Project Overview

**LLM Memory** is a human-inspired memory system for LLMs that provides persistent context across sessions. It implements three memory layers:
- **Episodic** - Event-based memories (what happened)
- **Semantic** - Extracted knowledge and patterns (what we know)
- **Intent** - Goals and current direction (where we're going)

The system offers multiple interfaces: CLI tool, MCP server, FastAPI backend, and Next.js dashboard.

---

## Quick Commands

### Setup & Installation
```bash
# Install development dependencies
pip install -e ".[dev]"

# Install all optional dependencies
pip install -e ".[all]"
```

### Testing
```bash
# Run all tests
pytest

# Run with verbose output and coverage
pytest -v --cov=llm_memory

# Run specific test file
pytest tests/test_memory.py
```

### Linting & Formatting
```bash
# Check code
ruff check .

# Auto-fix issues
ruff check --fix .

# Format code
ruff format .
```

### Running the System
```bash
# Initialize in a project
llm-memory init --type code

# CLI commands
llm-memory record "Event description"
llm-memory decision "What was decided" "Why"
llm-memory recall "search query"
llm-memory context

# Start API server
uvicorn llm_memory.server.app:app --port 8000

# Start dashboard (from llm-memory-dashboard directory)
cd llm-memory-dashboard && npm install && npm run dev
```

---

## Architecture Overview

### Core Components

**Memory System** (`src/llm_memory/core/`):
- `memory.py` - Unified interface coordinating all layers
- `storage.py` / `neo4j_storage.py` / `remote_storage.py` - Storage backends
- `embeddings.py` - Vector embeddings for semantic search
- `compression.py` - Compress episodics into semantic knowledge

**Memory Layers** (`src/llm_memory/layers/`):
- `episodic.py` - Time-bound events with categories
- `semantic.py` - Timeless knowledge and patterns
- `intent.py` - Current goals and constraints

**Interfaces** (`src/llm_memory/interfaces/`):
- `cli.py` - Command-line interface (Typer + Rich)
- `mcp.py` - Model Context Protocol server

**Server** (`src/llm_memory/server/`):
- `app.py` - FastAPI REST API
- `static/` - Embedded Next.js dashboard

**Dashboard** (`llm-memory-dashboard/`):
- Next.js app with TypeScript, Tailwind CSS, shadcn/ui
- Graph visualization, intent management, memory stats

---

## Key Patterns & Conventions

### Memory IDs
All memories have 16-character SHA256-based IDs generated from `content + timestamp`. Ensures uniqueness while being deterministic.

### Importance Scoring
- Range: 0.0 to 1.0
- Decisions: 0.7, Warnings: 0.7-0.9, Regular events: 0.5
- Used for ranking and memory decay

### Repository Scoping
**Default:** All memories shared across workspace (enables cross-project learning).

**Optional Isolation:** Use `repo_id` parameter or `--repo` flag for completely separate contexts:
```bash
llm-memory init --repo client-xyz
llm-memory record "..." --repo project-name
```

### Search & Recall
- Vector embeddings for semantic search
- Combines similarity (60%) + importance (40%)
- Cross-layer search via `Memory.recall()`

### Context Generation
`Memory.context()` generates markdown for LLM injection:
1. Current intent (focus, task, constraints) - highest priority
2. Semantic knowledge (warnings, conventions, known issues)
3. Recent episodic history
4. Metadata and statistics

---

## Storage Backends

### Neo4j (Default)
- Graph database with vector embeddings
- Requires Neo4j 5.15+ with vector index support
- Best for production, multi-repo, teams

**Configuration:**
```bash
export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=your_password
```

### LocalStorage (Legacy)
- SQLite + ChromaDB
- No external dependencies
- Best for local single-user development

### RemoteStorage
- HTTP client for FastAPI server
- Best for shared team memory

**See:** [Storage Backends Documentation](docs/development/STORAGE.md)

---

## Important Notes

### Neo4j Configuration
Default Neo4j password is hardcoded in `config.py:44`. **Change for production:**
```bash
export NEO4J_PASSWORD=secure_password
```

### Vector Embeddings
- Uses sentence-transformers by default
- Neo4jStorage and LocalStorage accept `embedding_fn` parameter
- Auto-initialized from embedding config
- Graceful fallback to text filtering if embeddings fail

### Memory Persistence
- CLI operations persist immediately to storage
- No in-memory caching by default
- Each operation hits storage backend
- For batch operations, consider API mode with remote server

### MCP Server
- Tools execute in isolated contexts (new Memory instance per call)
- Configuration auto-discovered each time
- Heavy operations (compression, large recalls) may be slow

### Storage Backend Behaviors
- **Deduplication**: Full batch dedup only works with LocalStorage (ChromaDB). Neo4j requires content parameter.
- **DateTime**: Neo4j returns timezone-aware datetimes. Always use `datetime.now(timezone.utc)`.
- **Field Initialization**: Ensure `accessed_at`, `created_at` are initialized in storage operations.

---

## Development Resources

### Detailed Documentation
- [Architecture Details](docs/development/ARCHITECTURE.md) - Core system, layers, interfaces
- [Testing Guide](docs/development/TESTING.md) - Testing practices, fixtures, manual testing
- [Storage Backends](docs/development/STORAGE.md) - Backend comparison, migration, configuration

### Deployment Documentation
- [Packaging & Distribution](docs/deployment/PACKAGING.md) - Build wheels, Docker, standalone executables
- [Release Process](docs/deployment/RELEASING.md) - How to create releases
- [Workflow Troubleshooting](docs/deployment/WORKFLOW-TROUBLESHOOTING.md) - GitHub Actions issues

### Project Planning
- [Roadmap](docs/ROADMAP.md) - Future phases and vision

---

## Testing Practices

### Test Isolation
- Use `tempfile.TemporaryDirectory()` for isolated storage
- Each test creates fresh `MemoryConfig` with temporary `data_dir`
- No shared state between tests

### Running Tests
```bash
# All tests
pytest

# Specific backend
export LLM_MEMORY_STORAGE_BACKEND=neo4j
pytest tests/test_neo4j_storage.py

# With coverage
pytest --cov=llm_memory --cov-report=html
```

### Manual CLI Testing
```bash
llm-memory init --type code
llm-memory decision "Use PostgreSQL" "Need ACID"
llm-memory warn "file.py" "Watch for race conditions"
llm-memory recall "bug fix"
llm-memory inject --file "src/module.py"
llm-memory status
```

**See:** [Testing Guide](docs/development/TESTING.md) for comprehensive testing documentation.

---

## Dashboard Development

The Next.js dashboard is in `llm-memory-dashboard/`:

```bash
cd llm-memory-dashboard
npm install
npm run dev      # Development server
npm run export   # Static export for embedding
npm run build    # Production build
npm run lint     # ESLint
```

**Tech Stack:** Next.js, TypeScript, Tailwind CSS, shadcn/ui, D3.js

**Integration:** API calls to FastAPI backend at `http://localhost:8000`

---

## Common Issues

### "python: command not found"
Use `python3` instead of `python` in scripts and commands.

### Vector embeddings not working
Ensure `embedding_fn` is initialized before passing to storage backends. Check `config.embedding` (not `config.embeddings`).

### Compression/decay failures
- Use timezone-aware datetimes: `datetime.now(timezone.utc)`
- Ensure `accessed_at` field exists on all memories

### Dedup errors with Neo4j
Neo4j dedup requires `--content` parameter. For full batch dedup, use LocalStorage backend.

---

## File References

When referencing code, use the pattern `file_path:line_number`:
- `compression.py:260` - DateTime comparison
- `neo4j_storage.py:134` - Field initialization
- `config.py:44` - Default Neo4j password

---

## Development Workflow

1. **Read files first** - Never propose changes without reading the file
2. **Use TodoWrite** - Track progress on complex tasks
3. **Test thoroughly** - Run pytest before committing
4. **Lint code** - Run ruff check and format
5. **Avoid over-engineering** - Only make necessary changes
6. **Document decisions** - Use commit messages or memory system

---

## Additional Context

- Configuration discovery: `llm-memory.yaml` → `.llm-memory/config.yaml` → env vars → defaults
- Proactive recall: `memory.relevant_for(task="...", files=["..."])` returns targeted context
- File-triggered recall: Surfaces warnings/patterns when files are opened
- Compression flow: Episodics → patterns → semantic memories (tracks `source_ids`)
