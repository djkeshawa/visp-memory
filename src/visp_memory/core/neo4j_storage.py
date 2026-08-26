"""
Neo4j Storage implementation for Visp Memory.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

try:
    from neo4j import GraphDatabase
except ImportError:  # pragma: no cover - exercised only when optional extra is absent
    GraphDatabase = None

from visp_memory.config import load_config
from visp_memory.core.indexing import EmbeddingIndexReport, ReindexResult, ReindexScope
from visp_memory.core.ranking import (
    clamp_score,
    rank_memory_results,
    relationship_score,
    text_similarity,
)
from visp_memory.core.storage import (
    STORAGE_SCHEMA_VERSION,
    BaseStorage,
    EvidenceUnsupportedError,
    LocalStorage,
    MemoryLayer,
    SessionCompletionStatus,
    StorageCapabilities,
    StorageMigrationRequired,
)
from visp_memory.quality.secrets import SecretBearingContentError, redact_for_storage

logger = logging.getLogger(__name__)
_RELATIONSHIP_TYPE_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
# Valid memory layers. ``layer`` is later f-string-interpolated into Cypher as a
# node label (``SET m:{label}``), so it must be validated against this allow-list
# before use to prevent label injection.
_VALID_LAYERS = frozenset({"raw", "episodic", "semantic", "intent"})


def _normalize_relationship_type(relationship: str) -> str:
    """Return a safe Cypher relationship type for direct query interpolation."""
    rel_type = "_".join(relationship.upper().split())
    if not rel_type or _RELATIONSHIP_TYPE_RE.fullmatch(rel_type) is None:
        raise ValueError(
            "Relationship type must contain only letters, numbers, spaces, and underscores, "
            "and must start with a letter or underscore"
        )
    return rel_type


class Neo4jStorage(BaseStorage):
    """
    Storage implementation using Neo4j for both structured data and vector embeddings.
    """

    AUTO_LINK_RELATIONSHIP = "related_to"
    SOURCE_LINK_RELATIONSHIP = "derived_from"
    DEFAULT_AUTO_LINK_LIMIT = 3
    DEFAULT_AUTO_LINK_MIN_SCORE = 0.53
    RELATIONSHIP_CONFIDENCE_VALUES = {"observed", "inferred", "ambiguous", "manual"}
    LEGACY_RELATIONSHIP_EVIDENCE_REASON = "Legacy relationship without evidence metadata."
    UNSPECIFIED_RELATIONSHIP_EVIDENCE_REASON = "Relationship created without evidence metadata."
    MEMORY_NODE_FIELDS = {
        "id", "content", "layer", "repo_id", "category", "importance", "tags", "metadata",
        "source_ids", "status", "source", "quality_flags", "created_at", "accessed_at",
        "access_count", "approved_by", "approved_at", "archived_at", "compressed_at",
        "last_quality_checked_at",
    }
    INTENT_NODE_FIELDS = {
        "id", "description", "priority", "repo_id", "context", "status", "created_at",
        "updated_at",
    }
    REPOSITORY_NODE_FIELDS = {
        "id", "name", "url", "description", "tech_stack", "team_id", "metadata", "created_at",
    }
    USER_NODE_FIELDS = {
        "id", "username", "email", "display_name", "metadata", "created_at", "last_active",
    }
    TEAM_NODE_FIELDS = {"id", "name", "description", "metadata", "created_at"}
    SESSION_NODE_FIELDS = {
        "id", "owner_id", "team_id", "repo_id", "summary", "memory_ids",
        "started_at", "ended_at",
    }
    AUDIT_NODE_FIELDS = {
        "id", "event_type", "actor_id", "repo_id", "target_type", "target_id", "metadata",
        "created_at",
    }
    FEEDBACK_NODE_FIELDS = {
        "id", "memory_id", "event_type", "repo_id", "query_hash", "task_id", "outcome",
        "metadata", "created_at",
    }

    def __init__(
        self,
        uri: str = None,
        user: str = None,
        password: str = None,
        embedding_fn=None,
        embedding_dimension: int = None,
    ):
        """Initialize Neo4j driver."""
        if GraphDatabase is None:
            raise ImportError("neo4j is required for Neo4j storage: pip install visp-memory[neo4j]")

        config = load_config()
        self.uri = uri or config.storage.neo4j_uri
        self.user = user or config.storage.neo4j_user
        self.password = password or config.storage.neo4j_password
        self._embedding_fn = embedding_fn
        embedding_owner = getattr(embedding_fn, "__self__", None)
        self._embedding_dimension = embedding_dimension or getattr(
            embedding_owner, "dimension", None
        )
        self._vector_property = self._vector_property_name(self._embedding_dimension)
        self._vector_index = self._vector_index_name(self._embedding_dimension)
        embedding_owner_name = embedding_owner.__class__.__name__.lower() if embedding_owner else ""
        self._uses_noop_embeddings = embedding_owner_name == "noopprovider"
        self._upgrade_session_schema_marker = False

        try:
            self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
            self.verify_connectivity()
            marker_exists = self._probe_schema_compatibility()
            if not marker_exists:
                self._ensure_schema_version()
            elif self._upgrade_session_schema_marker:
                self._upgrade_v4_session_schema()
            self._ensure_indexes()
        except Exception as e:
            logger.error(f"Failed to initialize Neo4j driver: {e}")
            driver = getattr(self, "driver", None)
            if driver is not None:
                driver.close()
                self.driver = None
            raise

    def close(self):
        """Close driver connection. Idempotent: safe to call more than once."""
        driver = getattr(self, "driver", None)
        if driver is not None:
            driver.close()
            self.driver = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def verify_connectivity(self):
        """Verify connection to Neo4j."""
        self.driver.verify_connectivity()

    def _ensure_indexes(self):
        """Create necessary indexes and constraints."""
        with self.driver.session() as session:
            # ID Constraints
            session.run(
                "CREATE CONSTRAINT memory_id_unique IF NOT EXISTS "
                "FOR (m:Memory) REQUIRE m.id IS UNIQUE"
            )
            session.run(
                "CREATE CONSTRAINT intent_id_unique IF NOT EXISTS "
                "FOR (i:Intent) REQUIRE i.id IS UNIQUE"
            )
            session.run(
                "CREATE CONSTRAINT session_id_unique IF NOT EXISTS "
                "FOR (s:Session) REQUIRE s.id IS UNIQUE"
            )
            session.run(
                "CREATE INDEX session_owner IF NOT EXISTS FOR (s:Session) ON (s.owner_id)"
            )
            session.run(
                "CREATE INDEX session_repo IF NOT EXISTS FOR (s:Session) ON (s.repo_id)"
            )
            session.run(
                "CREATE CONSTRAINT repo_id_unique IF NOT EXISTS "
                "FOR (r:Repository) REQUIRE r.id IS UNIQUE"
            )
            session.run(
                "CREATE CONSTRAINT user_id_unique IF NOT EXISTS FOR (u:User) REQUIRE u.id IS UNIQUE"
            )
            session.run(
                "CREATE CONSTRAINT team_id_unique IF NOT EXISTS FOR (t:Team) REQUIRE t.id IS UNIQUE"
            )
            session.run(
                "CREATE INDEX audit_log_event IF NOT EXISTS FOR (a:AuditLog) ON (a.event_type)"
            )
            session.run(
                "CREATE INDEX audit_log_actor IF NOT EXISTS FOR (a:AuditLog) ON (a.actor_id)"
            )
            session.run(
                "CREATE INDEX audit_log_repo IF NOT EXISTS FOR (a:AuditLog) ON (a.repo_id)"
            )
            session.run(
                "CREATE INDEX memory_repo_status_created IF NOT EXISTS "
                "FOR (m:Memory) ON (m.repo_id, m.status, m.created_at)"
            )
            session.run(
                "CREATE INDEX memory_repo_layer_status IF NOT EXISTS "
                "FOR (m:Memory) ON (m.repo_id, m.layer, m.status)"
            )
            session.run(
                "CREATE INDEX intent_repo_status_priority IF NOT EXISTS "
                "FOR (i:Intent) ON (i.repo_id, i.status, i.priority)"
            )

            # Vector index dimensions must match the active embedding provider.
            if self._embedding_dimension:
                try:
                    dimensions = int(self._embedding_dimension)
                    session.run(f"""
                    CREATE VECTOR INDEX {self._vector_index} IF NOT EXISTS
                    FOR (m:Memory) ON (m.`{self._vector_property}`)
                    OPTIONS {{indexConfig: {{
                        `vector.dimensions`: {dimensions},
                        `vector.similarity_function`: 'cosine'
                    }}}}
                """)
                except Exception as e:
                    logger.warning(
                        "Could not create vector index "
                        f"(might be already present or incompatible version): {e}"
                    )

    def _ensure_schema_version(self) -> None:
        with self.driver.session() as session:
            record = session.run(
                """
                MERGE (v:SchemaVersion {component: 'storage'})
                ON CREATE SET v.version = $version, v.applied_at = datetime()
                RETURN v.version AS version
                """,
                version=STORAGE_SCHEMA_VERSION,
            ).single()
            if not record:
                raise StorageMigrationRequired(
                    "Neo4j schema marker could not be confirmed after initialization"
                )
            stored_version = int(record["version"])
            if stored_version > STORAGE_SCHEMA_VERSION:
                raise RuntimeError(
                    "Storage schema is newer than this visp-memory build "
                    f"({stored_version} > {STORAGE_SCHEMA_VERSION})"
                )
            if stored_version < STORAGE_SCHEMA_VERSION:
                raise StorageMigrationRequired(
                    "Neo4j schema marker could not be initialized at the current version"
                )

    def _upgrade_v4_session_schema(self) -> None:
        """Advance the marker for additive Session properties on schema-less nodes."""
        with self.driver.session() as session:
            record = session.run(
                """
                MATCH (v:SchemaVersion {component: 'storage', version: $from_version})
                SET v.version = $to_version, v.applied_at = datetime()
                RETURN v.version AS version
                """,
                from_version=STORAGE_SCHEMA_VERSION - 1,
                to_version=STORAGE_SCHEMA_VERSION,
            ).single()
        if not record or int(record["version"]) != STORAGE_SCHEMA_VERSION:
            raise StorageMigrationRequired("Neo4j session schema marker upgrade failed")

    def _probe_schema_compatibility(self) -> bool:
        """Read marker and graph emptiness before any constraint or marker write."""
        with self.driver.session() as session:
            markers = list(
                session.run(
                    "MATCH (v:SchemaVersion {component: 'storage'}) "
                    "RETURN v.version AS version LIMIT 2"
                )
            )
            if markers:
                if len(markers) != 1 or markers[0].get("version") is None:
                    raise StorageMigrationRequired(
                        "Neo4j storage schema marker is missing or ambiguous"
                    )
                stored_version = int(markers[0]["version"])
                if stored_version > STORAGE_SCHEMA_VERSION:
                    raise RuntimeError(
                        "Storage schema is newer than this visp-memory build "
                        f"({stored_version} > {STORAGE_SCHEMA_VERSION})"
                    )
                if stored_version < STORAGE_SCHEMA_VERSION - 1:
                    raise StorageMigrationRequired(
                        "Neo4j schema migration is not implemented; export the older store "
                        f"with its original build before using schema v{STORAGE_SCHEMA_VERSION}"
                    )
                if list(
                    session.run(
                        "MATCH (m:Memory) RETURN m.id AS id LIMIT 1"
                    )
                ):
                    raise StorageMigrationRequired(
                        "Neo4j storage cannot serve existing Memory nodes because "
                        "the governed Evidence graph is unsupported"
                    )
                if stored_version == STORAGE_SCHEMA_VERSION - 1:
                    self._upgrade_session_schema_marker = True
                return True
            if list(session.run("MATCH (n) RETURN true AS present LIMIT 1")):
                raise StorageMigrationRequired(
                    "Unversioned non-empty Neo4j storage requires an explicit migration"
                )
        return False

    @staticmethod
    def _vector_property_name(dimension: int = None) -> str:
        if dimension:
            return f"embedding_{int(dimension)}"
        return "embedding"

    @staticmethod
    def _vector_index_name(dimension: int = None) -> str:
        if dimension:
            return f"memory_embedding_index_{int(dimension)}"
        return "memory_embedding_index"

    @staticmethod
    def _generate_id(content: str) -> str:
        """Generate collision-resistant IDs consistently across backends."""
        return LocalStorage._generate_id(content)

    @staticmethod
    def _json_serialize(data: Any) -> str:
        return json.dumps(data)

    @staticmethod
    def _json_deserialize(data: str) -> Any:
        if not data:
            return None
        try:
            return json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return data

    @classmethod
    def _normalize_node(
        cls,
        node: Dict[str, Any],
        *,
        fields: set[str],
        json_fields: set[str] | None = None,
        defaults: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Normalize and constrain Neo4j properties for one entity contract."""
        data = {key: value for key, value in dict(node).items() if key in fields}
        for key in list(data):
            if key == "embedding" or key.startswith("embedding_"):
                data.pop(key, None)

        for field in json_fields or set():
            if field in data and isinstance(data[field], str):
                data[field] = cls._json_deserialize(data[field])

        for field in (
            "created_at", "updated_at", "accessed_at", "last_active", "started_at", "ended_at",
            "approved_at", "archived_at", "compressed_at", "last_quality_checked_at",
        ):
            value = data.get(field)
            if hasattr(value, "iso_format"):
                data[field] = value.iso_format()
            elif hasattr(value, "isoformat"):
                data[field] = value.isoformat()

        for field, value in (defaults or {}).items():
            data.setdefault(field, value.copy() if isinstance(value, (dict, list)) else value)
        return data

    @classmethod
    def _memory_node_to_dict(cls, node: Dict[str, Any]) -> Dict[str, Any]:
        return cls._normalize_node(
            node,
            fields=cls.MEMORY_NODE_FIELDS,
            json_fields={"metadata", "tags", "source_ids", "quality_flags"},
            defaults={
                "tags": [],
                "metadata": {},
                "source_ids": [],
                "status": "active",
                "quality_flags": [],
            },
        )

    # Compatibility for integrations that used the historical memory normalizer directly.
    _node_to_dict = _memory_node_to_dict

    @classmethod
    def _intent_node_to_dict(cls, node: Dict[str, Any]) -> Dict[str, Any]:
        return cls._normalize_node(
            node, fields=cls.INTENT_NODE_FIELDS, json_fields={"context"},
            defaults={"context": {}, "status": "active"},
        )

    @classmethod
    def _repository_node_to_dict(cls, node: Dict[str, Any]) -> Dict[str, Any]:
        return cls._normalize_node(
            node, fields=cls.REPOSITORY_NODE_FIELDS, json_fields={"metadata", "tech_stack"},
            defaults={"metadata": {}, "tech_stack": []},
        )

    @classmethod
    def _user_node_to_dict(cls, node: Dict[str, Any]) -> Dict[str, Any]:
        return cls._normalize_node(
            node, fields=cls.USER_NODE_FIELDS, json_fields={"metadata"}, defaults={"metadata": {}},
        )

    @classmethod
    def _team_node_to_dict(cls, node: Dict[str, Any]) -> Dict[str, Any]:
        return cls._normalize_node(
            node, fields=cls.TEAM_NODE_FIELDS, json_fields={"metadata"}, defaults={"metadata": {}},
        )

    @classmethod
    def _audit_node_to_dict(cls, node: Dict[str, Any]) -> Dict[str, Any]:
        return cls._normalize_node(
            node, fields=cls.AUDIT_NODE_FIELDS, json_fields={"metadata"}, defaults={"metadata": {}},
        )

    def get_capabilities(self) -> StorageCapabilities:
        return StorageCapabilities(vector_search=True, audit_log=True, reindex=True)

    def get_schema_status(self) -> Dict[str, Any]:
        with self.driver.session() as session:
            record = session.run(
                "MATCH (v:SchemaVersion {component: 'storage'}) RETURN v.version AS version"
            ).single()
        stored_version = int(record["version"]) if record else 0
        return {
            "current_version": STORAGE_SCHEMA_VERSION,
            "stored_version": stored_version,
            "status": "ready" if stored_version == STORAGE_SCHEMA_VERSION else "migration_required",
        }

    @staticmethod
    def _format_temporal(value: Any) -> Any:
        if hasattr(value, "iso_format"):
            return value.iso_format()
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return value

    @classmethod
    def _normalize_relationship_evidence(
        cls,
        evidence: Dict[str, Any] = None,
        *,
        strength: float = None,
        created_at: Any = None,
        legacy: bool = True,
    ) -> Dict[str, Any]:
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

        evidence_created_at = evidence.get("created_at")
        if evidence_created_at is None:
            evidence_created_at = created_at

        return {
            "confidence": confidence,
            "confidence_score": clamp_score(score),
            "source": evidence.get("source") or ("legacy" if legacy else "unspecified"),
            "source_file": evidence.get("source_file"),
            "source_location": evidence.get("source_location"),
            "reason": evidence.get("reason") or default_reason,
            "created_by": evidence.get("created_by"),
            "created_at": cls._format_temporal(evidence_created_at),
        }

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
        evidence_ids: List[str] = None,
        status: str = "active",
        source: str = None,
        quality_flags: List[str] = None,
        embedding: List[float] = None,
        auto_link: bool = True,
        auto_link_limit: int = DEFAULT_AUTO_LINK_LIMIT,
        auto_link_min_score: float = DEFAULT_AUTO_LINK_MIN_SCORE,
        # Accepted and unused. The governed belief fields exist on the local
        # backend; this one fails closed for every layer that carries them, a few
        # lines below. Omitting them from the signature did not prevent the write —
        # it turned an intended, explicit refusal into a TypeError raised before the
        # check could run, which the server surfaced as a 500 rather than a 501.
        epistemic_status: str = None,
        authority_attestation: str = None,
        replaces_belief_id: str = None,
    ) -> str:
        """Store a memory node."""
        # Enforce the secrets policy at the single choke point every write path funnels
        # through, so a new caller cannot opt out. See visp_memory.quality.secrets.
        try:
            content, quality_flags = redact_for_storage(
                content,
                quality_flags,
                reject_if_redacted=authority_attestation is not None,
            )
        except SecretBearingContentError as exc:
            from visp_memory.core.authority import ProhibitionAuthorityError

            raise ProhibitionAuthorityError(str(exc)) from exc
        if layer not in _VALID_LAYERS:
            raise ValueError(
                "Memory layer must be one of: " + ", ".join(sorted(_VALID_LAYERS))
            )
        if layer in {"raw", "episodic", "semantic"}:
            raise EvidenceUnsupportedError(
                "Neo4j does not yet support the schema-v3 Evidence graph; governed "
                f"{layer} writes fail closed"
            )

        # Generate embedding if not provided and embedding function is available
        if embedding is None and self._embedding_fn is not None:
            try:
                embedding = self._embedding_fn(content)
            except Exception as e:
                logger.warning(f"Failed to generate embedding: {e}")
                embedding = None
        memory_id = self._generate_id(content)
        tags = tags or []
        metadata = metadata or {}
        source_ids = source_ids or []
        quality_flags = quality_flags or []

        # Determine labels based on layer
        # Always add :Memory, and specific layer label (e.g. :Episodic)
        labels = ["Memory", layer.capitalize()]

        with self.driver.session() as session:
            # 1. Create/Update Node & Properties
            query = """
                MERGE (m:Memory {id: $id})
                SET m += {
                    content: $content,
                    layer: $layer,
                    repo_id: $repo_id,
                    category: $category,
                    importance: $importance,
                    tags: $tags,
                    metadata: $metadata,
                    source_ids: $source_ids,
                    status: $status,
                    source: $source,
                    quality_flags: $quality_flags,
                    created_at: coalesce(m.created_at, datetime()),
                    updated_at: datetime(),
                    accessed_at: coalesce(m.accessed_at, datetime()),
                    access_count: coalesce(m.access_count, 0)
                }
            """

            # Conditionally add vector property
            if embedding:
                query += """
                 WITH m
                 CALL db.create.setNodeVectorProperty(m, $vector_property, $embedding)
                 """

            session.run(
                query,
                id=memory_id,
                content=content,
                layer=layer,
                repo_id=repo_id,
                category=category,
                importance=importance,
                tags=tags,
                metadata=self._json_serialize(metadata),
                source_ids=source_ids,
                status=status,
                source=source,
                quality_flags=quality_flags,
                embedding=embedding,
                vector_property=self._vector_property,
            )

            # 2. Add extra labels
            for label in labels:
                if label != "Memory":
                    session.run(f"MATCH (m:Memory {{id: $id}}) SET m:{label}", id=memory_id)

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

        candidates = self._auto_link_candidates(
            content=content,
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

    def _auto_link_candidates(
        self, content: str, repo_id: str = None, limit: int = 12
    ) -> List[Dict[str, Any]]:
        """Find candidate memories without trusting noop vector similarity."""
        if self._embedding_fn is not None and not self._uses_noop_embeddings:
            return self.search_memories(query=content, repo_id=repo_id, limit=limit)

        memories = self.list_memories(repo_id=repo_id, limit=max(limit, 200))
        for memory in memories:
            memory["similarity"] = text_similarity(content, str(memory.get("content", "")))
        return rank_memory_results(memories, query=content, limit=limit)

    def get_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Get a memory by ID."""
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (m:Memory {id: $id})
                SET m.access_count = coalesce(m.access_count, 0) + 1, m.accessed_at = datetime()
                RETURN m
            """,
                id=memory_id,
            )
            record = result.single()

            if record:
                node = dict(record["m"])
                # Clean up internal props if any
                return self._memory_node_to_dict(node)
            return None

    def search_memories(
        self,
        query: str,
        repo_id: str = None,
        layer: str = None,
        category: str = None,
        limit: int = 10,
        min_importance: float = 0.0,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """Search memories using vector similarity or text filtering."""

        query, _ = redact_for_storage(query, None)
        embedding = kwargs.get("embedding")
        status = kwargs.get("status", "active")

        # Generate embedding from query if not provided and embedding function is available
        if not embedding and self._embedding_fn is not None:
            try:
                embedding = self._embedding_fn(query)
            except Exception as e:
                logger.warning(f"Failed to generate embedding for query: {e}")
                embedding = None

        # When no layer is requested, exclude the 'raw' layer from search results
        # (canonical SQLite behavior: search only episodic/semantic/intent). An explicit
        # ``layer='raw'`` request is still honored. list_memories keeps all layers.
        exclude_raw = layer is None
        # Over-fetch factor for the vector path so post-filtering still returns up to
        # ``limit`` results (the fallback text query applies LIMIT $limit in-query).
        vector_limit = max(limit * 5, 50)

        fallback_cypher = """
            MATCH (m:Memory)
            WHERE ($layer IS NULL OR m.layer = $layer)
            AND (NOT $exclude_raw OR m.layer IS NULL OR m.layer <> 'raw')
            AND ($repo_id IS NULL OR m.repo_id = $repo_id)
            AND ($category IS NULL OR m.category = $category)
            AND ($status = 'all' OR m.status = $status OR ($status = 'active' AND m.status IS NULL))
            AND m.importance >= $min_importance
            AND toLower(m.content) CONTAINS toLower($query)
            RETURN m, 0.0 as score
            LIMIT $limit
        """

        if not embedding:
            # Fallback to simple text search or property filter if no embedding available
            logger.warning(
                "No embedding available for vector search. Falling back to property filter."
            )
            cypher = fallback_cypher
        else:
            # Vector Search. queryNodes returns the k nearest nodes and the post-hoc
            # WHERE (layer/raw/repo/category/status/importance) can discard some of them,
            # so over-fetch candidates and re-apply $limit after filtering — otherwise a
            # page of nearest nodes that are all excluded (e.g. all 'raw') would yield
            # nothing even when non-raw matches exist further down the index.
            cypher = f"""
                CALL db.index.vector.queryNodes('{self._vector_index}', $vector_limit, $embedding)
                YIELD node, score
                WHERE ($layer IS NULL OR node.layer = $layer)
                AND (NOT $exclude_raw OR node.layer IS NULL OR node.layer <> 'raw')
                AND ($repo_id IS NULL OR node.repo_id = $repo_id)
                AND ($category IS NULL OR node.category = $category)
                AND (
                    $status = 'all'
                    OR node.status = $status
                    OR ($status = 'active' AND node.status IS NULL)
                )
                AND node.importance >= $min_importance
                RETURN node as m, score
                ORDER BY score DESC
                LIMIT $limit
            """

        try:
            return self._run_memory_search(
                cypher,
                query=query,
                embedding=embedding,
                limit=limit,
                vector_limit=vector_limit,
                layer=layer,
                exclude_raw=exclude_raw,
                repo_id=repo_id,
                category=category,
                status=status,
                min_importance=min_importance,
            )
        except Exception as e:
            if not embedding:
                raise
            logger.warning(f"Vector search failed; falling back to text search: {e}")
            return self._run_memory_search(
                fallback_cypher,
                query=query,
                embedding=None,
                limit=limit,
                vector_limit=vector_limit,
                layer=layer,
                exclude_raw=exclude_raw,
                repo_id=repo_id,
                category=category,
                status=status,
                min_importance=min_importance,
            )

    def _run_memory_search(self, cypher: str, **params) -> List[Dict[str, Any]]:
        with self.driver.session() as session:
            result = session.run(cypher, params)

            memories = []
            for record in result:
                mem = self._memory_node_to_dict(dict(record["m"]))
                mem["similarity"] = record["score"]
                memories.append(mem)
            return memories

    def list_memories(
        self,
        repo_id: str = None,
        layer: str = None,
        category: str = None,
        limit: int = 50,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """List memories."""
        status = kwargs.get("status", "active")
        query = """
            MATCH (m:Memory)
            WHERE ($layer IS NULL OR m.layer = $layer)
            AND ($repo_id IS NULL OR m.repo_id = $repo_id)
            AND ($category IS NULL OR m.category = $category)
            AND ($status = 'all' OR m.status = $status OR ($status = 'active' AND m.status IS NULL))
            RETURN m
            ORDER BY m.created_at DESC
            LIMIT $limit
        """
        with self.driver.session() as session:
            result = session.run(
                query, layer=layer, repo_id=repo_id, category=category, status=status, limit=limit
            )
            return [self._memory_node_to_dict(dict(record["m"])) for record in result]

    # Fields a caller may update. Property names are interpolated into Cypher,
    # so this allowlist prevents property-name injection via arbitrary kwargs.
    _UPDATABLE_FIELDS = frozenset(
        {
            "content",
            "importance",
            "tags",
            "metadata",
            "status",
            "category",
            "approved_by",
            "approved_at",
            "archived_at",
            "source",
            "quality_flags",
            "accessed_at",
        }
    )

    def update_memory(self, memory_id: str, **kwargs) -> bool:
        """Update properties."""
        clauses = []
        params = {"id": memory_id}
        content = kwargs.get("content")

        if content is not None:
            original_content = content
            content, redaction_flags = redact_for_storage(
                content,
                kwargs.get("quality_flags") or [],
            )
            kwargs["content"] = content
            if content != original_content:
                kwargs["quality_flags"] = redaction_flags

        for k, v in kwargs.items():
            if k not in self._UPDATABLE_FIELDS:
                logger.warning("Ignoring unsupported memory update field: %s", k)
                continue
            if k == "metadata":
                v = self._json_serialize(v)

            clauses.append(f"SET m.{k} = ${k}")
            params[k] = v

        if not clauses:
            return False

        guard = "WHERE coalesce(m.layer, '') <> 'semantic'\n" if content is not None else ""
        query = (
            "MATCH (m:Memory {id: $id}) "
            + guard
            + "\n".join(clauses)
            + " RETURN count(m) as c"
        )

        with self.driver.session() as session:
            result = session.run(query, **params)
            updated = result.single()["c"] > 0
            if updated and content is not None and self._embedding_fn is not None:
                try:
                    embedding = self._embedding_fn(content)
                    session.run(
                        """
                        MATCH (m:Memory {id: $id})
                        CALL db.create.setNodeVectorProperty(m, $vector_property, $embedding)
                        RETURN m.id AS id
                        """,
                        id=memory_id,
                        vector_property=self._vector_property,
                        embedding=embedding,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to update vector property for memory %s: %s", memory_id, exc
                    )
            return updated

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
        """Inspect active Neo4j vector-property coverage for a scoped memory set."""
        scope = scope or ReindexScope()
        where = ["1 = 1"]
        params: dict[str, Any] = {"vector_property": self._vector_property}
        if scope.layer:
            where.append("m.layer = $layer")
            params["layer"] = scope.layer
        if scope.repo_id:
            where.append("m.repo_id = $repo_id")
            params["repo_id"] = scope.repo_id
        if scope.category:
            where.append("m.category = $category")
            params["category"] = scope.category

        query = f"""
            MATCH (m:Memory)
            WHERE {" AND ".join(where)}
            RETURN count(m) AS matched,
                   count(m.`{self._vector_property}`) AS indexed
        """
        matched = 0
        indexed = 0
        try:
            with self.driver.session() as session:
                record = session.run(query, **params).single()
                matched = int(record["matched"])
                indexed = int(record["indexed"])
        except Exception:
            pass

        if self._uses_noop_embeddings:
            status = "disabled"
            message = "Noop embeddings are active; Neo4j vector search is disabled."
        elif self._embedding_fn is None:
            status = "not_configured"
            message = "No embedding function is configured; text fallback is used."
        elif dimension or self._embedding_dimension:
            status = "available"
            message = "Neo4j vector property is available for the active provider dimension."
        else:
            status = "unknown"
            message = "Embedding provider is active but its vector dimension is unknown."

        needs_reindex = bool(matched and status == "available" and indexed < matched)
        if needs_reindex:
            message = "The active Neo4j vector property has fewer vectors than matching memories."

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
            active_collections=[self._vector_property],
            legacy_collections=[],
            needs_reindex=needs_reindex,
        )

    def rebuild_embedding_index(
        self, *, scope: ReindexScope = None, dry_run: bool = True
    ) -> ReindexResult:
        """Regenerate active Neo4j vector properties for matching memories."""
        scope = scope or ReindexScope()
        candidates = self.list_memories(
            layer=scope.layer,
            repo_id=scope.repo_id,
            category=scope.category,
            limit=100000,
            order_by="created_at ASC",
        )

        if self._uses_noop_embeddings:
            return ReindexResult(
                dry_run=dry_run,
                status="disabled",
                message="Noop embeddings are active; there is no vector property to rebuild.",
                scope=scope.as_filter_dict(),
                matched_memories=len(candidates),
                dimension=self._embedding_dimension,
                active_collections=[self._vector_property],
            )
        if self._embedding_fn is None:
            return ReindexResult(
                dry_run=dry_run,
                status="not_configured",
                message="A real embedding function is required to rebuild vectors.",
                scope=scope.as_filter_dict(),
                matched_memories=len(candidates),
                dimension=self._embedding_dimension,
                active_collections=[self._vector_property],
            )
        if dry_run:
            return ReindexResult(
                dry_run=True,
                status="ready",
                message="Dry run complete; no embeddings were changed.",
                scope=scope.as_filter_dict(),
                matched_memories=len(candidates),
                dimension=self._embedding_dimension,
                active_collections=[self._vector_property],
            )

        errors: list[dict[str, str]] = []
        reindexed = 0
        with self.driver.session() as session:
            for memory in candidates:
                try:
                    safe_content, _ = redact_for_storage(memory["content"], None)
                    embedding = self._embedding_fn(safe_content)
                    session.run(
                        """
                        MATCH (m:Memory {id: $id})
                        CALL db.create.setNodeVectorProperty(m, $vector_property, $embedding)
                        RETURN m.id AS id
                        """,
                        id=memory["id"],
                        vector_property=self._vector_property,
                        embedding=embedding,
                    )
                    reindexed += 1
                except Exception as exc:
                    errors.append({"id": memory["id"], "error": exc.__class__.__name__})

        failed = len(errors)
        return ReindexResult(
            dry_run=False,
            status="completed" if failed == 0 else "partial_failure",
            message=(
                f"Rebuilt {reindexed} embedding vectors."
                if failed == 0
                else f"Rebuilt {reindexed} embedding vectors; {failed} failed."
            ),
            scope=scope.as_filter_dict(),
            matched_memories=len(candidates),
            reindexed_memories=reindexed,
            failed_memories=failed,
            dimension=self._embedding_dimension,
            active_collections=[self._vector_property],
            errors=errors[:50],
        )

    def delete_memory(self, memory_id: str) -> bool:
        """Delete node and relationships."""
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (m:Memory {id: $id})
                WITH collect(m) AS nodes
                FOREACH (node IN nodes | DETACH DELETE node)
                RETURN size(nodes) AS c
                """,
                id=memory_id,
            )
            return result.single()["c"] > 0

    def get_collection(self, layer: str):
        """Not applicable for Neo4j (unified)."""
        return None

    # Intent Operations
    def set_intent(
        self,
        description: str,
        priority: int = 0,
        repo_id: str = None,
        context: Dict[str, Any] = None,
    ) -> str:
        intent_id = self._generate_id(description)
        context = context or {}

        with self.driver.session() as session:
            session.run(
                """
                MERGE (i:Intent {id: $id})
                SET i.description = $description,
                    i.priority = $priority,
                    i.status = 'active',
                    i.repo_id = $repo_id,
                    i.context = $context,
                    i.created_at = datetime(),
                    i.updated_at = datetime()
            """,
                id=intent_id,
                description=description,
                priority=priority,
                repo_id=repo_id,
                context=self._json_serialize(context),
            )
        return intent_id

    def get_active_intents(
        self, repo_id: str = None, status: str = "active"
    ) -> List[Dict[str, Any]]:
        with self.driver.session() as session:
            query = "MATCH (i:Intent) WHERE ($status = 'all' OR i.status = $status)"
            params = {"status": status or "active"}
            if repo_id:
                query += " AND i.repo_id = $repo_id"
                params["repo_id"] = repo_id

            query += " RETURN i ORDER BY i.priority DESC, i.created_at DESC"

            result = session.run(query, params)
            return [self._intent_node_to_dict(dict(rec["i"])) for rec in result]

    def complete_intent(self, intent_id: str) -> bool:
        return False

    def update_intent(self, intent_id: str, **kwargs) -> bool:
        allowed_fields = {"description", "priority", "context"}
        params = {"id": intent_id}
        clauses = []

        for field, value in kwargs.items():
            if field not in allowed_fields or value is None:
                continue
            if field == "context":
                value = self._json_serialize(value)
            clauses.append(f"i.{field} = ${field}")
            params[field] = value

        if not clauses:
            return False

        with self.driver.session() as session:
            result = session.run(
                f"""
                MATCH (i:Intent {{id: $id}})
                SET {", ".join(clauses)}, i.updated_at = datetime()
                RETURN count(i) as c
                """,
                params,
            )
            return result.single()["c"] > 0

    # Relationship Operations
    def _get_memory_repo_id(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a memory's repo_id for validation without bumping access_count.

        Returns ``{"repo_id": ...}`` when the memory exists, otherwise ``None``.
        """
        with self.driver.session() as session:
            record = session.run(
                "MATCH (m:Memory {id: $id}) RETURN m.repo_id AS repo_id",
                id=memory_id,
            ).single()
            if record is None:
                return None
            return {"repo_id": record["repo_id"]}

    def add_relationship(
        self,
        source_id: str,
        target_id: str,
        relationship: str,
        strength: float = 1.0,
        evidence: Dict[str, Any] = None,
    ) -> str:
        """Create a relationship."""
        # Validate cheap/unsafe inputs before touching the database: the relationship
        # type is interpolated into Cypher, so reject injection and unsupported
        # confidence values before issuing any query.
        rel_type = _normalize_relationship_type(relationship)
        auto_rel_type = _normalize_relationship_type(self.AUTO_LINK_RELATIONSHIP)
        evidence_data = self._normalize_relationship_evidence(
            evidence,
            strength=strength,
            created_at=None,
            legacy=False,
        )

        # Both memories must exist and share a repository (parity with LocalStorage
        # and ArcadeDbStorage).
        source = self._get_memory_repo_id(source_id)
        target = self._get_memory_repo_id(target_id)
        if source is None or target is None:
            raise ValueError("Relationship source and target memories must both exist")
        if source["repo_id"] != target["repo_id"]:
            raise ValueError("Memory relationships cannot cross repository boundaries")

        rel_id = self._generate_id(f"{source_id}-{target_id}-{rel_type}")

        with self.driver.session() as session:
            if rel_type != auto_rel_type:
                session.run(
                    f"""
                    MATCH (a:Memory {{id: $source_id}})-[r:{auto_rel_type}]-
                          (b:Memory {{id: $target_id}})
                    DELETE r
                """,
                    source_id=source_id,
                    target_id=target_id,
                )
            session.run(
                f"""
                MATCH (a:Memory {{id: $source_id}})
                MATCH (b:Memory {{id: $target_id}})
                MERGE (a)-[r:{rel_type}]->(b)
                SET r.id = $rel_id,
                    r.weight = $strength,
                    r.created_at = coalesce(r.created_at, datetime()),
                    r.confidence = $confidence,
                    r.confidence_score = $confidence_score,
                    r.source = $source,
                    r.source_file = $source_file,
                    r.source_location = $source_location,
                    r.reason = $reason,
                    r.created_by = $created_by
            """,
                source_id=source_id,
                target_id=target_id,
                rel_id=rel_id,
                strength=strength,
                confidence=evidence_data["confidence"],
                confidence_score=evidence_data["confidence_score"],
                source=evidence_data["source"],
                source_file=evidence_data["source_file"],
                source_location=evidence_data["source_location"],
                reason=evidence_data["reason"],
                created_by=evidence_data["created_by"],
            )
        return rel_id

    def get_related_memories(
        self, memory_id: str, relationship: str = None
    ) -> List[Dict[str, Any]]:
        """Get connected neighbors."""
        # NOTE: Parameterizing relationship types in Cypher is tricky (apoc or simple workaround)
        # Check against basic injection
        rel_clause = ""
        if relationship:
            safe_rel = _normalize_relationship_type(relationship)
            rel_clause = f":{safe_rel}"

        query = f"""
            MATCH (m:Memory {{id: $id}})-[r{rel_clause}]-(related:Memory)
            RETURN related, type(r) as rel_type, r.weight as strength,
                r.confidence as confidence,
                r.confidence_score as confidence_score,
                r.source as source,
                r.source_file as source_file,
                r.source_location as source_location,
                r.reason as reason,
                r.created_by as created_by,
                r.created_at as created_at
        """

        with self.driver.session() as session:
            result = session.run(query, id=memory_id)
            items = []
            seen_ids = set()
            for record in result:
                item = self._memory_node_to_dict(dict(record["related"]))
                if item.get("id") in seen_ids:
                    continue
                seen_ids.add(item.get("id"))
                item["relationship"] = record["rel_type"]
                item["strength"] = record["strength"]
                item["relationship_evidence"] = self._normalize_relationship_evidence(
                    {
                        "confidence": record.get("confidence"),
                        "confidence_score": record.get("confidence_score"),
                        "source": record.get("source"),
                        "source_file": record.get("source_file"),
                        "source_location": record.get("source_location"),
                        "reason": record.get("reason"),
                        "created_by": record.get("created_by"),
                        "created_at": record.get("created_at"),
                    },
                    strength=record["strength"],
                    created_at=record.get("created_at"),
                    legacy=record.get("source") in (None, "legacy"),
                )
                items.append(item)
            return items

    def get_all_relationships(self, repo_id: str = None) -> List[Dict[str, Any]]:
        """Get all relationships for visualization."""
        with self.driver.session() as session:
            query = """
                MATCH (a)-[r]->(b)
                WHERE a:Memory AND b:Memory
            """
            params = {}
            if repo_id:
                query += " AND a.repo_id = $repo_id AND b.repo_id = $repo_id"
                params["repo_id"] = repo_id

            query += (
                " RETURN a.id as source, b.id as target, type(r) as type, "
                "r.weight as weight, r.id as id, "
                "r.confidence as confidence, r.confidence_score as confidence_score, "
                "r.source as evidence_source, r.source_file as source_file, "
                "r.source_location as source_location, r.reason as reason, "
                "r.created_by as created_by, r.created_at as created_at"
            )

            result = session.run(query, params)
            return [
                {
                    "id": rec.get("id") or f"{rec['source']}-{rec['target']}",
                    "source_id": rec["source"],
                    "target_id": rec["target"],
                    "relationship": rec["type"],
                    "strength": rec.get("weight", 1.0),
                    "evidence": self._normalize_relationship_evidence(
                        {
                            "confidence": rec.get("confidence"),
                            "confidence_score": rec.get("confidence_score"),
                            "source": rec.get("evidence_source"),
                            "source_file": rec.get("source_file"),
                            "source_location": rec.get("source_location"),
                            "reason": rec.get("reason"),
                            "created_by": rec.get("created_by"),
                            "created_at": rec.get("created_at"),
                        },
                        strength=rec.get("weight", 1.0),
                        created_at=rec.get("created_at"),
                        legacy=rec.get("evidence_source") in (None, "legacy"),
                    ),
                }
                for rec in result
            ]

    def delete_relationship(self, relationship_id: str) -> bool:
        with self.driver.session() as session:
            record = session.run(
                "MATCH ()-[r {id: $id}]->() WITH r, count(r) AS c DELETE r RETURN c",
                id=relationship_id,
            ).single()
        return bool(record and record["c"])

    # Session Ops
    def start_session(
        self,
        *,
        owner_id: str = None,
        team_id: str = None,
        repo_id: str = None,
    ) -> str:
        sid = self._generate_id("session")
        with self.driver.session() as session:
            session.run(
                "CREATE (s:Session {id: $id, owner_id: $owner_id, team_id: $team_id, "
                "repo_id: $repo_id, started_at: datetime()})",
                id=sid,
                owner_id=owner_id,
                team_id=team_id,
                repo_id=repo_id,
            )
        return sid

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self.driver.session() as session:
            record = session.run(
                """
                MATCH (s:Session {id: $id})
                RETURN s.id AS id, s.owner_id AS owner_id, s.team_id AS team_id,
                       s.repo_id AS repo_id, s.summary AS summary,
                       coalesce(s.memory_ids, []) AS memory_ids,
                       s.started_at AS started_at, s.ended_at AS ended_at
                """,
                id=session_id,
            ).single()
        if not record:
            return None
        return self._normalize_node(
            dict(record),
            fields=self.SESSION_NODE_FIELDS,
            json_fields={"memory_ids"},
            defaults={"memory_ids": []},
        )

    def end_session(
        self, session_id: str, summary: str, memory_ids: List[str]
    ) -> SessionCompletionStatus:
        with self.driver.session() as session:
            record = session.run(
                """
                MATCH (s:Session {id: $id})
                WHERE s.ended_at IS NULL
                SET s.summary = $summary,
                    s.memory_ids = $mem_ids,
                    s.ended_at = datetime()
                RETURN s.id AS id
            """,
                id=session_id,
                summary=summary,
                mem_ids=memory_ids,
            ).single()
            if record:
                return SessionCompletionStatus.COMPLETED
            exists = session.run(
                "MATCH (s:Session {id: $id}) RETURN s.id AS id", id=session_id
            ).single()
        return (
            SessionCompletionStatus.ALREADY_COMPLETED
            if exists
            else SessionCompletionStatus.NOT_FOUND
        )

    def get_stats(self, repo_id: str = None) -> Dict[str, Any]:
        with self.driver.session() as session:
            stats = {}

            # Base match clause
            match_clause = "MATCH (m:Memory)"
            where_clause = ""
            params = {}

            if repo_id:
                where_clause = " WHERE m.repo_id = $repo_id"
                params["repo_id"] = repo_id

            # Count by layer
            query = f"{match_clause}{where_clause} RETURN m.layer as layer, count(m) as c"
            res = session.run(query, params)
            stats["memories_by_layer"] = {rec["layer"]: rec["c"] for rec in res}

            # Count by category
            category_query = (
                f"{match_clause}{where_clause} RETURN m.category as category, count(m) as c"
            )
            res = session.run(category_query, params)
            stats["memories_by_category"] = {rec["category"]: rec["c"] for rec in res}

            # Total
            stats["total_memories"] = sum(stats["memories_by_layer"].values())

            # Relationships between filtered memories
            rel_query = "MATCH (a:Memory)-[r]->(b:Memory)"
            if repo_id:
                rel_query += " WHERE a.repo_id = $repo_id AND b.repo_id = $repo_id"
            rel_query += " RETURN count(r) as c"

            res = session.run(rel_query, params)
            stats["total_relationships"] = res.single()["c"]

            # Active intents, scoped to repo_id when provided.
            intent_query = "MATCH (i:Intent {status: 'active'})"
            if repo_id:
                intent_query += " WHERE i.repo_id = $repo_id"
            intent_query += " RETURN count(i) as c"

            res = session.run(intent_query, params)
            stats["active_intents"] = res.single()["c"]

            return stats

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
        with self.driver.session() as session:
            session.run(
                """
                CREATE (a:AuditLog {
                    id: $id,
                    event_type: $event_type,
                    actor_id: $actor_id,
                    repo_id: $repo_id,
                    target_type: $target_type,
                    target_id: $target_id,
                    metadata: $metadata,
                    created_at: datetime()
                })
                """,
                id=audit_id,
                event_type=event_type,
                actor_id=actor_id,
                repo_id=repo_id,
                target_type=target_type,
                target_id=target_id,
                metadata=self._json_serialize(metadata or {}),
            )
        return audit_id

    def list_audit_logs(
        self,
        actor_id: str = None,
        repo_id: str = None,
        event_type: str = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List audit log entries with optional filters."""
        query = """
            MATCH (a:AuditLog)
            WHERE ($actor_id IS NULL OR a.actor_id = $actor_id)
            AND ($repo_id IS NULL OR a.repo_id = $repo_id)
            AND ($event_type IS NULL OR a.event_type = $event_type)
            RETURN a
            ORDER BY a.created_at DESC
            LIMIT $limit
        """
        with self.driver.session() as session:
            result = session.run(
                query,
                actor_id=actor_id,
                repo_id=repo_id,
                event_type=event_type,
                limit=limit,
            )
            return [self._audit_node_to_dict(dict(record["a"])) for record in result]

    # Repository operations
    def store_repository(self, repo: Dict[str, Any]) -> str:
        repo_id = repo.get("id") or self._generate_id(repo["name"])

        with self.driver.session() as session:
            existing = session.run(
                "MATCH (r:Repository {id: $id}) RETURN r LIMIT 1",
                id=repo_id,
            ).single()
            if existing:
                raise ValueError(f"Repository already exists: {repo_id}")

            session.run(
                """
                CREATE (r:Repository {
                    id: $id,
                    name: $name,
                    url: $url,
                    description: $description,
                    tech_stack: $tech_stack,
                    team_id: $team_id,
                    metadata: $metadata,
                    status: $status,
                    created_at: datetime()
                })
            """,
                id=repo_id,
                name=repo["name"],
                url=repo.get("url"),
                description=repo.get("description"),
                tech_stack=repo.get("tech_stack", []),
                team_id=repo.get("team_id"),
                metadata=self._json_serialize(repo.get("metadata", {})),
                status=repo.get("status", "active"),
            )
        return repo_id

    def get_repository(self, repo_id: str) -> Optional[Dict[str, Any]]:
        with self.driver.session() as session:
            result = session.run("MATCH (r:Repository {id: $id}) RETURN r", id=repo_id)
            record = result.single()
            return self._repository_node_to_dict(dict(record["r"])) if record else None

    def list_repositories(
        self, team_id: str = None, status: str = "active"
    ) -> List[Dict[str, Any]]:
        query = "MATCH (r:Repository)"
        params = {}
        conditions = []
        if team_id:
            conditions.append("r.team_id = $team_id")
            params["team_id"] = team_id
        if status and status != "all":
            conditions.append("coalesce(r.status, 'active') = $status")
            params["status"] = status
        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " RETURN r"
        with self.driver.session() as session:
            result = session.run(query, params)
            return [self._repository_node_to_dict(dict(rec["r"])) for rec in result]

    def update_repository(self, repo_id: str, **kwargs) -> bool:
        allowed = {"name", "url", "description", "tech_stack", "metadata", "status"}
        updates = {
            key: value
            for key, value in kwargs.items()
            if key in allowed and value is not None
        }
        if not updates:
            return False
        if "metadata" in updates:
            updates["metadata"] = self._json_serialize(updates["metadata"])
        clauses = [f"r.{key} = ${key}" for key in updates]
        if updates.get("status") == "archived":
            clauses.append("r.archived_at = datetime()")
        elif updates.get("status") == "active":
            clauses.append("r.archived_at = null")
        with self.driver.session() as session:
            record = session.run(
                "MATCH (r:Repository {id: $id}) SET "
                + ", ".join(clauses)
                + " RETURN count(r) AS c",
                id=repo_id,
                **updates,
            ).single()
        return bool(record and record["c"])

    def delete_repository(self, repo_id: str) -> bool:
        with self.driver.session() as session:
            exists = session.run(
                "MATCH (r:Repository {id: $id}) RETURN count(r) AS c", id=repo_id
            ).single()["c"]
            if not exists:
                return False
            session.run(
                "MATCH (m:Memory {repo_id: $id}) DETACH DELETE m",
                id=repo_id,
            )
            session.run("MATCH (i:Intent {repo_id: $id}) DETACH DELETE i", id=repo_id)
            session.run("MATCH (r:Repository {id: $id}) DETACH DELETE r", id=repo_id)
        return True

    def list_project_ids(self) -> List[str]:
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (m:Memory)
                WHERE m.repo_id IS NOT NULL AND m.repo_id <> ''
                RETURN DISTINCT m.repo_id AS id
                UNION
                MATCH (i:Intent)
                WHERE i.repo_id IS NOT NULL AND i.repo_id <> ''
                RETURN DISTINCT i.repo_id AS id
                UNION
                MATCH (r:Repository)
                WHERE r.id IS NOT NULL AND r.id <> ''
                RETURN DISTINCT r.id AS id
                ORDER BY id
                """
            )
            return [record["id"] for record in result]

    def add_repo_dependency(
        self, source_id: str, target_id: str, dep_type: str, version: str = None, notes: str = None
    ) -> str:
        # Validate/normalize before interpolating into Cypher: ``dep_type`` is the
        # relationship type and cannot be parameterized, so an unsanitized value is a
        # Cypher-injection vector (and hyphen/space variants would be invalid syntax).
        rel_type = _normalize_relationship_type(dep_type)
        rel_id = self._generate_id(f"{source_id}-{target_id}-{rel_type}")

        with self.driver.session() as session:
            session.run(
                f"""
                MATCH (a:Repository {{id: $source_id}}), (b:Repository {{id: $target_id}})
                MERGE (a)-[r:{rel_type}]->(b)
                SET r.id = $rel_id,
                    r.version = $version,
                    r.notes = $notes,
                    r.created_at = datetime()
            """,
                source_id=source_id,
                target_id=target_id,
                rel_id=rel_id,
                version=version,
                notes=notes,
            )
        return rel_id

    def get_repo_dependencies(self, repo_id: str) -> List[Dict[str, Any]]:
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (a:Repository {id: $id})-[r]->(b:Repository)
                RETURN b.id as target_id, type(r) as type, r.version as version, r.notes as notes
            """,
                id=repo_id,
            )
            return [dict(rec) for rec in result]

    # Team and User operations
    def store_user(self, user: Dict[str, Any]) -> str:
        user_id = user["id"]
        if self.get_user(user_id) is not None:
            raise ValueError(f"User already exists: {user_id}")

        with self.driver.session() as session:
            session.run(
                """
                CREATE (u:User {
                    id: $id,
                    username: $username,
                    email: $email,
                    display_name: $display_name,
                    metadata: $metadata,
                    created_at: datetime(),
                    last_active: datetime()
                })
            """,
                id=user_id,
                username=user["username"],
                email=user.get("email"),
                display_name=user.get("display_name"),
                metadata=self._json_serialize(user.get("metadata", {})),
            )
        return user_id

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self.driver.session() as session:
            result = session.run("MATCH (u:User {id: $id}) RETURN u", id=user_id)
            record = result.single()
            return self._user_node_to_dict(dict(record["u"])) if record else None

    def store_team(self, team: Dict[str, Any]) -> str:
        team_id = team["id"]
        if self.get_team(team_id) is not None:
            raise ValueError(f"Team already exists: {team_id}")

        with self.driver.session() as session:
            session.run(
                """
                CREATE (t:Team {
                    id: $id,
                    name: $name,
                    description: $description,
                    metadata: $metadata,
                    created_at: datetime()
                })
            """,
                id=team_id,
                name=team["name"],
                description=team.get("description"),
                metadata=self._json_serialize(team.get("metadata", {})),
            )
        return team_id

    def get_team(self, team_id: str) -> Optional[Dict[str, Any]]:
        with self.driver.session() as session:
            result = session.run("MATCH (t:Team {id: $id}) RETURN t", id=team_id)
            record = result.single()
            return self._team_node_to_dict(dict(record["t"])) if record else None

    def add_team_member(self, team_id: str, user_id: str) -> bool:
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (t:Team {id: $team_id}), (u:User {id: $user_id})
                MERGE (u)-[r:MEMBER_OF]->(t)
                RETURN count(r) as c
            """,
                team_id=team_id,
                user_id=user_id,
            )
            record = result.single()
            return bool(record and record["c"] > 0)

    def get_user_teams(self, user_id: str) -> List[Dict[str, Any]]:
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (u:User {id: $id})-[:MEMBER_OF]->(t:Team)
                RETURN t
            """,
                id=user_id,
            )
            return [self._team_node_to_dict(dict(rec["t"])) for rec in result]
