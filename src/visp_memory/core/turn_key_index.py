"""Chroma-backed turn-key index for the SQLite storage backend.

Keys live in their own collection, so memory-level vector search, index
inspection and coverage counts are unaffected. Every key records its parent
memory, repository and turn span; search resolves hits to the parent's current
row and applies its status, so an archived or deleted parent never surfaces.
"""

from __future__ import annotations

import logging
from typing import Any

from visp_memory.core.ranking import normalize_distance_score
from visp_memory.core.turn_keys import KEY_LAYERS, conversation_keys, key_id, key_text

logger = logging.getLogger(__name__)


class ChromaTurnKeyIndex:
    def __init__(self, storage):
        self._storage = storage
        self._collection = None

    def _name(self) -> str:
        storage = self._storage
        name = f"turnkeys_{storage._embedding_dimension or 0}"
        return f"{name}_{storage._embedding_space}" if storage._embedding_space else name

    def collection(self):
        if self._collection is None:
            client = self._storage._get_chroma()
            if client is None:
                return None
            self._collection = client.get_or_create_collection(
                name=self._name(),
                metadata={"description": "Turn-level search keys", "hnsw:space": "cosine"},
            )
        return self._collection

    def available(self) -> bool:
        storage = self._storage
        return storage._embedding_fn is not None and not storage._uses_noop_embeddings

    def index(self, memory: dict[str, Any]) -> int:
        """(Re)index the keys of one memory; returns how many were written."""
        if memory.get("layer") not in KEY_LAYERS or not self.available():
            return 0
        collection = self.collection()
        if collection is None:
            return 0
        self.remove(memory["id"])
        content = memory.get("content") or ""
        spans = conversation_keys(content)
        if not spans:
            return 0
        embed = self._storage._embedding_fn
        collection.upsert(
            ids=[key_id(memory["id"], span) for span in spans],
            embeddings=[embed(key_text(content, span)) for span in spans],
            metadatas=[{
                "parent_id": memory["id"],
                "repo_id": memory.get("repo_id") or "",
                "start": span[0],
                "end": span[1],
            } for span in spans],
        )
        return len(spans)

    def remove(self, memory_id: str) -> None:
        collection = self.collection()
        if collection is not None:
            collection.delete(where={"parent_id": memory_id})

    def remove_repository(self, repo_id: str) -> None:
        collection = self.collection()
        if collection is not None:
            collection.delete(where={"repo_id": repo_id})

    def search(
        self, query_embedding, *, repo_id: str | None, limit: int, status: str = "active"
    ) -> list[dict[str, Any]]:
        collection = self.collection()
        # Missing project scope never grants a global read.
        if collection is None or limit <= 0 or not repo_id:
            return []
        try:
            result = collection.query(
                query_embeddings=[query_embedding],
                n_results=limit * 3,
                where={"repo_id": repo_id},
            )
        except Exception as exc:  # empty collection or index not yet built
            logger.debug("Turn-key search failed: %s", exc)
            return []
        hits = []
        parents: dict[str, dict[str, Any] | None] = {}  # many keys share one memory
        for index, metadata in enumerate((result.get("metadatas") or [[]])[0]):
            parent_id = metadata["parent_id"]
            if parent_id not in parents:
                parents[parent_id] = self._storage._get_memory_row(parent_id, track_access=False)
            parent = parents[parent_id]
            if parent is None or parent.get("repo_id") != repo_id or (
                status != "all" and parent.get("status") != status
            ):
                continue
            distance = result["distances"][0][index] if result.get("distances") else 0
            hits.append({
                "memory": parent,
                "span": (int(metadata["start"]), int(metadata["end"])),
                "similarity": normalize_distance_score(distance),
            })
            if len(hits) == limit:
                break
        return hits
