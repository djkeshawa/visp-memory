# LLM Memory - Storage Backends

This document covers storage backend implementation, configuration, and migration details.

---

## Overview

LLM Memory supports multiple storage backends through a unified `BaseStorage` interface:

- **LocalStorage** - SQLite + ChromaDB/local vector storage (default, single-user)
- **ArcadeDbStorage** - ArcadeDB Embedded local graph storage (optional, experimental)
- **Neo4jStorage** - Graph database with vector search (recommended for teams)
- **RemoteStorage** - Client for remote API server

All backends implement the same interface, allowing transparent switching.

---

## Storage Backend Comparison

| Feature | SQLite `sqlite` | ArcadeDB `arcadedb` | Neo4j `neo4j` | Remote client |
|---------|-----------------|----------------------|---------------|---------------|
| **Storage** | SQLite + ChromaDB | ArcadeDB Embedded graph files | Neo4j Graph DB | HTTP API Client |
| **Vector Search** | ChromaDB/text fallback | ChromaDB/text fallback for v1 | Neo4j Vector Index | Server-side |
| **Relationships** | Structured SQLite rows | Stable graph vertices/edges | Native Graphs | Server-side |
| **Multi-user** | No | No | Yes | Yes |
| **Requires Server** | No | No | Yes (Neo4j) | Yes (FastAPI) |
| **Install** | `llm-memory[api,mcp]` | `llm-memory[arcadedb,api,mcp]` | `llm-memory[neo4j,api,mcp]` | `llm-memory[mcp]` |
| **Best For** | Smallest local install | Local embedded graph storage | Mature shared/team graph deployment | Remote access |
| **Status** | **Default** | Experimental v1 | Production target | Experimental |

Backend selection uses:

```bash
export LLM_MEMORY_STORAGE_BACKEND=sqlite    # default
export LLM_MEMORY_STORAGE_BACKEND=arcadedb  # embedded local graph backend
export LLM_MEMORY_STORAGE_BACKEND=neo4j     # external graph backend
```

KuzuDB is intentionally not used because the upstream repository is
archived/read-only. ArcadeDB Embedded Python is the selected v1 local graph
candidate because it runs in-process, installs as an optional package extra, and
does not require Docker or a separate server.

---

## ArcadeDbStorage (Optional Local Graph)

### Overview

ArcadeDB Embedded stores structured records as local graph data while preserving
the same public storage shapes used by SQLite, Neo4j, the API, MCP tools,
dashboard, reports, import, and export.

**Advantages:**
- Runs in-process with `pip install "llm-memory[arcadedb,api,mcp]"`
- No Docker container or external database service
- Stores graph records under `<data_dir>/arcadedb`
- Uses stable vertex and edge types with relationship kind as a property
- Keeps v1 vector behavior conservative through the existing Chroma/text
  fallback path

**Limitations:**
- Experimental v1 backend
- Not the recommended shared/team backend
- Native ArcadeDB vector indexes are deferred until the structured graph backend
  is stable

### Installation

```bash
pip install "llm-memory[arcadedb,api,mcp]"
```

Lean installs that do not need ArcadeDB should use:

```bash
pip install "llm-memory[api,mcp]"
```

### Configuration

```bash
export LLM_MEMORY_STORAGE_BACKEND=arcadedb
export LLM_MEMORY_STORAGE_DATA_DIR=~/.llm-memory
llm-memory init --type code
```

Server example:

```bash
LLM_MEMORY_STORAGE_BACKEND=arcadedb llm-memory serve
```

Config file:

```yaml
storage:
  backend: arcadedb
  data_dir: ~/.llm-memory
```

### Schema Model

ArcadeDB uses stable graph types:

- Vertex types: `Memory`, `Intent`, `Session`, `Repository`, `User`, `Team`,
  `AuditLog`, `RecallFeedback`
- Edge types: `MemoryRelationship`, `RepoDependency`, `TeamMember`

Relationship kinds such as `supports`, `derived_from`, or `related` are stored
as edge properties. They are not interpolated into dynamic edge type names.

---

## Neo4jStorage (Team/Production Target)

### Overview

Graph-based storage with native relationship support and vector embeddings.
The code defaults to SQLite for zero-dependency local use; choose Neo4j when
you need shared/team memory, stronger graph traversal, or larger deployments.

**Advantages:**
- Native graph relationships between memories
- Efficient traversal for connected knowledge
- Vector similarity search built-in
- Scales to millions of memories
- Multi-user support
- Rich query capabilities (Cypher)

**Requirements:**
- Neo4j 5.15+ (for vector index support)
- Bolt protocol enabled
- Vector index configured

---

### Configuration

#### Environment Variables

```bash
export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=your_secure_password
```

#### Config File

```yaml
# llm-memory.yaml
storage:
  backend: neo4j
  neo4j_uri: bolt://localhost:7687
  neo4j_user: neo4j
  neo4j_password: ${NEO4J_PASSWORD}  # Use env var for security
```

#### Python Code

```python
from llm_memory.core.config import MemoryConfig, StorageConfig

config = MemoryConfig(
    storage=StorageConfig(
        backend="neo4j",
        neo4j_uri="bolt://localhost:7687",
        neo4j_user="neo4j",
        neo4j_password="your_password"
    )
)

memory = Memory(config)
```

---

### Installation

#### Using Docker

```bash
docker run -d \
  --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/your_password \
  -e NEO4J_PLUGINS='["apoc"]' \
  -v $HOME/neo4j/data:/data \
  neo4j:5.15
```

#### Using Neo4j Desktop

1. Download from https://neo4j.com/download/
2. Create new database
3. Set password
4. Start database
5. Note the Bolt URL (usually `bolt://localhost:7687`)

#### Using Neo4j Aura (Cloud)

1. Sign up at https://neo4j.com/cloud/aura/
2. Create database
3. Copy connection URI and credentials
4. Configure LLM Memory with cloud URI

---

### Vector Index Setup

Neo4jStorage requires a vector index for semantic search:

```cypher
// Create vector index (automatic on first use)
CREATE VECTOR INDEX memory_embeddings IF NOT EXISTS
FOR (m:Memory)
ON (m.embedding)
OPTIONS {
  indexConfig: {
    `vector.dimensions`: 384,
    `vector.similarity_function`: 'cosine'
  }
}
```

**Note:** Index is created automatically by Neo4jStorage on initialization if it doesn't exist.

---

### Schema

#### Nodes

```cypher
// Memory node
(:Memory {
  id: string,              // 16-char SHA256 hash
  layer: string,           // episodic, semantic, intent
  content: string,
  importance: float,
  category: string,
  embedding: float[],      // Vector embedding
  created_at: datetime,
  accessed_at: datetime,
  metadata: json,
  repo_id: string          // Optional repository scope
})
```

#### Relationships

```cypher
// Memory relationships
(m1:Memory)-[:RELATED_TO {strength: float}]->(m2:Memory)
(m1:Memory)-[:COMPRESSED_FROM]->(m2:Memory)
(m1:Memory)-[:REFERS_TO]->(m2:Memory)
```

---

### Cypher Examples

#### Find Related Memories

```cypher
MATCH (m:Memory {id: $memory_id})-[r:RELATED_TO]->(related:Memory)
RETURN related, r.strength
ORDER BY r.strength DESC
LIMIT 10
```

#### Find Compression Sources

```cypher
MATCH (semantic:Memory {layer: 'semantic'})-[:COMPRESSED_FROM]->(episodic:Memory)
WHERE semantic.id = $memory_id
RETURN episodic
```

#### Vector Search

```cypher
CALL db.index.vector.queryNodes(
  'memory_embeddings',
  10,
  $query_embedding
) YIELD node, score
RETURN node, score
```

---

### Backend-Specific Behaviors

#### DateTime Handling

**Issue:** Neo4j returns timezone-aware datetimes with `+00:00` suffix.

**Solution:** Always use timezone-aware datetimes:
```python
from datetime import datetime, timezone

# Correct
dt = datetime.now(timezone.utc)

# Incorrect (will fail comparison)
dt = datetime.now()
```

#### Field Initialization

**Issue:** Missing `accessed_at` field on newly created memories.

**Solution:** Neo4jStorage now initializes all required fields:
```cypher
MERGE (m:Memory {id: $id})
SET m.accessed_at = coalesce(m.accessed_at, datetime())
```

#### Deduplication Limitations

**Issue:** Full batch deduplication requires ChromaDB's `_get_collection()` method.

**Current Behavior:**
- Content-based dedup works (requires content parameter)
- Full batch dedup not supported (different data model)

**Workaround:**
```bash
# Dedup with specific content
llm-memory dedup --content "duplicate text"

# Or use SQLite for full batch dedup
export LLM_MEMORY_STORAGE_BACKEND=sqlite
llm-memory dedup
```

---

## SQLite LocalStorage (Default)

### Overview

SQLite-based storage with ChromaDB for vector embeddings.

**Advantages:**
- No external dependencies
- Simple setup
- Fast for small datasets
- File-based storage

**Limitations:**
- Single-user only
- Limited relationship support
- Not designed for large datasets
- No native graph queries

---

### Configuration

```yaml
storage:
  backend: sqlite
  data_dir: ~/.llm-memory
```

### File Structure

```
~/.llm-memory/
├── memories.db          # SQLite database
├── chromadb/            # Vector embeddings
│   └── chroma.sqlite3
└── config.yaml
```

### Schema

```sql
-- SQLite schema
CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    layer TEXT NOT NULL,
    content TEXT NOT NULL,
    importance REAL,
    category TEXT,
    created_at TEXT,
    accessed_at TEXT,
    metadata TEXT,  -- JSON
    repo_id TEXT
);

CREATE INDEX idx_layer ON memories(layer);
CREATE INDEX idx_created_at ON memories(created_at);
CREATE INDEX idx_repo_id ON memories(repo_id);
```

---

## RemoteStorage

### Overview

HTTP client for remote LLM Memory server.

**Use Cases:**
- Shared team memory
- Remote access
- Centralized deployment

---

### Configuration

```yaml
storage:
  mode: client
  server_url: http://localhost:8000
  api_key: your_api_key       # Optional - API key auth
  jwt_token: your_jwt_token   # Optional - JWT auth (takes precedence)
```

**Environment Variables:**
```bash
export LLM_MEMORY_API_KEY=your_api_key
export LLM_MEMORY_JWT_TOKEN=your_jwt_token
```

### Server Setup

```bash
# Start server
llm-memory serve --port 8000

# Or with uvicorn
uvicorn llm_memory.server.app:app --port 8000
```

### Client Usage

```python
from llm_memory.core.config import MemoryConfig, StorageConfig

config = MemoryConfig(
    storage=StorageConfig(
        mode="client",
        server_url="http://localhost:8000",
        jwt_token="your_jwt_token"  # or api_key="your_key"
    )
)

memory = Memory(config)
# All operations now go through HTTP API
```

---

## Migration Guide

No automatic backend migration runs in v1. Use the existing export/import flow
so users explicitly choose the source and target backend.

### SQLite to ArcadeDB

```bash
LLM_MEMORY_STORAGE_BACKEND=sqlite llm-memory export memory.json
LLM_MEMORY_STORAGE_BACKEND=arcadedb llm-memory import memory.json
```

### SQLite or ArcadeDB to Neo4j

Start Neo4j and set `NEO4J_URI`, `NEO4J_USER`, and `NEO4J_PASSWORD`, then:

```bash
LLM_MEMORY_STORAGE_BACKEND=sqlite llm-memory export memory.json
LLM_MEMORY_STORAGE_BACKEND=neo4j llm-memory import memory.json
```

To migrate from ArcadeDB to SQLite, reverse the backend values:

```bash
LLM_MEMORY_STORAGE_BACKEND=arcadedb llm-memory export memory.json
LLM_MEMORY_STORAGE_BACKEND=sqlite llm-memory import memory.json
```

---

## Storage Abstraction

### BaseStorage Interface

All storage backends must implement:

```python
from abc import ABC, abstractmethod

class BaseStorage(ABC):
    @abstractmethod
    def store_memory(self, layer: str, memory_data: dict) -> str:
        """Store a memory and return its ID."""
        pass

    @abstractmethod
    def get_memory(self, layer: str, memory_id: str) -> dict:
        """Retrieve a memory by ID."""
        pass

    @abstractmethod
    def search_memories(
        self,
        layer: str,
        query: str = None,
        embedding: list = None,
        filters: dict = None,
        limit: int = 10
    ) -> list:
        """Search memories with optional filters."""
        pass

    @abstractmethod
    def delete_memory(self, layer: str, memory_id: str) -> bool:
        """Delete a memory."""
        pass

    @abstractmethod
    def update_memory(self, layer: str, memory_id: str, updates: dict) -> bool:
        """Update memory fields."""
        pass

    @abstractmethod
    def get_all_memories(self, layer: str = None, repo_id: str = None) -> dict:
        """Get all memories, optionally filtered by layer/repo."""
        pass

    # Repository Operations (Phase 3.2)
    @abstractmethod
    def store_repository(self, repo: dict) -> str:
        """Store a repository."""
        pass

    @abstractmethod
    def get_repository(self, repo_id: str) -> dict:
        """Get repository by ID."""
        pass

    @abstractmethod
    def list_repositories(self, team_id: str = None) -> list:
        """List repositories, optionally filtered by team."""
        pass

    @abstractmethod
    def add_repo_dependency(self, source_id: str, target_id: str, dep_type: str) -> str:
        """Add dependency between repositories."""
        pass

    @abstractmethod
    def get_repo_dependencies(self, repo_id: str) -> list:
        """Get dependencies for a repository."""
        pass

    # Team Operations (Phase 3.3)
    @abstractmethod
    def store_user(self, user: dict) -> str:
        """Store a user."""
        pass

    @abstractmethod
    def get_user(self, user_id: str) -> dict:
        """Get user by ID."""
        pass

    @abstractmethod
    def store_team(self, team: dict) -> str:
        """Store a team."""
        pass

    @abstractmethod
    def get_team(self, team_id: str) -> dict:
        """Get team by ID."""
        pass

    @abstractmethod
    def add_team_member(self, team_id: str, user_id: str) -> bool:
        """Add user to team."""
        pass

    @abstractmethod
    def get_user_teams(self, user_id: str) -> list:
        """Get teams for a user."""
        pass

---

## Custom Storage Backend

### Implementation

```python
from llm_memory.core.storage import BaseStorage

class CustomStorage(BaseStorage):
    def __init__(self, config):
        self.config = config
        # Initialize your storage

    def store_memory(self, layer, memory_data):
        # Store implementation
        memory_id = generate_id(memory_data)
        # ... your storage logic
        return memory_id

    def search_memories(self, layer, query=None, embedding=None, **kwargs):
        # Search implementation
        results = []
        # ... your search logic
        return results

    # Implement other required methods...
```

### Registration

```python
from llm_memory.core.config import MemoryConfig
from llm_memory.core.memory import Memory

# Use custom storage
config = MemoryConfig(storage=StorageConfig(backend="custom"))
memory = Memory(config)
memory.storage = CustomStorage(config.storage)
```

---

## Performance Tuning

### Benchmark Before Tuning

Run the benchmark utility before and after storage changes so tuning claims have
repeatable numbers:

```bash
python scripts/benchmark_memory.py --items 1000
python scripts/benchmark_memory.py --items 1000 --json
```

For ArcadeDB:

```bash
pip install "llm-memory[arcadedb,api,mcp]"
python scripts/benchmark_memory.py --backend arcadedb --items 100 --json
```

For Neo4j:

```bash
export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=your_secure_password
python scripts/benchmark_memory.py --backend neo4j --items 1000
```

### Neo4j Optimization

#### Memory Settings

```conf
# neo4j.conf
dbms.memory.heap.initial_size=2g
dbms.memory.heap.max_size=4g
dbms.memory.pagecache.size=2g
```

#### Index Configuration

```cypher
// Create indexes for better query performance
CREATE INDEX memory_layer IF NOT EXISTS FOR (m:Memory) ON (m.layer);
CREATE INDEX memory_repo IF NOT EXISTS FOR (m:Memory) ON (m.repo_id);
CREATE INDEX memory_created IF NOT EXISTS FOR (m:Memory) ON (m.created_at);
```

### ChromaDB Optimization

```python
# Increase batch size for bulk operations
collection.add(
    ids=ids,
    documents=docs,
    embeddings=embeddings,
    metadatas=metadata
)
```

---

## Backup and Recovery

### Neo4j Backup

```bash
# Dump database
neo4j-admin database dump neo4j --to-path=/backups

# Restore
neo4j-admin database load neo4j --from-path=/backups
```

### LocalStorage Backup

```bash
# Backup SQLite and ChromaDB
tar -czf llm-memory-backup.tar.gz ~/.llm-memory
```

---

## Troubleshooting

### Neo4j Connection Issues

**Problem:** `Failed to connect to Neo4j`

**Solutions:**
```bash
# Check Neo4j is running
docker ps | grep neo4j

# Test connection
cypher-shell -u neo4j -p password

# Check firewall
telnet localhost 7687
```

### Vector Index Missing

**Problem:** `Vector index not found`

**Solution:**
```cypher
// Check indexes
SHOW INDEXES;

// Recreate if missing
CREATE VECTOR INDEX memory_embeddings IF NOT EXISTS
FOR (m:Memory) ON (m.embedding)
OPTIONS {indexConfig: {`vector.dimensions`: 384}};
```

### Slow Queries

**Problem:** Searches taking too long

**Solutions:**
- Check indexes exist
- Increase Neo4j memory allocation
- Reduce result limit
- Use filters to narrow search scope

---

## Security Considerations

### Neo4j Security

```yaml
# Use environment variables for credentials
storage:
  neo4j_password: ${NEO4J_PASSWORD}  # Not hardcoded

# Enable auth
NEO4J_AUTH=neo4j/secure_password

# Use encrypted connections (production)
NEO4J_URI=neo4j+s://hostname:7687
```

### Data Encryption

- Neo4j: Enable encryption at rest
- LocalStorage: Use encrypted filesystem
- RemoteStorage: Use HTTPS with TLS

---

## Future Enhancements

- [ ] Postgres backend with pgvector
- [ ] MongoDB backend
- [ ] Redis backend for caching layer
- [ ] Automatic migration tools
- [ ] Backup/restore utilities
- [ ] Sharding support for large datasets
- [ ] Read replicas for scaling
