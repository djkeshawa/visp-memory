"""
Neo4j Storage implementation for LLM Memory.
"""

import hashlib
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from neo4j import GraphDatabase
except ImportError:  # pragma: no cover - exercised only when optional extra is absent
    GraphDatabase = None

from llm_memory.config import load_config
from llm_memory.core.indexing import EmbeddingIndexReport, ReindexResult, ReindexScope
from llm_memory.core.ranking import (
    clamp_score,
    rank_memory_results,
    relationship_score,
    text_similarity,
)
from llm_memory.core.storage import BaseStorage, MemoryLayer

logger = logging.getLogger(__name__)
_RELATIONSHIP_TYPE_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


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
            raise ImportError("neo4j is required for Neo4j storage: pip install llm-memory[neo4j]")

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

        try:
            self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
            self.verify_connectivity()
            self._ensure_indexes()
        except Exception as e:
            logger.error(f"Failed to initialize Neo4j driver: {e}")
            raise

    def close(self):
        """Close driver connection."""
        if self.driver:
            self.driver.close()

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
        """Generate unique ID for content."""
        timestamp = datetime.now().isoformat()
        return hashlib.sha256(f"{content}{timestamp}".encode()).hexdigest()[:16]

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
    def _node_to_dict(cls, node: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize Neo4j node properties to the storage dict contract."""
        data = dict(node)
        for key in list(data):
            if key == "embedding" or key.startswith("embedding_"):
                data.pop(key, None)

        for field in ("metadata", "context"):
            if field in data and isinstance(data[field], str):
                data[field] = cls._json_deserialize(data[field])

        for field in ("tags", "source_ids", "quality_flags", "tech_stack", "memory_ids"):
            if field in data and isinstance(data[field], str):
                data[field] = cls._json_deserialize(data[field])
            elif field in data and data[field] is None:
                data[field] = []

        for field in ("created_at", "updated_at", "accessed_at", "last_active"):
            value = data.get(field)
            if hasattr(value, "iso_format"):
                data[field] = value.iso_format()
            elif hasattr(value, "isoformat"):
                data[field] = value.isoformat()

        data.setdefault("tags", [])
        data.setdefault("metadata", {})
        data.setdefault("source_ids", [])
        data.setdefault("status", "active")
        data.setdefault("quality_flags", [])
        return data

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
        status: str = "active",
        source: str = None,
        quality_flags: List[str] = None,
        embedding: List[float] = None,
        auto_link: bool = True,
        auto_link_limit: int = DEFAULT_AUTO_LINK_LIMIT,
        auto_link_min_score: float = DEFAULT_AUTO_LINK_MIN_SCORE,
    ) -> str:
        """Store a memory node."""

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
            self.add_relationship(
                source_id=source_id,
                target_id=memory_id,
                relationship=self.SOURCE_LINK_RELATIONSHIP,
                strength=1.0,
            )

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

            self.add_relationship(
                source_id=memory_id,
                target_id=candidate_id,
                relationship=self.AUTO_LINK_RELATIONSHIP,
                strength=score,
            )

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
                SET m.access_count = m.access_count + 1, m.accessed_at = datetime()
                RETURN m
            """,
                id=memory_id,
            )
            record = result.single()

            if record:
                node = dict(record["m"])
                # Clean up internal props if any
                return self._node_to_dict(node)
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

        embedding = kwargs.get("embedding")
        status = kwargs.get("status", "active")

        # Generate embedding from query if not provided and embedding function is available
        if not embedding and self._embedding_fn is not None:
            try:
                embedding = self._embedding_fn(query)
            except Exception as e:
                logger.warning(f"Failed to generate embedding for query: {e}")
                embedding = None

        fallback_cypher = """
            MATCH (m:Memory)
            WHERE ($layer IS NULL OR m.layer = $layer)
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
            # Vector Search
            cypher = f"""
                CALL db.index.vector.queryNodes('{self._vector_index}', $limit, $embedding)
                YIELD node, score
                WHERE ($layer IS NULL OR node.layer = $layer)
                AND ($repo_id IS NULL OR node.repo_id = $repo_id)
                AND ($category IS NULL OR node.category = $category)
                AND (
                    $status = 'all'
                    OR node.status = $status
                    OR ($status = 'active' AND node.status IS NULL)
                )
                AND node.importance >= $min_importance
                RETURN node as m, score
            """

        try:
            return self._run_memory_search(
                cypher,
                query=query,
                embedding=embedding,
                limit=limit,
                layer=layer,
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
                layer=layer,
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
                mem = self._node_to_dict(dict(record["m"]))
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
            return [self._node_to_dict(dict(record["m"])) for record in result]

    def update_memory(self, memory_id: str, **kwargs) -> bool:
        """Update properties."""
        clauses = []
        params = {"id": memory_id}
        content = kwargs.get("content")

        for k, v in kwargs.items():
            if k == "metadata":
                v = self._json_serialize(v)
            if k == "tags":
                v = v  # List is fine

            clauses.append(f"SET m.{k} = ${k}")
            params[k] = v

        if not clauses:
            return False

        query = "MATCH (m:Memory {id: $id}) " + "\n".join(clauses) + " RETURN count(m) as c"

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
                except Exception:
                    pass
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
                    embedding = self._embedding_fn(memory["content"])
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
            return [self._node_to_dict(dict(rec["i"])) for rec in result]

    def complete_intent(self, intent_id: str) -> bool:
        return self.update_intent(intent_id, status="completed")

    def update_intent(self, intent_id: str, **kwargs) -> bool:
        allowed_fields = {"description", "priority", "status", "context"}
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
    def add_relationship(
        self,
        source_id: str,
        target_id: str,
        relationship: str,
        strength: float = 1.0,
        evidence: Dict[str, Any] = None,
    ) -> str:
        """Create a relationship."""
        rel_type = _normalize_relationship_type(relationship)
        rel_id = self._generate_id(f"{source_id}-{target_id}-{rel_type}")
        auto_rel_type = _normalize_relationship_type(self.AUTO_LINK_RELATIONSHIP)
        evidence_data = self._normalize_relationship_evidence(
            evidence,
            strength=strength,
            created_at=None,
            legacy=False,
        )

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
                item = self._node_to_dict(dict(record["related"]))
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

    # Session Ops
    def start_session(self) -> str:
        sid = self._generate_id("session")
        with self.driver.session() as session:
            session.run("create (s:Session {id: $id, started_at: datetime()})", id=sid)
        return sid

    def end_session(self, session_id: str, summary: str, memory_ids: List[str]):
        with self.driver.session() as session:
            session.run(
                """
                MATCH (s:Session {id: $id})
                SET s.summary = $summary,
                    s.memory_ids = $mem_ids,
                    s.ended_at = datetime()
            """,
                id=session_id,
                summary=summary,
                mem_ids=memory_ids,
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

            # Total
            stats["total_memories"] = sum(stats["memories_by_layer"].values())

            # Intents (Intents might not have repo_id property on the node itself yet?
            # We implemented set_intent but need to check if it adds repo_id)
            # Assuming we want to filter intents too if we add repo_id to them.
            # For now, let's keep intents global or update set_intent?
            # set_intent in Neo4jStorage may not take repo_id in older deployments.
            # Keep intents global unless the property exists.

            # Count active intents
            # If intents are shared, maybe we don't filter?
            # But goals should probably be isolated too.
            # Let's check if we can filter by repo_id on Intent nodes.
            # Assuming set_intent adds it if passed.

            # For now, just filtering Memories.

            # Rels
            # Rels between filtered memories
            rel_query = """
                MATCH (a:Memory)-[r]->(b:Memory)
                """
            if repo_id:
                rel_query += " WHERE a.repo_id = $repo_id AND b.repo_id = $repo_id"
            rel_query += " RETURN count(r) as c"

            res = session.run(rel_query, params)
            stats["total_relationships"] = res.single()["c"]

            # Active Intents count requires Intent nodes to have repo_id.
            # Let's query active intents with repo_id if available
            intent_query = "MATCH (i:Intent {status: 'active'})"
            # Note: We haven't verified if Intent nodes have repo_id.
            # If not, this might return 0 if we filter.
            # Let's assume for this step we only filter Memories strictly.
            # But for consistency, valid project stats should include project goals.

            # Let's assume global intents for now or filter if property exists
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
            return [self._node_to_dict(dict(record["a"])) for record in result]

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
            )
        return repo_id

    def get_repository(self, repo_id: str) -> Optional[Dict[str, Any]]:
        with self.driver.session() as session:
            result = session.run("MATCH (r:Repository {id: $id}) RETURN r", id=repo_id)
            record = result.single()
            return self._node_to_dict(dict(record["r"])) if record else None

    def list_repositories(self, team_id: str = None) -> List[Dict[str, Any]]:
        query = "MATCH (r:Repository)"
        params = {}
        if team_id:
            query += " WHERE r.team_id = $team_id"
            params["team_id"] = team_id

        query += " RETURN r"
        with self.driver.session() as session:
            result = session.run(query, params)
            return [self._node_to_dict(dict(rec["r"])) for rec in result]

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
        rel_type = dep_type.upper()
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
            return self._node_to_dict(dict(record["u"])) if record else None

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
            return self._node_to_dict(dict(record["t"])) if record else None

    def add_team_member(self, team_id: str, user_id: str) -> bool:
        with self.driver.session() as session:
            session.run(
                """
                MATCH (t:Team {id: $team_id}), (u:User {id: $user_id})
                MERGE (u)-[:MEMBER_OF]->(t)
            """,
                team_id=team_id,
                user_id=user_id,
            )
        return True

    def get_user_teams(self, user_id: str) -> List[Dict[str, Any]]:
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (u:User {id: $id})-[:MEMBER_OF]->(t:Team)
                RETURN t
            """,
                id=user_id,
            )
            return [self._node_to_dict(dict(rec["t"])) for rec in result]
