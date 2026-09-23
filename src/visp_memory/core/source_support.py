"""Resolve source evidence without widening its scope or provenance tier."""

from visp_memory.core.eligibility import normalize_optional_scope_values
from visp_memory.core.storage import EvidenceReferenceError
from visp_memory.core.trust import TIER_POLICIES, channel_policy, provenance_of


def scope_signature(metadata):
    metadata = metadata or {}
    return tuple(normalize_optional_scope_values(metadata.get(field), field=field)
                 for field in ("environment", "task_type"))


def source_support(storage, source_ids, repo_id, write_channel):
    evidence_ids, scopes = [], []
    provenance = channel_policy(write_channel).provenance
    peek = getattr(storage, "peek_memory", None)
    for source_id in dict.fromkeys(source_ids or []):
        source = peek(source_id) if callable(peek) else storage.get_memory(source_id)
        if not source or source.get("status") == "deleted":
            raise EvidenceReferenceError(f"Lineage source {source_id!r} is missing or deleted")
        if source.get("repo_id") != repo_id:
            raise EvidenceReferenceError("Lineage sources must belong to the same repository")
        evidence_ids.extend(source.get("evidence_ids") or [])
        scopes.append(scope_signature(source.get("metadata")))
        source_provenance = provenance_of(source)
        if TIER_POLICIES[source_provenance].base_trust < TIER_POLICIES[provenance].base_trust:
            provenance = source_provenance
    if len(set(scopes)) > 1:
        raise ValueError("Source scopes differ; learn separately scoped facts")
    metadata = {
        field: list(value) for field, value in zip(("environment", "task_type"), scopes[0])
        if value
    } if scopes else {}
    return list(dict.fromkeys(evidence_ids)), metadata, provenance
