"""
Storage layer for LLM Memory.

Combines:
- SQLite for structured data (memories, metadata, relationships)
- ChromaDB for vector embeddings (semantic search)
"""

import hashlib
import json
import sqlite3
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from llm_memory.core.ranking import normalize_distance_score, rank_memory_results, text_similarity

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
    def store_memory(
        self, content: str, layer: MemoryLayer = "episodic", repo_id: str = None, **kwargs
    ) -> str:
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
    def set_intent(
        self,
        description: str,
        priority: int = 0,
        context: Dict[str, Any] = None,
        repo_id: str = None,
    ) -> str:
        """Set a new intent."""
        pass

    @abstractmethod
    def get_active_intents(self, repo_id: str = None) -> List[Dict[str, Any]]:
        """Get active intents."""
        pass

    @abstractmethod
    def complete_intent(self, intent_id: str) -> bool:
        """Complete an intent."""
        pass

    # Relationship Operations
    @abstractmethod
    def add_relationship(
        self, source_id: str, target_id: str, relationship: str, strength: float = 1.0
    ) -> str:
        """Add a relationship."""
        pass

    @abstractmethod
    def get_related_memories(
        self, memory_id: str, relationship: str = None
    ) -> List[Dict[str, Any]]:
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

    @abstractmethod
    def get_all_relationships(self, repo_id: str = None) -> List[Dict[str, Any]]:
        """Get all relationships."""
        pass

    # Stats
    @abstractmethod
    def get_stats(self, repo_id: str = None) -> Dict[str, Any]:
        """Get statistics."""
        pass

    # Repository operations (Phase 3.2)
    @abstractmethod
    def store_repository(self, repo: Dict[str, Any]) -> str:
        """Store a repository."""
        pass

    @abstractmethod
    def get_repository(self, repo_id: str) -> Optional[Dict[str, Any]]:
        """Get repository by ID."""
        pass

    @abstractmethod
    def list_repositories(self, team_id: str = None) -> List[Dict[str, Any]]:
        """List all repositories."""
        pass

    @abstractmethod
    def add_repo_dependency(
        self, source_id: str, target_id: str, dep_type: str, version: str = None, notes: str = None
    ) -> str:
        """Add dependency between repos."""
        pass

    @abstractmethod
    def get_repo_dependencies(self, repo_id: str) -> List[Dict[str, Any]]:
        """Get repository dependencies."""
        pass

    # Team and User operations (Phase 3.3)
    @abstractmethod
    def store_user(self, user: Dict[str, Any]) -> str:
        """Store a user."""
        pass

    @abstractmethod
    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user by ID."""
        pass

    @abstractmethod
    def store_team(self, team: Dict[str, Any]) -> str:
        """Store a team."""
        pass

    @abstractmethod
    def get_team(self, team_id: str) -> Optional[Dict[str, Any]]:
        """Get team by ID."""
        pass

    @abstractmethod
    def add_team_member(self, team_id: str, user_id: str) -> bool:
        """Add member to team."""
        pass

    @abstractmethod
    def get_user_teams(self, user_id: str) -> List[Dict[str, Any]]:
        """Get all teams for a user."""
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
        embedding_owner = getattr(embedding_fn, "__self__", None)
        embedding_owner_name = embedding_owner.__class__.__name__.lower() if embedding_owner else ""
        self._uses_noop_embeddings = embedding_owner_name == "noopprovider"
        self._embedding_dimension = getattr(embedding_owner, "dimension", None)
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

            # Migration: Check if repo_id column exists in memories
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

            # Migration: Check if repo_id column exists in intents
            try:
                conn.execute("SELECT repo_id FROM intents LIMIT 1")
            except sqlite3.OperationalError:
                # Column doesn't exist, add it
                conn.execute("ALTER TABLE intents ADD COLUMN repo_id TEXT DEFAULT NULL")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_intents_repo ON intents(repo_id)")

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

            conn.execute("""
                CREATE TABLE IF NOT EXISTS repositories (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    url TEXT,
                    description TEXT,
                    tech_stack TEXT DEFAULT '[]',
                    team_id TEXT,
                    metadata TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Team management (Phase 3.3)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    email TEXT,
                    display_name TEXT,
                    metadata TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS teams (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    metadata TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS team_members (
                    team_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT DEFAULT 'member',
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (team_id, user_id),
                    FOREIGN KEY (team_id) REFERENCES teams(id),
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS repository_dependencies (
                    id TEXT PRIMARY KEY,
                    source_repo_id TEXT NOT NULL,
                    target_repo_id TEXT NOT NULL,
                    dependency_type TEXT NOT NULL,
                    version TEXT,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (source_repo_id) REFERENCES repositories(id),
                    FOREIGN KEY (target_repo_id) REFERENCES repositories(id)
                )
            """)

            # Indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_layer ON memories(layer)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_repo ON memories(repo_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_intents_status ON intents(status)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_relationships_source ON relationships(source_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_relationships_target ON relationships(target_id)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_repos_team ON repositories(team_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_repo_deps_source "
                "ON repository_dependencies(source_repo_id)"
            )

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
        if not CHROMADB_AVAILABLE or self._embedding_fn is None or self._uses_noop_embeddings:
            return None

        if self._chroma_client is None:
            self.chroma_path.mkdir(parents=True, exist_ok=True)
            self._chroma_client = chromadb.PersistentClient(
                path=str(self.chroma_path),
                settings=Settings(anonymized_telemetry=False, allow_reset=True),
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
            collection_name = self._collection_name(layer)
            try:
                self._collections[layer] = client.get_collection(collection_name)
            except Exception:
                # Create collection with cosine distance for proper similarity scores
                # ChromaDB uses "hnsw:space" parameter - "cosine", "l2", or "ip" (inner product)
                self._collections[layer] = client.create_collection(
                    name=collection_name,
                    metadata={
                        "description": f"Memory embeddings for {layer} layer",
                        "hnsw:space": "cosine",
                        "embedding_dimension": self._embedding_dimension or 0,
                    },
                )
                self._backfill_collection(layer, self._collections[layer])

        return self._collections[layer]

    def _collection_name(self, layer: MemoryLayer) -> str:
        """Use dimension-specific collections so old noop vectors do not poison search."""
        if self._embedding_dimension:
            return f"memories_{layer}_{self._embedding_dimension}"
        return f"memories_{layer}"

    def _backfill_collection(self, layer: MemoryLayer, collection) -> None:
        """Populate a newly-created vector collection from SQLite memory rows."""
        if self._embedding_fn is None:
            return

        memories = self.list_memories(layer=layer, limit=10000, order_by="created_at ASC")
        if not memories:
            return

        ids = []
        documents = []
        metadatas = []
        embeddings = []

        for memory in memories:
            try:
                embedding = self._embedding_fn(memory["content"])
            except Exception:
                continue

            metadata = {
                "category": memory.get("category") or "general",
                "importance": memory.get("importance") or 0.5,
                "tags": self._json_serialize(memory.get("tags") or []),
            }
            if memory.get("repo_id"):
                metadata["repo_id"] = memory["repo_id"]

            ids.append(memory["id"])
            documents.append(memory["content"])
            metadatas.append(metadata)
            embeddings.append(embedding)

        if ids:
            collection.upsert(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
                embeddings=embeddings,
            )

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
        embedding: List[float] = None,
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

        if embedding is None and self._embedding_fn is not None:
            try:
                embedding = self._embedding_fn(content)
            except Exception:
                embedding = None

        # Store in SQLite
        with self._get_db() as conn:
            conn.execute(
                """
                INSERT INTO memories (
                    id, content, layer, repo_id, category, importance, tags, metadata, source_ids
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    memory_id,
                    content,
                    layer,
                    repo_id,
                    category,
                    importance,
                    self._json_serialize(tags),
                    self._json_serialize(metadata),
                    self._json_serialize(source_ids),
                ),
            )
            conn.commit()

        # Store in vector DB
        collection = self._get_collection(layer)
        if collection is not None:
            metadata_dict = {
                "category": category,
                "importance": importance,
                "tags": self._json_serialize(tags),
            }
            if repo_id:
                metadata_dict["repo_id"] = repo_id

            add_kwargs = {"ids": [memory_id], "documents": [content], "metadatas": [metadata_dict]}

            if embedding is not None:
                add_kwargs["embeddings"] = [embedding]

            collection.upsert(**add_kwargs)

        return memory_id

    def _get_memory_row(self, memory_id: str, *, track_access: bool) -> Optional[Dict[str, Any]]:
        """Get a memory by ID, optionally updating explicit access metrics."""
        with self._get_db() as conn:
            cursor = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,))
            row = cursor.fetchone()

            if row is None:
                return None

            if track_access:
                conn.execute(
                    """
                    UPDATE memories
                    SET access_count = access_count + 1, accessed_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """,
                    (memory_id,),
                )
                conn.commit()

            return self._row_to_dict(row)

    def get_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Get a memory by ID and record an explicit access."""
        return self._get_memory_row(memory_id, track_access=True)

    def search_memories(
        self,
        query: str,
        layer: MemoryLayer = None,
        repo_id: str = None,
        category: str = None,
        limit: int = 10,
        min_importance: float = 0.0,
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
        seen_ids = set()

        # Search each relevant layer's vector collection
        layers_to_search = [layer] if layer else ["episodic", "semantic", "intent"]

        for search_layer in layers_to_search:
            if self._uses_noop_embeddings:
                continue

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
                query_kwargs = {
                    "n_results": limit,
                    "where": where if where else None,
                }
                if self._embedding_fn is not None:
                    query_kwargs["query_embeddings"] = [self._embedding_fn(query)]
                else:
                    query_kwargs["query_texts"] = [query]

                search_results = collection.query(**query_kwargs)

                if search_results["ids"] and search_results["ids"][0]:
                    for i, mem_id in enumerate(search_results["ids"][0]):
                        distance = (
                            search_results["distances"][0][i]
                            if search_results.get("distances")
                            else 0
                        )
                        similarity = normalize_distance_score(distance)

                        memory = self._get_memory_row(mem_id, track_access=False)
                        if memory:
                            seen_ids.add(mem_id)
                            memory["similarity"] = similarity
                            results.append(memory)

            except Exception:
                # Collection might be empty
                pass

        if len(results) < limit:
            results.extend(
                self._text_search_memories(
                    query=query,
                    layers=layers_to_search,
                    repo_id=repo_id,
                    category=category,
                    limit=limit - len(results),
                    min_importance=min_importance,
                    exclude_ids=seen_ids,
                )
            )

        return rank_memory_results(results, query=query, limit=limit)

    def _text_search_memories(
        self,
        query: str,
        layers: List[str],
        repo_id: str = None,
        category: str = None,
        limit: int = 10,
        min_importance: float = 0.0,
        exclude_ids: set[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fallback SQLite search used when vector search is unavailable or incomplete."""
        exclude_ids = exclude_ids or set()
        terms = [term.lower() for term in query.split() if term.strip()]
        sql = "SELECT * FROM memories WHERE importance >= ?"
        params: list[Any] = [min_importance]

        if layers:
            placeholders = ", ".join("?" for _ in layers)
            sql += f" AND layer IN ({placeholders})"
            params.extend(layers)

        if repo_id:
            sql += " AND repo_id = ?"
            params.append(repo_id)

        if category:
            sql += " AND category = ?"
            params.append(category)

        if terms:
            sql += " AND ("
            sql += " OR ".join("lower(content) LIKE ?" for _ in terms)
            sql += ")"
            params.extend(f"%{term}%" for term in terms)

        sql += " ORDER BY importance DESC, created_at DESC LIMIT ?"
        params.append(limit + len(exclude_ids))

        with self._get_db() as conn:
            cursor = conn.execute(sql, params)
            rows = [self._row_to_dict(row) for row in cursor.fetchall()]

        results = []
        for row in rows:
            if row["id"] in exclude_ids:
                continue
            row["similarity"] = text_similarity(query, row["content"])
            results.append(row)
            if len(results) >= limit:
                break

        return results

    @staticmethod
    def _text_similarity(query: str, content: str) -> float:
        return text_similarity(query, content)

    def list_memories(
        self,
        layer: MemoryLayer = None,
        repo_id: str = None,
        category: str = None,
        limit: int = 50,
        order_by: str = "created_at DESC",
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

        allowed_order_by = {
            "created_at DESC",
            "created_at ASC",
            "importance DESC",
            "importance ASC",
            "accessed_at DESC",
            "accessed_at ASC",
        }
        if order_by not in allowed_order_by:
            order_by = "created_at DESC"

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
        metadata: Dict[str, Any] = None,
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
            cursor = conn.execute(f"UPDATE memories SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()
            updated = cursor.rowcount > 0

        if updated and any(value is not None for value in (content, importance, tags)):
            memory = self._get_memory_row(memory_id, track_access=False)
            if memory:
                collection = self._get_collection(memory["layer"])
                if collection is not None:
                    try:
                        metadata_dict = {
                            "category": memory.get("category", "general"),
                            "importance": memory.get("importance", 0.5),
                            "tags": self._json_serialize(memory.get("tags", [])),
                        }
                        if memory.get("repo_id"):
                            metadata_dict["repo_id"] = memory["repo_id"]
                        update_kwargs = {
                            "ids": [memory_id],
                            "metadatas": [metadata_dict],
                        }
                        if content is not None:
                            update_kwargs["documents"] = [content]
                        collection.update(**update_kwargs)
                    except Exception:
                        pass

        return updated

    def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory from both stores."""
        # Get layer first for vector DB cleanup
        memory = self._get_memory_row(memory_id, track_access=False)
        if not memory:
            return False

        # Delete from SQLite
        with self._get_db() as conn:
            conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            conn.execute(
                "DELETE FROM relationships WHERE source_id = ? OR target_id = ?",
                (memory_id, memory_id),
            )
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
        context: Dict[str, Any] = None,
        repo_id: str = None,
    ) -> str:
        """Set a new intent (goal/direction)."""
        intent_id = self._generate_id(description)
        context = context or {}

        with self._get_db() as conn:
            conn.execute(
                """
                INSERT INTO intents (id, description, priority, context, repo_id)
                VALUES (?, ?, ?, ?, ?)
            """,
                (intent_id, description, priority, self._json_serialize(context), repo_id),
            )
            conn.commit()

        return intent_id

    def get_active_intents(self, repo_id: str = None) -> List[Dict[str, Any]]:
        """Get all active intents, ordered by priority."""
        query = "SELECT * FROM intents WHERE status = 'active'"
        params = []

        if repo_id:
            query += " AND repo_id = ?"
            params.append(repo_id)

        query += " ORDER BY priority DESC, created_at DESC"

        with self._get_db() as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def complete_intent(self, intent_id: str) -> bool:
        """Mark an intent as completed."""
        with self._get_db() as conn:
            cursor = conn.execute(
                """
                UPDATE intents
                SET status = 'completed', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """,
                (intent_id,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def add_relationship(
        self, source_id: str, target_id: str, relationship: str, strength: float = 1.0
    ) -> str:
        """Add a relationship between memories."""
        source = self._get_memory_row(source_id, track_access=False)
        target = self._get_memory_row(target_id, track_access=False)
        if source is None or target is None:
            raise ValueError("Relationship source and target memories must both exist")
        if source.get("repo_id") != target.get("repo_id"):
            raise ValueError("Memory relationships cannot cross repository boundaries")

        rel_id = self._generate_id(f"{source_id}-{target_id}-{relationship}")

        with self._get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO relationships (
                    id, source_id, target_id, relationship, strength
                )
                VALUES (?, ?, ?, ?, ?)
            """,
                (rel_id, source_id, target_id, relationship, strength),
            )
            conn.commit()

        return rel_id

    def get_related_memories(
        self, memory_id: str, relationship: str = None
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

        source = self._get_memory_row(memory_id, track_access=False)
        if source is None:
            return []
        if source.get("repo_id") is None:
            query += " AND m.repo_id IS NULL"
        else:
            query += " AND m.repo_id = ?"
            params.append(source["repo_id"])

        with self._get_db() as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def get_all_relationships(self, repo_id: str = None) -> List[Dict[str, Any]]:
        """Get all relationships."""
        query = "SELECT r.* FROM relationships r"
        params = []
        if repo_id:
            query += """
                JOIN memories source ON source.id = r.source_id
                JOIN memories target ON target.id = r.target_id
                WHERE source.repo_id = ? AND target.repo_id = ?
            """
            params.extend([repo_id, repo_id])

        with self._get_db() as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def start_session(self) -> str:
        """Start a new session for tracking."""
        session_id = self._generate_id("session")

        with self._get_db() as conn:
            conn.execute("INSERT INTO sessions (id) VALUES (?)", (session_id,))
            conn.commit()

        return session_id

    def end_session(self, session_id: str, summary: str, memory_ids: List[str]):
        """End a session with summary."""
        with self._get_db() as conn:
            conn.execute(
                """
                UPDATE sessions
                SET summary = ?, memory_ids = ?, ended_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """,
                (summary, self._json_serialize(memory_ids), session_id),
            )
            conn.commit()

    def get_stats(self, repo_id: str = None) -> Dict[str, Any]:
        """Get storage statistics."""
        with self._get_db() as conn:
            stats = {}

            # Build WHERE clause for repo filtering
            repo_filter = ""
            repo_params = []
            if repo_id:
                repo_filter = " WHERE repo_id = ?"
                repo_params = [repo_id]

            # Memory counts by layer
            cursor = conn.execute(
                f"""
                SELECT layer, COUNT(*) as count
                FROM memories
                {repo_filter}
                GROUP BY layer
            """,
                repo_params,
            )
            stats["memories_by_layer"] = dict(cursor.fetchall())

            # Memory counts by category
            cursor = conn.execute(
                f"""
                SELECT category, COUNT(*) as count
                FROM memories
                {repo_filter}
                GROUP BY category
            """,
                repo_params,
            )
            stats["memories_by_category"] = dict(cursor.fetchall())

            # Total counts
            cursor = conn.execute(f"SELECT COUNT(*) FROM memories{repo_filter}", repo_params)
            stats["total_memories"] = cursor.fetchone()[0]

            intent_query = "SELECT COUNT(*) FROM intents WHERE status = 'active'"
            intent_params = []
            if repo_id:
                intent_query += " AND repo_id = ?"
                intent_params.append(repo_id)

            cursor = conn.execute(intent_query, intent_params)
            stats["active_intents"] = cursor.fetchone()[0]

            rel_query = "SELECT COUNT(*) FROM relationships r"
            rel_params = []
            if repo_id:
                rel_query += """
                    JOIN memories source ON source.id = r.source_id
                    JOIN memories target ON target.id = r.target_id
                    WHERE source.repo_id = ? AND target.repo_id = ?
                """
                rel_params.extend([repo_id, repo_id])

            cursor = conn.execute(rel_query, rel_params)
            stats["total_relationships"] = cursor.fetchone()[0]

            return stats

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        """Convert SQLite row to dictionary."""
        d = dict(row)

        # Parse JSON fields
        for field in ["tags", "metadata", "source_ids", "memory_ids", "context", "tech_stack"]:
            if field in d and d[field]:
                d[field] = LocalStorage._json_deserialize(d[field])

        return d

    # Repository operations
    def store_repository(self, repo: Dict[str, Any]) -> str:
        repo_id = repo.get("id") or self._generate_id(repo["name"])

        with self._get_db() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO repositories (
                        id, name, url, description, tech_stack, team_id, metadata
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        repo_id,
                        repo["name"],
                        repo.get("url"),
                        repo.get("description"),
                        self._json_serialize(repo.get("tech_stack", [])),
                        repo.get("team_id"),
                        self._json_serialize(repo.get("metadata", {})),
                    ),
                )
            except sqlite3.IntegrityError as e:
                raise ValueError(f"Repository already exists: {repo_id}") from e
            conn.commit()
        return repo_id

    def get_repository(self, repo_id: str) -> Optional[Dict[str, Any]]:
        with self._get_db() as conn:
            cursor = conn.execute("SELECT * FROM repositories WHERE id = ?", (repo_id,))
            row = cursor.fetchone()
            return self._row_to_dict(row) if row else None

    def list_repositories(self, team_id: str = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM repositories"
        params = []
        if team_id:
            query += " WHERE team_id = ?"
            params.append(team_id)

        with self._get_db() as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def add_repo_dependency(
        self,
        source_id: str,
        target_id: str,
        dep_type: str,
        version: str = None,
        notes: str = None,
    ) -> str:
        if self.get_repository(source_id) is None:
            raise ValueError(f"Repository not found: {source_id}")
        if self.get_repository(target_id) is None:
            raise ValueError(f"Repository not found: {target_id}")

        dep_id = self._generate_id(f"{source_id}-{target_id}-{dep_type}")

        with self._get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO repository_dependencies (
                    id, source_repo_id, target_repo_id, dependency_type, version, notes
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (dep_id, source_id, target_id, dep_type, version, notes),
            )
            conn.commit()
        return dep_id

    def get_repo_dependencies(self, repo_id: str) -> List[Dict[str, Any]]:
        with self._get_db() as conn:
            cursor = conn.execute(
                """
                SELECT target_repo_id as target_id, dependency_type as type, version, notes
                FROM repository_dependencies
                WHERE source_repo_id = ?
            """,
                (repo_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    # Team and User operations
    def store_user(self, user: Dict[str, Any]) -> str:
        user_id = user["id"]
        with self._get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO users (id, username, email, display_name, metadata)
                VALUES (?, ?, ?, ?, ?)
            """,
                (
                    user_id,
                    user["username"],
                    user.get("email"),
                    user.get("display_name"),
                    self._json_serialize(user.get("metadata", {})),
                ),
            )
            conn.commit()
        return user_id

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self._get_db() as conn:
            cursor = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            return self._row_to_dict(row) if row else None

    def store_team(self, team: Dict[str, Any]) -> str:
        team_id = team["id"]
        with self._get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO teams (id, name, description, metadata)
                VALUES (?, ?, ?, ?)
            """,
                (
                    team_id,
                    team["name"],
                    team.get("description"),
                    self._json_serialize(team.get("metadata", {})),
                ),
            )
            conn.commit()
        return team_id

    def get_team(self, team_id: str) -> Optional[Dict[str, Any]]:
        with self._get_db() as conn:
            cursor = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,))
            row = cursor.fetchone()
            return self._row_to_dict(row) if row else None

    def add_team_member(self, team_id: str, user_id: str) -> bool:
        if self.get_team(team_id) is None or self.get_user(user_id) is None:
            return False

        with self._get_db() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO team_members (team_id, user_id)
                VALUES (?, ?)
            """,
                (team_id, user_id),
            )
            conn.commit()
        return True

    def get_user_teams(self, user_id: str) -> List[Dict[str, Any]]:
        with self._get_db() as conn:
            cursor = conn.execute(
                """
                SELECT t.* FROM teams t
                JOIN team_members tm ON t.id = tm.team_id
                WHERE tm.user_id = ?
            """,
                (user_id,),
            )
            return [self._row_to_dict(row) for row in cursor.fetchall()]
