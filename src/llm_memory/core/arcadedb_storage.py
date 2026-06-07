"""ArcadeDB storage backend."""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from llm_memory.core.ranking import rank_memory_results, text_similarity
from llm_memory.core.storage import BaseStorage, LocalStorage, MemoryLayer, MemoryStatus

ARCADEDB_INSTALL_MESSAGE = (
    "ArcadeDB storage requires the optional ArcadeDB extra. "
    'Install it with: pip install "llm-memory[arcadedb]"'
)


class ArcadeDbDependencyError(ImportError):
    """Raised when ArcadeDB storage is selected without the optional extra."""


def load_arcadedb_driver():
    """Load the optional ArcadeDB embedded driver."""
    try:
        import arcadedb_embedded as arcadedb
    except ImportError as exc:
        raise ArcadeDbDependencyError(ARCADEDB_INSTALL_MESSAGE) from exc
    return arcadedb


class ArcadeDbStorage(BaseStorage):
    """ArcadeDB-backed storage."""

    MEMORY_TYPE = "Memory"
    SESSION_TYPE = "Session"
    MEMORY_JSON_FIELDS = {"tags", "metadata", "source_ids", "quality_flags"}
    MEMORY_FIELDS = [
        "id",
        "content",
        "layer",
        "repo_id",
        "category",
        "importance",
        "tags",
        "metadata",
        "source_ids",
        "status",
        "source",
        "quality_flags",
        "created_at",
        "accessed_at",
        "access_count",
        "approved_by",
        "approved_at",
        "archived_at",
    ]

    def __init__(
        self,
        data_dir: Path,
        embedding_fn=None,
        embedding_dimension: int | None = None,
    ):
        self._arcadedb = load_arcadedb_driver()
        self.data_dir = Path(data_dir) / "arcadedb"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._embedding_fn = embedding_fn
        self._embedding_dimension = embedding_dimension
        self._init_schema()

    def _database(self):
        return self._arcadedb.create_database(str(self.data_dir))

    def _init_schema(self) -> None:
        with self._database() as db:
            with db.transaction():
                db.command("sql", f"CREATE VERTEX TYPE {self.MEMORY_TYPE} IF NOT EXISTS")
                db.command("sql", f"CREATE DOCUMENT TYPE {self.SESSION_TYPE} IF NOT EXISTS")

    @staticmethod
    def _json_serialize(data: Any) -> str:
        return LocalStorage._json_serialize(data)

    @staticmethod
    def _json_deserialize(data: str) -> Any:
        return LocalStorage._json_deserialize(data)

    @staticmethod
    def _generate_id(content: str) -> str:
        return LocalStorage._generate_id(content)

    @staticmethod
    def _rows(result) -> List[Any]:
        if result is None:
            return []
        return list(result)

    @staticmethod
    def _record_get(record, field: str, default=None):
        if isinstance(record, dict):
            return record.get(field, default)
        getter = getattr(record, "get", None)
        if getter is not None:
            try:
                return getter(field)
            except TypeError:
                return getter(field, default)
        return getattr(record, field, default)

    @classmethod
    def _memory_record_to_dict(cls, record) -> Dict[str, Any]:
        memory = {
            field: cls._record_get(record, field)
            for field in cls.MEMORY_FIELDS
            if cls._record_get(record, field) is not None
        }
        for field in cls.MEMORY_JSON_FIELDS:
            if field in memory:
                parsed = cls._json_deserialize(memory[field])
                if parsed is None:
                    parsed = {} if field == "metadata" else []
                memory[field] = parsed
        memory.setdefault("tags", [])
        memory.setdefault("metadata", {})
        memory.setdefault("source_ids", [])
        memory.setdefault("quality_flags", [])
        memory.setdefault("access_count", 0)
        return memory

    def _query_memory(self, memory_id: str):
        with self._database() as db:
            rows = self._rows(
                db.query("sql", f"SELECT FROM {self.MEMORY_TYPE} WHERE id = ?", memory_id)
            )
        return rows[0] if rows else None

    def _query_memories(
        self,
        *,
        layer: MemoryLayer = None,
        repo_id: str = None,
        category: str = None,
        status: str = "active",
        limit: int = 50,
        order_by: str = "created_at DESC",
    ) -> List[Dict[str, Any]]:
        query = f"SELECT FROM {self.MEMORY_TYPE}"
        conditions = []
        params: list[Any] = []

        if layer:
            conditions.append("layer = ?")
            params.append(layer)
        if repo_id:
            conditions.append("repo_id = ?")
            params.append(repo_id)
        if category:
            conditions.append("category = ?")
            params.append(category)
        if status and status != "all":
            conditions.append("status = ?")
            params.append(status)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

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

        with self._database() as db:
            rows = self._rows(db.query("sql", query, *params))
        return [self._memory_record_to_dict(row) for row in rows]

    def _not_implemented(self):
        raise NotImplementedError(
            "This ArcadeDB storage operation is implemented in a later ArcadeDB feature task."
        )

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
        auto_link_limit: int = 3,
        auto_link_min_score: float = 0.53,
    ) -> str:
        memory_id = self._generate_id(content)
        tags = tags or []
        metadata = metadata or {}
        source_ids = source_ids or []
        quality_flags = quality_flags or []
        now = datetime.now().isoformat()

        with self._database() as db:
            with db.transaction():
                db.command(
                    "sql",
                    f"""
                    INSERT INTO {self.MEMORY_TYPE} SET
                    id = ?, content = ?, layer = ?, repo_id = ?, category = ?,
                    importance = ?, tags = ?, metadata = ?, source_ids = ?,
                    status = ?, source = ?, quality_flags = ?, created_at = ?,
                    accessed_at = ?, access_count = ?
                    """,
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
                    now,
                    now,
                    0,
                )

        return memory_id

    def get_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        record = self._query_memory(memory_id)
        if record is None:
            return None

        memory = self._memory_record_to_dict(record)
        access_count = int(memory.get("access_count") or 0) + 1
        accessed_at = datetime.now().isoformat()
        with self._database() as db:
            with db.transaction():
                db.command(
                    "sql",
                    f"UPDATE {self.MEMORY_TYPE} SET access_count = ?, accessed_at = ? WHERE id = ?",
                    access_count,
                    accessed_at,
                    memory_id,
                )

        memory["access_count"] = access_count
        memory["accessed_at"] = accessed_at
        return memory

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
        candidates = self._query_memories(
            layer=layer,
            repo_id=repo_id,
            category=category,
            status=status,
            limit=max(limit * 4, 50),
            order_by="importance DESC",
        )
        terms = [term.lower() for term in query.split() if term.strip()]
        results = []
        for memory in candidates:
            if float(memory.get("importance") or 0.0) < min_importance:
                continue
            content = str(memory.get("content", ""))
            if terms and not any(term in content.lower() for term in terms):
                continue
            memory["similarity"] = text_similarity(query, content)
            results.append(memory)

        return rank_memory_results(results, query=query, limit=limit)

    def list_memories(
        self,
        layer: MemoryLayer = None,
        repo_id: str = None,
        category: str = None,
        status: str = "active",
        limit: int = 50,
        order_by: str = "created_at DESC",
    ) -> List[Dict[str, Any]]:
        return self._query_memories(
            layer=layer,
            repo_id=repo_id,
            category=category,
            status=status,
            limit=limit,
            order_by=order_by,
        )

    def update_memory(self, memory_id: str, **kwargs) -> bool:
        if self._query_memory(memory_id) is None:
            return False

        allowed_fields = {
            "content",
            "importance",
            "tags",
            "metadata",
            "status",
            "approved_by",
            "approved_at",
            "archived_at",
            "source",
            "quality_flags",
        }
        updates = []
        params: list[Any] = []
        for field in allowed_fields:
            if field not in kwargs or kwargs[field] is None:
                continue
            value = kwargs[field]
            if field in self.MEMORY_JSON_FIELDS:
                value = self._json_serialize(value)
            updates.append(f"{field} = ?")
            params.append(value)

        if kwargs.get("status") == "archived" and "archived_at" not in kwargs:
            updates.append("archived_at = ?")
            params.append(datetime.now().isoformat())
        elif kwargs.get("status") == "active" and "archived_at" not in kwargs:
            updates.append("archived_at = ?")
            params.append(None)

        if not updates:
            return False

        params.append(memory_id)
        with self._database() as db:
            with db.transaction():
                db.command(
                    "sql",
                    f"UPDATE {self.MEMORY_TYPE} SET {', '.join(updates)} WHERE id = ?",
                    *params,
                )
        return True

    def delete_memory(self, memory_id: str) -> bool:
        if self._query_memory(memory_id) is None:
            return False
        with self._database() as db:
            with db.transaction():
                db.command("sql", f"DELETE FROM {self.MEMORY_TYPE} WHERE id = ?", memory_id)
        return True

    def get_collection(self, layer: str):
        return None

    def set_intent(
        self,
        description: str,
        priority: int = 0,
        context: Dict[str, Any] = None,
        repo_id: str = None,
    ) -> str:
        self._not_implemented()

    def get_active_intents(
        self, repo_id: str = None, status: str = "active"
    ) -> List[Dict[str, Any]]:
        self._not_implemented()

    def complete_intent(self, intent_id: str) -> bool:
        self._not_implemented()

    def update_intent(self, intent_id: str, **kwargs) -> bool:
        self._not_implemented()

    def add_relationship(
        self, source_id: str, target_id: str, relationship: str, strength: float = 1.0
    ) -> str:
        self._not_implemented()

    def get_related_memories(
        self, memory_id: str, relationship: str = None
    ) -> List[Dict[str, Any]]:
        self._not_implemented()

    def start_session(self) -> str:
        session_id = self._generate_id("session")
        now = datetime.now().isoformat()
        with self._database() as db:
            with db.transaction():
                db.command(
                    "sql",
                    f"INSERT INTO {self.SESSION_TYPE} SET id = ?, started_at = ?",
                    session_id,
                    now,
                )
        return session_id

    def end_session(self, session_id: str, summary: str, memory_ids: List[str]):
        ended_at = datetime.now().isoformat()
        with self._database() as db:
            with db.transaction():
                db.command(
                    "sql",
                    f"""
                    UPDATE {self.SESSION_TYPE}
                    SET summary = ?, memory_ids = ?, ended_at = ?
                    WHERE id = ?
                    """,
                    summary,
                    self._json_serialize(memory_ids),
                    ended_at,
                    session_id,
                )

    def get_all_relationships(self, repo_id: str = None) -> List[Dict[str, Any]]:
        self._not_implemented()

    def get_stats(self, repo_id: str = None) -> Dict[str, Any]:
        memories = self.list_memories(repo_id=repo_id, status="active", limit=100000)
        by_layer: dict[str, int] = {}
        by_category: dict[str, int] = {}
        for memory in memories:
            layer = str(memory.get("layer") or "episodic")
            category = str(memory.get("category") or "general")
            by_layer[layer] = by_layer.get(layer, 0) + 1
            by_category[category] = by_category.get(category, 0) + 1

        return {
            "memories_by_layer": by_layer,
            "memories_by_category": by_category,
            "total_memories": len(memories),
            "active_intents": 0,
            "total_relationships": 0,
        }

    def store_repository(self, repo: Dict[str, Any]) -> str:
        self._not_implemented()

    def get_repository(self, repo_id: str) -> Optional[Dict[str, Any]]:
        self._not_implemented()

    def list_repositories(self, team_id: str = None) -> List[Dict[str, Any]]:
        self._not_implemented()

    def list_project_ids(self) -> List[str]:
        memories = self.list_memories(status="all", limit=100000)
        return sorted(
            {
                str(memory["repo_id"])
                for memory in memories
                if memory.get("repo_id") not in (None, "")
            }
        )

    def add_repo_dependency(
        self, source_id: str, target_id: str, dep_type: str, version: str = None, notes: str = None
    ) -> str:
        self._not_implemented()

    def get_repo_dependencies(self, repo_id: str) -> List[Dict[str, Any]]:
        self._not_implemented()

    def store_user(self, user: Dict[str, Any]) -> str:
        self._not_implemented()

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        self._not_implemented()

    def store_team(self, team: Dict[str, Any]) -> str:
        self._not_implemented()

    def get_team(self, team_id: str) -> Optional[Dict[str, Any]]:
        self._not_implemented()

    def add_team_member(self, team_id: str, user_id: str) -> bool:
        self._not_implemented()

    def get_user_teams(self, user_id: str) -> List[Dict[str, Any]]:
        self._not_implemented()


__all__ = [
    "ARCADEDB_INSTALL_MESSAGE",
    "ArcadeDbDependencyError",
    "ArcadeDbStorage",
    "load_arcadedb_driver",
]
