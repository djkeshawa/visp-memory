# Visp Memory - Architecture

This document provides detailed information about the system architecture and core components.

## Overview

Visp Memory implements a three-layer memory system mimicking human cognition:
- **Episodic Layer**: Event-based memories (what happened)
- **Semantic Layer**: Extracted knowledge and patterns (what we know)
- **Intent Layer**: Goals and current direction (where we're going)

---

## Core Memory System (`src/visp_memory/core/`)

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
- `auto` (default) - selects the first workable option: a configured cloud key
  (OpenRouter/OpenAI), then a local `sentence-transformers` install, then a reachable
  Ollama daemon, then keyword search
- `sentence-transformers` - Local embeddings (requires the `local-embeddings` extra)
- `openai` / `openrouter` - Cloud API embeddings
- `ollama` - Local Ollama server
- `noop` / `none` - Disable vector search explicitly

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

## Memory Layers (`src/visp_memory/layers/`)

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
- `record_outcome()` - Append provenance-bearing external outcome history without
  changing workflow status

Intent status is externally owned. Completion, close, reopen, and `done`
compatibility surfaces record non-authoritative history only; they do not create
an intent-status transition.

**Example:**
```python
memory.intent.set_goal("Refactor authentication system", priority=1)
memory.intent.working_on("Implementing JWT tokens")
memory.intent.add_constraint("Must maintain backwards compatibility")
```

---

## Interfaces (`src/visp_memory/interfaces/`)

### CLI (`cli.py`)

Typer-based command-line interface with Rich formatting.

**Command Mapping:**
- Each CLI command maps directly to Memory API methods
- Rich formatting for tables and colored output
- Support for JSON output via `--json` flag

**Examples:**
```bash
visp-memory record "Fixed bug X"
visp-memory decision "Use PostgreSQL" "Need ACID guarantees"
visp-memory recall "authentication"
visp-memory context
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
1. `visp-memory.yaml` in current directory
2. `.visp-memory/config.yaml`
3. Environment variables with `VISP_MEMORY_` prefix
4. Default values

### StorageConfig

Data directory, backend selection, Neo4j credentials.

**Options:**
```yaml
storage:
  backend: neo4j  # or local, remote
  data_dir: ~/.visp-memory
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
1. Per-command: `visp-memory record "..." --repo client-xyz`
2. Persistent: `visp-memory init --repo client-xyz` (stored in config)
3. Environment: `export VISP_MEMORY_REPO_ID=client-xyz`

When `repo_id` is set in config, it's automatically used for all operations unless overridden.

---

## Server/API (`src/visp_memory/server/`)

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
- `GET /repos` - List repositories
- `POST /repos` - Register repository
- `GET /repos/{id}/context` - Cross-repo context
- `POST /teams` - Create team
- `GET /teams/{id}` - Get team
- `POST /teams/users` - Create user

**Schemas:**
Defined in `schemas.py` using Pydantic models.

---

## Authentication (`server/auth.py`)

Secure access control for the API.

**Supported Methods:**
- **JWT Tokens**: Bearer token authentication via `Authorization` header
- **API Keys**: Legacy support via `X-API-Key` header

**Configuration:**
```yaml
server:
  auth_enabled: true
  jwt_secret: your_secret_key
  jwt_algorithm: HS256
  jwt_expiry_hours: 24
  api_keys:
    - your_api_key_1
    - your_api_key_2
```

**User Context:**
Every authenticated request has a `UserContext` containing:
- `user_id`: Unique identifier
- `username`: Display name
- `team_id`: Optional team affiliation

---

## Repository Management (`core/repository.py`)

Repositories are first-class entities enabling project isolation and dependency tracking.

**Core Models:**
- `Repository`: Project metadata (name, URL, tech stack, team)
- `RepositoryDependency`: Relationship between repos
- `DependencyType`: Enum for dependency kinds (depends_on, imports, extends)

**RepositoryManager:**
- `register()`: Add a new repository
- `get()`: Retrieve repository details
- `list()`: List all repositories (optionally by team)
- `add_dependency()`: Create repo-to-repo relationships
- `get_dependencies()`: Get direct dependencies

**Storage:**
All storage backends support:
- `store_repository()`, `get_repository()`, `list_repositories()`
- `add_repo_dependency()`, `get_repo_dependencies()`

---

## Team Collaboration (`core/team.py`)

User identity and team management for multi-user deployments.

**Core Models:**
- `User`: Identity with username, email, display name
- `Team`: Group of users with shared access

**TeamManager:**
- `create_user()`, `get_user()`
- `create_team()`, `get_team()`
- `add_member()`: Add user to team
- `get_user_teams()`: Get teams for a user

**Storage:**
All storage backends support:
- `store_user()`, `get_user()`
- `store_team()`, `get_team()`
- `add_team_member()`, `get_user_teams()`

---

## Cross-Repo Context (`core/cross_repo.py`)

Aggregates knowledge across related repositories.

**CrossRepoContext:**
- `get_context_for_repo()`: Returns warnings, breaking changes, and knowledge from a repo and its dependencies.
- `search_across_repos()`: Unified search across multiple repos.

**Use Case:**
When working on Service A that depends on Library B, automatically surface warnings and breaking changes from Library B.

---

## Web Dashboard (`visp-memory-dashboard/`)

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

### Retrieval-Induced Reinforcement ("use it or lose it")

Mirroring how human memory strengthens what is actually retrieved, a memory that is
*used* (not merely surfaced) is reinforced: its `access_count` increments and its
`accessed_at` refreshes. Reinforcement is triggered by the positive recall-utility
events (`used`, `task_linked`, `outcome_linked`) in `storage.log_recall_event`.
Two effects follow:

- **Slower decay** — decay measures idle time from `accessed_at`, so a used memory's
  decay clock resets (`t ← 0`), and its `access_count` stretches the decay half-life
  via a saturating function (`ranking.effective_halflife_days`; spaced-repetition dynamics
  inspired by MemoryBank), so frequently-used memories fade far more slowly.
- **Easier recall** — `access_count` feeds a bounded base-level *activation* factor in
  ranking (`ranking.activation_rank_adjustment`, capped at `ACTIVATION_RANKING_LIMIT`),
  so frequently-useful memories surface a little higher. Mere surfacing or dismissal
  does **not** reinforce, which avoids popularity bias from exposure.

### Search & Recall

- Uses vector embeddings for semantic search
- Canonical relevance blends similarity, lexical overlap, importance, and recency
  (`ranking.score_memory_result`), with small bounded adjustments for utility feedback,
  intent-aware context factors, and base-level activation (frequency of use)
- All adjustments are strictly bounded so they tune ties without ever overriding direct
  query relevance
- Cross-layer search supported via `Memory.recall()`

### Token Efficiency

The whole value proposition is replacing file reads with compact memory, so that
saving is made measurable (`core/tokens.py`, `Memory.token_efficiency()`, the
`visp-memory tokens` CLI):

- **Consolidation savings** — when compression collapses many episodic memories into
  one semantic memory, the token delta is recorded on the resulting memory's metadata
  (`token_savings`). This is an auditable, source-grounded figure.
- **Context compactness** — the injected project context versus the full active store.
- The MCP tool surface is itself token-aware: `VISP_MEMORY_MCP_PROFILE=core` advertises
  only the everyday tools, roughly halving per-session tool-schema overhead.

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

---

## Selection, Eligibility, Trust, and Anchoring

Four modules govern what actually reaches an assistant's prompt. They run in this order,
and each can independently decide the answer is "nothing".

### `core/injection.py`

The precision gate. Recall is user-initiated and can afford marginal hits; injection is
unsolicited and steers the model, so it applies a stricter bar: a budget (4 memories,
1,200 characters), a relevance floor, a redundancy filter, a complexity gate, and
abstain-by-default. Every decision is recorded on `InjectionResult` so the policy is
measurable rather than asserted. See
[INJECTION_POLICY.md](INJECTION_POLICY.md) for the evidence and threshold calibration.

### `core/eligibility.py`

The shared fail-closed read boundary requires a repository ID, applies inclusive
`valid_from` and exclusive `valid_to` bounds, and matches normalized `environment` and
`task_type` scopes. It runs before ranking, prompt budgeting, trust, relationship
traversal, contradiction rendering, and guarded answer generation. Compression uses the
same canonical scope values and refuses mixed or malformed source groups.

### `core/trust.py`

Provenance tiers (`authored`, `derived`, `assisted`, `external`, `unknown`) with
per-tier trust decay and a read-only write-channel policy. Provenance is assigned by the
package entrypoint rather than accepted from payload tags: CLI is authored; MCP,
conversation capture, compression, and reflection are assisted; repository/test/Kit
capture is derived; HTTP, import, and instruction ingestion are external; direct
library, missing, and malformed cases are unknown. External and unknown memories are
quarantined from prompt-adjacent output. One structured filter is shared by context,
targeted relevance, SessionStart/task briefs, proactive recall, graph traversal, and the
related-memory and guarded-answer APIs. It records rejection counts and reasons at the
prompt boundary; task briefs filter before budgeting, and graph edges touching rejected
nodes are removed before traversal. Trust falls with age so stale entries stop outranking
newer information; explicit primary recall may return quarantined data only after
eligibility succeeds. See [../TRUST.md](../TRUST.md).

### `core/anchors.py`

Ties memories to the files they describe, extracted from bracketed warnings, capture
summaries, and bare source filenames. Anchored memories are selected first and bypass
the relevance floor; memories whose anchored code has been deleted are withheld.
Staleness is self-calibrating — a "missing" verdict is only acted on when some other
anchor in the pool resolves, so running from the wrong directory does not read as "the
codebase was deleted".

### `capture/bootstrap.py`

First-run seeding. Mines existing git history selectively: reverts and repeatedly-fixed
code files become warnings, commits that explain their reasoning become decisions,
mechanical commits are skipped and counted. Instruction files are ingested through
`capture/instructions.py`. Safe to re-run — content is hashed by the capture manifest.

---

## New in Phase 3

### Files Added
- `core/repository.py` - Repository models and manager
- `core/team.py` - User and team models
- `core/cross_repo.py` - Cross-repository context aggregation
- `server/auth.py` - JWT and API key authentication
- `server/routers/repositories.py` - Repository API
- `server/routers/teams.py` - Team and user API

### Storage Extensions
All storage backends now support:
- Repository CRUD and dependency tracking
- User and team management
- Team membership relationships

### Configuration Additions
```yaml
# visp-memory.yaml
server:
  auth_enabled: true
  jwt_secret: your_secret
  api_keys:
    - key1
    - key2

storage:
  jwt_token: your_jwt_token  # For client mode
```
