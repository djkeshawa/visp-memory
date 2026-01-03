"""
Neo4j Storage implementation for LLM Memory.
"""

from typing import Optional, List, Dict, Any, Union
import json
import hashlib
from datetime import datetime
import logging
import re
from neo4j import GraphDatabase, Driver

from llm_memory.core.storage import BaseStorage, MemoryLayer
from llm_memory.config import load_config

logger = logging.getLogger(__name__)

class Neo4jStorage(BaseStorage):
    """
    Storage implementation using Neo4j for both structured data and vector embeddings.
    """

    def __init__(self, uri: str = None, user: str = None, password: str = None):
        """Initialize Neo4j driver."""
        config = load_config()
        self.uri = uri or config.storage.neo4j_uri
        self.user = user or config.storage.neo4j_user
        self.password = password or config.storage.neo4j_password
        
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
            session.run("CREATE CONSTRAINT memory_id_unique IF NOT EXISTS FOR (m:Memory) REQUIRE m.id IS UNIQUE")
            session.run("CREATE CONSTRAINT intent_id_unique IF NOT EXISTS FOR (i:Intent) REQUIRE i.id IS UNIQUE")
            session.run("CREATE CONSTRAINT session_id_unique IF NOT EXISTS FOR (s:Session) REQUIRE s.id IS UNIQUE")
            
            # Vector Index for embeddings (dim=384 for all-MiniLM-L6-v2)
            # Note: This assumes Neo4j 5.15+
            try:
                session.run("""
                    CREATE VECTOR INDEX memory_embedding_index IF NOT EXISTS
                    FOR (m:Memory) ON (m.embedding)
                    OPTIONS {indexConfig: {
                        `vector.dimensions`: 384,
                        `vector.similarity_function`: 'cosine'
                    }}
                """)
            except Exception as e:
                logger.warning(f"Could not create vector index (might be already present or incompatible version): {e}")

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
        """Store a memory node."""
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
                    access_count: coalesce(m.access_count, 0)
                }
            """
            
            # Conditionally add vector property
            if embedding:
                 query += """
                 WITH m
                 CALL db.create.setNodeVectorProperty(m, 'embedding', $embedding)
                 """

            session.run(query, 
                id=memory_id,
                content=content,
                layer=layer,
                repo_id=repo_id,
                category=category,
                importance=importance,
                tags=tags,
                metadata=self._json_serialize(metadata),
                source_ids=source_ids,
                embedding=embedding
            )
            
            # 2. Add extra labels
            for label in labels:
                if label != "Memory":
                     session.run(f"MATCH (m:Memory {{id: $id}}) SET m:{label}", id=memory_id)
        
        return memory_id

    def get_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Get a memory by ID."""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (m:Memory {id: $id})
                SET m.access_count = m.access_count + 1, m.accessed_at = datetime()
                RETURN m
            """, id=memory_id)
            record = result.single()
            
            if record:
                node = dict(record["m"])
                # Clean up internal props if any
                return self._node_to_dict(node)
            return None

    def search_memories(
        self,
        query: str, # Note: This interface expects text, but Neo4j needs vector. 
                    # Caller should ideally provide embedding, but here we assume 'storage' implies *just* storage.
                    # HOWEVER, BaseStorage interface is leaky.
                    # For now, we assume the caller handles embedding generation logic OR this class needs access to embedding_fn.
                    # We will implement exact match or property match here, BUT for vector we need embedding.
                    # Wait, LocalStorage takes embedding_fn. We should too or expect embedding passed in kwargs.
        repo_id: str = None,
        layer: str = None,
        category: str = None,
        limit: int = 10,
        min_importance: float = 0.0,
        **kwargs # embedding: List[float] should be here
    ) -> List[Dict[str, Any]]:
        
        embedding = kwargs.get("embedding")
        if not embedding:
            # Fallback to simple text search or property filter if no embedding provided
            # Or raise error / warn
            logger.warning("No embedding provided for vector search. Falling back to property filter.")
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
                "min_importance": min_importance
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
        **kwargs
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
            result = session.run(query, layer=layer, repo_id=repo_id, category=category, limit=limit)
            return [self._node_to_dict(dict(record["m"])) for record in result]

    def update_memory(self, memory_id: str, **kwargs) -> bool:
        """Update properties."""
        clauses = []
        params = {"id": memory_id}
        
        for k, v in kwargs.items():
            if k == "metadata":
                v = self._json_serialize(v)
            if k == "tags":
                v = v # List is fine
            
            clauses.append(f"SET m.{k} = ${k}")
            params[k] = v
            
        if not clauses:
            return False
            
        query = f"MATCH (m:Memory {{id: $id}}) " + "\n".join(clauses) + " RETURN count(m) as c"
        
        with self.driver.session() as session:
            result = session.run(query, **params)
            return result.single()["c"] > 0

    def delete_memory(self, memory_id: str) -> bool:
        """Delete node and relationships."""
        with self.driver.session() as session:
            result = session.run("MATCH (m:Memory {id: $id}) DETACH DELETE m", id=memory_id)
            return True # Neo4j doesn't easily return count of deleted items in simple query without stats

    def get_collection(self, layer: str):
        """Not applicable for Neo4j (unified)."""
        return None

    # Intent Operations
    def set_intent(self, description: str, priority: int = 0, repo_id: str = None, context: Dict[str, Any] = None) -> str:
        intent_id = self._generate_id(description)
        context = context or {}
        
        with self.driver.session() as session:
            session.run("""
                MERGE (i:Intent {id: $id})
                SET i.description = $description,
                    i.priority = $priority,
                    i.status = 'active',
                    i.repo_id = $repo_id,
                    i.context = $context,
                    i.created_at = datetime(),
                    i.updated_at = datetime()
            """, id=intent_id, description=description, priority=priority, repo_id=repo_id, context=self._json_serialize(context))
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
            result = session.run("""
                MATCH (i:Intent {id: $id})
                SET i.status = 'completed', i.updated_at = datetime()
                RETURN count(i) as c
            """, id=intent_id)
            return result.single()["c"] > 0

    # Relationship Operations
    def add_relationship(
        self,
        source_id: str,
        target_id: str,
        relationship: str,
        strength: float = 1.0
    ) -> str:
        """Create a relationship."""
        # Normalize relationship type (uppercase, no spaces)
        rel_type = relationship.upper().replace(" ", "_")
        rel_id = self._generate_id(f"{source_id}-{target_id}-{rel_type}")
        
        with self.driver.session() as session:
            session.run(f"""
                MATCH (a:Memory {{id: $source_id}}), (b:Memory {{id: $target_id}})
                MERGE (a)-[r:{rel_type}]->(b)
                SET r.id = $rel_id, r.weight = $strength, r.created_at = datetime()
            """, source_id=source_id, target_id=target_id, rel_id=rel_id, strength=strength)
        return rel_id

    def get_related_memories(self, memory_id: str, relationship: str = None) -> List[Dict[str, Any]]:
        """Get connected neighbors."""
        # NOTE: Parameterizing relationship types in Cypher is tricky (apoc or simple workaround)
        # Check against basic injection
        rel_clause = ""
        if relationship:
            safe_rel = relationship.upper().replace(" ", "_")
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
            
            query += " RETURN a.id as source, b.id as target, type(r) as type, r.weight as weight, r.id as id"
            
            result = session.run(query, params)
            return [{
                "id": rec.get("id") or f"{rec['source']}-{rec['target']}",
                "source_id": rec["source"],
                "target_id": rec["target"],
                "relationship": rec["type"],
                "strength": rec.get("weight", 1.0)
            } for rec in result]

    # Session Ops
    def start_session(self) -> str:
        sid = self._generate_id("session")
        with self.driver.session() as session:
            session.run("create (s:Session {id: $id, started_at: datetime()})", id=sid)
        return sid

    def end_session(self, session_id: str, summary: str, memory_ids: List[str]):
         with self.driver.session() as session:
            session.run("""
                MATCH (s:Session {id: $id})
                SET s.summary = $summary, 
                    s.memory_ids = $mem_ids,
                    s.ended_at = datetime()
            """, id=session_id, summary=summary, mem_ids=memory_ids)

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
            # set_intent in Neo4jStorage doesn't seem to take repo_id in the signature shown earlier?
            # Let's check set_intent first. For now, leaving intents as is, or adding simple filter if properties exist.
            
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
            
            # Active Intents count - if we want to isolate, we need to ensure Intent nodes have repo_id
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

    def _node_to_dict(self, node: Dict[str, Any]) -> Dict[str, Any]:
        """Convert Neo4j node props to dict, deserializing JSON."""
        d = dict(node)
        for k in ["metadata", "context"]:
            if k in d and isinstance(d[k], str):
                d[k] = self._json_deserialize(d[k])
        # Convert Neo4j DateTime to str compatible with Python
        for k, v in d.items():
            if hasattr(v, "isoformat"):
                s = v.isoformat()
                # Truncate nanoseconds (9 digits) to microseconds (6 digits)
                # Matches .123456... and keeps .123456
                s = re.sub(r'(\.\d{6})\d+', r'\1', s)
                d[k] = s
        return d
