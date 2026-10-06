"""Import/export helpers for the Memory facade."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict

from visp_memory.capture.git import CaptureManifest
from visp_memory.core.attribution import suppress_attribution
from visp_memory.core.beliefs import migrate_legacy_belief_fields
from visp_memory.core.clock import parse_utc, utc_now
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
    "bootstrap_admin_password",
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
        if key not in {"embedding", "authority_attestation"}
        and not key.startswith("embedding_")
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
    server_export = getattr(memory._storage, "export_graph", None)
    export_data = (
        server_export(repo_id=repo_id)
        if callable(server_export)
        else export_storage(memory._storage, repo_id)
    )
    export_data["config"] = _redact_config_secrets(memory.config.model_dump())
    export_data["capture_manifest"] = CaptureManifest(memory).to_export()

    if path:
        Path(path).write_text(json.dumps(export_data, indent=2, default=str), encoding="utf-8")
    return export_data


def export_storage(storage: Any, repo_id: str) -> Dict[str, Any]:
    """Serialize a repository at the storage owner, without HTTP page limits."""
    if not storage.get_capabilities().complete_graph_export:
        raise EvidenceUnsupportedError(
            f"{storage.__class__.__name__} does not support complete graph export"
        )

    exported_memories = {
        layer: _complete_export_page(
            [
                _scrub_memory_vectors(item)
                for item in storage.list_memories(
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
    }
    authority_attestations = []
    belief_authority = []
    get_attestation = getattr(storage, "get_authority_attestation", None)
    for belief in exported_memories["semantic"]:
        attestation = get_attestation(belief["id"]) if get_attestation else None
        if attestation is None:
            # peek, not get: exporting is not a recall, and counting it as one
            # made the export mutate the rows it was serialising.
            peek = getattr(storage, "peek_memory", None)
            source = (
                peek(belief["id"]) if peek else storage.get_memory(belief["id"])
            )
            envelope = (source or {}).get("authority_attestation")
            if envelope:
                parsed = json.loads(envelope)
                attestation = {
                    "digest": hashlib.sha256(envelope.encode("utf-8")).hexdigest(),
                    "belief_id": belief["id"],
                    "key_id": parsed.get("key_id"),
                    "nonce": parsed.get("nonce"),
                    "envelope": envelope,
                    "created_at": belief.get("created_at"),
                }
        if attestation is None:
            continue
        attestation = dict(attestation)
        attestation_id = f"att-{attestation['digest']}"
        authority_attestations.append({"id": attestation_id, **attestation})
        belief_authority.append(
            {
                "id": f"ba-{belief['id']}-{attestation_id}",
                "belief_id": belief["id"],
                "attestation_id": attestation_id,
                "created_at": attestation.get("created_at"),
            }
        )

    return {
        "version": "3.0",
        "exported_at": utc_now().isoformat(),
        "config": {"repo_id": repo_id},
        "evidence": _complete_export_page(
            storage.list_evidence(
                repo_id=repo_id, limit=_MAX_GRAPH_EXPORT_ITEMS + 1
            ),
            "Evidence",
        ),
        "memories": exported_memories,
        "authority_attestations": sorted(
            authority_attestations, key=lambda item: item["id"]
        ),
        "belief_authority": sorted(belief_authority, key=lambda item: item["id"]),
        "intents": _complete_export_page(
            storage.get_active_intents(repo_id=repo_id, status="all"),
            "intent",
        ),
        "relationships": _complete_export_page(
            storage.get_all_relationships(repo_id=repo_id),
            "relationship",
        ),
        "stats": storage.get_stats(repo_id=repo_id),
        # Capture state is local to the facade, not a shared repository graph.
        "capture_manifest": CaptureManifest(None).to_export(),
    }


def require_dict(value: Any, field_path: str) -> Dict[str, Any]:
    """Require an object, naming the invalid field in any validation error."""
    if not isinstance(value, dict):
        raise ValueError(f"Import field '{field_path}' must be an object")
    return value


def require_list(value: Any, field_path: str) -> list[Any]:
    """Require a collection before walking untrusted import records."""
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
    item = require_dict(item, field_path)
    if not isinstance(item.get("content"), str):
        raise ValueError(f"Import field '{field_path}.content' must be a string")

    _validate_optional_type(item, "category", str, field_path)
    _validate_optional_type(item, "importance", (int, float), field_path)
    _validate_optional_type(item, "tags", list, field_path)
    _validate_optional_type(item, "metadata", dict, field_path)
    _validate_optional_type(item, "repo_id", str, field_path)


def _validate_intent_item(item: Any, field_path: str) -> None:
    item = require_dict(item, field_path)
    if not isinstance(item.get("description"), str):
        raise ValueError(f"Import field '{field_path}.description' must be a string")

    _validate_optional_type(item, "priority", int, field_path)
    _validate_optional_type(item, "context", dict, field_path)
    _validate_optional_type(item, "repo_id", str, field_path)


def validate_import_data(data: Any) -> Dict[str, Any]:
    """Validate the common document shape used by file and REST imports."""
    data = require_dict(data, "root")
    memories = require_dict(data.get("memories", {}), "memories")

    for layer in ("episodic", "semantic"):
        layer_memories = require_list(memories.get(layer, []), f"memories.{layer}")
        for index, item in enumerate(layer_memories):
            _validate_memory_item(item, f"memories.{layer}[{index}]")

    intents = require_list(data.get("intents", []), "intents")
    for index, item in enumerate(intents):
        _validate_intent_item(item, f"intents[{index}]")

    capture_manifest = data.get("capture_manifest")
    if capture_manifest is not None:
        require_dict(capture_manifest, "capture_manifest")
        _validate_optional_type(capture_manifest, "entries", dict, "capture_manifest")
        _validate_optional_type(
            capture_manifest, "capture_version", str, "capture_manifest"
        )

    return data


def import_memories(memory: Any, path: Path) -> None:
    """Import memories from a JSON export into a Memory instance."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    result = import_memory_data(
        memory._storage, data, default_repo_id=memory.config.repo_id
    )
    if data.get("capture_manifest"):
        CaptureManifest(memory).replace(data["capture_manifest"])
    return result


def import_memory_data(
    storage: Any, data: Dict[str, Any], *, default_repo_id: str
) -> Dict[str, Any] | None:
    """Share version handling and trust policy between file and server imports.

    Both paths run with attribution suppressed: an imported record keeps the
    ``written_by`` its export carried instead of taking the importer's.
    """
    with suppress_attribution():
        return _import_memory_data(storage, data, default_repo_id=default_repo_id)


def _import_memory_data(
    storage: Any, data: Dict[str, Any], *, default_repo_id: str
) -> Dict[str, Any] | None:
    data = validate_import_data(data)
    version = data.get("version")
    if version not in (None, "1.0", "2.0", "3.0"):
        raise ValueError(f"Unsupported memory export version: {version!r}")

    if version in {"2.0", "3.0"}:
        if not storage.get_capabilities().atomic_graph_import:
            raise EvidenceUnsupportedError(
                f"{storage.__class__.__name__} does not support atomic graph import"
            )
        import_graph = getattr(storage, "import_graph", None)
        if import_graph is None:
            raise ValueError(
                f"{storage.__class__.__name__} cannot atomically import schema-v3 graphs"
            )
        if version == "2.0":
            data = _quarantine_format2_graph(data)
        return import_graph(data, default_repo_id=default_repo_id)

    import_policy = channel_policy(WriteChannel.IMPORT)
    for mem in data.get("memories", {}).get("episodic", []):
        storage.store_memory(
            content=mem["content"],
            layer="episodic",
            category=mem.get("category", "note"),
            importance=mem.get("importance", 0.5),
            tags=with_channel_provenance(mem.get("tags"), WriteChannel.IMPORT),
            metadata={
                **(mem.get("metadata") or {}),
                "write_channel": WriteChannel.IMPORT.value,
            },
            repo_id=mem.get("repo_id") or default_repo_id,
            source=import_policy.source,
        )

    for mem in data.get("memories", {}).get("semantic", []):
        repo_id = mem.get("repo_id") or default_repo_id
        # A v1 export predates the governed belief vocabulary, so its semantic
        # categories are legacy names ("fragile_area", "convention", ...). The
        # v2 path migrates those through migrate_legacy_belief_fields before
        # anything validates them; this loop passed them through raw, so the
        # governed allow-list refused the write and the documented
        # export -> import migration failed for every pre-schema-v3 export.
        # Same treatment as v2: map the type, keep the original name in
        # metadata, and flag the row as unreviewed legacy material.
        legacy_category = mem.get("category", "fact")
        belief_type, epistemic_status = migrate_legacy_belief_fields(
            legacy_category, mem.get("status")
        )
        evidence_id = storage.store_evidence(
            mem["content"],
            repo_id=repo_id,
            evidence_type="legacy_import",
            provenance=import_policy.provenance.value,
            metadata={"write_channel": WriteChannel.IMPORT.value},
        )
        storage.store_memory(
            content=mem["content"],
            layer="semantic",
            category=belief_type,
            epistemic_status=epistemic_status,
            quality_flags=["legacy_unreviewed"],
            importance=mem.get("importance", 0.5),
            tags=with_channel_provenance(mem.get("tags"), WriteChannel.IMPORT),
            metadata={
                **(mem.get("metadata") or {}),
                "write_channel": WriteChannel.IMPORT.value,
                "legacy_category": legacy_category,
            },
            repo_id=repo_id,
            source=import_policy.source,
            evidence_ids=[evidence_id],
        )

    for intent in data.get("intents", []):
        storage.set_intent(
            description=intent["description"],
            priority=intent.get("priority", 1),
            context=intent.get("context", {}),
            repo_id=intent.get("repo_id") or default_repo_id,
        )


def _quarantine_format2_graph(data: Dict[str, Any]) -> Dict[str, Any]:
    """Conservatively add governed fields to a format-2 graph."""
    transformed = copy.deepcopy(data)
    transformed["version"] = "3.0"
    transformed["_legacy_format2"] = True
    transformed.setdefault("authority_attestations", [])
    transformed.setdefault("belief_authority", [])
    exported_at = parse_utc(transformed.get("exported_at"))
    fallback_created_at = exported_at or parse_utc("1970-01-01T00:00:00+00:00")
    for item in (transformed.get("memories") or {}).get("semantic", []):
        legacy_category = item.get("category")
        belief_type, epistemic_status = migrate_legacy_belief_fields(
            legacy_category, item.get("status")
        )
        item["category"] = belief_type
        item["belief_type"] = belief_type
        item["epistemic_status"] = epistemic_status
        item["source"] = "unknown"
        item["quality_flags"] = list(
            dict.fromkeys([*(item.get("quality_flags") or []), "legacy_unreviewed"])
        )
        item["metadata"] = {
            **(item.get("metadata") or {}),
            "legacy_category": legacy_category,
        }
        if belief_type == "hypothesis":
            created_at = parse_utc(item.get("created_at")) or fallback_created_at
            item["created_at"] = created_at.isoformat()
            item["metadata"]["valid_to"] = (
                created_at + timedelta(days=7)
            ).isoformat()
    return transformed
