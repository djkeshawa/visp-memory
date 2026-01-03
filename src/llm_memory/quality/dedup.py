"""
Deduplication Module

Identifies and merges duplicate memories.
"""

from typing import List, Dict, Any, Optional, Set
import logging
from collections import defaultdict

try:
    from sklearn.metrics.pairwise import cosine_similarity
    import numpy as np
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    # Fallback to simple numpy if available, or just error
    try:
        import numpy as np
        NUMPY_AVAILABLE = True
    except ImportError:
        NUMPY_AVAILABLE = False

from llm_memory.core.storage import BaseStorage

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
        limit: int = 5
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
            
        # Get collection
        collection = self.storage._get_collection(layer)
        if not collection:
            return []

        # If content/embedding provided, check against it
        if content or embedding:
            return self._find_similar_to_new(
                collection, content, embedding, threshold, limit
            )
        
        # Otherwise, check for duplicates within the collection (batch mode)
        # This is expensive and should be run periodically
        return self._find_internal_duplicates(collection, threshold, limit)

    def _find_similar_to_new(
        self, 
        collection, 
        content: str, 
        embedding: List[float], 
        threshold: float,
        limit: int
    ) -> List[Dict[str, Any]]:
        """Find memories similar to new content."""
        query_texts = [content] if content else None
        query_embeddings = [embedding] if embedding else None
        
        try:
            results = collection.query(
                query_texts=query_texts,
                query_embeddings=query_embeddings,
                n_results=limit
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
        self, 
        collection, 
        threshold: float,
        limit: int
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

        if not data or not data["embeddings"]:
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
                
                if sim_matrix[i][j] >= threshold:
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
            
        # Merge metadata/stats from others
        merged_source_ids = primary_mem.get("source_ids", [])
        
        for oid in others:
            mem = self.storage.get_memory(oid)
            if not mem:
                continue
                
            # Add as source
            merged_source_ids.append(oid)
            if mem.get("source_ids"):
                merged_source_ids.extend(mem["source_ids"])
                
            # Delete the duplicate
            self.storage.delete_memory(oid)
            
        # Update primary with merged sources
        self.storage.update_memory(primary_id, metadata={"merged_count": len(others)})
        
        return primary_id

    def _check_deps(self) -> bool:
        """Check if dependencies are available."""
        if not (SKLEARN_AVAILABLE or NUMPY_AVAILABLE):
            logger.warning("Deduplication requires scikit-learn or numpy. Install with: pip install llm-memory[analysis]")
            return False
        return True
