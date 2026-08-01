"""
Base Memory Layer

Abstract base class for all memory layers.
"""

from abc import ABC
from typing import Any, Dict, List

from visp_memory.core.storage import BaseStorage


class BaseMemoryLayer(ABC):
    """
    Abstract base class for memory layers.

    Provides common functionality for storage access and basic operations.
    """

    def __init__(self, storage: BaseStorage):
        self.storage = storage

    def search(
        self,
        query: str,
        layer: str,
        category: str = None,
        limit: int = 10,
        repo_id: str = None,
        environment: Any = None,
        task_type: Any = None,
        as_of: Any = None,
    ) -> List[Dict[str, Any]]:
        """
        Search memories in this layer.

        Args:
            query: Search query (semantic search)
            layer: Name of the layer (episodic, semantic, etc.)
            category: Filter by category
            limit: Maximum results
            repo_id: Optional repository filter

        Returns:
            List of matching memories
        """
        return self.storage.search_memories(
            query=query,
            layer=layer,
            category=category,
            limit=limit,
            repo_id=repo_id,
            environment=environment,
            task_type=task_type,
            as_of=as_of,
        )

    def list_items(
        self,
        layer: str,
        category: str = None,
        limit: int = 50,
        order_by: str = "created_at DESC",
        repo_id: str = None,
    ) -> List[Dict[str, Any]]:
        """
        List items in this layer.

        Args:
            layer: Name of the layer
            category: Filter by category
            limit: Max items
            order_by: Ordering clause
            repo_id: Optional repository filter

        Returns:
            List of items
        """
        return self.storage.list_memories(
            layer=layer, category=category, limit=limit, order_by=order_by, repo_id=repo_id
        )
