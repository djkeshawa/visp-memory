"""Import/export helpers for the Memory facade."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from visp_memory.capture.git import CaptureManifest
from visp_memory.core.clock import utc_now
from visp_memory.core.eligibility import UNSCOPED_REPO_ID
from visp_memory.core.storage import EvidenceUnsupportedError
from visp_memory.core.trust import WriteChannel, channel_policy, with_channel_provenance

REDACTED_SECRET = "***REDACTED***"
_SENSITIVE_CONFIG_KEYS = {
    "api_key",
    "api_keys",
    "jwt_token",
    "jwt_secret",
    "neo4j_password",
}
_MAX_GRAPH_EXPORT_ITEMS = 10000


def _complete_export_page(items: list[Dict[str, Any]], kind: str) -> list[Dict[str, Any]]:
    if len(items) > _MAX_GRAPH_EXPORT_ITEMS:
        raise ValueError(
            f"Refusing to truncate {kind} export above {_MAX_GRAPH_EXPORT_ITEMS} records"
        )
    return items


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
    if not memory._storage.get_capabilities().complete_graph_export:
        raise EvidenceUnsupportedError(
            f"{memory._storage.__class__.__name__} does not support complete graph export"
        )
    repo_id = memory.config.repo_id or UNSCOPED_REPO_ID

    export_data = {
        "version": "2.0",
        "exported_at": utc_now().isoformat(),
        "config": _redact_config_secrets(memory.config.model_dump()),
        "evidence": _complete_export_page(
            memory._storage.list_evidence(
                repo_id=repo_id, limit=_MAX_GRAPH_EXPORT_ITEMS + 1
            ),
            "Evidence",
        ),
        "memories": {
            layer: _complete_export_page(
                [
                    _scrub_memory_vectors(item)
                    for item in memory._storage.list_memories(
                        layer=layer,
                        limit=_MAX_GRAPH_EXPORT_ITEMS + 1,
                        repo_id=repo_id,
                        status="all",
                        order_by="created_at ASC",
                    )
                ],
                f"{layer} memory",
            )
            for layer in ("raw", "episodic", "semantic", "intent")
        },
        "intents": _complete_export_page(
            memory._storage.get_active_intents(repo_id=repo_id, status="all"),
            "intent",
        ),
        "relationships": _complete_export_page(
            memory._storage.get_all_relationships(repo_id=repo_id),
            "relationship",
        ),
        "stats": memory._storage.get_stats(repo_id=repo_id),
        "capture_manifest": CaptureManifest(memory).to_export(),
    }

    if path:
        Path(path).write_text(json.dumps(export_data, indent=2, default=str), encoding="utf-8")

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

    capture_manifest = data.get("capture_manifest")
    if capture_manifest is not None:
        _require_dict(capture_manifest, "capture_manifest")
        _validate_optional_type(capture_manifest, "entries", dict, "capture_manifest")
        _validate_optional_type(
            capture_manifest, "capture_version", str, "capture_manifest"
        )

    return data


def import_memories(memory: Any, path: Path) -> None:
    """Import memories from a JSON export into a Memory instance."""
    data = _validate_import_data(json.loads(Path(path).read_text(encoding="utf-8")))
    version = data.get("version")
    if version not in (None, "1.0", "2.0"):
        raise ValueError(f"Unsupported memory export version: {version!r}")

    if version == "2.0":
        if not memory._storage.get_capabilities().atomic_graph_import:
            raise EvidenceUnsupportedError(
                f"{memory._storage.__class__.__name__} does not support atomic graph import"
            )
        import_graph = getattr(memory._storage, "import_graph", None)
        if import_graph is None:
            raise ValueError(
                f"{memory._storage.__class__.__name__} cannot atomically import schema-v3 graphs"
            )
        result = import_graph(data, default_repo_id=memory.config.repo_id)
        if data.get("capture_manifest"):
            CaptureManifest(memory).replace(data["capture_manifest"])
        return result

    import_policy = channel_policy(WriteChannel.IMPORT)
    for mem in data.get("memories", {}).get("episodic", []):
        memory._storage.store_memory(
            content=mem["content"],
            layer="episodic",
            category=mem.get("category", "note"),
            importance=mem.get("importance", 0.5),
            tags=with_channel_provenance(mem.get("tags"), WriteChannel.IMPORT),
            metadata={
                **(mem.get("metadata") or {}),
                "write_channel": WriteChannel.IMPORT.value,
            },
            repo_id=mem.get("repo_id") or memory.config.repo_id,
            source=import_policy.source,
        )

    for mem in data.get("memories", {}).get("semantic", []):
        repo_id = mem.get("repo_id") or memory.config.repo_id
        evidence_id = memory._storage.store_evidence(
            mem["content"],
            repo_id=repo_id,
            evidence_type="legacy_import",
            provenance=import_policy.provenance.value,
            metadata={"write_channel": WriteChannel.IMPORT.value},
        )
        memory._storage.store_memory(
            content=mem["content"],
            layer="semantic",
            category=mem.get("category", "fact"),
            importance=mem.get("importance", 0.5),
            tags=with_channel_provenance(mem.get("tags"), WriteChannel.IMPORT),
            metadata={
                **(mem.get("metadata") or {}),
                "write_channel": WriteChannel.IMPORT.value,
            },
            repo_id=repo_id,
            source=import_policy.source,
            evidence_ids=[evidence_id],
        )

    for intent in data.get("intents", []):
        memory._storage.set_intent(
            description=intent["description"],
            priority=intent.get("priority", 1),
            context=intent.get("context", {}),
            repo_id=intent.get("repo_id") or memory.config.repo_id,
        )

    if data.get("capture_manifest"):
        CaptureManifest(memory).replace(data["capture_manifest"])
