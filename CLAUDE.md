# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

LLM Memory is a human-inspired memory system for LLMs that provides persistent context across sessions. It implements three memory layers mimicking human cognition:
- **Episodic Layer**: Event-based memories (what happened)
- **Semantic Layer**: Extracted knowledge and patterns (what we know)
- **Intent Layer**: Goals and current direction (where we're going)

The system offers multiple interfaces: CLI tool, MCP server, FastAPI backend, and Next.js dashboard.

## Development Commands

### Setup & Installation
```bash
# Install development dependencies
pip install -e ".[dev]"

# Install all optional dependencies (recommended for development)
pip install -e ".[all]"

# Install Neo4j storage backend
# Requires Neo4j running at bolt://localhost:7687
```

### Testing
```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_memory.py

# Run with verbose output
pytest -v

# Run with asyncio support (for async tests)
pytest --asyncio-mode=auto
```

### Linting & Code Quality
```bash
# Run ruff linter
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
python3 -m uvicorn llm_memory.server.app:app --port 8000

# Start dashboard (from llm-memory-dashboard directory)
cd llm-memory-dashboard
npm install
npm run dev
```

## Architecture

### Core Memory System (`src/llm_memory/core/`)

**Memory (`memory.py`)**: The unified interface that coordinates all memory layers. This is the main entry point users interact with. It handles:
- Automatic configuration discovery via `MemoryConfig.find_and_load()`
- Storage backend selection (LocalStorage, RemoteStorage, or Neo4jStorage)
- Routing operations to appropriate layers
- Context generation for LLM injection
- Memory compression and maintenance

**Storage Abstraction**: Three storage implementations all inherit from `BaseStorage`:
- `LocalStorage` (`storage.py`): SQLite-based local storage
- `Neo4jStorage` (`neo4j_storage.py`): Graph database with vector embeddings (default backend)
- `RemoteStorage` (`remote_storage.py`): Client for API server mode

**Embeddings (`embeddings.py`)**: Handles vector embeddings via sentence-transformers, OpenAI, or Ollama. Used for semantic search across all memory layers.

**Compression (`compression.py`)**: Compresses old episodic memories into semantic knowledge, optionally using LLMs for intelligent synthesis.

### Memory Layers (`src/llm_memory/layers/`)

Each layer inherits from `BaseMemoryLayer` and provides domain-specific methods:

**EpisodicMemory (`episodic.py`)**:
- Stores time-bound events with categories (decisions, bugs, discoveries)
- Helper methods: `record()`, `decision()`, `bug()`, `discovery()`
- Events can be compressed into semantic knowledge over time

**SemanticMemory (`semantic.py`)**:
- Stores timeless knowledge extracted from experiences
- Categories: invariants, patterns, warnings, conventions, known issues
- Helper methods: `establish()`, `warn()`, `convention()`, `known_issue()`
- Used for proactive context injection when working on specific areas

**IntentMemory (`intent.py`)**:
- Tracks current goals, focus areas, and constraints
- Special intent types: `FOCUS:`, `CONSTRAINT:`, `WORKING ON:`
- Highest priority memories - always included in context
- Helper methods: `set_goal()`, `set_focus()`, `working_on()`, `add_constraint()`

### Interfaces (`src/llm_memory/interfaces/`)

**CLI (`cli.py`)**: Typer-based command-line interface with Rich formatting. Each command maps to Memory API methods.

**MCP Server (`mcp.py`)**: Model Context Protocol server exposing memory tools to AI assistants. Tools include: `memory_recall`, `memory_record`, `memory_decision`, `memory_warn`, `memory_goal`, `memory_file_context`.

### Configuration (`config.py`)

Configuration uses Pydantic settings with environment variable support:
- `MemoryConfig`: Top-level config with auto-discovery via `find_and_load()`
- `StorageConfig`: Data directory, backend selection, Neo4j credentials
- `EmbeddingConfig`: Embedding provider and model settings
- `CompressionConfig`: Memory compression parameters
- `CaptureConfig`: Automatic capture settings (git hooks, test results)

Config discovery order:
1. `llm-memory.yaml` in current directory
2. `.llm-memory/config.yaml`
3. Environment variables with `LLM_MEMORY_` prefix
4. Default values

### Repository Scoping

**Default Behavior**: All memories are shared across workspace by default (no isolation). This enables cross-project learning and knowledge connections.

**Optional Isolation**: Use `repo_id` parameter or `--repo` flag to isolate memories for specific projects. This should be rare - only for completely unrelated work.

**Configuration**:
- Per-command: `--repo client-xyz`
- Persistent: `llm-memory init --repo client-xyz` (stored in config)
- Environment: `export LLM_MEMORY_REPO_ID=client-xyz`

When `repo_id` is set in config, it's automatically used for all operations unless overridden.

### Web Dashboard (`llm-memory-dashboard/`)

Next.js app with TypeScript:
- Graph visualization of memory relationships
- Intent management UI
- Memory statistics and filtering
- Connects to FastAPI backend at `http://localhost:8000`

### Server/API (`src/llm_memory/server/`)

FastAPI server (`app.py`) exposing REST endpoints:
- `/memories`: List/search memories
- `/memories/{id}`: Get specific memory
- `/intents`: Manage goals and focus
- `/stats`: Memory statistics
- Schemas defined in `schemas.py`

## Key Patterns & Conventions

### Memory IDs
All memories have 16-character SHA256-based IDs generated from `content + timestamp`. This ensures uniqueness while being deterministic for the same content at the same time.

### Importance Scoring
- Range: 0.0 to 1.0
- Decisions default to 0.7
- Warnings default to 0.7-0.9
- Regular events default to 0.5
- Used for ranking and memory decay

### Memory Decay
Over time, unused memories decrease in importance via exponential decay (configurable halflife). This prevents context pollution with outdated information.

### Compression Flow
1. Episodic memories accumulate over time
2. Compression process detects patterns (similar events, repeated issues)
3. Multiple episodes compressed into single semantic memory
4. Source episode IDs tracked via `source_ids` relationship
5. Can use LLM-based compression for intelligent synthesis

### Search & Recall
- Uses vector embeddings for semantic search
- Combines similarity scores with importance ratings
- Results sorted by relevance: `similarity * 0.6 + importance * 0.4`
- Cross-layer search supported via `Memory.recall()`

### Context Generation
`Memory.context()` generates markdown-formatted context for LLM injection:
1. Current intent (focus, task, constraints) - always shown first
2. Semantic knowledge (warnings, conventions, known issues)
3. Recent episodic history (last 5-10 events)
4. Metadata and statistics

This format is optimized for system prompts or context windows.

## Testing Practices

- Fixtures use `tempfile.TemporaryDirectory()` for isolated test storage
- Each test creates fresh `MemoryConfig` with temporary `data_dir`
- Use `pytest.fixture` for shared `memory` instances
- Test files follow pattern: `test_<module>.py`
- Async tests supported via `pytest-asyncio`

### Manual CLI Testing
Test the system with real commands:
```bash
# Initialize and add memories
llm-memory init --type code
llm-memory decision "Architecture choice" "Reasoning here"
llm-memory warn "file.py" "Watch out for race conditions"
llm-memory record "Fixed bug X"

# Search and retrieve
llm-memory recall "bug fix"
llm-memory inject --file "src/module.py"
llm-memory find-error "AttributeError"

# View state
llm-memory status
llm-memory list --limit 10
llm-memory context
```

## Storage Backend Migration

The codebase is currently migrating from SQLite to Neo4j as the default backend (now fully functional):
- Neo4j provides graph relationships and vector search
- Legacy LocalStorage (SQLite) still supported
- All storage backends implement `BaseStorage` interface
- Neo4j connection configured via environment variables or config

When working with storage:
- Always use the `BaseStorage` abstraction
- Don't assume specific backend features
- Test against multiple backends when possible
- Neo4j requires vector index support (Neo4j 5.15+)

### Backend-Specific Behaviors
- **Deduplication**: Full batch dedup only works with LocalStorage (ChromaDB). Neo4j dedup requires content parameter.
- **DateTime Handling**: Neo4j returns timezone-aware datetimes. Always use `datetime.now(timezone.utc)` for comparisons.
- **Field Initialization**: Ensure all required fields (`accessed_at`, `created_at`, etc.) are initialized in storage operations.

## Dashboard Development

The Next.js dashboard is in `llm-memory-dashboard/`:
- Uses TypeScript, React, and Tailwind CSS
- shadcn/ui components in `components/ui/`
- API integration via fetch to FastAPI backend
- Graph visualization using D3.js or similar (check `components/`)

When modifying dashboard:
```bash
cd llm-memory-dashboard
npm install
npm run dev  # Development server
npm run build  # Production build
npm run lint  # ESLint
```

## Important Notes

### Neo4j Configuration
Default Neo4j password is hardcoded in `config.py` (line 44). This should be changed for production use or configured via environment variables:
```bash
export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=your_password
```

### Vector Embeddings
The system uses sentence-transformers by default for vector embeddings. Neo4jStorage and LocalStorage both accept an `embedding_fn` parameter which is automatically initialized from the embedding config. If embedding generation fails, the system falls back to property-based text filtering.

### Memory Persistence
- CLI operations persist immediately to storage backend
- No in-memory caching by default - each operation hits storage
- For batch operations, consider using API mode with remote server

### MCP Server Usage
When Claude Desktop or other MCP clients use the server:
- Tools execute in isolated contexts (new Memory instance per call)
- Configuration auto-discovered each time
- Heavy operations (compression, large recalls) may be slow

### Proactive Recall
The system supports proactive context injection:
- `memory.relevant_for(task="...", files=["..."])` returns targeted context
- File-triggered recall surfaces warnings and patterns for specific files
- Used by MCP `memory_file_context` tool
