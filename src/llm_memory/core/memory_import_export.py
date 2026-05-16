"""Import/export helpers for the Memory facade."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


def export_memory(memory: Any, path: Path = None) -> Dict[str, Any]:
    """Export memories, intents, config, and stats for the configured repository."""
    repo_id = memory.config.repo_id

    export_data = {
        "version": "1.0",
        "exported_at": datetime.now().isoformat(),
        "config": memory.config.model_dump(),
        "memories": {
            "episodic": memory._storage.list_memories(
                layer="episodic", limit=10000, repo_id=repo_id
            ),
            "semantic": memory._storage.list_memories(
                layer="semantic", limit=10000, repo_id=repo_id
            ),
        },
        "intents": memory._storage.get_active_intents(repo_id=repo_id),
        "stats": memory.stats(),
    }

    if path:
        Path(path).write_text(json.dumps(export_data, indent=2, default=str))

    return export_data


def import_memories(memory: Any, path: Path) -> None:
    """Import memories from a JSON export into a Memory instance."""
    data = json.loads(Path(path).read_text())

    for mem in data.get("memories", {}).get("episodic", []):
        memory._storage.store_memory(
            content=mem["content"],
            layer="episodic",
            category=mem.get("category", "note"),
            importance=mem.get("importance", 0.5),
            tags=mem.get("tags", []),
            metadata=mem.get("metadata", {}),
            repo_id=mem.get("repo_id") or memory.config.repo_id,
        )

    for mem in data.get("memories", {}).get("semantic", []):
        memory._storage.store_memory(
            content=mem["content"],
            layer="semantic",
            category=mem.get("category", "fact"),
            importance=mem.get("importance", 0.5),
            tags=mem.get("tags", []),
            metadata=mem.get("metadata", {}),
            repo_id=mem.get("repo_id") or memory.config.repo_id,
        )

    for intent in data.get("intents", []):
        memory._storage.set_intent(
            description=intent["description"],
            priority=intent.get("priority", 1),
            context=intent.get("context", {}),
            repo_id=intent.get("repo_id") or memory.config.repo_id,
        )
