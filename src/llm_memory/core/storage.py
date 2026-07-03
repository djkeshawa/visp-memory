"""
Storage layer for LLM Memory.

Combines:
- SQLite for structured data (memories, metadata, relationships)
- ChromaDB for vector embeddings (semantic search)
"""

import hashlib
import json
import logging
import re
import sqlite3
import uuid
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from llm_memory.core.clock import utc_now
from llm_memory.core.indexing import EmbeddingIndexReport, ReindexResult, ReindexScope
from llm_memory.core.ranking import (
    clamp_score,
    normalize_distance_score,
    rank_memory_results,
    relationship_score,
    text_similarity,
    utility_rank_adjustment,
)

try:
    import chromadb
    from chromadb.config import Settings

    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False


logger = logging.getLogger(__name__)


MemoryLayer = Literal["raw", "episodic", "semantic", "intent"]
MemoryStatus = Literal["active", "pending", "archived", "deleted"]
RecallEventType = Literal["surfaced", "used", "dismissed", "task_linked", "outcome_linked"]

RECALL_EVENT_WEIGHTS: dict[str, float] = {
    "surfaced": 0.03,
    "used": 0.30,
    "dismissed": -0.25,
    "task_linked": 0.20,
    "outcome_linked": 0.25,
}
# Events that signal a memory was actually *used* (not merely surfaced) trigger
# retrieval reinforcement: the memory's access count grows and its last-access time
# refreshes. This strengthens future recall (via base-level activation) and resets
# decay, implementing the "use it or lose it" principle. Surfaced/dismissed events
# deliberately do not reinforce, to avoid popularity bias from mere exposure.
REINFORCING_RECALL_EVENTS = frozenset({"used", "task_linked", "outcome_linked"})
SENSITIVE_RECALL_METADATA_KEYS = {"prompt", "response", "query", "content", "messages"}


class BaseStorage(ABC):
    """Abstract interface for memory storage."""

    def close(self) -> None:
        """Release any resources held by the backend (connection pools, sessions).

        Default is a no-op so backends without long-lived handles need not override
        it. Backends that own such resources (e.g. Neo4j drivers, HTTP sessions,
        ChromaDB clients) override this and must make it idempotent.
        """

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

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
    def get_active_intents(
        self, repo_id: str = None, status: str = "active"
    ) -> List[Dict[str, Any]]:
        """Get intents filtered by status."""
        pass

    @abstractmethod
    def complete_intent(self, intent_id: str) -> bool:
        """Complete an intent."""
        pass

    @abstractmethod
    def update_intent(self, intent_id: str, **kwargs) -> bool:
        """Update an intent."""
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
    def list_project_ids(self) -> List[str]:
        """List repository/project IDs referenced by stored data."""
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

    AUTO_LINK_RELATIONSHIP = "related_to"
    SOURCE_LINK_RELATIONSHIP = "derived_from"
    DEFAULT_AUTO_LINK_LIMIT = 3
    DEFAULT_AUTO_LINK_MIN_SCORE = 0.53
    RELATIONSHIP_CONFIDENCE_VALUES = {"observed", "inferred", "ambiguous", "manual"}
    LEGACY_RELATIONSHIP_EVIDENCE_REASON = "Legacy relationship without evidence metadata."
    UNSPECIFIED_RELATIONSHIP_EVIDENCE_REASON = "Relationship created without evidence metadata."

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
            # Enable WAL once: it persists in the database file header, so every
            # later connection inherits it (readers do not block writers).
            conn.execute("PRAGMA journal_mode=WAL")

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
                    status TEXT DEFAULT 'active',
                    approved_by TEXT DEFAULT NULL,
                    approved_at TIMESTAMP DEFAULT NULL,
                    archived_at TIMESTAMP DEFAULT NULL,
                    source TEXT DEFAULT NULL,
                    quality_flags TEXT DEFAULT '[]',
                    last_quality_checked_at TIMESTAMP DEFAULT NULL,
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

            memory_migrations = {
                "status": "ALTER TABLE memories ADD COLUMN status TEXT DEFAULT 'active'",
                "approved_by": "ALTER TABLE memories ADD COLUMN approved_by TEXT DEFAULT NULL",
                "approved_at": "ALTER TABLE memories ADD COLUMN approved_at TIMESTAMP DEFAULT NULL",
                "archived_at": "ALTER TABLE memories ADD COLUMN archived_at TIMESTAMP DEFAULT NULL",
                "source": "ALTER TABLE memories ADD COLUMN source TEXT DEFAULT NULL",
                "quality_flags": "ALTER TABLE memories ADD COLUMN quality_flags TEXT DEFAULT '[]'",
                "last_quality_checked_at": (
                    "ALTER TABLE memories ADD COLUMN last_quality_checked_at TIMESTAMP DEFAULT NULL"
                ),
            }
            for column, statement in memory_migrations.items():
                try:
                    conn.execute(f"SELECT {column} FROM memories LIMIT 1")
                except sqlite3.OperationalError:
                    conn.execute(statement)

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
                    confidence TEXT DEFAULT 'ambiguous',
                    confidence_score REAL DEFAULT NULL,
                    source TEXT DEFAULT 'legacy',
                    source_file TEXT DEFAULT NULL,
                    source_location TEXT DEFAULT NULL,
                    reason TEXT DEFAULT 'Legacy relationship without evidence metadata.',
                    created_by TEXT DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (source_id) REFERENCES memories(id),
                    FOREIGN KEY (target_id) REFERENCES memories(id)
                )
            """)

            relationship_migrations = {
                "confidence": (
                    "ALTER TABLE relationships ADD COLUMN confidence TEXT DEFAULT 'ambiguous'"
                ),
                "confidence_score": (
                    "ALTER TABLE relationships ADD COLUMN confidence_score REAL DEFAULT NULL"
                ),
                "source": "ALTER TABLE relationships ADD COLUMN source TEXT DEFAULT 'legacy'",
                "source_file": (
                    "ALTER TABLE relationships ADD COLUMN source_file TEXT DEFAULT NULL"
                ),
                "source_location": (
                    "ALTER TABLE relationships ADD COLUMN source_location TEXT DEFAULT NULL"
                ),
                "reason": (
                    "ALTER TABLE relationships ADD COLUMN reason TEXT DEFAULT "
                    "'Legacy relationship without evidence metadata.'"
                ),
                "created_by": (
                    "ALTER TABLE relationships ADD COLUMN created_by TEXT DEFAULT NULL"
                ),
            }
            for column, statement in relationship_migrations.items():
                try:
                    conn.execute(f"SELECT {column} FROM relationships LIMIT 1")
                except sqlite3.OperationalError:
                    conn.execute(statement)

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

            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    actor_id TEXT,
                    repo_id TEXT,
                    target_type TEXT,
                    target_id TEXT,
                    metadata TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS recall_events (
                    id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    repo_id TEXT DEFAULT NULL,
                    query_hash TEXT DEFAULT NULL,
                    task_id TEXT DEFAULT NULL,
                    outcome TEXT DEFAULT NULL,
                    metadata TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (memory_id) REFERENCES memories(id)
                )
            """)

            # Indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_layer ON memories(layer)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_repo ON memories(repo_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status)")
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_event ON audit_logs(event_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_logs(actor_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_repo ON audit_logs(repo_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_recall_events_memory "
                "ON recall_events(memory_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_recall_events_repo ON recall_events(repo_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_recall_events_type "
                "ON recall_events(event_type)"
            )

            conn.commit()

    @contextmanager
    def _get_db(self):
        """Get SQLite connection context.

        Each connection is configured for safe concurrent multi-client access
        (server / MCP): WAL journaling so readers do not block writers, a busy
        timeout so competing writers wait instead of raising "database is
        locked", and foreign-key enforcement so referential integrity is
        actually honored.
        """
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            # busy_timeout and foreign_keys are per-connection settings; WAL is a
            # persistent database-level setting applied once in _init_sqlite.
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
        finally:
            conn.close()

    def close(self) -> None:
        """Release the ChromaDB client, if one was created. Idempotent.

        SQLite connections are opened and closed per operation (see ``_get_db``),
        so there is no persistent database handle to release here.
        """
        # ChromaDB's PersistentClient exposes no explicit close; drop references so
        # the underlying system/DB handles can be released by garbage collection.
        self._collections = {}
        self._chroma_client = None

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

    def _list_vector_collection_names(self) -> List[str]:
        """Return Chroma collection names without creating new collections."""
        client = self._get_chroma()
        if client is None:
            return []
        try:
            collections = client.list_collections()
        except Exception:
            return []
        names = []
        for collection in collections:
            name = getattr(collection, "name", None) or str(collection)
            if name:
                names.append(name)
        return names

    def _count_vector_collection(self, collection_name: str) -> Optional[int]:
        client = self._get_chroma()
        if client is None:
            return None
        try:
            return int(client.get_collection(collection_name).count())
        except Exception:
            return None

    def _memory_filters_from_scope(self, scope: ReindexScope) -> tuple[str, list[Any]]:
        clauses = ["1=1"]
        params: list[Any] = []
        if scope.layer:
            clauses.append("layer = ?")
            params.append(scope.layer)
        if scope.repo_id:
            clauses.append("repo_id = ?")
            params.append(scope.repo_id)
        if scope.category:
            clauses.append("category = ?")
            params.append(scope.category)
        return " AND ".join(clauses), params

    def _list_reindex_candidates(self, scope: ReindexScope) -> List[Dict[str, Any]]:
        where, params = self._memory_filters_from_scope(scope)
        query = f"SELECT * FROM memories WHERE {where} ORDER BY layer ASC, created_at ASC"
        with self._get_db() as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def _count_reindex_candidates(self, scope: ReindexScope) -> int:
        where, params = self._memory_filters_from_scope(scope)
        with self._get_db() as conn:
            cursor = conn.execute(f"SELECT COUNT(*) FROM memories WHERE {where}", params)
            return int(cursor.fetchone()[0])

    def _embedding_collection_summary(
        self, scope: ReindexScope
    ) -> tuple[list[str], list[str], Optional[int]]:
        layers = [scope.layer] if scope.layer else ["raw", "episodic", "semantic", "intent"]
        active = [self._collection_name(layer) for layer in layers]
        existing = set(self._list_vector_collection_names())
        legacy = []
        for name in sorted(existing):
            for layer in layers:
                if name == f"memories_{layer}" and name != self._collection_name(layer):
                    legacy.append(name)
                    break
                dimension_collection = re.fullmatch(rf"memories_{re.escape(layer)}_\d+", name)
                if dimension_collection and name != self._collection_name(layer):
                    legacy.append(name)
                    break

        indexed_total = 0
        indexed_known = False
        for name in active:
            count = self._count_vector_collection(name)
            if count is not None:
                indexed_total += count
                indexed_known = True

        return active, sorted(set(legacy)), indexed_total if indexed_known else None

    def inspect_embedding_index(
        self,
        *,
        storage_backend: str,
        provider: str,
        effective_provider: str = None,
        model: str = None,
        dimension: int = None,
        scope: ReindexScope = None,
    ) -> EmbeddingIndexReport:
        """Inspect whether the active embedding index needs maintenance."""
        scope = scope or ReindexScope()
        matched = self._count_reindex_candidates(scope)
        active, legacy, indexed = self._embedding_collection_summary(scope)

        if self._uses_noop_embeddings:
            status = "disabled"
            message = "Noop embeddings are active; vector index search is disabled."
        elif self._embedding_fn is None:
            status = "not_configured"
            message = "No embedding function is configured; text fallback is used."
        elif not CHROMADB_AVAILABLE:
            status = "not_configured"
            message = "ChromaDB is not installed, so vector indexes cannot be rebuilt."
        elif dimension or self._embedding_dimension:
            status = "available"
            message = "Embedding index is available for the active provider dimension."
        else:
            status = "unknown"
            message = "Embedding provider is active but its vector dimension is unknown."

        needs_reindex = bool(
            matched
            and status == "available"
            and (legacy or indexed is None or indexed < matched)
        )
        if legacy:
            message = (
                "Legacy embedding collections were found for a different dimension; "
                "run a dry-run and rebuild after provider changes."
            )
        elif indexed is not None and indexed < matched and status == "available":
            message = "The active embedding index has fewer vectors than matching memories."

        return EmbeddingIndexReport(
            storage_backend=storage_backend,
            provider=provider,
            effective_provider=effective_provider,
            model=model,
            dimension=dimension or self._embedding_dimension,
            status=status,
            message=message,
            scope=scope.as_filter_dict(),
            matched_memories=matched,
            indexed_memories=indexed,
            active_collections=active,
            legacy_collections=legacy,
            needs_reindex=needs_reindex,
        )

    def rebuild_embedding_index(
        self, *, scope: ReindexScope = None, dry_run: bool = True
    ) -> ReindexResult:
        """Rebuild active-dimension vector entries for matching memories."""
        scope = scope or ReindexScope()
        candidates = self._list_reindex_candidates(scope)
        active, legacy, _ = self._embedding_collection_summary(scope)

        if self._uses_noop_embeddings:
            return ReindexResult(
                dry_run=dry_run,
                status="disabled",
                message="Noop embeddings are active; there is no vector index to rebuild.",
                scope=scope.as_filter_dict(),
                matched_memories=len(candidates),
                dimension=self._embedding_dimension,
                active_collections=active,
                legacy_collections=legacy,
            )
        if self._embedding_fn is None or not CHROMADB_AVAILABLE:
            return ReindexResult(
                dry_run=dry_run,
                status="not_configured",
                message="A real embedding function and ChromaDB are required to rebuild vectors.",
                scope=scope.as_filter_dict(),
                matched_memories=len(candidates),
                dimension=self._embedding_dimension,
                active_collections=active,
                legacy_collections=legacy,
            )
        if dry_run:
            return ReindexResult(
                dry_run=True,
                status="ready",
                message="Dry run complete; no embeddings were changed.",
                scope=scope.as_filter_dict(),
                matched_memories=len(candidates),
                dimension=self._embedding_dimension,
                active_collections=active,
                legacy_collections=legacy,
            )

        grouped: dict[str, dict[str, list[Any]]] = {}
        errors: list[dict[str, str]] = []
        for memory in candidates:
            try:
                embedding = self._embedding_fn(memory["content"])
            except Exception as exc:
                errors.append({"id": memory["id"], "error": exc.__class__.__name__})
                continue

            metadata = {
                "category": memory.get("category") or "general",
                "importance": memory.get("importance") or 0.5,
                "tags": self._json_serialize(memory.get("tags") or []),
                "status": memory.get("status") or "active",
            }
            if memory.get("repo_id"):
                metadata["repo_id"] = memory["repo_id"]

            layer = memory["layer"]
            layer_group = grouped.setdefault(
                layer, {"ids": [], "documents": [], "metadatas": [], "embeddings": []}
            )
            layer_group["ids"].append(memory["id"])
            layer_group["documents"].append(memory["content"])
            layer_group["metadatas"].append(metadata)
            layer_group["embeddings"].append(embedding)

        reindexed = 0
        for layer, payload in grouped.items():
            collection = self._get_collection(layer)
            if collection is None:
                errors.extend(
                    {"id": memory_id, "error": "CollectionUnavailable"}
                    for memory_id in payload["ids"]
                )
                continue
            try:
                collection.upsert(**payload)
                reindexed += len(payload["ids"])
            except Exception as exc:
                errors.extend(
                    {"id": memory_id, "error": exc.__class__.__name__}
                    for memory_id in payload["ids"]
                )

        failed = len(errors)
        status = "completed" if failed == 0 else "partial_failure"
        message = (
            f"Rebuilt {reindexed} embedding vectors."
            if failed == 0
            else f"Rebuilt {reindexed} embedding vectors; {failed} failed."
        )
        return ReindexResult(
            dry_run=False,
            status=status,
            message=message,
            scope=scope.as_filter_dict(),
            matched_memories=len(candidates),
            reindexed_memories=reindexed,
            failed_memories=failed,
            dimension=self._embedding_dimension,
            active_collections=active,
            legacy_collections=legacy,
            errors=errors[:50],
        )

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
                "status": memory.get("status") or "active",
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
        """Generate a unique ID.

        Includes a random nonce in addition to the timestamp so that rapid
        successive calls with identical content (which can share a microsecond
        timestamp, especially under WAL's faster writes) do not collide on the
        truncated hash. IDs are not content-addressed, so the extra entropy is
        behavior-preserving.
        """
        timestamp = utc_now().isoformat()
        nonce = uuid.uuid4().hex
        return hashlib.sha256(f"{content}{timestamp}{nonce}".encode()).hexdigest()[:16]

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
        status: MemoryStatus = "active",
        source: str = None,
        quality_flags: List[str] = None,
        embedding: List[float] = None,
        auto_link: bool = True,
        auto_link_limit: int = DEFAULT_AUTO_LINK_LIMIT,
        auto_link_min_score: float = DEFAULT_AUTO_LINK_MIN_SCORE,
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
            auto_link: Whether to create graph relationships to related memories
            auto_link_limit: Maximum inferred similarity links to create
            auto_link_min_score: Minimum similarity score required for inferred links

        Returns:
            Memory ID
        """
        memory_id = self._generate_id(content)
        tags = tags or []
        metadata = metadata or {}
        source_ids = source_ids or []
        quality_flags = quality_flags or []

        if embedding is None and self._embedding_fn is not None:
            try:
                embedding = self._embedding_fn(content)
            except Exception as exc:
                logger.warning("Embedding computation failed for new memory: %s", exc)
                embedding = None

        # Store in SQLite
        created_at = utc_now().isoformat()
        with self._get_db() as conn:
            conn.execute(
                """
                INSERT INTO memories (
                    id, content, layer, repo_id, category, importance, tags, metadata,
                    source_ids, status, source, quality_flags, created_at, accessed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    status,
                    source,
                    self._json_serialize(quality_flags),
                    created_at,
                    created_at,
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
                "status": status,
            }
            if repo_id:
                metadata_dict["repo_id"] = repo_id

            add_kwargs = {"ids": [memory_id], "documents": [content], "metadatas": [metadata_dict]}

            if embedding is not None:
                add_kwargs["embeddings"] = [embedding]

            # The SQLite row is already committed above. If the vector write
            # fails we keep the structured row (it is the source of truth) but
            # log loudly so the divergence is visible; rebuild_embedding_index
            # / inspect_embedding_index can reconcile the stale vector index.
            try:
                collection.upsert(**add_kwargs)
            except Exception as exc:
                logger.warning(
                    "Vector upsert failed for memory %s; SQLite row persisted but the "
                    "vector index is now stale (run rebuild_embedding_index to "
                    "reconcile): %s",
                    memory_id,
                    exc,
                )

        self._auto_link_memory(
            memory_id=memory_id,
            content=content,
            repo_id=repo_id,
            source_ids=source_ids,
            enabled=auto_link,
            limit=auto_link_limit,
            min_score=auto_link_min_score,
        )

        return memory_id

    def _auto_link_memory(
        self,
        memory_id: str,
        content: str,
        repo_id: str = None,
        source_ids: List[str] = None,
        enabled: bool = True,
        limit: int = DEFAULT_AUTO_LINK_LIMIT,
        min_score: float = DEFAULT_AUTO_LINK_MIN_SCORE,
    ) -> None:
        """Create provenance and conservative similarity links for a new memory."""
        source_ids = list(dict.fromkeys(source_ids or []))
        excluded_ids = {memory_id, *source_ids}

        for source_id in source_ids:
            if source_id == memory_id:
                continue
            try:
                self.add_relationship(
                    source_id=source_id,
                    target_id=memory_id,
                    relationship=self.SOURCE_LINK_RELATIONSHIP,
                    strength=1.0,
                )
            except ValueError:
                # Source IDs can come from imports or legacy data. Invalid cross-repo or
                # missing sources should not block storing the memory itself.
                continue

        if not enabled or limit <= 0:
            return

        candidates = self.search_memories(
            query=content,
            repo_id=repo_id,
            limit=max(limit * 4, limit + len(excluded_ids) + 1),
        )

        created = 0
        threshold = clamp_score(min_score)
        for candidate in candidates:
            candidate_id = candidate.get("id")
            if not candidate_id or candidate_id in excluded_ids:
                continue

            score = relationship_score(
                candidate.get("similarity"),
                content,
                str(candidate.get("content", "")),
            )
            if score < threshold:
                continue

            try:
                self.add_relationship(
                    source_id=memory_id,
                    target_id=candidate_id,
                    relationship=self.AUTO_LINK_RELATIONSHIP,
                    strength=score,
                )
            except ValueError:
                continue

            created += 1
            if created >= limit:
                break

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
        status: str = "active",
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

            # Build where clause. Filtering status at the vector layer (rather
            # than only post-fetch in Python) ensures active matches ranked
            # beyond the top-N non-active hits are not silently dropped.
            where = {}
            if category:
                where["category"] = category
            if repo_id:
                where["repo_id"] = repo_id
            if min_importance > 0:
                where["importance"] = {"$gte": min_importance}
            if status and status != "all":
                where["status"] = status

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
                        if not memory:
                            continue
                        status_matches = (
                            not status or status == "all" or memory.get("status") == status
                        )
                        if status_matches:
                            seen_ids.add(mem_id)
                            memory["similarity"] = similarity
                            results.append(memory)

            except Exception as exc:
                # Collection might be empty / not yet populated; fall back to
                # text search below. Logged at debug to avoid noise.
                logger.debug("Vector search failed on layer %s: %s", search_layer, exc)

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
                    status=status,
                )
            )

        self._attach_recall_utility_scores(results)
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
        status: str = "active",
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

        if status and status != "all":
            sql += " AND status = ?"
            params.append(status)

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
        status: str = "active",
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

        if status and status != "all":
            query += " AND status = ?"
            params.append(status)

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

        # Deterministic tiebreaker on insertion order (rowid) so rows that share
        # a timestamp/importance are not returned in arbitrary order, which makes
        # "latest"-style queries (limit=1) flaky under same-microsecond writes.
        tiebreak = "rowid ASC" if order_by.endswith("ASC") else "rowid DESC"
        query += f" ORDER BY {order_by}, {tiebreak} LIMIT ?"
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
        status: MemoryStatus = None,
        approved_by: str = None,
        approved_at: str = None,
        archived_at: str = None,
        source: str = None,
        quality_flags: List[str] = None,
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

        if status is not None:
            updates.append("status = ?")
            params.append(status)
            if status == "archived":
                updates.append("archived_at = COALESCE(archived_at, CURRENT_TIMESTAMP)")
            elif status == "active":
                updates.append("archived_at = NULL")

        if approved_by is not None:
            updates.append("approved_by = ?")
            params.append(approved_by)

        if approved_at is not None:
            updates.append("approved_at = ?")
            params.append(approved_at)

        if archived_at is not None:
            updates.append("archived_at = ?")
            params.append(archived_at)

        if source is not None:
            updates.append("source = ?")
            params.append(source)

        if quality_flags is not None:
            updates.append("quality_flags = ?")
            params.append(self._json_serialize(quality_flags))

        if not updates:
            return False

        params.append(memory_id)

        with self._get_db() as conn:
            cursor = conn.execute(f"UPDATE memories SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()
            updated = cursor.rowcount > 0

        if updated and any(value is not None for value in (content, importance, tags, status)):
            memory = self._get_memory_row(memory_id, track_access=False)
            if memory:
                collection = self._get_collection(memory["layer"])
                if collection is not None:
                    try:
                        metadata_dict = {
                            "category": memory.get("category", "general"),
                            "importance": memory.get("importance", 0.5),
                            "tags": self._json_serialize(memory.get("tags", [])),
                            "status": memory.get("status", "active"),
                        }
                        if memory.get("repo_id"):
                            metadata_dict["repo_id"] = memory["repo_id"]
                        update_kwargs = {
                            "ids": [memory_id],
                            "metadatas": [metadata_dict],
                        }
                        if content is not None:
                            update_kwargs["documents"] = [content]
                            if self._embedding_fn is not None:
                                try:
                                    update_kwargs["embeddings"] = [self._embedding_fn(content)]
                                except Exception as exc:
                                    logger.warning(
                                        "Embedding computation failed during update of "
                                        "memory %s: %s",
                                        memory_id,
                                        exc,
                                    )
                        collection.update(**update_kwargs)
                    except Exception as exc:
                        logger.warning(
                            "Vector update failed for memory %s; vector index may be "
                            "stale (run rebuild_embedding_index to reconcile): %s",
                            memory_id,
                            exc,
                        )

        return updated

    def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory from both stores."""
        # Get layer first for vector DB cleanup
        memory = self._get_memory_row(memory_id, track_access=False)
        if not memory:
            return False

        # Delete from SQLite. Child rows (which carry FK references to
        # memories.id) must be removed before the parent row so that
        # foreign_keys=ON enforcement does not reject the parent delete.
        with self._get_db() as conn:
            conn.execute("DELETE FROM recall_events WHERE memory_id = ?", (memory_id,))
            conn.execute(
                "DELETE FROM relationships WHERE source_id = ? OR target_id = ?",
                (memory_id, memory_id),
            )
            conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            conn.commit()

        # Delete from vector DB
        collection = self._get_collection(memory["layer"])
        if collection:
            try:
                collection.delete(ids=[memory_id])
            except Exception as exc:
                logger.warning(
                    "Vector delete failed for memory %s; an orphaned vector may remain "
                    "(run rebuild_embedding_index to reconcile): %s",
                    memory_id,
                    exc,
                )

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

    def get_active_intents(
        self, repo_id: str = None, status: str = "active"
    ) -> List[Dict[str, Any]]:
        """Get intents ordered by priority."""
        query = "SELECT * FROM intents WHERE 1=1"
        params = []

        if status and status != "all":
            query += " AND status = ?"
            params.append(status)

        if repo_id:
            query += " AND repo_id = ?"
            params.append(repo_id)

        query += " ORDER BY priority DESC, created_at DESC"

        with self._get_db() as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def complete_intent(self, intent_id: str) -> bool:
        """Mark an intent as completed."""
        return self.update_intent(intent_id, status="completed")

    def update_intent(
        self,
        intent_id: str,
        description: str = None,
        priority: int = None,
        status: str = None,
        context: Dict[str, Any] = None,
    ) -> bool:
        """Update mutable intent fields."""
        updates = []
        params = []

        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if priority is not None:
            updates.append("priority = ?")
            params.append(priority)
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if context is not None:
            updates.append("context = ?")
            params.append(self._json_serialize(context))

        if not updates:
            return False

        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(intent_id)

        with self._get_db() as conn:
            cursor = conn.execute(
                f"""
                UPDATE intents
                SET {", ".join(updates)}
                WHERE id = ?
                """,
                params,
            )
            conn.commit()
            return cursor.rowcount > 0

    def add_relationship(
        self,
        source_id: str,
        target_id: str,
        relationship: str,
        strength: float = 1.0,
        evidence: Dict[str, Any] = None,
    ) -> str:
        """Add a relationship between memories."""
        source = self._get_memory_row(source_id, track_access=False)
        target = self._get_memory_row(target_id, track_access=False)
        if source is None or target is None:
            raise ValueError("Relationship source and target memories must both exist")
        if source.get("repo_id") != target.get("repo_id"):
            raise ValueError("Memory relationships cannot cross repository boundaries")

        rel_id = self._generate_id(f"{source_id}-{target_id}-{relationship}")
        evidence_data = self._normalize_relationship_evidence(
            evidence,
            strength=strength,
            created_at=None,
            legacy=False,
        )

        with self._get_db() as conn:
            if relationship != self.AUTO_LINK_RELATIONSHIP:
                conn.execute(
                    """
                    DELETE FROM relationships
                    WHERE relationship = ?
                    AND (
                        (source_id = ? AND target_id = ?)
                        OR (source_id = ? AND target_id = ?)
                    )
                """,
                    (
                        self.AUTO_LINK_RELATIONSHIP,
                        source_id,
                        target_id,
                        target_id,
                        source_id,
                    ),
                )
            conn.execute(
                """
                INSERT OR REPLACE INTO relationships (
                    id, source_id, target_id, relationship, strength,
                    confidence, confidence_score, source, source_file,
                    source_location, reason, created_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    rel_id,
                    source_id,
                    target_id,
                    relationship,
                    strength,
                    evidence_data["confidence"],
                    evidence_data["confidence_score"],
                    evidence_data["source"],
                    evidence_data["source_file"],
                    evidence_data["source_location"],
                    evidence_data["reason"],
                    evidence_data["created_by"],
                ),
            )
            conn.commit()

        return rel_id

    def get_related_memories(
        self, memory_id: str, relationship: str = None
    ) -> List[Dict[str, Any]]:
        """Get memories related to a given memory."""
        query = """
            SELECT m.*, r.relationship, r.strength,
                r.confidence AS relationship_confidence,
                r.confidence_score AS relationship_confidence_score,
                r.source AS relationship_evidence_source,
                r.source_file AS relationship_source_file,
                r.source_location AS relationship_source_location,
                r.reason AS relationship_reason,
                r.created_by AS relationship_created_by,
                r.created_at AS relationship_created_at
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
            related = []
            seen_ids = set()
            for row in cursor.fetchall():
                item = self._row_to_dict(row)
                evidence_data = {
                    "confidence": item.pop("relationship_confidence", None),
                    "confidence_score": item.pop("relationship_confidence_score", None),
                    "source": item.pop("relationship_evidence_source", None),
                    "source_file": item.pop("relationship_source_file", None),
                    "source_location": item.pop("relationship_source_location", None),
                    "reason": item.pop("relationship_reason", None),
                    "created_by": item.pop("relationship_created_by", None),
                    "created_at": item.pop("relationship_created_at", None),
                }
                item["relationship_evidence"] = self._normalize_relationship_evidence(
                    evidence_data,
                    strength=item.get("strength"),
                    created_at=evidence_data["created_at"],
                    legacy=evidence_data["source"] == "legacy",
                )
                if item["id"] in seen_ids:
                    continue
                seen_ids.add(item["id"])
                related.append(item)
            return related

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
            return [self._relationship_row_to_dict(row) for row in cursor.fetchall()]

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
            repo_filter = " WHERE status = 'active'"
            repo_params = []
            if repo_id:
                repo_filter += " AND repo_id = ?"
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

    def log_recall_event(
        self,
        memory_id: str,
        event_type: RecallEventType,
        repo_id: str = None,
        query: str = None,
        task_id: str = None,
        outcome: str = None,
        metadata: Dict[str, Any] = None,
    ) -> str:
        """Record a privacy-conscious recall utility event."""
        normalized_type = self._normalize_recall_event_type(event_type)
        memory = self._get_memory_row(memory_id, track_access=False)
        if not memory:
            raise ValueError(f"Memory not found: {memory_id}")

        event_id = self._generate_id(f"{memory_id}:{normalized_type}")
        event_repo_id = repo_id if repo_id is not None else memory.get("repo_id")
        with self._get_db() as conn:
            conn.execute(
                """
                INSERT INTO recall_events (
                    id, memory_id, event_type, repo_id, query_hash, task_id,
                    outcome, metadata, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    memory_id,
                    normalized_type,
                    event_repo_id,
                    self._hash_recall_query(query),
                    task_id,
                    outcome,
                    self._json_serialize(self._sanitize_recall_metadata(metadata)),
                    utc_now().isoformat(),
                ),
            )
            if normalized_type in REINFORCING_RECALL_EVENTS:
                # Retrieval-induced strengthening: a used memory becomes easier to
                # recall and resets its decay clock.
                conn.execute(
                    """
                    UPDATE memories
                    SET access_count = access_count + 1, accessed_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (memory_id,),
                )
            conn.commit()
        return event_id

    def inspect_recall_utility(
        self,
        memory_id: str = None,
        repo_id: str = None,
        event_type: str = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Return recall utility signals and recent sanitized events."""
        where, params = self._recall_event_filters(memory_id, repo_id, event_type)
        with self._get_db() as conn:
            count_cursor = conn.execute(
                f"""
                SELECT event_type, COUNT(*) AS count
                FROM recall_events
                {where}
                GROUP BY event_type
                """,
                params,
            )
            by_event_type = {row["event_type"]: row["count"] for row in count_cursor.fetchall()}

            signal_cursor = conn.execute(
                f"""
                SELECT memory_id, repo_id, event_type, COUNT(*) AS count,
                       MAX(created_at) AS last_event_at
                FROM recall_events
                {where}
                GROUP BY memory_id, repo_id, event_type
                ORDER BY MAX(created_at) DESC
                """,
                params,
            )
            signals_by_memory: dict[str, dict[str, Any]] = {}
            for row in signal_cursor.fetchall():
                signal = signals_by_memory.setdefault(
                    row["memory_id"],
                    {
                        "memory_id": row["memory_id"],
                        "repo_id": row["repo_id"],
                        "counts": {},
                        "total_events": 0,
                        "last_event_at": row["last_event_at"],
                    },
                )
                signal["counts"][row["event_type"]] = row["count"]
                signal["total_events"] += row["count"]
                signal["last_event_at"] = max(
                    signal["last_event_at"] or "", row["last_event_at"] or ""
                )

            events_cursor = conn.execute(
                f"""
                SELECT id, memory_id, event_type, repo_id, query_hash, task_id,
                       outcome, metadata, created_at
                FROM recall_events
                {where}
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                [*params, max(0, int(limit))],
            )
            events = [
                self._recall_event_row_to_dict(row) for row in events_cursor.fetchall()
            ]

        signals = []
        for signal in signals_by_memory.values():
            utility_score = self._recall_utility_score_from_counts(signal["counts"])
            signal["utility_score"] = utility_score
            signal["utility_rank_adjustment"] = utility_rank_adjustment(utility_score)
            signals.append(signal)

        signals.sort(
            key=lambda item: (
                item.get("utility_score", 0.0),
                item.get("last_event_at") or "",
            ),
            reverse=True,
        )
        return {
            "summary": {
                "total_events": sum(by_event_type.values()),
                "by_event_type": by_event_type,
                "memories": len(signals),
            },
            "signals": signals,
            "events": events,
        }

    def reset_recall_utility(
        self,
        memory_id: str = None,
        repo_id: str = None,
        event_type: str = None,
    ) -> int:
        """Delete recall utility events matching optional filters."""
        where, params = self._recall_event_filters(memory_id, repo_id, event_type)
        with self._get_db() as conn:
            cursor = conn.execute(f"DELETE FROM recall_events {where}", params)
            conn.commit()
            return cursor.rowcount

    def _attach_recall_utility_scores(self, memories: List[Dict[str, Any]]) -> None:
        """Attach aggregate recall utility scores to memory rows in-place."""
        memory_ids = [memory.get("id") for memory in memories if memory.get("id")]
        if not memory_ids:
            return

        placeholders = ", ".join("?" for _ in memory_ids)
        with self._get_db() as conn:
            cursor = conn.execute(
                f"""
                SELECT memory_id, event_type, COUNT(*) AS count
                FROM recall_events
                WHERE memory_id IN ({placeholders})
                GROUP BY memory_id, event_type
                """,
                memory_ids,
            )
            counts_by_memory: dict[str, dict[str, int]] = {}
            for row in cursor.fetchall():
                counts_by_memory.setdefault(row["memory_id"], {})[row["event_type"]] = row[
                    "count"
                ]

        for memory in memories:
            counts = counts_by_memory.get(memory.get("id"), {})
            utility_score = self._recall_utility_score_from_counts(counts)
            memory["utility_score"] = utility_score
            memory["utility_signal"] = {
                "counts": counts,
                "total_events": sum(counts.values()),
                "rank_adjustment": utility_rank_adjustment(utility_score),
            }

    @classmethod
    def _normalize_recall_event_type(cls, event_type: str) -> str:
        normalized = str(event_type or "").strip().lower().replace("-", "_")
        if normalized not in RECALL_EVENT_WEIGHTS:
            allowed = ", ".join(sorted(RECALL_EVENT_WEIGHTS))
            raise ValueError(f"Invalid recall event type: {event_type}. Expected one of: {allowed}")
        return normalized

    @classmethod
    def _recall_utility_score_from_counts(cls, counts: Dict[str, int]) -> float:
        score = 0.0
        for event_type, count in counts.items():
            score += RECALL_EVENT_WEIGHTS.get(event_type, 0.0) * int(count)
        return max(-1.0, min(1.0, score))

    @classmethod
    def _sanitize_recall_metadata(cls, metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        if not isinstance(metadata, dict):
            return {}

        safe: dict[str, Any] = {}
        for key, value in metadata.items():
            normalized_key = str(key).lower()
            if any(sensitive in normalized_key for sensitive in SENSITIVE_RECALL_METADATA_KEYS):
                continue
            if isinstance(value, str) and len(value) > 500:
                value = f"{value[:500]}..."
            safe[str(key)] = value
        return json.loads(json.dumps(safe, default=str))

    @classmethod
    def _hash_recall_query(cls, query: str = None) -> str | None:
        if not query:
            return None
        return hashlib.sha256(str(query).encode("utf-8")).hexdigest()

    @classmethod
    def _recall_event_filters(
        cls,
        memory_id: str = None,
        repo_id: str = None,
        event_type: str = None,
    ) -> tuple[str, list[Any]]:
        where = "WHERE 1=1"
        params: list[Any] = []
        if memory_id:
            where += " AND memory_id = ?"
            params.append(memory_id)
        if repo_id:
            where += " AND repo_id = ?"
            params.append(repo_id)
        if event_type:
            where += " AND event_type = ?"
            params.append(cls._normalize_recall_event_type(event_type))
        return where, params

    @classmethod
    def _recall_event_row_to_dict(cls, row: sqlite3.Row) -> Dict[str, Any]:
        event = dict(row)
        event["metadata"] = cls._json_deserialize(event.get("metadata") or "{}") or {}
        return event

    def list_project_ids(self) -> List[str]:
        """List distinct repository/project IDs referenced by stored data."""
        with self._get_db() as conn:
            cursor = conn.execute(
                """
                SELECT repo_id AS id FROM memories WHERE repo_id IS NOT NULL AND repo_id != ''
                UNION
                SELECT repo_id AS id FROM intents WHERE repo_id IS NOT NULL AND repo_id != ''
                UNION
                SELECT id FROM repositories WHERE id IS NOT NULL AND id != ''
                ORDER BY id
                """
            )
            return [row[0] for row in cursor.fetchall()]

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        """Convert SQLite row to dictionary."""
        d = dict(row)

        # Parse JSON fields
        for field in [
            "tags",
            "metadata",
            "source_ids",
            "quality_flags",
            "memory_ids",
            "context",
            "tech_stack",
        ]:
            if field in d and d[field]:
                d[field] = LocalStorage._json_deserialize(d[field])

        return d

    @classmethod
    def _normalize_relationship_evidence(
        cls,
        evidence: Dict[str, Any] = None,
        *,
        strength: float = None,
        created_at: str = None,
        legacy: bool = True,
    ) -> Dict[str, Any]:
        """Return relationship evidence with safe, contract-compatible defaults."""
        evidence = evidence or {}
        confidence = evidence.get("confidence") or "ambiguous"
        if confidence not in cls.RELATIONSHIP_CONFIDENCE_VALUES:
            raise ValueError(
                "Relationship confidence must be one of: "
                + ", ".join(sorted(cls.RELATIONSHIP_CONFIDENCE_VALUES))
            )

        score = evidence.get("confidence_score")
        if score is None:
            score = strength if strength is not None else 0.5

        default_reason = (
            cls.LEGACY_RELATIONSHIP_EVIDENCE_REASON
            if legacy
            else cls.UNSPECIFIED_RELATIONSHIP_EVIDENCE_REASON
        )

        return {
            "confidence": confidence,
            "confidence_score": clamp_score(score),
            "source": evidence.get("source") or ("legacy" if legacy else "unspecified"),
            "source_file": evidence.get("source_file"),
            "source_location": evidence.get("source_location"),
            "reason": evidence.get("reason") or default_reason,
            "created_by": evidence.get("created_by"),
            "created_at": evidence.get("created_at") or created_at,
        }

    @classmethod
    def _relationship_row_to_dict(cls, row: sqlite3.Row) -> Dict[str, Any]:
        """Convert a relationship row to its public shape with nested evidence."""
        d = dict(row)
        evidence_data = {
            "confidence": d.pop("confidence", None),
            "confidence_score": d.pop("confidence_score", None),
            "source": d.pop("source", None),
            "source_file": d.pop("source_file", None),
            "source_location": d.pop("source_location", None),
            "reason": d.pop("reason", None),
            "created_by": d.pop("created_by", None),
            "created_at": d.get("created_at"),
        }
        d["evidence"] = cls._normalize_relationship_evidence(
            evidence_data,
            strength=d.get("strength"),
            created_at=d.get("created_at"),
            legacy=evidence_data["source"] == "legacy",
        )
        return d

    def append_audit_log(
        self,
        event_type: str,
        actor_id: str = None,
        repo_id: str = None,
        target_type: str = None,
        target_id: str = None,
        metadata: Dict[str, Any] = None,
    ) -> str:
        """Append a non-secret audit event."""
        audit_id = self._generate_id(f"{event_type}:{target_id or ''}")
        with self._get_db() as conn:
            conn.execute(
                """
                INSERT INTO audit_logs (
                    id, event_type, actor_id, repo_id, target_type, target_id, metadata
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_id,
                    event_type,
                    actor_id,
                    repo_id,
                    target_type,
                    target_id,
                    self._json_serialize(metadata or {}),
                ),
            )
            conn.commit()
        return audit_id

    def list_audit_logs(
        self,
        actor_id: str = None,
        repo_id: str = None,
        event_type: str = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List audit log entries with optional filters."""
        query = "SELECT * FROM audit_logs WHERE 1=1"
        params: list[Any] = []
        if actor_id:
            query += " AND actor_id = ?"
            params.append(actor_id)
        if repo_id:
            query += " AND repo_id = ?"
            params.append(repo_id)
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        query += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        params.append(limit)

        with self._get_db() as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_dict(row) for row in cursor.fetchall()]

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
            try:
                conn.execute(
                    """
                    INSERT INTO users (id, username, email, display_name, metadata)
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
            except sqlite3.IntegrityError as e:
                raise ValueError(f"User already exists: {user_id}") from e
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
            try:
                conn.execute(
                    """
                    INSERT INTO teams (id, name, description, metadata)
                    VALUES (?, ?, ?, ?)
                """,
                    (
                        team_id,
                        team["name"],
                        team.get("description"),
                        self._json_serialize(team.get("metadata", {})),
                    ),
                )
            except sqlite3.IntegrityError as e:
                raise ValueError(f"Team already exists: {team_id}") from e
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
