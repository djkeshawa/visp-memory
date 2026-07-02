"""ArcadeDB storage backend."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from llm_memory.core.ranking import rank_memory_results, text_similarity, utility_rank_adjustment
from llm_memory.core.storage import (
    REINFORCING_RECALL_EVENTS,
    BaseStorage,
    LocalStorage,
    MemoryLayer,
    MemoryStatus,
)

logger = logging.getLogger(__name__)

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
    VERTEX_TYPES = [
        "Memory",
        "Intent",
        "Session",
        "Repository",
        "User",
        "Team",
        "AuditLog",
        "RecallFeedback",
    ]
    EDGE_TYPES = ["MemoryRelationship", "RepoDependency", "TeamMember"]
    MEMORY_RELATIONSHIP_EDGE = "MemoryRelationship"
    REPO_DEPENDENCY_EDGE = "RepoDependency"
    TEAM_MEMBER_EDGE = "TeamMember"
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
    RELATIONSHIP_FIELDS = [
        "id",
        "source_id",
        "target_id",
        "relationship",
        "strength",
        "confidence",
        "confidence_score",
        "source",
        "source_file",
        "source_location",
        "reason",
        "created_by",
        "created_at",
    ]
    INTENT_FIELDS = [
        "id",
        "description",
        "priority",
        "context",
        "repo_id",
        "status",
        "created_at",
        "updated_at",
    ]
    REPOSITORY_FIELDS = ["id", "name", "url", "description", "tech_stack", "team_id", "metadata"]
    USER_FIELDS = ["id", "username", "email", "display_name", "metadata"]
    TEAM_FIELDS = ["id", "name", "description", "metadata"]
    AUDIT_FIELDS = [
        "id",
        "event_type",
        "actor_id",
        "repo_id",
        "target_type",
        "target_id",
        "metadata",
        "created_at",
    ]
    RECALL_EVENT_FIELDS = [
        "id",
        "memory_id",
        "event_type",
        "repo_id",
        "query_hash",
        "task_id",
        "outcome",
        "metadata",
        "created_at",
    ]
    REPO_DEPENDENCY_FIELDS = [
        "id",
        "source_repo_id",
        "target_repo_id",
        "type",
        "version",
        "notes",
    ]
    TEAM_MEMBER_FIELDS = ["id", "team_id", "user_id"]
    RECORD_JSON_FIELDS = {
        "Intent": {"context"},
        "Repository": {"tech_stack", "metadata"},
        "User": {"metadata"},
        "Team": {"metadata"},
        "AuditLog": {"metadata"},
        "RecallFeedback": {"metadata"},
    }
    RECORD_FIELDS = {
        "Intent": INTENT_FIELDS,
        "Repository": REPOSITORY_FIELDS,
        "User": USER_FIELDS,
        "Team": TEAM_FIELDS,
        "AuditLog": AUDIT_FIELDS,
        "RecallFeedback": RECALL_EVENT_FIELDS,
    }

    def __init__(
        self,
        data_dir: Path,
        embedding_fn=None,
        embedding_dimension: int | None = None,
    ):
        self._arcadedb = load_arcadedb_driver()
        self.data_dir = Path(data_dir) / "arcadedb"
        self.data_dir.parent.mkdir(parents=True, exist_ok=True)
        self._embedding_fn = embedding_fn
        self._embedding_dimension = embedding_dimension
        # ArcadeDB search is lexical (keyword) only — embeddings are not indexed.
        # Surface that explicitly so an operator who configured a real vector
        # provider knows vector recall is unavailable on this backend.
        embedding_owner = getattr(embedding_fn, "__self__", None)
        owner_name = embedding_owner.__class__.__name__.lower() if embedding_owner else ""
        if embedding_fn is not None and owner_name != "noopprovider":
            logger.warning(
                "ArcadeDB backend performs lexical (keyword) search only; the configured "
                "embedding provider will not be used for vector recall."
            )
        self._init_schema()

    def _database(self):
        database_path = str(self.data_dir)
        if self._arcadedb.database_exists(database_path):
            return self._arcadedb.open_database(database_path)
        return self._arcadedb.create_database(database_path)

    def _init_schema(self) -> None:
        with self._database() as db:
            with db.transaction():
                for vertex_type in self.VERTEX_TYPES:
                    db.command("sql", f"CREATE VERTEX TYPE {vertex_type} IF NOT EXISTS")
                for edge_type in self.EDGE_TYPES:
                    db.command("sql", f"CREATE EDGE TYPE {edge_type} IF NOT EXISTS")

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

    @classmethod
    def _record_to_dict(
        cls, record, fields: List[str], json_fields: set[str] = None
    ) -> Dict[str, Any]:
        json_fields = json_fields or set()
        item = {
            field: cls._record_get(record, field)
            for field in fields
            if cls._record_get(record, field) is not None
        }
        for field in json_fields:
            if field in item:
                parsed = cls._json_deserialize(item[field])
                if parsed is None:
                    parsed = [] if field == "tech_stack" else {}
                item[field] = parsed
        return item

    @classmethod
    def _relationship_record_to_dict(cls, record) -> Dict[str, Any]:
        relationship = {
            field: cls._record_get(record, field)
            for field in cls.RELATIONSHIP_FIELDS
            if cls._record_get(record, field) is not None
        }
        evidence_data = {
            "confidence": relationship.pop("confidence", None),
            "confidence_score": relationship.pop("confidence_score", None),
            "source": relationship.pop("source", None),
            "source_file": relationship.pop("source_file", None),
            "source_location": relationship.pop("source_location", None),
            "reason": relationship.pop("reason", None),
            "created_by": relationship.pop("created_by", None),
            "created_at": relationship.get("created_at"),
        }
        relationship["evidence"] = LocalStorage._normalize_relationship_evidence(
            evidence_data,
            strength=relationship.get("strength"),
            created_at=relationship.get("created_at"),
            legacy=evidence_data["source"] == "legacy",
        )
        return relationship

    def _query_memory(self, memory_id: str):
        with self._database() as db:
            rows = self._rows(
                db.query("sql", f"SELECT FROM {self.MEMORY_TYPE} WHERE id = ?", memory_id)
            )
            return self._memory_record_to_dict(rows[0]) if rows else None

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

    def _insert_record(
        self,
        type_name: str,
        record: Dict[str, Any],
        fields: List[str],
        json_fields: set[str] = None,
    ) -> None:
        json_fields = json_fields or set()
        field_names = [field for field in fields if field in record]
        assignments = ", ".join(f"{field} = ?" for field in field_names)
        values = [
            self._json_serialize(record[field]) if field in json_fields else record[field]
            for field in field_names
        ]
        with self._database() as db:
            with db.transaction():
                db.command("sql", f"INSERT INTO {type_name} SET {assignments}", *values)

    def _update_record(
        self,
        type_name: str,
        record_id: str,
        updates: Dict[str, Any],
        json_fields: set[str] = None,
    ) -> bool:
        if self._get_record(type_name, record_id) is None:
            return False
        json_fields = json_fields or set()
        field_names = [field for field, value in updates.items() if value is not None]
        if not field_names:
            return False
        assignments = ", ".join(f"{field} = ?" for field in field_names)
        values = [
            self._json_serialize(updates[field]) if field in json_fields else updates[field]
            for field in field_names
        ]
        with self._database() as db:
            with db.transaction():
                db.command(
                    "sql",
                    f"UPDATE {type_name} SET {assignments} WHERE id = ?",
                    *values,
                    record_id,
                )
        return True

    def _get_record(self, type_name: str, record_id: str):
        with self._database() as db:
            rows = self._rows(db.query("sql", f"SELECT FROM {type_name} WHERE id = ?", record_id))
            if not rows:
                return None
            return self._record_to_dict(
                rows[0],
                self.RECORD_FIELDS.get(type_name, ["id"]),
                self.RECORD_JSON_FIELDS.get(type_name, set()),
            )

    def _list_records(
        self,
        type_name: str,
        fields: List[str],
        *,
        json_fields: set[str] = None,
        filters: Dict[str, Any] = None,
        limit: int = 100000,
        order_by: str = "created_at DESC",
    ) -> List[Dict[str, Any]]:
        query = f"SELECT FROM {type_name}"
        params = []
        conditions = []
        for field, value in (filters or {}).items():
            if value is not None:
                conditions.append(f"{field} = ?")
                params.append(value)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += f" ORDER BY {order_by} LIMIT ?"
        params.append(limit)
        with self._database() as db:
            rows = self._rows(db.query("sql", query, *params))
            return [self._record_to_dict(row, fields, json_fields or set()) for row in rows]

    def _delete_records(self, type_name: str, filters: Dict[str, Any]) -> int:
        records = self._list_records(type_name, ["id"], filters=filters, order_by="id ASC")
        with self._database() as db:
            with db.transaction():
                for record in records:
                    db.command("sql", f"DELETE FROM {type_name} WHERE id = ?", record["id"])
        return len(records)

    def _create_edge(
        self,
        edge_type: str,
        source_type: str,
        source_id: str,
        target_type: str,
        target_id: str,
        record: Dict[str, Any],
        fields: List[str],
    ) -> None:
        field_names = [field for field in fields if field in record]
        assignments = ", ".join(f"{field} = ?" for field in field_names)
        values = [record[field] for field in field_names]
        with self._database() as db:
            with db.transaction():
                db.command(
                    "sql",
                    f"""
                    CREATE EDGE {edge_type}
                    FROM (SELECT FROM {source_type} WHERE id = ?)
                    TO (SELECT FROM {target_type} WHERE id = ?)
                    SET {assignments}
                    """,
                    source_id,
                    target_id,
                    *values,
                )

    def _list_edge_records(self, edge_type: str, fields: List[str]) -> List[Dict[str, Any]]:
        with self._database() as db:
            rows = self._rows(db.query("sql", f"SELECT FROM {edge_type}"))
            return [self._record_to_dict(row, fields) for row in rows]

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

        # ``_query_memory`` already returns a normalized dict; reuse it directly.
        memory = record
        # Increment in SQL (not read-modify-write) so concurrent reads cannot lose an
        # update, matching ``log_recall_event`` and the SQLite backend.
        accessed_at = datetime.now().isoformat()
        with self._database() as db:
            with db.transaction():
                db.command(
                    "sql",
                    f"UPDATE {self.MEMORY_TYPE} "
                    "SET access_count = access_count + 1, accessed_at = ? WHERE id = ?",
                    accessed_at,
                    memory_id,
                )

        # Reflect the post-increment state in the returned dict (best-effort; the
        # authoritative count lives in the store).
        memory["access_count"] = int(memory.get("access_count") or 0) + 1
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
        # When no layer is requested, exclude the 'raw' layer from search results
        # (canonical SQLite behavior: search only episodic/semantic/intent). An explicit
        # ``layer='raw'`` request is still honored. list_memories keeps all layers.
        exclude_raw = layer is None
        terms = [term.lower() for term in query.split() if term.strip()]
        results = []
        for memory in candidates:
            if exclude_raw and memory.get("layer") == "raw":
                continue
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
        intent_id = self._generate_id(description)
        now = datetime.now().isoformat()
        self._insert_record(
            "Intent",
            {
                "id": intent_id,
                "description": description,
                "priority": priority,
                "context": context or {},
                "repo_id": repo_id,
                "status": "active",
                "created_at": now,
                "updated_at": now,
            },
            self.INTENT_FIELDS,
            self.RECORD_JSON_FIELDS["Intent"],
        )
        return intent_id

    def get_active_intents(
        self, repo_id: str = None, status: str = "active"
    ) -> List[Dict[str, Any]]:
        filters = {}
        if status and status != "all":
            filters["status"] = status
        if repo_id:
            filters["repo_id"] = repo_id
        intents = self._list_records(
            "Intent",
            self.INTENT_FIELDS,
            json_fields=self.RECORD_JSON_FIELDS["Intent"],
            filters=filters,
            order_by="priority DESC",
        )
        intents.sort(
            key=lambda item: (item.get("priority") or 0, item.get("created_at") or ""),
            reverse=True,
        )
        return intents

    def complete_intent(self, intent_id: str) -> bool:
        return self.update_intent(intent_id, status="completed")

    def update_intent(self, intent_id: str, **kwargs) -> bool:
        updates = {
            key: kwargs[key]
            for key in ("description", "priority", "status", "context")
            if key in kwargs and kwargs[key] is not None
        }
        if updates:
            updates["updated_at"] = datetime.now().isoformat()
        return self._update_record(
            "Intent",
            intent_id,
            updates,
            json_fields=self.RECORD_JSON_FIELDS["Intent"],
        )

    def add_relationship(
        self,
        source_id: str,
        target_id: str,
        relationship: str,
        strength: float = 1.0,
        evidence: Dict[str, Any] = None,
    ) -> str:
        source = self._query_memory(source_id)
        target = self._query_memory(target_id)
        if source is None or target is None:
            raise ValueError("Relationship source and target memories must both exist")
        source_memory = self._memory_record_to_dict(source)
        target_memory = self._memory_record_to_dict(target)
        if source_memory.get("repo_id") != target_memory.get("repo_id"):
            raise ValueError("Memory relationships cannot cross repository boundaries")

        relationship_id = self._generate_id(f"{source_id}-{target_id}-{relationship}")
        created_at = datetime.now().isoformat()
        evidence_data = LocalStorage._normalize_relationship_evidence(
            evidence,
            strength=strength,
            created_at=created_at,
            legacy=False,
        )

        with self._database() as db:
            with db.transaction():
                db.command(
                    "sql",
                    f"""
                    CREATE EDGE {self.MEMORY_RELATIONSHIP_EDGE}
                    FROM (SELECT FROM {self.MEMORY_TYPE} WHERE id = ?)
                    TO (SELECT FROM {self.MEMORY_TYPE} WHERE id = ?)
                    SET id = ?, source_id = ?, target_id = ?, relationship = ?,
                    strength = ?, confidence = ?, confidence_score = ?, source = ?,
                    source_file = ?, source_location = ?, reason = ?, created_by = ?,
                    created_at = ?
                    """,
                    source_id,
                    target_id,
                    relationship_id,
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
                    evidence_data["created_at"],
                )

        return relationship_id

    def get_related_memories(
        self, memory_id: str, relationship: str = None
    ) -> List[Dict[str, Any]]:
        source = self._query_memory(memory_id)
        if source is None:
            return []
        source_memory = self._memory_record_to_dict(source)
        related = []
        seen_ids = set()
        for edge in self.get_all_relationships(repo_id=source_memory.get("repo_id")):
            if relationship and edge.get("relationship") != relationship:
                continue
            if edge.get("source_id") == memory_id:
                related_id = edge.get("target_id")
            elif edge.get("target_id") == memory_id:
                related_id = edge.get("source_id")
            else:
                continue
            if not related_id or related_id in seen_ids:
                continue
            record = self._query_memory(related_id)
            if record is None:
                continue
            memory = self._memory_record_to_dict(record)
            memory["relationship"] = edge.get("relationship")
            memory["strength"] = edge.get("strength")
            memory["relationship_evidence"] = edge.get("evidence")
            related.append(memory)
            seen_ids.add(related_id)
        return related

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
        with self._database() as db:
            rows = self._rows(db.query("sql", f"SELECT FROM {self.MEMORY_RELATIONSHIP_EDGE}"))
            relationships = [self._relationship_record_to_dict(row) for row in rows]
        if not repo_id:
            return relationships

        filtered = []
        for relationship in relationships:
            source = self._query_memory(relationship.get("source_id"))
            target = self._query_memory(relationship.get("target_id"))
            if source is None or target is None:
                continue
            source_memory = self._memory_record_to_dict(source)
            target_memory = self._memory_record_to_dict(target)
            if source_memory.get("repo_id") == repo_id and target_memory.get("repo_id") == repo_id:
                filtered.append(relationship)
        return filtered

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
            "active_intents": len(self.get_active_intents(repo_id=repo_id)),
            "total_relationships": len(self.get_all_relationships(repo_id=repo_id)),
        }

    def store_repository(self, repo: Dict[str, Any]) -> str:
        repo_id = repo.get("id") or self._generate_id(repo["name"])
        if self.get_repository(repo_id) is not None:
            raise ValueError(f"Repository already exists: {repo_id}")
        self._insert_record(
            "Repository",
            {
                "id": repo_id,
                "name": repo["name"],
                "url": repo.get("url"),
                "description": repo.get("description"),
                "tech_stack": repo.get("tech_stack", []),
                "team_id": repo.get("team_id"),
                "metadata": repo.get("metadata", {}),
            },
            self.REPOSITORY_FIELDS,
            self.RECORD_JSON_FIELDS["Repository"],
        )
        return repo_id

    def get_repository(self, repo_id: str) -> Optional[Dict[str, Any]]:
        record = self._get_record("Repository", repo_id)
        if record is None:
            return None
        return self._record_to_dict(
            record,
            self.REPOSITORY_FIELDS,
            self.RECORD_JSON_FIELDS["Repository"],
        )

    def list_repositories(self, team_id: str = None) -> List[Dict[str, Any]]:
        filters = {"team_id": team_id} if team_id else None
        return self._list_records(
            "Repository",
            self.REPOSITORY_FIELDS,
            json_fields=self.RECORD_JSON_FIELDS["Repository"],
            filters=filters,
            order_by="name ASC",
        )

    def list_project_ids(self) -> List[str]:
        memories = self.list_memories(status="all", limit=100000)
        repo_ids = {
            str(memory["repo_id"]) for memory in memories if memory.get("repo_id") not in (None, "")
        }
        repo_ids.update(
            intent["repo_id"]
            for intent in self.get_active_intents(status="all")
            if intent.get("repo_id") not in (None, "")
        )
        repo_ids.update(repo["id"] for repo in self.list_repositories() if repo.get("id"))
        return sorted(repo_ids)

    def add_repo_dependency(
        self, source_id: str, target_id: str, dep_type: str, version: str = None, notes: str = None
    ) -> str:
        if self.get_repository(source_id) is None:
            raise ValueError(f"Repository not found: {source_id}")
        if self.get_repository(target_id) is None:
            raise ValueError(f"Repository not found: {target_id}")
        dependency_id = self._generate_id(f"{source_id}-{target_id}-{dep_type}")
        self._create_edge(
            self.REPO_DEPENDENCY_EDGE,
            "Repository",
            source_id,
            "Repository",
            target_id,
            {
                "id": dependency_id,
                "source_repo_id": source_id,
                "target_repo_id": target_id,
                "type": dep_type,
                "version": version,
                "notes": notes,
            },
            self.REPO_DEPENDENCY_FIELDS,
        )
        return dependency_id

    def get_repo_dependencies(self, repo_id: str) -> List[Dict[str, Any]]:
        return [
            {
                "target_id": dependency.get("target_repo_id"),
                "type": dependency.get("type"),
                "version": dependency.get("version"),
                "notes": dependency.get("notes"),
            }
            for dependency in self._list_edge_records(
                self.REPO_DEPENDENCY_EDGE, self.REPO_DEPENDENCY_FIELDS
            )
            if dependency.get("source_repo_id") == repo_id
        ]

    def store_user(self, user: Dict[str, Any]) -> str:
        user_id = user["id"]
        if self.get_user(user_id) is not None:
            raise ValueError(f"User already exists: {user_id}")
        self._insert_record(
            "User",
            {
                "id": user_id,
                "username": user["username"],
                "email": user.get("email"),
                "display_name": user.get("display_name"),
                "metadata": user.get("metadata", {}),
            },
            self.USER_FIELDS,
            self.RECORD_JSON_FIELDS["User"],
        )
        return user_id

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        record = self._get_record("User", user_id)
        if record is None:
            return None
        return self._record_to_dict(record, self.USER_FIELDS, self.RECORD_JSON_FIELDS["User"])

    def store_team(self, team: Dict[str, Any]) -> str:
        team_id = team["id"]
        if self.get_team(team_id) is not None:
            raise ValueError(f"Team already exists: {team_id}")
        self._insert_record(
            "Team",
            {
                "id": team_id,
                "name": team["name"],
                "description": team.get("description"),
                "metadata": team.get("metadata", {}),
            },
            self.TEAM_FIELDS,
            self.RECORD_JSON_FIELDS["Team"],
        )
        return team_id

    def get_team(self, team_id: str) -> Optional[Dict[str, Any]]:
        record = self._get_record("Team", team_id)
        if record is None:
            return None
        return self._record_to_dict(record, self.TEAM_FIELDS, self.RECORD_JSON_FIELDS["Team"])

    def add_team_member(self, team_id: str, user_id: str) -> bool:
        if self.get_team(team_id) is None or self.get_user(user_id) is None:
            return False
        membership_id = self._generate_id(f"{team_id}-{user_id}")
        self._create_edge(
            self.TEAM_MEMBER_EDGE,
            "Team",
            team_id,
            "User",
            user_id,
            {"id": membership_id, "team_id": team_id, "user_id": user_id},
            self.TEAM_MEMBER_FIELDS,
        )
        return True

    def get_user_teams(self, user_id: str) -> List[Dict[str, Any]]:
        teams = []
        seen_ids = set()
        for membership in self._list_edge_records(self.TEAM_MEMBER_EDGE, self.TEAM_MEMBER_FIELDS):
            if membership.get("user_id") != user_id:
                continue
            team_id = membership.get("team_id")
            if not team_id or team_id in seen_ids:
                continue
            team = self.get_team(team_id)
            if team is not None:
                teams.append(team)
                seen_ids.add(team_id)
        return teams

    def append_audit_log(
        self,
        event_type: str,
        actor_id: str = None,
        repo_id: str = None,
        target_type: str = None,
        target_id: str = None,
        metadata: Dict[str, Any] = None,
    ) -> str:
        audit_id = self._generate_id(f"{event_type}:{target_id or ''}")
        self._insert_record(
            "AuditLog",
            {
                "id": audit_id,
                "event_type": event_type,
                "actor_id": actor_id,
                "repo_id": repo_id,
                "target_type": target_type,
                "target_id": target_id,
                "metadata": metadata or {},
                "created_at": datetime.now().isoformat(),
            },
            self.AUDIT_FIELDS,
            self.RECORD_JSON_FIELDS["AuditLog"],
        )
        return audit_id

    def list_audit_logs(
        self,
        actor_id: str = None,
        repo_id: str = None,
        event_type: str = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        return self._list_records(
            "AuditLog",
            self.AUDIT_FIELDS,
            json_fields=self.RECORD_JSON_FIELDS["AuditLog"],
            filters={"actor_id": actor_id, "repo_id": repo_id, "event_type": event_type},
            limit=limit,
            order_by="created_at DESC",
        )

    def log_recall_event(
        self,
        memory_id: str,
        event_type: str,
        repo_id: str = None,
        query: str = None,
        task_id: str = None,
        outcome: str = None,
        metadata: Dict[str, Any] = None,
    ) -> str:
        memory = self._query_memory(memory_id)
        if memory is None:
            raise ValueError(f"Memory not found: {memory_id}")
        memory_data = self._memory_record_to_dict(memory)
        normalized_type = LocalStorage._normalize_recall_event_type(event_type)
        event_id = self._generate_id(f"{memory_id}:{normalized_type}")
        self._insert_record(
            "RecallFeedback",
            {
                "id": event_id,
                "memory_id": memory_id,
                "event_type": normalized_type,
                "repo_id": repo_id if repo_id is not None else memory_data.get("repo_id"),
                "query_hash": LocalStorage._hash_recall_query(query),
                "task_id": task_id,
                "outcome": outcome,
                "metadata": LocalStorage._sanitize_recall_metadata(metadata),
                "created_at": datetime.now().isoformat(),
            },
            self.RECALL_EVENT_FIELDS,
            self.RECORD_JSON_FIELDS["RecallFeedback"],
        )
        # Retrieval-induced strengthening, kept in parity with LocalStorage so the
        # activation boost and spaced-repetition decay behave the same across backends.
        # Increment in SQL (not read-modify-write) so concurrent use events cannot lose
        # an update, matching SQLite's `access_count = access_count + 1`.
        if normalized_type in REINFORCING_RECALL_EVENTS:
            with self._database() as db:
                with db.transaction():
                    db.command(
                        "sql",
                        f"UPDATE {self.MEMORY_TYPE} "
                        "SET access_count = access_count + 1, accessed_at = ? WHERE id = ?",
                        datetime.now().isoformat(),
                        memory_id,
                    )
        return event_id

    def inspect_recall_utility(
        self,
        memory_id: str = None,
        repo_id: str = None,
        event_type: str = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        filters = {
            "memory_id": memory_id,
            "repo_id": repo_id,
            "event_type": LocalStorage._normalize_recall_event_type(event_type)
            if event_type
            else None,
        }
        events = self._list_records(
            "RecallFeedback",
            self.RECALL_EVENT_FIELDS,
            json_fields=self.RECORD_JSON_FIELDS["RecallFeedback"],
            filters=filters,
            limit=limit,
            order_by="created_at DESC",
        )
        all_events = self._list_records(
            "RecallFeedback",
            self.RECALL_EVENT_FIELDS,
            json_fields=self.RECORD_JSON_FIELDS["RecallFeedback"],
            filters=filters,
            limit=100000,
            order_by="created_at DESC",
        )
        by_event_type: dict[str, int] = {}
        signals_by_memory: dict[str, dict[str, Any]] = {}
        for event in all_events:
            event_type_value = event.get("event_type")
            by_event_type[event_type_value] = by_event_type.get(event_type_value, 0) + 1
            signal = signals_by_memory.setdefault(
                event["memory_id"],
                {
                    "memory_id": event["memory_id"],
                    "repo_id": event.get("repo_id"),
                    "counts": {},
                    "total_events": 0,
                    "last_event_at": event.get("created_at"),
                },
            )
            signal["counts"][event_type_value] = signal["counts"].get(event_type_value, 0) + 1
            signal["total_events"] += 1
            signal["last_event_at"] = max(
                signal.get("last_event_at") or "", event.get("created_at") or ""
            )

        signals = []
        for signal in signals_by_memory.values():
            utility_score = LocalStorage._recall_utility_score_from_counts(signal["counts"])
            signal["utility_score"] = utility_score
            signal["utility_rank_adjustment"] = utility_rank_adjustment(utility_score)
            signals.append(signal)
        signals.sort(
            key=lambda item: (item.get("utility_score", 0.0), item.get("last_event_at") or ""),
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
        filters = {
            "memory_id": memory_id,
            "repo_id": repo_id,
            "event_type": LocalStorage._normalize_recall_event_type(event_type)
            if event_type
            else None,
        }
        return self._delete_records("RecallFeedback", filters)


__all__ = [
    "ARCADEDB_INSTALL_MESSAGE",
    "ArcadeDbDependencyError",
    "ArcadeDbStorage",
    "load_arcadedb_driver",
]
