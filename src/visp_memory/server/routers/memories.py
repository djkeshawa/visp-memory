from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from visp_memory.config import load_config
from visp_memory.core.authority import ProhibitionAuthorityError
from visp_memory.core.clock import utc_now
from visp_memory.core.eligibility import (
    filter_recall_eligible,
    normalize_scope_values,
)
from visp_memory.core.lifecycle import LifecycleError
from visp_memory.core.ranking import rank_memory_results
from visp_memory.core.storage import (
    EvidenceImmutableError,
    EvidenceReferenceError,
    EvidenceUnsupportedError,
    SemanticMemoryImmutableError,
)
from visp_memory.core.trust import (
    WriteChannel,
    channel_policy,
    filter_unsolicited,
    with_channel_provenance,
)
from visp_memory.quality.secrets import SecretBearingContentError, redact_for_storage
from visp_memory.server.auth import UserContext, get_current_user
from visp_memory.server.authorization import (
    can_access_scoped_record,
    require_admin,
    require_repo_scope_access,
    require_repo_writable,
    require_scoped_record_access,
)
from visp_memory.server.routers.platform import append_audit_event
from visp_memory.server.schemas import (
    EvidenceAttachRequest,
    EvidenceCreate,
    EvidenceResponse,
    MemoryCreate,
    MemoryMergePreviewRequest,
    MemoryMergeRequest,
    MemoryPurgeRequest,
    MemoryResponse,
    MemoryRevision,
    MemoryUpdate,
    RelatedMemoryResponse,
    SearchQuery,
)

# No prefix to maintain backward compatibility for /recall and /relationships
router = APIRouter(tags=["memories"])

PROVENANCE_FIELDS = (
    "title",
    "summary",
    "observed_at",
    "valid_from",
    "valid_to",
    "source_revision",
    "source_hash",
    "confidence",
    "entities",
    "files",
    "symbols",
    "keywords",
    "evidence",
    "lineage",
    "pinned",
    "hold",
    "environment",
    "task_type",
)


def _as_datetime(value):
    """Normalize storage timestamps for API responses."""
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return value or utc_now()


def _as_optional_datetime(value):
    if value is None:
        return None
    return _as_datetime(value)


def _memory_response_payload(memory: dict):
    metadata = memory.get("metadata", {})

    def response_scope(field):
        if field not in metadata or metadata[field] is None:
            return []
        try:
            return list(normalize_scope_values(metadata[field], field=field))
        except ValueError:
            return []

    return {
        "id": memory["id"],
        "content": memory["content"],
        "layer": memory["layer"],
        "category": memory["category"],
        "belief_type": memory.get("belief_type"),
        "epistemic_status": memory.get("epistemic_status"),
        "repo_id": memory.get("repo_id"),
        "importance": memory.get("importance", 0.5),
        "tags": memory.get("tags", []),
        "metadata": metadata,
        "status": memory.get("status", "active"),
        "source": memory.get("source"),
        "quality_flags": memory.get("quality_flags", []),
        "evidence_ids": memory.get("evidence_ids", []),
        "approved_by": memory.get("approved_by"),
        "approved_at": _as_optional_datetime(memory.get("approved_at")),
        "archived_at": _as_optional_datetime(memory.get("archived_at")),
        "created_at": _as_datetime(memory.get("created_at")),
        "accessed_at": _as_datetime(memory.get("accessed_at")),
        "similarity": memory.get("similarity"),
        "relevance_score": memory.get("relevance_score"),
        "title": metadata.get("title"),
        "summary": metadata.get("summary"),
        "observed_at": _as_optional_datetime(
            metadata.get("observed_at") or memory.get("created_at")
        ),
        "valid_from": _as_optional_datetime(metadata.get("valid_from")),
        "valid_to": _as_optional_datetime(metadata.get("valid_to")),
        "source_revision": metadata.get("source_revision"),
        "source_hash": metadata.get("source_hash"),
        "confidence": metadata.get("confidence", 0.5),
        "entities": metadata.get("entities", []),
        "files": metadata.get("files", metadata.get("applies_to", [])),
        "symbols": metadata.get("symbols", []),
        "keywords": metadata.get("keywords", []),
        "evidence": metadata.get("evidence", []),
        "lineage": metadata.get("lineage", memory.get("source_ids", [])),
        "pinned": bool(metadata.get("pinned", False)),
        "hold": bool(metadata.get("hold", False)),
        "environment": response_scope("environment"),
        "task_type": response_scope("task_type"),
    }


def _latest_visible_memory(
    storage,
    *,
    repo_id: str,
    user: UserContext,
    layer: str = None,
    category: str = None,
    status: str = "active",
):
    memories = storage.list_memories(
        limit=50,
        repo_id=repo_id,
        layer=layer,
        category=category,
        status=status,
        order_by="created_at DESC",
    )
    return next(
        (
            memory
            for memory in memories
            if can_access_scoped_record(storage, memory, user, scope_field="metadata")
        ),
        None,
    )


@router.get("/remember", response_model=MemoryResponse)
async def remember_latest_memory(
    request: Request,
    repo_id: str = None,
    layer: str = None,
    category: str = None,
    status: str = "active",
    user: UserContext = Depends(get_current_user),
):
    """Recall the newest visible memory for the current repository scope."""
    storage = request.app.state.storage
    config = load_config()
    memory_repo_id = repo_id or config.repo_id
    require_repo_scope_access(storage, memory_repo_id, user)
    latest = _latest_visible_memory(
        storage,
        repo_id=memory_repo_id,
        user=user,
        layer=layer,
        category=category,
        status=status,
    )
    if latest is None:
        raise HTTPException(status_code=404, detail="No memories found")
    return _memory_response_payload(latest)


@router.get("/memories", response_model=List[MemoryResponse])
async def list_memories(
    request: Request,
    repo_id: str = None,
    layer: str = None,
    category: str = None,
    status: str = "active",
    limit: int = 50,
    offset: int = 0,
    order_by: str = "created_at DESC",
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    config = load_config()
    memory_repo_id = repo_id or config.repo_id
    require_repo_scope_access(storage, memory_repo_id, user)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    visible_memories = []
    storage_offset = 0
    storage_page_size = 200
    visible_target = offset + limit
    while len(visible_memories) < visible_target:
        page = storage.list_memories(
            limit=storage_page_size,
            offset=storage_offset,
            repo_id=memory_repo_id,
            layer=layer,
            category=category,
            status=status,
            order_by=order_by,
        )
        visible_memories.extend(
            memory
            for memory in page
            if can_access_scoped_record(storage, memory, user, scope_field="metadata")
        )
        if len(page) < storage_page_size:
            break
        storage_offset += storage_page_size
    selected = visible_memories[offset:visible_target]
    return [_memory_response_payload(memory) for memory in selected]


@router.post("/memories", response_model=MemoryResponse)
async def create_memory(
    request: Request, memory: MemoryCreate, user: UserContext = Depends(get_current_user)
):
    storage = request.app.state.storage
    config = load_config()
    try:
        content, quality_flags = redact_for_storage(
            memory.content,
            memory.quality_flags,
            reject_if_redacted=memory.authority_attestation is not None,
        )
    except SecretBearingContentError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    memory_repo_id = memory.repo_id or config.repo_id
    require_repo_writable(storage, memory_repo_id, user)
    if memory.layer == "semantic" and not memory.evidence_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Semantic beliefs require at least one existing Evidence ID",
        )
    for evidence_id in memory.evidence_ids:
        require_scoped_record_access(
            storage,
            storage.get_evidence(evidence_id),
            user,
            scope_field="metadata",
            not_found_detail="Evidence not found",
        )

    # Add author attribution to metadata
    metadata = dict(memory.metadata or {})
    metadata["author_id"] = user.user_id
    metadata["write_channel"] = WriteChannel.HTTP.value
    if user.team_id:
        metadata["team_id"] = user.team_id
    for field in PROVENANCE_FIELDS:
        value = getattr(memory, field)
        if isinstance(value, datetime):
            value = value.isoformat()
        if value not in (None, [], ""):
            metadata[field] = value

    http_policy = channel_policy(WriteChannel.HTTP)
    try:
        mem_id = storage.store_memory(
            content=content,
            layer=memory.layer,
            category=memory.category,
            importance=memory.importance,
            repo_id=memory_repo_id,
            tags=with_channel_provenance(memory.tags, WriteChannel.HTTP),
            metadata=metadata,
            source_ids=memory.source_ids,
            evidence_ids=memory.evidence_ids,
            status=memory.status,
            authority_attestation=memory.authority_attestation,
            source=http_policy.source,
            quality_flags=quality_flags or [],
        )
    except ProhibitionAuthorityError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except EvidenceReferenceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except EvidenceUnsupportedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)
        ) from exc
    append_audit_event(
        storage,
        event_type="memory.created",
        actor_id=user.user_id,
        repo_id=memory_repo_id,
        target_type="memory",
        target_id=mem_id,
        metadata={"layer": memory.layer, "category": memory.category},
    )
    return _memory_response_payload(storage.get_memory(mem_id))


@router.post("/evidence", response_model=EvidenceResponse)
async def create_evidence(
    request: Request,
    evidence: EvidenceCreate,
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    require_repo_writable(storage, evidence.repo_id, user)
    metadata = {
        key: value
        for key, value in evidence.metadata.items()
        if key not in {"author_id", "team_id", "write_channel"}
    }
    metadata.update({"author_id": user.user_id, "write_channel": "http"})
    if user.team_id:
        metadata["team_id"] = user.team_id
    try:
        evidence_id = storage.store_evidence(
            evidence.content,
            repo_id=evidence.repo_id,
            evidence_type=evidence.evidence_type,
            provenance="external",
            metadata=metadata,
        )
    except EvidenceUnsupportedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)
        ) from exc
    append_audit_event(
        storage,
        event_type="evidence.created",
        actor_id=user.user_id,
        repo_id=evidence.repo_id,
        target_type="evidence",
        target_id=evidence_id,
        metadata={"evidence_type": evidence.evidence_type},
    )
    return storage.get_evidence(evidence_id)


@router.get("/evidence", response_model=List[EvidenceResponse])
async def list_evidence(
    request: Request,
    repo_id: str,
    limit: int = 10000,
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    require_repo_scope_access(storage, repo_id, user)
    requested_limit = max(1, min(limit, 10000))
    evidence = storage.list_evidence(repo_id=repo_id, limit=10000)
    return [
        item
        for item in evidence
        if can_access_scoped_record(storage, item, user, scope_field="metadata")
    ][:requested_limit]


@router.get("/evidence/{evidence_id}", response_model=EvidenceResponse)
async def get_evidence(
    request: Request,
    evidence_id: str,
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    evidence = require_scoped_record_access(
        storage,
        storage.get_evidence(evidence_id),
        user,
        scope_field="metadata",
        not_found_detail="Evidence not found",
    )
    return evidence


@router.patch("/evidence/{evidence_id}")
async def update_evidence(
    request: Request,
    evidence_id: str,
    payload: Dict[str, Any],
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    evidence = require_scoped_record_access(
        storage,
        storage.get_evidence(evidence_id),
        user,
        scope_field="metadata",
        not_found_detail="Evidence not found",
    )
    require_repo_writable(storage, evidence["repo_id"], user)
    try:
        storage.update_evidence(evidence_id, **payload)
    except EvidenceImmutableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Evidence is immutable")


@router.post("/memories/{belief_id}/evidence", response_model=MemoryResponse)
async def attach_evidence(
    request: Request,
    belief_id: str,
    payload: EvidenceAttachRequest,
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    belief = require_scoped_record_access(
        storage,
        storage.get_memory(belief_id),
        user,
        scope_field="metadata",
        not_found_detail="Memory not found",
    )
    if belief.get("repo_id") != payload.repo_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Belief and Evidence must share a repository",
        )
    require_repo_writable(storage, payload.repo_id, user)
    for evidence_id in payload.evidence_ids:
        require_scoped_record_access(
            storage,
            storage.get_evidence(evidence_id),
            user,
            scope_field="metadata",
            not_found_detail="Evidence not found",
        )
    try:
        storage.attach_evidence(
            belief_id, payload.evidence_ids, repo_id=payload.repo_id
        )
    except EvidenceReferenceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except EvidenceUnsupportedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)
        ) from exc
    return _memory_response_payload(storage.get_memory(belief_id))


@router.get("/memories/{memory_id}", response_model=MemoryResponse)
async def get_memory(
    request: Request, memory_id: str, user: UserContext = Depends(get_current_user)
):
    storage = request.app.state.storage
    mem = storage.get_memory(memory_id)
    mem = require_scoped_record_access(
        storage, mem, user, scope_field="metadata", not_found_detail="Memory not found"
    )

    return _memory_response_payload(mem)


@router.get("/memories/{memory_id}/related", response_model=List[RelatedMemoryResponse])
async def get_related_memories(
    request: Request,
    memory_id: str,
    relationship: str = None,
    environment: List[str] = Query(default=None),
    task_type: List[str] = Query(default=None),
    as_of: datetime = None,
    user: UserContext = Depends(get_current_user),
):
    """Return visible memories directly related to a memory."""
    storage = request.app.state.storage
    source = require_scoped_record_access(
        storage,
        storage.get_memory(memory_id),
        user,
        scope_field="metadata",
        not_found_detail="Memory not found",
    )
    source_eligibility = filter_recall_eligible(
        [source],
        repo_id=source["repo_id"],
        environment=environment,
        task_type=task_type,
        as_of=as_of,
    )
    if not source_eligibility.allowed:
        return []
    related = storage.get_related_memories(memory_id, relationship=relationship)
    access_visible = [
        item
        for item in related
        if item.get("repo_id") == source.get("repo_id")
        and can_access_scoped_record(storage, item, user, scope_field="metadata")
    ]
    eligible = filter_recall_eligible(
        access_visible,
        repo_id=source["repo_id"],
        environment=environment,
        task_type=task_type,
        as_of=as_of,
    ).allowed
    visible = filter_unsolicited(eligible, now=as_of).allowed
    return [
        {
            **_memory_response_payload(item),
            "relationship": item.get("relationship", "related_to"),
            "strength": item.get("strength", 1.0),
            "relationship_evidence": item.get("relationship_evidence"),
        }
        for item in visible
    ]


@router.delete("/memories/{memory_id}")
async def delete_memory(
    request: Request,
    memory_id: str,
    reason: str = "Deleted by user",
    user: UserContext = Depends(get_current_user),
):
    """Move a memory to recoverable trash."""
    storage = request.app.state.storage
    mem = storage.get_memory(memory_id)
    require_scoped_record_access(
        storage, mem, user, scope_field="metadata", not_found_detail="Memory not found"
    )
    success = request.app.state.memory_lifecycle.soft_delete(
        memory_id, actor_id=user.user_id, reason=reason
    )
    if not success:
        raise HTTPException(status_code=404, detail="Memory not found")
    append_audit_event(
        storage,
        event_type="memory.deleted",
        actor_id=user.user_id,
        repo_id=mem.get("repo_id"),
        target_type="memory",
        target_id=memory_id,
    )
    return {"status": "deleted", "id": memory_id, "recoverable": True}


@router.post("/memories/{memory_id}/restore")
async def restore_memory(
    request: Request,
    memory_id: str,
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    memory = require_scoped_record_access(
        storage,
        storage.get_memory(memory_id),
        user,
        scope_field="metadata",
        not_found_detail="Memory not found",
    )
    if not request.app.state.memory_lifecycle.restore(memory_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only deleted memories can be restored",
        )
    append_audit_event(
        storage,
        event_type="memory.restored",
        actor_id=user.user_id,
        repo_id=memory.get("repo_id"),
        target_type="memory",
        target_id=memory_id,
    )
    return {"status": "restored", "id": memory_id}


@router.post("/memories/merge/preview")
async def preview_memory_merge(
    request: Request,
    payload: MemoryMergePreviewRequest,
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    for memory_id in payload.memory_ids:
        require_scoped_record_access(
            storage,
            storage.get_memory(memory_id),
            user,
            scope_field="metadata",
            not_found_detail="Memory not found",
        )
    try:
        return request.app.state.memory_lifecycle.preview_merge(
            payload.memory_ids,
            target_id=payload.target_id,
            target_content=payload.target_content,
        )
    except LifecycleError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error


@router.post("/memories/merge")
async def merge_memories(
    request: Request,
    payload: MemoryMergeRequest,
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    for memory_id in payload.memory_ids:
        require_scoped_record_access(
            storage,
            storage.get_memory(memory_id),
            user,
            scope_field="metadata",
            not_found_detail="Memory not found",
        )
    preview = request.app.state.memory_lifecycle.preview_merge(
        payload.memory_ids,
        target_id=payload.target_id,
        target_content=payload.target_content,
    )
    if not preview["exact_duplicate"] and not payload.reviewed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Semantic merges require reviewed=true after inspecting the preview",
        )
    try:
        result = request.app.state.memory_lifecycle.merge(
            payload.memory_ids,
            actor_id=user.user_id,
            target_id=payload.target_id,
            target_content=payload.target_content,
        )
    except LifecycleError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    append_audit_event(
        storage,
        event_type="memory.merged",
        actor_id=user.user_id,
        repo_id=result.get("repo_id"),
        target_type="memory_merge",
        target_id=result["operation_id"],
        metadata={"memory_ids": result["memory_ids"], "target_id": result["target_id"]},
    )
    return result


@router.post("/memories/merge/{operation_id}/undo")
async def undo_memory_merge(
    request: Request,
    operation_id: str,
    user: UserContext = Depends(get_current_user),
):
    operation = request.app.state.memory_lifecycle.get_operation(operation_id)
    if not operation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Merge not found")
    target = require_scoped_record_access(
        request.app.state.storage,
        request.app.state.storage.get_memory(operation["target_id"]),
        user,
        scope_field="metadata",
        not_found_detail="Merge not found",
    )
    try:
        result = request.app.state.memory_lifecycle.undo_merge(
            operation_id, actor_id=user.user_id
        )
    except LifecycleError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    append_audit_event(
        request.app.state.storage,
        event_type="memory.merge_undone",
        actor_id=user.user_id,
        repo_id=target.get("repo_id"),
        target_type="memory_merge",
        target_id=operation_id,
    )
    return result


@router.post("/memories/purge/preview")
async def preview_memory_purge(
    request: Request,
    payload: MemoryPurgeRequest,
    user: UserContext = Depends(get_current_user),
):
    require_admin(user)
    return request.app.state.memory_lifecycle.purge_preview(payload.memory_ids)


@router.delete("/memories/{memory_id}/purge")
async def purge_memory(
    request: Request,
    memory_id: str,
    confirmation: str,
    user: UserContext = Depends(get_current_user),
):
    require_admin(user)
    if confirmation != memory_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Confirmation must exactly match the memory ID",
        )
    memory = request.app.state.storage.get_memory(memory_id)
    if not memory:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")
    try:
        result = request.app.state.memory_lifecycle.purge([memory_id])
    except LifecycleError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    append_audit_event(
        request.app.state.storage,
        event_type="memory.purged",
        actor_id=user.user_id,
        repo_id=memory.get("repo_id"),
        target_type="memory",
        target_id=memory_id,
        metadata={"reason": "confirmed permanent purge"},
    )
    return result


@router.get("/maintenance/retention-preview")
async def retention_preview(
    request: Request,
    repo_id: str,
    retention_days: int = 30,
    user: UserContext = Depends(get_current_user),
):
    require_admin(user)
    return request.app.state.memory_lifecycle.retention_preview(
        repo_id, retention_days=retention_days
    )


@router.post("/maintenance/retention")
async def execute_retention(
    request: Request,
    repo_id: str,
    confirmation: str,
    retention_days: int = 30,
    user: UserContext = Depends(get_current_user),
):
    require_admin(user)
    if confirmation != repo_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Confirmation must exactly match the repository ID",
        )
    try:
        result = request.app.state.memory_lifecycle.execute_retention(
            repo_id, retention_days=retention_days
        )
    except LifecycleError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    append_audit_event(
        request.app.state.storage,
        event_type="memory.retention_executed",
        actor_id=user.user_id,
        repo_id=repo_id,
        target_type="repository",
        target_id=repo_id,
        metadata={"retention_days": retention_days, "purged": len(result["purged_ids"])},
    )
    return result


@router.get("/maintenance/verify")
async def verify_memory_consistency(
    request: Request,
    repo_id: str = None,
    user: UserContext = Depends(get_current_user),
):
    require_admin(user)
    return request.app.state.memory_lifecycle.verify_consistency(repo_id)


@router.post("/memories/{memory_id}/revisions", response_model=MemoryResponse)
async def revise_memory(
    request: Request,
    memory_id: str,
    revision: MemoryRevision,
    user: UserContext = Depends(get_current_user),
):
    """Create an evidence-backed successor without mutating belief content."""
    storage = request.app.state.storage
    existing = require_scoped_record_access(
        storage,
        storage.get_memory(memory_id),
        user,
        scope_field="metadata",
        not_found_detail="Memory not found",
    )
    if existing.get("layer") != "semantic":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Only semantic memories can be revised",
        )
    require_repo_writable(storage, existing.get("repo_id"), user)
    for evidence_id in revision.evidence_ids:
        require_scoped_record_access(
            storage,
            storage.get_evidence(evidence_id),
            user,
            scope_field="metadata",
            not_found_detail="Evidence not found",
        )

    metadata = dict(revision.metadata or {})
    if not user.is_admin:
        existing_metadata = existing.get("metadata") or {}
        for reserved_key in ("author_id", "team_id", "environment", "task_type"):
            if reserved_key in existing_metadata:
                metadata[reserved_key] = existing_metadata[reserved_key]
            else:
                metadata.pop(reserved_key, None)

    try:
        content, quality_flags = redact_for_storage(
            revision.content,
            revision.quality_flags,
            reject_if_redacted=revision.authority_attestation is not None,
        )
        successor_id = storage.revise_memory(
            memory_id,
            content,
            evidence_ids=revision.evidence_ids,
            authority_attestation=revision.authority_attestation,
            metadata=metadata,
            quality_flags=quality_flags,
            reason=revision.reason,
            importance=revision.importance,
            tags=revision.tags,
        )
    except SecretBearingContentError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except ProhibitionAuthorityError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except EvidenceReferenceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except EvidenceUnsupportedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)
        ) from exc
    except SemanticMemoryImmutableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    append_audit_event(
        storage,
        event_type="memory.revised",
        actor_id=user.user_id,
        repo_id=existing.get("repo_id"),
        target_type="memory",
        target_id=successor_id,
        metadata={"supersedes": memory_id},
    )
    return _memory_response_payload(storage.get_memory(successor_id))


@router.patch("/memories/{memory_id}")
async def update_memory(
    request: Request,
    memory_id: str,
    update: MemoryUpdate,
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    mem = storage.get_memory(memory_id)
    mem = require_scoped_record_access(
        storage, mem, user, scope_field="metadata", not_found_detail="Memory not found"
    )

    # Filter out None values
    update_data = {k: v for k, v in update.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
    if "content" in update_data and mem.get("layer") == "semantic":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Semantic belief content is immutable; use the evidence-backed "
                "revision endpoint"
            ),
        )
    if "content" in update_data:
        original_content = update_data["content"]
        sanitized_content, redaction_flags = redact_for_storage(
            original_content,
            update_data.get("quality_flags") or mem.get("quality_flags") or [],
        )
        update_data["content"] = sanitized_content
        if sanitized_content != original_content:
            update_data["quality_flags"] = redaction_flags
    if "metadata" in update_data and not user.is_admin:
        metadata = dict(update_data["metadata"] or {})
        existing_metadata = mem.get("metadata") or {}
        # Non-admins may never set ownership/scope fields. Pin them to the record's
        # existing values, and strip them entirely when absent so a caller cannot
        # introduce a team_id/author_id (which drives record visibility) on a record
        # that derived its scope from the repo.
        for reserved_key in ("author_id", "team_id", "environment", "task_type"):
            if reserved_key in existing_metadata:
                metadata[reserved_key] = existing_metadata[reserved_key]
            else:
                metadata.pop(reserved_key, None)
        update_data["metadata"] = metadata
    if update_data.get("status") == "active" and mem.get("status") == "pending":
        update_data["approved_by"] = user.user_id
        update_data["approved_at"] = utc_now().isoformat()

    try:
        success = storage.update_memory(memory_id, **update_data)
    except SemanticMemoryImmutableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not success:
        raise HTTPException(status_code=404, detail="Memory not found")
    event_type = "memory.archived" if update_data.get("status") == "archived" else "memory.updated"
    append_audit_event(
        storage,
        event_type=event_type,
        actor_id=user.user_id,
        repo_id=mem.get("repo_id"),
        target_type="memory",
        target_id=memory_id,
        metadata={"fields": sorted(update_data)},
    )
    return {"status": "updated", "id": memory_id}


@router.post("/recall", response_model=List[MemoryResponse])
async def recall(
    request: Request, query: SearchQuery, user: UserContext = Depends(get_current_user)
):
    storage = request.app.state.storage
    config = load_config()
    recall_repo_id = query.repo_id or config.repo_id
    require_repo_scope_access(storage, recall_repo_id, user)
    layers = query.layers or [None]
    results = []
    for layer in layers:
        layer_results = storage.search_memories(
            query=query.query,
            layer=layer,
            limit=query.limit,
            repo_id=recall_repo_id,
            status=query.status,
        )
        results.extend(
            r
            for r in layer_results
            if can_access_scoped_record(storage, r, user, scope_field="metadata")
        )
    results = filter_recall_eligible(
        results,
        repo_id=recall_repo_id,
        environment=query.environment,
        task_type=query.task_type,
        as_of=query.as_of,
    ).allowed
    results = rank_memory_results(
        results, query=query.query, limit=query.limit, min_score=query.min_score
    )
    return [_memory_response_payload(r) for r in results]


@router.get("/graph")
async def get_graph_data(
    request: Request, repo_id: str = None, user: UserContext = Depends(get_current_user)
):
    """Get memory graph (nodes and edges)."""
    storage = request.app.state.storage
    config = load_config()
    graph_repo_id = repo_id or config.repo_id
    require_repo_scope_access(storage, graph_repo_id, user)
    memories = storage.list_memories(limit=200, repo_id=graph_repo_id)
    memories = [
        m for m in memories if can_access_scoped_record(storage, m, user, scope_field="metadata")
    ]
    visible_memory_ids = {m["id"] for m in memories}
    relationships = storage.get_all_relationships(repo_id=graph_repo_id)

    return {
        "nodes": [
            {
                "id": m["id"],
                "group": m["layer"],
                "label": m["content"][:30] + "..." if len(m["content"]) > 30 else m["content"],
                "full_label": m["content"],
                "radius": 5 + (m.get("importance", 0.5) * 5),
                "layer": m["layer"],
            }
            for m in memories
        ],
        "links": [
            {
                "source": r["source_id"],
                "target": r["target_id"],
                "value": r["strength"],
                "label": r["relationship"],
                "evidence": r.get("evidence"),
            }
            for r in relationships
            if r["source_id"] in visible_memory_ids and r["target_id"] in visible_memory_ids
        ],
    }
