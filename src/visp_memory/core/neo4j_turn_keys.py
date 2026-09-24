"""Turn-level search keys for the Neo4j backend.

Keys are ``:MemoryKey`` nodes linked from their memory by ``HAS_TURN_KEY`` and
indexed by their own vector index, so memory-level vector search is unchanged.
Search resolves each hit to the parent memory and applies its current scope and
status. Keys hold no content of their own: the span points into the parent's
verbatim text, and keys are removed with the parent or its repository.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from visp_memory.core.turn_keys import KEY_LAYERS, conversation_keys, key_id, key_text
from visp_memory.quality.secrets import redact_for_storage

logger = logging.getLogger(__name__)


class Neo4jTurnKeys:
    _turn_keys_enabled = False

    @property
    def _turn_key_index(self) -> str:
        name = f"memory_turn_key_index_{int(self._embedding_dimension or 0)}"
        return f"{name}_{self._embedding_space}" if self._embedding_space else name

    def _turn_keys_available(self) -> bool:
        return (
            self._turn_keys_enabled
            and self._embedding_fn is not None
            and not self._uses_noop_embeddings
            and bool(self._embedding_dimension)
        )

    def _ensure_turn_key_index(self, session) -> None:
        if not self._turn_keys_available():
            return
        session.run(f"""
            CREATE VECTOR INDEX {self._turn_key_index} IF NOT EXISTS
            FOR (k:MemoryKey) ON (k.`{self._vector_property}`)
            OPTIONS {{indexConfig: {{
                `vector.dimensions`: {int(self._embedding_dimension)},
                `vector.similarity_function`: 'cosine'
            }}}}
        """)

    @staticmethod
    def _remove_turn_keys_tx(tx, memory_id: str) -> None:
        tx.run("MATCH (k:MemoryKey {parent_id: $id}) DETACH DELETE k", id=memory_id).consume()

    def _index_turn_keys(self, memory_id: str) -> int:
        """(Re)index one memory's keys. Keys are an index: failures only log."""
        if not self._turn_keys_available():
            return 0
        try:
            with self.driver.session() as session:
                record = session.run(
                    "MATCH (m:Memory {id: $id}) RETURN m.content AS content, m.layer AS layer, "
                    "m.repo_id AS repo_id", id=memory_id,
                ).single()
            if record is None:
                return 0
            content = record["content"] or ""
            spans = conversation_keys(content) if record["layer"] in KEY_LAYERS else []
            keys = [{
                "id": key_id(memory_id, span), "start": span[0], "end": span[1],
                "embedding": self._embedding_fn(key_text(content, span)),
            } for span in spans]
            with self._write_session() as tx:
                self._remove_turn_keys_tx(tx, memory_id)
                if keys:
                    tx.run(f"""
                        MATCH (m:Memory {{id: $parent}})
                        UNWIND $keys AS key
                        CREATE (m)-[:HAS_TURN_KEY]->(k:MemoryKey {{
                            id: key.id, parent_id: $parent, repo_id: $repo_id,
                            start: key.start, end: key.end, `{self._vector_property}`: key.embedding
                        }})
                    """, parent=memory_id, repo_id=record["repo_id"], keys=keys).consume()
            return len(keys)
        except Exception as exc:
            logger.warning(
                "Turn-key indexing failed for memory %s (run rebuild_embedding_index "
                "to reconcile): %s", memory_id, exc,
            )
            return 0

    def search_turn_keys(
        self, query: str, *, repo_id: str = None, limit: int = 10, status: str = "active"
    ) -> List[Dict[str, Any]]:
        if not self._turn_keys_available() or limit <= 0:
            return []
        query, _ = redact_for_storage(query, None)
        try:
            embedding = self._query_embedding_fn(query)
            with self.driver.session() as session:
                rows = list(session.run(f"""
                    CALL db.index.vector.queryNodes('{self._turn_key_index}', $fetch, $embedding)
                    YIELD node AS k, score
                    MATCH (m:Memory {{id: k.parent_id}})
                    WHERE ($repo_id IS NULL OR m.repo_id = $repo_id)
                    AND ($status = 'all' OR m.status = $status
                         OR ($status = 'active' AND m.status IS NULL))
                    RETURN m, k.start AS start, k.end AS end, score
                    ORDER BY score DESC LIMIT $limit
                """, fetch=limit * 3, embedding=embedding, repo_id=repo_id,
                    status=status, limit=limit))
        except Exception as exc:
            logger.warning("Turn-key search failed: %s", exc)
            return []
        return [{
            "memory": self._memory_node_to_dict(dict(row["m"])),
            "span": (int(row["start"]), int(row["end"])),
            "similarity": float(row["score"]),
        } for row in rows]
