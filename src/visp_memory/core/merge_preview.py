"""Build non-mutating merge previews and compatibility diagnostics."""

from typing import Any, Optional

from visp_memory.core.tokens import estimate_tokens
from visp_memory.quality.secrets import redact_for_storage


def build_merge_preview(
    storage,
    memories: list[dict[str, Any]],
    target: dict[str, Any],
    target_content: Optional[str] = None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    repo_ids = {memory.get("repo_id") for memory in memories}
    layers = {memory.get("layer") for memory in memories}
    if len(repo_ids) != 1:
        errors.append("Memories from different projects cannot be merged")
    if len(layers) != 1:
        errors.append("Memories from different layers cannot be merged")
    invalid_statuses = {
        memory.get("status")
        for memory in memories
        if memory.get("status") not in {"active", "archived"}
    }
    if invalid_statuses:
        errors.append(
            "Only active or archived memories can be merged; found "
            + ", ".join(sorted(str(status) for status in invalid_statuses))
        )

    normalized = {" ".join(memory.get("content", "").casefold().split()) for memory in memories}
    exact_duplicate = len(normalized) == 1
    temporal_ranges = {
        (
            (memory.get("metadata") or {}).get("valid_from"),
            (memory.get("metadata") or {}).get("valid_to"),
        )
        for memory in memories
    }
    if len(temporal_ranges) > 1 and not exact_duplicate:
        errors.append("Temporally different facts must be superseded or linked, not merged")
    if not exact_duplicate:
        warnings.append("Semantic merges require explicit human review")

    source_tokens = sum(estimate_tokens(memory.get("content", "")) for memory in memories)
    proposed_content = (
        target_content if target_content is not None else target.get("content", "")
    )
    proposed_content, _ = redact_for_storage(proposed_content, None)
    if target.get("layer") == "semantic" and proposed_content != target.get("content", ""):
        errors.append(
            "Semantic belief content is immutable; use the evidence-backed revision endpoint"
        )
    result_tokens = estimate_tokens(proposed_content)
    relationships = storage.get_all_relationships(repo_id=target.get("repo_id"))
    selected_ids = {memory["id"] for memory in memories}
    relationship_rewrites = sum(
        1
        for relationship in relationships
        if relationship.get("source_id") in selected_ids
        or relationship.get("target_id") in selected_ids
    )
    return {
        "memory_ids": [memory["id"] for memory in memories],
        "target_id": target["id"],
        "repo_id": target.get("repo_id"),
        "layer": target.get("layer"),
        "target_content": proposed_content,
        "exact_duplicate": exact_duplicate,
        "validation_errors": errors,
        "warnings": warnings,
        "relationship_rewrites": relationship_rewrites,
        "source_tokens": source_tokens,
        "result_tokens": result_tokens,
        "estimated_tokens_saved": max(0, source_tokens - result_tokens),
        "merged_tags": list(
            dict.fromkeys(tag for memory in memories for tag in (memory.get("tags") or []))
        ),
        "merged_source_ids": list(
            dict.fromkeys(
                source_id
                for memory in memories
                for source_id in [memory["id"], *(memory.get("source_ids") or [])]
                if source_id != target["id"]
            )
        ),
    }
