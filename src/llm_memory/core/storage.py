"""
Storage layer for LLM Memory.

Combines:
- SQLite for structured data (memories, metadata, relationships)
- ChromaDB for vector embeddings (semantic search)
"""

import sqlite3
import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Literal
from contextlib import contextmanager
from abc import ABC, abstractmethod

try:
    import chromadb
    from chromadb.config import Settings
    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False


MemoryLayer = Literal["raw", "episodic", "semantic", "intent"]


class BaseStorage(ABC):
    """Abstract interface for memory storage."""
    
    @abstractmethod
    def store_memory(self, content: str, layer: MemoryLayer = "episodic", repo_id: str = None, **kwargs) -> str:
        """Store a memory."""
        pass

    @abstractmethod
    def get_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Get a memory by ID."""
        pass
        
    @abstractmethod
    def search_memories(self, query: str, repo_id: str = None, **kwargs) -> List[Dict[str, Any]]:
        """Search across memories."""
        pass

    @abstractmethod
    def list_memories(self, repo_id: str = None, **kwargs) -> List[Dict[str, Any]]:
        """List memories."""
        pass

    @abstractmethod
    def update_memory(self, memory_id: str, **kwargs) -> bool:
        """Update a memory."""
        pass

    @abstractmethod
    def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory."""
        pass
    
    @abstractmethod
    def get_collection(self, layer: str):
        """Get underlying vector collection (if applicable)."""
        pass

    # Intent Operations
    @abstractmethod
    def set_intent(self, description: str, priority: int = 0, context: Dict[str, Any] = None) -> str:
        """Set a new intent."""
        pass

    @abstractmethod
    def get_active_intents(self) -> List[Dict[str, Any]]:
        """Get active intents."""
        pass
    
    @abstractmethod
    def complete_intent(self, intent_id: str) -> bool:
        """Complete an intent."""
        pass
        
    # Relationship Operations
    @abstractmethod
    def add_relationship(self, source_id: str, target_id: str, relationship: str, strength: float = 1.0) -> str:
        """Add a relationship."""
        pass
        
    @abstractmethod
    def get_related_memories(self, memory_id: str, relationship: str = None) -> List[Dict[str, Any]]:
        """Get related memories."""
        pass

    # Session Operations
    @abstractmethod
    def start_session(self) -> str:
        """Start a session."""
        pass

    @abstractmethod
    def end_session(self, session_id: str, summary: str, memory_ids: List[str]):
        """End a session."""
        pass

    # Stats
    @abstractmethod
    def get_stats(self) -> Dict[str, Any]:
        """Get statistics."""
        pass


class LocalStorage(BaseStorage):
    """Unified storage for structured data and vector embeddings (Local SQLite + Chroma)."""

    def __init__(self, data_dir: Path, embedding_fn=None):
        """
        Initialize storage.

        Args:
            data_dir: Directory for all data files
            embedding_fn: Optional function to generate embeddings.
                         If None, ChromaDB's default will be used.
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = self.data_dir / "memories.db"
        self.chroma_path = self.data_dir / "vectors"

        self._embedding_fn = embedding_fn
        self._chroma_client = None
        self._collections = {}

        self._init_sqlite()

    def _init_sqlite(self):
        """Initialize SQLite schema."""
        with self._get_db() as conn:
            # Main memories table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    layer TEXT NOT NULL DEFAULT 'episodic',
                    category TEXT DEFAULT 'general',
                    importance REAL DEFAULT 0.5,
                    repo_id TEXT DEFAULT NULL,
                    access_count INTEGER DEFAULT 0,
                    tags TEXT DEFAULT '[]',
                    metadata TEXT DEFAULT '{}',
                    source_ids TEXT DEFAULT '[]',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    compressed_at TIMESTAMP DEFAULT NULL
                )
            """)
            
            # Migration: Check if repo_id column exists
            try:
                conn.execute("SELECT repo_id FROM memories LIMIT 1")
            except sqlite3.OperationalError:
                # Column doesn't exist, add it
                conn.execute("ALTER TABLE memories ADD COLUMN repo_id TEXT DEFAULT NULL")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_repo ON memories(repo_id)")

            # Intent tracking (current direction/goals)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS intents (
                    id TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    priority INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'active',
                    context TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Knowledge graph (relationships between memories)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS relationships (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    relationship TEXT NOT NULL,
                    strength REAL DEFAULT 1.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (source_id) REFERENCES memories(id),
                    FOREIGN KEY (target_id) REFERENCES memories(id)
                )
            """)

            # Session tracking (for compression)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    summary TEXT,
                    memory_ids TEXT DEFAULT '[]',
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ended_at TIMESTAMP DEFAULT NULL
                )
            """)

            # Indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_layer ON memories(layer)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_repo ON memories(repo_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_intents_status ON intents(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_relationships_source ON relationships(source_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_relationships_target ON relationships(target_id)")

            conn.commit()

    @contextmanager
    def _get_db(self):
        """Get SQLite connection context."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _get_chroma(self):
        """Get or create ChromaDB client."""
        if not CHROMADB_AVAILABLE:
            return None

        if self._chroma_client is None:
            self.chroma_path.mkdir(parents=True, exist_ok=True)
            self._chroma_client = chromadb.PersistentClient(
                path=str(self.chroma_path),
                settings=Settings(
                    anonymized_telemetry=False,
                    allow_reset=True
                )
            )

        return self._chroma_client

    def get_collection(self, layer: str):
        """Public accessor for vector collection."""
        return self._get_collection(layer)

    def _get_collection(self, layer: MemoryLayer):
        """Get or create ChromaDB collection for a layer."""
        client = self._get_chroma()
        if client is None:
            return None

        if layer not in self._collections:
            try:
                self._collections[layer] = client.get_collection(f"memories_{layer}")
            except Exception:
                self._collections[layer] = client.create_collection(
                    name=f"memories_{layer}",
                    metadata={"description": f"Memory embeddings for {layer} layer"}
                )

        return self._collections[layer]

    @staticmethod
    def _generate_id(content: str) -> str:
        """Generate unique ID for content."""
        timestamp = datetime.now().isoformat()
        return hashlib.sha256(f"{content}{timestamp}".encode()).hexdigest()[:16]

    @staticmethod
    def _json_serialize(data: Any) -> str:
        """Serialize data to JSON."""
        return json.dumps(data)

    @staticmethod
    def _json_deserialize(data: str) -> Any:
        """Deserialize data from JSON."""
        if not data:
            return None
        try:
            return json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return data

    def store_memory(
        self,
        content: str,
        layer: MemoryLayer = "episodic",
        repo_id: str = None,
        category: str = "general",
        importance: float = 0.5,
        tags: List[str] = None,
        metadata: Dict[str, Any] = None,
        source_ids: List[str] = None,
        embedding: List[float] = None
    ) -> str:
        """
        Store a memory in both SQLite and vector DB.

        Args:
            content: The memory content
            layer: Memory layer (raw, episodic, semantic, intent)
            repo_id: Repository identifier for context
            category: Category for organization
            importance: Importance score (0.0 to 1.0)
            tags: List of tags
            metadata: Additional metadata
            source_ids: IDs of source memories (for compression tracking)
            embedding: Pre-computed embedding (optional)

        Returns:
            Memory ID
        """
        memory_id = self._generate_id(content)
        tags = tags or []
        metadata = metadata or {}
        source_ids = source_ids or []

        # Store in SQLite
        with self._get_db() as conn:
            conn.execute("""
                INSERT INTO memories (id, content, layer, repo_id, category, importance, tags, metadata, source_ids)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                memory_id,
                content,
                layer,
                repo_id,
                category,
                importance,
                self._json_serialize(tags),
                self._json_serialize(metadata),
                self._json_serialize(source_ids)
            ))
            conn.commit()

        # Store in vector DB
        collection = self._get_collection(layer)
        if collection is not None:
            metadata_dict = {
                "category": category,
                "importance": importance,
                "tags": self._json_serialize(tags)
            }
            if repo_id:
                metadata_dict["repo_id"] = repo_id
                
            add_kwargs = {
                "ids": [memory_id],
                "documents": [content],
                "metadatas": [metadata_dict]
            }

            if embedding is not None:
                add_kwargs["embeddings"] = [embedding]

            collection.add(**add_kwargs)

        return memory_id

    def get_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Get a memory by ID."""
        with self._get_db() as conn:
            cursor = conn.execute(
                "SELECT * FROM memories WHERE id = ?",
                (memory_id,)
            )
            row = cursor.fetchone()

            if row is None:
                return None

            # Update access tracking
            conn.execute("""
                UPDATE memories
                SET access_count = access_count + 1, accessed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (memory_id,))
            conn.commit()

            return self._row_to_dict(row)

    def search_memories(
        self,
        query: str,
        layer: MemoryLayer = None,
        repo_id: str = None,
        category: str = None,
        limit: int = 10,
        min_importance: float = 0.0
    ) -> List[Dict[str, Any]]:
        """
        Semantic search across memories.

        Args:
            query: Search query (natural language)
            layer: Filter by layer
            repo_id: Filter by repository context
            category: Filter by category
            limit: Maximum results
            min_importance: Minimum importance threshold

        Returns:
            List of matching memories with similarity scores
        """
        results = []

        # Search each relevant layer's vector collection
        layers_to_search = [layer] if layer else ["episodic", "semantic", "intent"]

        for search_layer in layers_to_search:
            collection = self._get_collection(search_layer)
            if collection is None:
                continue

            # Build where clause
            where = {}
            if category:
                where["category"] = category
            if repo_id:
                where["repo_id"] = repo_id
            if min_importance > 0:
                where["importance"] = {"$gte": min_importance}

            try:
                search_results = collection.query(
                    query_texts=[query],
                    n_results=limit,
                    where=where if where else None
                )

                if search_results["ids"] and search_results["ids"][0]:
                    for i, mem_id in enumerate(search_results["ids"][0]):
                        distance = search_results["distances"][0][i] if search_results.get("distances") else 0
                        similarity = 1 - distance

                        memory = self.get_memory(mem_id)
                        if memory:
                            memory["similarity"] = similarity
                            results.append(memory)

            except Exception as e:
                # Collection might be empty
                pass

        # Sort by similarity and limit
        results.sort(key=lambda x: x.get("similarity", 0), reverse=True)
        return results[:limit]

    def list_memories(
        self,
        layer: MemoryLayer = None,
        repo_id: str = None,
        category: str = None,
        limit: int = 50,
        order_by: str = "created_at DESC"
    ) -> List[Dict[str, Any]]:
        """List memories with optional filtering."""
        query = "SELECT * FROM memories WHERE 1=1"
        params = []

        if layer:
            query += " AND layer = ?"
            params.append(layer)

        if repo_id:
            query += " AND repo_id = ?"
            params.append(repo_id)

        if category:
            query += " AND category = ?"
            params.append(category)

        query += f" ORDER BY {order_by} LIMIT ?"
        params.append(limit)

        with self._get_db() as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def update_memory(
        self,
        memory_id: str,
        content: str = None,
        importance: float = None,
        tags: List[str] = None,
        metadata: Dict[str, Any] = None
    ) -> bool:
        """Update an existing memory."""
        updates = []
        params = []

        if content is not None:
            updates.append("content = ?")
            params.append(content)

        if importance is not None:
            updates.append("importance = ?")
            params.append(importance)

        if tags is not None:
            updates.append("tags = ?")
            params.append(self._json_serialize(tags))

        if metadata is not None:
            updates.append("metadata = ?")
            params.append(self._json_serialize(metadata))

        if not updates:
            return False

        params.append(memory_id)

        with self._get_db() as conn:
            cursor = conn.execute(
                f"UPDATE memories SET {', '.join(updates)} WHERE id = ?",
                params
            )
            conn.commit()
            return cursor.rowcount > 0

    def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory from both stores."""
        # Get layer first for vector DB cleanup
        memory = self.get_memory(memory_id)
        if not memory:
            return False

        # Delete from SQLite
        with self._get_db() as conn:
            conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            conn.execute("DELETE FROM relationships WHERE source_id = ? OR target_id = ?",
                        (memory_id, memory_id))
            conn.commit()

        # Delete from vector DB
        collection = self._get_collection(memory["layer"])
        if collection:
            try:
                collection.delete(ids=[memory_id])
            except Exception:
                pass

        return True

    def set_intent(
        self,
        description: str,
        priority: int = 0,
        context: Dict[str, Any] = None
    ) -> str:
        """Set a new intent (goal/direction)."""
        intent_id = self._generate_id(description)
        context = context or {}

        with self._get_db() as conn:
            conn.execute("""
                INSERT INTO intents (id, description, priority, context)
                VALUES (?, ?, ?, ?)
            """, (intent_id, description, priority, self._json_serialize(context)))
            conn.commit()

        return intent_id

    def get_active_intents(self) -> List[Dict[str, Any]]:
        """Get all active intents, ordered by priority."""
        with self._get_db() as conn:
            cursor = conn.execute("""
                SELECT * FROM intents
                WHERE status = 'active'
                ORDER BY priority DESC, created_at DESC
            """)
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def complete_intent(self, intent_id: str) -> bool:
        """Mark an intent as completed."""
        with self._get_db() as conn:
            cursor = conn.execute("""
                UPDATE intents
                SET status = 'completed', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (intent_id,))
            conn.commit()
            return cursor.rowcount > 0

    def add_relationship(
        self,
        source_id: str,
        target_id: str,
        relationship: str,
        strength: float = 1.0
    ) -> str:
        """Add a relationship between memories."""
        rel_id = self._generate_id(f"{source_id}-{target_id}-{relationship}")

        with self._get_db() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO relationships (id, source_id, target_id, relationship, strength)
                VALUES (?, ?, ?, ?, ?)
            """, (rel_id, source_id, target_id, relationship, strength))
            conn.commit()

        return rel_id

    def get_related_memories(
        self,
        memory_id: str,
        relationship: str = None
    ) -> List[Dict[str, Any]]:
        """Get memories related to a given memory."""
        query = """
            SELECT m.*, r.relationship, r.strength
            FROM memories m
            JOIN relationships r ON (m.id = r.target_id OR m.id = r.source_id)
            WHERE (r.source_id = ? OR r.target_id = ?)
            AND m.id != ?
        """
        params = [memory_id, memory_id, memory_id]

        if relationship:
            query += " AND r.relationship = ?"
            params.append(relationship)

        with self._get_db() as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def start_session(self) -> str:
        """Start a new session for tracking."""
        session_id = self._generate_id("session")

        with self._get_db() as conn:
            conn.execute(
                "INSERT INTO sessions (id) VALUES (?)",
                (session_id,)
            )
            conn.commit()

        return session_id

    def end_session(self, session_id: str, summary: str, memory_ids: List[str]):
        """End a session with summary."""
        with self._get_db() as conn:
            conn.execute("""
                UPDATE sessions
                SET summary = ?, memory_ids = ?, ended_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (summary, self._json_serialize(memory_ids), session_id))
            conn.commit()

    def get_stats(self) -> Dict[str, Any]:
        """Get storage statistics."""
        with self._get_db() as conn:
            stats = {}

            # Memory counts by layer
            cursor = conn.execute("""
                SELECT layer, COUNT(*) as count
                FROM memories
                GROUP BY layer
            """)
            stats["memories_by_layer"] = dict(cursor.fetchall())

            # Memory counts by category
            cursor = conn.execute("""
                SELECT category, COUNT(*) as count
                FROM memories
                GROUP BY category
            """)
            stats["memories_by_category"] = dict(cursor.fetchall())

            # Total counts
            cursor = conn.execute("SELECT COUNT(*) FROM memories")
            stats["total_memories"] = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM intents WHERE status = 'active'")
            stats["active_intents"] = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM relationships")
            stats["total_relationships"] = cursor.fetchone()[0]

            return stats

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        """Convert SQLite row to dictionary."""
        d = dict(row)

        # Parse JSON fields
        for field in ["tags", "metadata", "source_ids", "memory_ids", "context"]:
            if field in d and d[field]:
                d[field] = LocalStorage._json_deserialize(d[field])
        
        return d
