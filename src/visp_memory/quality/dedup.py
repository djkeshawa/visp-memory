"""
Deduplication Module

Identifies and merges duplicate memories.
"""

import logging
from typing import Any, Dict, List

try:
    import numpy as np
    from sklearn.metrics.pairwise import cosine_similarity

    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    # Fallback to simple numpy if available, or just error
    try:
        import numpy as np

        NUMPY_AVAILABLE = True
    except ImportError:
        NUMPY_AVAILABLE = False

from visp_memory.core.storage import BaseStorage

logger = logging.getLogger(__name__)


class Deduplicator:
    """
    Identifies and handles duplicate memories.
    """

    def __init__(self, storage: BaseStorage):
        self.storage = storage

    def find_duplicates(
        self,
        layer: str = "episodic",
        content: str = None,
        embedding: List[float] = None,
        threshold: float = 0.9,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Find duplicates for a given content or existing memories.

        Args:
            layer: Memory layer to search
            content: Content to check against (optional)
            embedding: Embedding to check against (optional)
            threshold: Similarity threshold (0.0 to 1.0)
            limit: Max duplicates to return

        Returns:
            List of similar memories with 'similarity' score
        """
        if not self._check_deps():
            return []

        # Check if storage has ChromaDB collection interface
        if hasattr(self.storage, "_get_collection"):
            # LocalStorage with ChromaDB
            collection = self.storage._get_collection(layer)
            if not collection:
                return []

            # If content/embedding provided, check against it
            if content or embedding:
                return self._find_similar_to_new(collection, content, embedding, threshold, limit)

            # Otherwise, check for duplicates within the collection (batch mode)
            return self._find_internal_duplicates(collection, threshold, limit)
        else:
            # Neo4jStorage or other backend - use search_memories interface
            return self._find_duplicates_via_search(layer, content, embedding, threshold, limit)

    def _find_similar_to_new(
        self, collection, content: str, embedding: List[float], threshold: float, limit: int
    ) -> List[Dict[str, Any]]:
        """Find memories similar to new content."""
        query_texts = [content] if content else None
        query_embeddings = [embedding] if embedding else None

        try:
            results = collection.query(
                query_texts=query_texts, query_embeddings=query_embeddings, n_results=limit
            )
        except Exception:
            return []

        duplicates = []
        if results and results["ids"] and results["ids"][0]:
            for i, mem_id in enumerate(results["ids"][0]):
                distance = results["distances"][0][i] if results.get("distances") else 0
                similarity = 1 - distance

                if similarity >= threshold:
                    mem = self.storage.get_memory(mem_id)
                    if mem:
                        mem["similarity"] = similarity
                        duplicates.append(mem)

        return duplicates

    def _find_internal_duplicates(
        self, collection, threshold: float, limit: int
    ) -> List[List[Dict[str, Any]]]:
        """
        Find duplicates within existing memories.

        Returns groups of duplicates.
        """
        # Fetch all embeddings
        # Note: robust implementation would do this in batches
        try:
            data = collection.get(include=["embeddings", "metadatas", "documents"])
        except Exception:
            return []

        # Check if data is valid and has embeddings
        if not data or data.get("embeddings") is None or len(data.get("embeddings", [])) == 0:
            return []

        embeddings = data["embeddings"]
        ids = data["ids"]

        if SKLEARN_AVAILABLE:
            matrix = np.array(embeddings)
            sim_matrix = cosine_similarity(matrix)
        elif NUMPY_AVAILABLE:
            # Basic cosine similarity manually
            matrix = np.array(embeddings)
            norm = np.linalg.norm(matrix, axis=1, keepdims=True)
            sim_matrix = np.dot(matrix, matrix.T) / (np.dot(norm, norm.T) + 1e-9)
        else:
            return []

        duplicate_groups = []
        visited = set()

        for i in range(len(ids)):
            if i in visited:
                continue

            group = []
            for j in range(i + 1, len(ids)):
                if j in visited:
                    continue

                # Use proper 2D indexing for numpy arrays
                similarity = (
                    float(sim_matrix[i, j])
                    if hasattr(sim_matrix[i, j], "__float__")
                    else sim_matrix[i, j]
                )
                if similarity >= threshold:
                    if not group:
                        group.append(self.storage.get_memory(ids[i]))
                        visited.add(i)

                    group.append(self.storage.get_memory(ids[j]))
                    visited.add(j)

            if group:
                duplicate_groups.append(group)
                if len(duplicate_groups) >= limit:
                    break

        return duplicate_groups

    def merge_memories(self, memory_ids: List[str], target_content: str = None) -> str:
        """
        Merge multiple memories into one.

        Args:
            memory_ids: IDs of memories to merge
            target_content: Content for the merged memory (optional, defaults to first)

        Returns:
            ID of the preserved memory
        """
        if not memory_ids:
            return None

        primary_id = memory_ids[0]
        others = memory_ids[1:]

        primary_mem = self.storage.get_memory(primary_id)
        if not primary_mem:
            return None

        # Update content if provided
        if target_content:
            self.storage.update_memory(primary_id, content=target_content)

        # Merge provenance from the duplicates into the primary. ``update_memory``
        # exposes no ``source_ids`` parameter, so the merged provenance is preserved
        # inside metadata rather than being silently dropped.
        merged_source_ids = list(primary_mem.get("source_ids") or [])

        # Union tags from the merged-away duplicates into the primary. Otherwise the
        # duplicates are hard-deleted below and their tags are lost forever. Order is
        # preserved (primary's tags first, then newly seen tags from duplicates) and
        # duplicates are dropped via ``dict.fromkeys``.
        merged_tags = list(primary_mem.get("tags") or [])

        retargeted_relationships = self._retarget_relationships(primary_id, others)

        for oid in others:
            mem = self.storage.get_memory(oid)
            if not mem:
                continue

            # Record the duplicate (and its own sources) as provenance.
            merged_source_ids.append(oid)
            if mem.get("source_ids"):
                merged_source_ids.extend(mem["source_ids"])

            # Preserve the duplicate's tags on the primary before deletion.
            if mem.get("tags"):
                merged_tags.extend(mem["tags"])

            # Delete the duplicate after relationship retargeting has preserved
            # graph evidence that would otherwise cascade away.
            self.storage.delete_memory(oid)

        # De-duplicate while preserving order, dropping any self-reference.
        deduped_source_ids = [
            sid for sid in dict.fromkeys(merged_source_ids) if sid and sid != primary_id
        ]

        # De-duplicate tags while preserving insertion order, dropping empties.
        deduped_tags = [tag for tag in dict.fromkeys(merged_tags) if tag]

        # Merge into the primary's existing metadata instead of replacing it, so
        # prior keys (applies_to, established_at, compressed_from, ...) survive.
        merged_metadata = {
            **(primary_mem.get("metadata") or {}),
            "merged_count": len(others),
            "merged_source_ids": deduped_source_ids,
        }
        if retargeted_relationships:
            merged_metadata["retargeted_relationship_count"] = retargeted_relationships
        self.storage.update_memory(primary_id, metadata=merged_metadata, tags=deduped_tags)

        return primary_id

    def _retarget_relationships(self, primary_id: str, duplicate_ids: List[str]) -> int:
        """Retarget duplicate relationships to the preserved primary before deletion."""
        if not duplicate_ids:
            return 0
        if not hasattr(self.storage, "get_all_relationships") or not hasattr(
            self.storage, "add_relationship"
        ):
            return 0

        primary_mem = self.storage.get_memory(primary_id)
        repo_id = primary_mem.get("repo_id") if primary_mem else None
        duplicate_set = set(duplicate_ids)

        try:
            relationships = self.storage.get_all_relationships(repo_id=repo_id)
        except Exception as exc:
            logger.warning("Could not inspect relationships before dedup merge: %s", exc)
            return 0

        retargeted = 0
        seen: set[tuple[str, str, str]] = set()
        for relationship in relationships:
            source_id = relationship.get("source_id")
            target_id = relationship.get("target_id")
            if source_id not in duplicate_set and target_id not in duplicate_set:
                continue

            new_source_id = primary_id if source_id in duplicate_set else source_id
            new_target_id = primary_id if target_id in duplicate_set else target_id
            relationship_type = relationship.get("relationship") or "related"
            if not new_source_id or not new_target_id or new_source_id == new_target_id:
                continue

            retarget_key = (new_source_id, new_target_id, relationship_type)
            if retarget_key in seen:
                continue
            seen.add(retarget_key)

            try:
                self.storage.add_relationship(
                    new_source_id,
                    new_target_id,
                    relationship_type,
                    strength=relationship.get("strength", 1.0),
                    evidence=relationship.get("evidence"),
                )
                retargeted += 1
            except Exception as exc:
                logger.warning(
                    "Could not retarget relationship %s during dedup merge: %s",
                    relationship.get("id"),
                    exc,
                )

        return retargeted

    def _find_duplicates_via_search(
        self, layer: str, content: str, embedding: List[float], threshold: float, limit: int
    ) -> List[Dict[str, Any]]:
        """
        Find duplicates using storage search interface (for Neo4j and others).

        This is simpler but less exhaustive than ChromaDB-based deduplication.
        """
        if not content:
            # Without content, we can't do much with search-based approach
            # For now, just return empty - full dedup requires collection access
            logger.info(
                "Full deduplication is not supported for this storage backend. "
                "Provide content to check for duplicates."
            )
            return []

        # Search for similar memories
        results = self.storage.search_memories(
            query=content, layer=layer, limit=limit, embedding=embedding
        )

        # Filter by threshold
        duplicates = [r for r in results if r.get("similarity", 0) >= threshold]

        return duplicates

    def _check_deps(self) -> bool:
        """Check if dependencies are available."""
        if not (SKLEARN_AVAILABLE or NUMPY_AVAILABLE):
            logger.warning(
                "Deduplication requires scikit-learn or numpy. "
                "Install with: pip install visp-memory[analysis]"
            )
            return False
        return True
