"""
Deduplication Module

Identifies and merges duplicate memories.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, List, Optional

try:
    import numpy as np

    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

try:
    from sklearn.metrics.pairwise import cosine_similarity

    SKLEARN_AVAILABLE = NUMPY_AVAILABLE
except ImportError:
    # Without scikit-learn the cosine similarity is computed from numpy directly.
    SKLEARN_AVAILABLE = False

from visp_memory.core.storage import BaseStorage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DedupReport:
    """What a duplicate check concluded, kept distinct from why it concluded nothing.

    An empty list used to mean three different things — the check ran and the
    layer is clean, the analysis dependencies are missing, and the backend has no
    batch duplicate check at all. Every one of them printed "No duplicates
    found.", so on the default ``sqlite`` + no-ChromaDB configuration the CLI gave
    a green all-clear it had never earned. An operator who acts on that is acting
    on a check that never ran.

    ``determined`` says whether the check actually completed. ``duplicates`` is
    what it found when it did. ``reason`` says why it could not, and is set only
    when ``determined`` is False. Checked-and-clean, checked-and-found-N, and
    could-not-check-because-X are three answers, and a caller can now tell them
    apart.
    """

    determined: bool
    duplicates: List[Any] = field(default_factory=list)
    reason: Optional[str] = None

    @property
    def count(self) -> int:
        """How many duplicates (or duplicate groups) the check found."""
        return len(self.duplicates)

    @property
    def found_any(self) -> bool:
        """True only when the check ran *and* found something."""
        return self.determined and bool(self.duplicates)

    @property
    def is_clean(self) -> bool:
        """True only when the check ran and found nothing. Never true if it could not run."""
        return self.determined and not self.duplicates

    @classmethod
    def checked(cls, duplicates: List[Any]) -> "DedupReport":
        """The check ran to completion; ``duplicates`` is the whole finding."""
        return cls(determined=True, duplicates=list(duplicates))

    @classmethod
    def clear(cls) -> "DedupReport":
        """The check ran and there was nothing to find."""
        return cls(determined=True)

    @classmethod
    def undetermined(cls, reason: str) -> "DedupReport":
        """The check could not run or could not finish. Never a clean result."""
        return cls(determined=False, reason=reason)


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
    ) -> DedupReport:
        """
        Find duplicates for a given content or existing memories.

        Args:
            layer: Memory layer to search
            content: Content to check against (optional)
            embedding: Embedding to check against (optional)
            threshold: Similarity threshold (0.0 to 1.0)
            limit: Max duplicates to return

        Returns:
            A :class:`DedupReport`. Check ``determined`` before trusting the
            absence of duplicates — an undetermined report is not a clean one.
        """
        unmet = self._unmet_dependency()
        if unmet:
            return DedupReport.undetermined(unmet)

        # Check if storage has ChromaDB collection interface
        if hasattr(self.storage, "_get_collection"):
            # LocalStorage with ChromaDB
            collection = self.storage._get_collection(layer)
            if not collection:
                return DedupReport.undetermined(
                    f"no vector collection is available for the {layer!r} layer — "
                    "ChromaDB is not installed or embeddings are disabled, so there "
                    "are no vectors to compare"
                )

            # If content/embedding provided, check against it. Tested explicitly
            # rather than for truthiness: a numpy embedding has no truth value.
            if content or (embedding is not None and len(embedding) > 0):
                return self._find_similar_to_new(collection, content, embedding, threshold, limit)

            # Otherwise, check for duplicates within the collection (batch mode)
            return self._find_internal_duplicates(collection, layer, threshold, limit)
        else:
            # Neo4jStorage or other backend - use search_memories interface
            return self._find_duplicates_via_search(layer, content, embedding, threshold, limit)

    def _find_similar_to_new(
        self, collection, content: str, embedding: List[float], threshold: float, limit: int
    ) -> DedupReport:
        """Find memories similar to new content.

        The query vector must come from the same provider that wrote the stored
        vectors. Passing ``query_texts`` to Chroma does not do that: Chroma
        embeds the text with the *collection's* embedding function, which
        visp-memory never configures and never uses on the write path. When the
        two providers happen to share a dimension nothing raises — the distances
        are simply computed across two unrelated vector spaces, no candidate
        clears the threshold, and the layer is reported clean. Verified against
        chromadb 1.5.9: three byte-identical memories scored 0.99999 under the
        configured provider's own vector and -0.03 under the collection's, so
        the check returned ``determined=True, is_clean=True`` over all three.
        That is the false green just removed from the other branches, and it is
        why this path never hands raw text to the collection.
        """
        query_embedding, reason = self._query_vector(content, embedding)
        if query_embedding is None:
            return DedupReport.undetermined(reason)

        try:
            results = collection.query(
                query_texts=None, query_embeddings=[query_embedding], n_results=limit
            )
        except Exception as exc:
            return DedupReport.undetermined(f"the vector similarity query failed: {exc}")

        # A malformed result is not an empty one. `ids: [[]]` is a collection
        # that answered and had no neighbour to offer; a missing `ids` is a
        # query whose outcome is unknown, and the two must not share a verdict.
        if not isinstance(results, dict) or "ids" not in results:
            return DedupReport.undetermined(
                "the vector similarity query returned no result set, so whether "
                "the layer holds duplicates is unknown"
            )

        ids = results["ids"] or []
        if not ids or not ids[0]:
            return DedupReport.clear()

        # A result set with no distances carries no evidence of similarity. It
        # used to be read as distance 0 — a perfect match — so every id came
        # back a duplicate on nothing at all. Unmeasured is not identical.
        distances = results.get("distances") or []
        if not distances or distances[0] is None or len(distances[0]) < len(ids[0]):
            return DedupReport.undetermined(
                "the vector similarity query returned matches without distance "
                "scores, so nothing can be compared against the threshold"
            )

        duplicates = []
        for i, mem_id in enumerate(ids[0]):
            similarity = 1 - distances[0][i]
            if similarity >= threshold:
                mem = self.storage.get_memory(mem_id)
                if mem:
                    mem["similarity"] = similarity
                    duplicates.append(mem)

        return DedupReport.checked(duplicates)

    def _query_vector(
        self, content: Optional[str], embedding: Optional[List[float]]
    ) -> tuple[Optional[List[float]], Optional[str]]:
        """Return the vector to query with, or the reason there cannot be one.

        A caller-supplied embedding is used verbatim. Otherwise the content is
        embedded by the storage backend's configured provider — the same one
        that produced every vector in the collection.
        """
        # `if embedding:` would raise on a numpy vector, whose truth value is
        # ambiguous — and callers that already hold a vector tend to hold that.
        if embedding is not None and len(embedding) > 0:
            return list(embedding), None

        embedding_fn = getattr(self.storage, "_embedding_fn", None)
        if embedding_fn is None or getattr(self.storage, "_uses_noop_embeddings", False):
            return None, (
                "the storage backend has no usable embedding provider, so the "
                "content cannot be turned into a query vector comparable with "
                "the stored ones. Pass an explicit embedding, or configure a "
                "real embedding provider."
            )

        try:
            vector = embedding_fn(content)
        except Exception as exc:
            return None, f"the configured embedding provider could not embed the content: {exc}"

        if vector is None or len(vector) == 0:
            return None, "the configured embedding provider returned an empty vector"

        return list(vector), None

    def _find_internal_duplicates(
        self, collection, layer: str, threshold: float, limit: int
    ) -> DedupReport:
        """
        Find duplicates within existing memories.

        Reports groups of duplicates.
        """
        # Fetch all embeddings
        # Note: robust implementation would do this in batches
        try:
            data = collection.get(include=["embeddings", "metadatas", "documents"])
        except Exception as exc:
            return DedupReport.undetermined(
                f"the {layer!r} vector collection could not be read: {exc}"
            )

        # A collection that hands back no embedding data was not inspected; a
        # collection that hands back an empty one genuinely holds nothing to compare.
        if not data or data.get("embeddings") is None:
            return DedupReport.undetermined(
                f"the {layer!r} vector collection returned no embedding data"
            )
        if len(data["embeddings"]) == 0:
            return DedupReport.clear()

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
        else:  # pragma: no cover - _unmet_dependency() already rejected this case
            return DedupReport.undetermined(
                "no similarity backend is available (scikit-learn and numpy are both missing)"
            )

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

        return DedupReport.checked(duplicate_groups)

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
    ) -> DedupReport:
        """
        Find duplicates using storage search interface (for Neo4j and others).

        This is simpler but less exhaustive than ChromaDB-based deduplication.
        """
        if not content:
            # Without content there is no query to run, and this backend exposes no
            # collection to scan. That is a check that did not happen, not a clean layer.
            backend = type(self.storage).__name__
            return DedupReport.undetermined(
                f"whole-layer deduplication is not implemented for {backend} — "
                "it exposes no vector collection to scan. Pass content to check "
                "one memory against the layer."
            )

        # Search for similar memories
        results = self.storage.search_memories(
            query=content, layer=layer, limit=limit, embedding=embedding
        )

        # Filter by threshold
        duplicates = [r for r in results if r.get("similarity", 0) >= threshold]

        return DedupReport.checked(duplicates)

    def _unmet_dependency(self) -> Optional[str]:
        """Return why the similarity analysis cannot run, or None when it can."""
        if not (SKLEARN_AVAILABLE or NUMPY_AVAILABLE):
            return (
                "deduplication requires scikit-learn or numpy, and neither is "
                "installed. Install with: pip install visp-memory[analysis]"
            )
        return None
