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

            # Vector index dimensions must match the active embedding provider.
            if self._embedding_dimension:
                try:
                    dimensions = int(self._embedding_dimension)
                    session.run(f"""
                    CREATE VECTOR INDEX memory_embedding_index IF NOT EXISTS
                    FOR (m:Memory) ON (m.embedding)
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
        data.pop("embedding", None)

        for field in ("metadata", "context"):
            if field in data and isinstance(data[field], str):
                data[field] = cls._json_deserialize(data[field])

        for field in ("tags", "source_ids", "tech_stack", "memory_ids"):
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
                 CALL db.create.setNodeVectorProperty(m, 'embedding', $embedding)
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
                embedding=embedding,
            )

            # 2. Add extra labels
            for label in labels:
                if label != "Memory":
                    session.run(f"MATCH (m:Memory {{id: $id}}) SET m:{label}", id=memory_id)

        return memory_id

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

        # Generate embedding from query if not provided and embedding function is available
        if not embedding and self._embedding_fn is not None:
            try:
                embedding = self._embedding_fn(query)
            except Exception as e:
                logger.warning(f"Failed to generate embedding for query: {e}")
                embedding = None

        if not embedding:
            # Fallback to simple text search or property filter if no embedding available
            logger.warning(
                "No embedding available for vector search. Falling back to property filter."
            )
            cypher = """
                MATCH (m:Memory)
                WHERE ($layer IS NULL OR m.layer = $layer)
                AND ($repo_id IS NULL OR m.repo_id = $repo_id)
                AND ($category IS NULL OR m.category = $category)
                AND m.importance >= $min_importance
                AND toLower(m.content) CONTAINS toLower($query)
                RETURN m, 0.0 as score
                LIMIT $limit
            """
        else:
            # Vector Search
            cypher = """
                CALL db.index.vector.queryNodes('memory_embedding_index', $limit, $embedding)
                YIELD node, score
                WHERE ($layer IS NULL OR node.layer = $layer)
                AND ($repo_id IS NULL OR node.repo_id = $repo_id)
                AND ($category IS NULL OR node.category = $category)
                AND node.importance >= $min_importance
                RETURN node as m, score
            """

        with self.driver.session() as session:
            params = {
                "query": query,
                "embedding": embedding,
                "limit": limit,
                "layer": layer,
                "repo_id": repo_id,
                "category": category,
                "min_importance": min_importance,
            }
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
        query = """
            MATCH (m:Memory)
            WHERE ($layer IS NULL OR m.layer = $layer)
            AND ($repo_id IS NULL OR m.repo_id = $repo_id)
            AND ($category IS NULL OR m.category = $category)
            RETURN m
            ORDER BY m.created_at DESC
            LIMIT $limit
        """
        with self.driver.session() as session:
            result = session.run(
                query, layer=layer, repo_id=repo_id, category=category, limit=limit
            )
            return [self._node_to_dict(dict(record["m"])) for record in result]

    def update_memory(self, memory_id: str, **kwargs) -> bool:
        """Update properties."""
        clauses = []
        params = {"id": memory_id}

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
            return result.single()["c"] > 0

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

    def get_active_intents(self, repo_id: str = None) -> List[Dict[str, Any]]:
        with self.driver.session() as session:
            query = "MATCH (i:Intent {status: 'active'})"
            params = {}
            if repo_id:
                query += " WHERE i.repo_id = $repo_id"
                params["repo_id"] = repo_id

            query += " RETURN i ORDER BY i.priority DESC, i.created_at DESC"

            result = session.run(query, params)
            return [self._node_to_dict(dict(rec["i"])) for rec in result]

    def complete_intent(self, intent_id: str) -> bool:
        with self.driver.session() as session:
            result = session.run(
                """
                MATCH (i:Intent {id: $id})
                SET i.status = 'completed', i.updated_at = datetime()
                RETURN count(i) as c
            """,
                id=intent_id,
            )
            return result.single()["c"] > 0

    # Relationship Operations
    def add_relationship(
        self, source_id: str, target_id: str, relationship: str, strength: float = 1.0
    ) -> str:
        """Create a relationship."""
        rel_type = _normalize_relationship_type(relationship)
        rel_id = self._generate_id(f"{source_id}-{target_id}-{rel_type}")

        with self.driver.session() as session:
            session.run(
                f"""
                MATCH (a:Memory {{id: $source_id}}), (b:Memory {{id: $target_id}})
                MERGE (a)-[r:{rel_type}]->(b)
                SET r.id = $rel_id, r.weight = $strength, r.created_at = datetime()
            """,
                source_id=source_id,
                target_id=target_id,
                rel_id=rel_id,
                strength=strength,
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
            RETURN related, type(r) as rel_type, r.weight as strength
        """

        with self.driver.session() as session:
            result = session.run(query, id=memory_id)
            items = []
            for record in result:
                item = self._node_to_dict(dict(record["related"]))
                item["relationship"] = record["rel_type"]
                item["strength"] = record["strength"]
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
                "r.weight as weight, r.id as id"
            )

            result = session.run(query, params)
            return [
                {
                    "id": rec.get("id") or f"{rec['source']}-{rec['target']}",
                    "source_id": rec["source"],
                    "target_id": rec["target"],
                    "relationship": rec["type"],
                    "strength": rec.get("weight", 1.0),
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
