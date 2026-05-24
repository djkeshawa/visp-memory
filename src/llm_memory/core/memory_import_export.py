"""Import/export helpers for the Memory facade."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

REDACTED_SECRET = "***REDACTED***"
_SENSITIVE_CONFIG_KEYS = {
    "api_key",
    "api_keys",
    "jwt_token",
    "jwt_secret",
    "neo4j_password",
}


def _scrub_memory_vectors(item: Dict[str, Any]) -> Dict[str, Any]:
    """Remove vector payloads before export files are written."""
    return {
        key: value
        for key, value in item.items()
        if key != "embedding" and not key.startswith("embedding_")
    }


def _redact_config_secrets(value: Any, key: str | None = None) -> Any:
    """Redact known credential fields before config is written to export files."""
    if isinstance(value, dict):
        return {k: _redact_config_secrets(v, k) for k, v in value.items()}
    if isinstance(value, list):
        if key in _SENSITIVE_CONFIG_KEYS:
            return [REDACTED_SECRET for _ in value]
        return [_redact_config_secrets(item) for item in value]
    if key in _SENSITIVE_CONFIG_KEYS and value:
        return REDACTED_SECRET
    return value


def export_memory(memory: Any, path: Path = None) -> Dict[str, Any]:
    """Export memories, intents, config, and stats for the configured repository."""
    repo_id = memory.config.repo_id

    export_data = {
        "version": "1.0",
        "exported_at": datetime.now().isoformat(),
        "config": _redact_config_secrets(memory.config.model_dump()),
        "memories": {
            "episodic": [
                _scrub_memory_vectors(item)
                for item in memory._storage.list_memories(
                    layer="episodic", limit=10000, repo_id=repo_id
                )
            ],
            "semantic": [
                _scrub_memory_vectors(item)
                for item in memory._storage.list_memories(
                    layer="semantic", limit=10000, repo_id=repo_id
                )
            ],
        },
        "intents": memory._storage.get_active_intents(repo_id=repo_id),
        "stats": memory.stats(),
    }

    if path:
        Path(path).write_text(json.dumps(export_data, indent=2, default=str))

    return export_data


def _require_dict(value: Any, field_path: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"Import field '{field_path}' must be an object")
    return value


def _require_list(value: Any, field_path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"Import field '{field_path}' must be a list")
    return value


def _validate_optional_type(
    item: Dict[str, Any],
    key: str,
    expected_type: type | tuple[type, ...],
    field_path: str,
) -> None:
    if key in item and item[key] is not None and not isinstance(item[key], expected_type):
        raise ValueError(f"Import field '{field_path}.{key}' has an invalid type")


def _validate_memory_item(item: Any, field_path: str) -> None:
    item = _require_dict(item, field_path)
    if not isinstance(item.get("content"), str):
        raise ValueError(f"Import field '{field_path}.content' must be a string")

    _validate_optional_type(item, "category", str, field_path)
    _validate_optional_type(item, "importance", (int, float), field_path)
    _validate_optional_type(item, "tags", list, field_path)
    _validate_optional_type(item, "metadata", dict, field_path)
    _validate_optional_type(item, "repo_id", str, field_path)


def _validate_intent_item(item: Any, field_path: str) -> None:
    item = _require_dict(item, field_path)
    if not isinstance(item.get("description"), str):
        raise ValueError(f"Import field '{field_path}.description' must be a string")

    _validate_optional_type(item, "priority", int, field_path)
    _validate_optional_type(item, "context", dict, field_path)
    _validate_optional_type(item, "repo_id", str, field_path)


def _validate_import_data(data: Any) -> Dict[str, Any]:
    data = _require_dict(data, "root")
    memories = _require_dict(data.get("memories", {}), "memories")

    for layer in ("episodic", "semantic"):
        layer_memories = _require_list(memories.get(layer, []), f"memories.{layer}")
        for index, item in enumerate(layer_memories):
            _validate_memory_item(item, f"memories.{layer}[{index}]")

    intents = _require_list(data.get("intents", []), "intents")
    for index, item in enumerate(intents):
        _validate_intent_item(item, f"intents[{index}]")

    return data


def import_memories(memory: Any, path: Path) -> None:
    """Import memories from a JSON export into a Memory instance."""
    data = _validate_import_data(json.loads(Path(path).read_text()))

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
