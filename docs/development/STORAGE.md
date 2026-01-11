# LLM Memory - Storage Backends

This document covers storage backend implementation, configuration, and migration details.

---

## Overview

LLM Memory supports multiple storage backends through a unified `BaseStorage` interface:

- **LocalStorage** - SQLite + ChromaDB (legacy, single-user)
- **Neo4jStorage** - Graph database with vector search (default, recommended)
- **RemoteStorage** - Client for remote API server

All backends implement the same interface, allowing transparent switching.

---

## Storage Backend Comparison

| Feature | LocalStorage | Neo4jStorage | RemoteStorage |
|---------|-------------|--------------|---------------|
| **Storage** | SQLite + ChromaDB | Neo4j Graph DB | HTTP API Client |
| **Vector Search** | ChromaDB | Neo4j Vector Index | Server-side |
| **Relationships** | Limited | Native Graphs | Server-side |
| **Multi-user** | No | Yes | Yes |
| **Requires Server** | No | Yes (Neo4j) | Yes (FastAPI) |
| **Best For** | Local dev | Production, teams | Remote access |
| **Status** | Legacy | **Default** | Experimental |

---

## Neo4jStorage (Default)

### Overview

Graph-based storage with native relationship support and vector embeddings.

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

# Or use LocalStorage for full batch dedup
export LLM_MEMORY_STORAGE_BACKEND=local
llm-memory dedup
```

---

## LocalStorage (Legacy)

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
  backend: local
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
  backend: remote
  server_url: http://localhost:8000
  api_key: your_api_key  # Optional
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
        backend="remote",
        server_url="http://localhost:8000"
    )
)

memory = Memory(config)
# All operations now go through HTTP API
```

---

## Migration Guide

### From LocalStorage to Neo4j

#### 1. Export from LocalStorage

```python
from llm_memory import Memory
from llm_memory.core.config import MemoryConfig, StorageConfig

# Load from local
local_config = MemoryConfig(
    storage=StorageConfig(backend="local", data_dir="~/.llm-memory")
)
local_memory = Memory(local_config)

# Get all memories
all_memories = local_memory.storage.get_all_memories()
```

#### 2. Import to Neo4j

```python
# Connect to Neo4j
neo4j_config = MemoryConfig(
    storage=StorageConfig(
        backend="neo4j",
        neo4j_uri="bolt://localhost:7687",
        neo4j_password="password"
    )
)
neo4j_memory = Memory(neo4j_config)

# Import memories
for layer, memories in all_memories.items():
    for memory_data in memories:
        neo4j_memory.storage.store_memory(layer, memory_data)
```

#### 3. Migration Script

```bash
# Use built-in migration (future feature)
llm-memory migrate --from local --to neo4j
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
```

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
