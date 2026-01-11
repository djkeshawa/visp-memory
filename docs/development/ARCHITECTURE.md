# LLM Memory - Architecture

This document provides detailed information about the system architecture and core components.

## Overview

LLM Memory implements a three-layer memory system mimicking human cognition:
- **Episodic Layer**: Event-based memories (what happened)
- **Semantic Layer**: Extracted knowledge and patterns (what we know)
- **Intent Layer**: Goals and current direction (where we're going)

---

## Core Memory System (`src/llm_memory/core/`)

### Memory (`memory.py`)

The unified interface that coordinates all memory layers. This is the main entry point users interact with.

**Responsibilities:**
- Automatic configuration discovery via `MemoryConfig.find_and_load()`
- Storage backend selection (LocalStorage, RemoteStorage, or Neo4jStorage)
- Routing operations to appropriate layers
- Context generation for LLM injection
- Memory compression and maintenance

**Key Methods:**
- `record()` - Store episodic events
- `decision()` - Record architectural decisions
- `recall()` - Search across all layers
- `context()` - Generate markdown context for LLM
- `compress()` - Compress old episodics into semantic knowledge

---

### Storage Abstraction

Three storage implementations all inherit from `BaseStorage`:

#### LocalStorage (`storage.py`)
- SQLite-based local storage
- ChromaDB for vector embeddings
- Best for: Single-user, local development

#### Neo4jStorage (`neo4j_storage.py`)
- Graph database with vector embeddings
- **Default backend**
- Requires Neo4j 5.15+ with vector index support
- Best for: Production, multi-repo, team collaboration

#### RemoteStorage (`remote_storage.py`)
- Client for API server mode
- Connects to FastAPI backend
- Best for: Shared team server, remote access

**Interface:**
All storage backends implement:
- `store_memory()`
- `get_memory()`
- `search_memories()`
- `delete_memory()`
- `update_memory()`
- `get_all_memories()`

---

### Embeddings (`embeddings.py`)

Handles vector embeddings for semantic search.

**Supported Providers:**
- `sentence-transformers` (default) - Local embeddings
- `openai` - OpenAI API embeddings
- `ollama` - Local Ollama server

**Usage:**
Vector embeddings are used for semantic search across all memory layers. If embedding generation fails, the system gracefully falls back to property-based text filtering.

---

### Compression (`compression.py`)

Compresses old episodic memories into semantic knowledge.

**How it Works:**
1. Identify old episodic memories (configurable threshold)
2. Detect patterns (similar events, repeated issues)
3. Group related episodes
4. Compress into semantic memories
5. Track source episodes via `source_ids` relationship

**Compression Strategies:**
- **Simple**: Text concatenation and deduplication
- **LLM-based**: Intelligent synthesis using language models

**Configuration:**
```yaml
compression:
  enabled: true
  min_age_days: 30
  similarity_threshold: 0.8
  use_llm: true
```

---

## Memory Layers (`src/llm_memory/layers/`)

Each layer inherits from `BaseMemoryLayer` and provides domain-specific methods.

### EpisodicMemory (`episodic.py`)

Stores time-bound events with categories.

**Categories:**
- `decision` - Architectural decisions
- `bug` - Bug fixes
- `discovery` - New findings
- `event` - General events

**Helper Methods:**
- `record()` - Store general event
- `decision()` - Record decision with rationale
- `bug()` - Log bug fix
- `discovery()` - Capture new learning

**Lifecycle:**
Events can be compressed into semantic knowledge over time based on compression settings.

---

### SemanticMemory (`semantic.py`)

Stores timeless knowledge extracted from experiences.

**Categories:**
- `invariant` - Fundamental truths
- `pattern` - Recurring patterns
- `warning` - Cautions and gotchas
- `convention` - Code conventions
- `known_issue` - Known problems

**Helper Methods:**
- `establish()` - Store fundamental truth
- `warn()` - Flag fragile areas
- `convention()` - Document coding standards
- `known_issue()` - Track known problems

**Usage:**
Used for proactive context injection when working on specific areas. For example, warnings about a file are surfaced when that file is opened.

---

### IntentMemory (`intent.py`)

Tracks current goals, focus areas, and constraints.

**Special Intent Types:**
- `FOCUS:` - Current area of attention
- `CONSTRAINT:` - Limitations or requirements
- `WORKING ON:` - Active tasks
- `GOAL:` - High-level objectives

**Priority:**
Intent memories have highest priority and are always included in context generation.

**Helper Methods:**
- `set_goal()` - Define objective
- `set_focus()` - Set current focus
- `working_on()` - Mark active task
- `add_constraint()` - Add limitation

**Example:**
```python
memory.intent.set_goal("Refactor authentication system", priority=1)
memory.intent.working_on("Implementing JWT tokens")
memory.intent.add_constraint("Must maintain backwards compatibility")
```

---

## Interfaces (`src/llm_memory/interfaces/`)

### CLI (`cli.py`)

Typer-based command-line interface with Rich formatting.

**Command Mapping:**
- Each CLI command maps directly to Memory API methods
- Rich formatting for tables and colored output
- Support for JSON output via `--json` flag

**Examples:**
```bash
llm-memory record "Fixed bug X"
llm-memory decision "Use PostgreSQL" "Need ACID guarantees"
llm-memory recall "authentication"
llm-memory context
```

---

### MCP Server (`mcp.py`)

Model Context Protocol server exposing memory tools to AI assistants.

**Available Tools:**
- `memory_recall` - Search past events and knowledge
- `memory_record` - Save new findings or events
- `memory_decision` - Document architectural choices
- `memory_warn` - Flag fragile code areas
- `memory_goal` - Manage project intent
- `memory_file_context` - Get proactive context for specific files

**Integration:**
Used by Claude Desktop and other MCP-compatible clients to access project memory directly.

---

## Configuration (`config.py`)

Configuration uses Pydantic settings with environment variable support.

### MemoryConfig

Top-level configuration with auto-discovery via `find_and_load()`

**Discovery Order:**
1. `llm-memory.yaml` in current directory
2. `.llm-memory/config.yaml`
3. Environment variables with `LLM_MEMORY_` prefix
4. Default values

### StorageConfig

Data directory, backend selection, Neo4j credentials.

**Options:**
```yaml
storage:
  backend: neo4j  # or local, remote
  data_dir: ~/.llm-memory
  neo4j_uri: bolt://localhost:7687
  neo4j_user: neo4j
  neo4j_password: your_password
```

### EmbeddingConfig

Embedding provider and model settings.

**Options:**
```yaml
embedding:
  provider: sentence-transformers  # or openai, ollama
  model: all-MiniLM-L6-v2
  # For OpenAI:
  # provider: openai
  # model: text-embedding-ada-002
  # api_key: your_key
```

### CompressionConfig

Memory compression parameters.

```yaml
compression:
  enabled: true
  min_age_days: 30
  similarity_threshold: 0.8
  use_llm: false
```

### CaptureConfig

Automatic capture settings (future feature).

```yaml
capture:
  git_hooks: true
  test_results: true
  llm_conversations: false
```

---

## Repository Scoping

### Default Behavior

**All memories are shared across workspace by default** (no isolation). This enables cross-project learning and knowledge connections.

### Optional Isolation

Use `repo_id` parameter or `--repo` flag to isolate memories for specific projects. This should be rare - only for completely unrelated work.

**Configuration Methods:**
1. Per-command: `llm-memory record "..." --repo client-xyz`
2. Persistent: `llm-memory init --repo client-xyz` (stored in config)
3. Environment: `export LLM_MEMORY_REPO_ID=client-xyz`

When `repo_id` is set in config, it's automatically used for all operations unless overridden.

---

## Server/API (`src/llm_memory/server/`)

### FastAPI Server (`app.py`)

REST API exposing memory operations.

**Endpoints:**
- `GET /` - Root stats
- `GET /memories` - List/search memories
- `GET /memories/{id}` - Get specific memory
- `POST /memories` - Create memory
- `GET /intents` - Manage goals and focus
- `GET /stats` - Memory statistics
- `GET /dashboard` - Web dashboard (static files)

**Schemas:**
Defined in `schemas.py` using Pydantic models.

---

## Web Dashboard (`llm-memory-dashboard/`)

Next.js app with TypeScript providing web interface for memory management.

**Features:**
- Graph visualization of memory relationships
- Intent management UI
- Memory search and filtering
- Statistics and analytics

**Tech Stack:**
- Next.js 14+ (App Router)
- TypeScript
- Tailwind CSS
- shadcn/ui components
- D3.js for graph visualization

**Connection:**
Connects to FastAPI backend at `http://localhost:8000`

---

## Key Design Patterns

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

### Proactive Recall

The system supports proactive context injection:
- `memory.relevant_for(task="...", files=["..."])` returns targeted context
- File-triggered recall surfaces warnings and patterns for specific files
- Used by MCP `memory_file_context` tool

---

## Data Flow

```
User Input
    ↓
CLI / MCP / API
    ↓
Memory (core/memory.py)
    ↓
Layer Selection (episodic/semantic/intent)
    ↓
Storage Backend (neo4j/local/remote)
    ↓
Database (Neo4j / SQLite+ChromaDB)
```

**Retrieval Flow:**
```
Query
    ↓
Memory.recall()
    ↓
Embedding Generation
    ↓
Cross-layer Search
    ↓
Importance Weighting
    ↓
Ranked Results
```

---

## Extension Points

### Custom Storage Backends

Implement `BaseStorage` interface:
```python
class CustomStorage(BaseStorage):
    def store_memory(self, layer, memory_data):
        # Your implementation
        pass

    def search_memories(self, layer, query, limit=10):
        # Your implementation
        pass
```

### Custom Embedding Providers

Implement embedding function:
```python
def custom_embeddings(texts: List[str]) -> List[List[float]]:
    # Return embeddings for texts
    return embeddings
```

### Custom Compression Strategies

Extend `MemoryCompressor`:
```python
class CustomCompressor(MemoryCompressor):
    def compress_pattern(self, episodes):
        # Your compression logic
        return semantic_memory
```

---

## Performance Considerations

### Memory Operations
- CLI operations persist immediately to storage (no in-memory caching)
- Each operation hits storage backend
- For batch operations, consider using API mode with remote server

### MCP Server
- Tools execute in isolated contexts (new Memory instance per call)
- Configuration auto-discovered each time
- Heavy operations (compression, large recalls) may be slow

### Scalability
- Neo4j backend scales to millions of memories
- Vector search performance depends on index configuration
- Consider batching for bulk operations

---

## Security Notes

### Neo4j Configuration
Default Neo4j password is hardcoded in `config.py` for development. **Must be changed for production** via environment variables:
```bash
export NEO4J_PASSWORD=secure_password
```

### API Authentication
Current FastAPI server has no authentication. **Add authentication for production deployment.**

### Data Privacy
- All memories stored locally by default
- Remote mode shares data with server
- Consider encryption for sensitive data
