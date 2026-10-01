from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request, status

from visp_memory.core.memory_import_export import (
    export_storage,
    import_memory_data,
    require_dict,
    require_list,
    validate_import_data,
)
from visp_memory.core.storage import EvidenceUnsupportedError
from visp_memory.server.auth import UserContext, get_current_user
from visp_memory.server.authorization import (
    require_owner_or_admin,
    require_repo_scope_access,
    require_repo_writable,
    require_scoped_record_access,
)

router = APIRouter(tags=["portability"])


def _validate_import_scope(data: dict, repo_id: str) -> None:
    """Reject foreign records before even a legacy import can write its first row."""
    collections = {
        f"memories.{layer}": items
        for layer, items in data.get("memories", {}).items()
    }
    collections.update({
        key: data.get(key, [])
        for key in (
            "evidence", "intents", "relationships", "authority_attestations", "belief_authority"
        )
    })
    portable_evidence_ids = set()
    for name, items in collections.items():
        for index, item in enumerate(require_list(items, name)):
            record = require_dict(item, f"{name}[{index}]")
            if name.startswith("memories.") and not record.get("repo_id"):
                portable_evidence_ids.update(record.get("evidence_ids") or [])
            # LocalStorage.import_graph moves linked Evidence with memories
            # whose scope is omitted. Preserve that existing portable-file rule.
            if (
                name == "evidence"
                and data.get("version") in ("2.0", "3.0")
                and record.get("id") in portable_evidence_ids
            ):
                continue
            if record.get("repo_id") not in (None, "", repo_id):
                raise ValueError(
                    f"Import record '{name}[{index}]' names a different repository; "
                    f"all records must belong to {repo_id!r}"
                )


def _require_export_access(storage, data: dict, user: UserContext) -> None:
    """A complete export must refuse hidden rows instead of silently omitting them."""
    collections = [
        (rows, "metadata") for rows in data["memories"].values()
    ] + [(data["evidence"], "metadata"), (data["intents"], "context")]
    for rows, scope_field in collections:
        for record in rows:
            require_scoped_record_access(
                storage, record, user, scope_field=scope_field,
                not_found_detail="Repository not found",
            )


@router.get("/repos/{repo_id:path}/export", response_model=Dict[str, Any])
def export_repository(
    request: Request,
    repo_id: str,
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    require_repo_scope_access(storage, repo_id, user)
    try:
        data = export_storage(storage, repo_id)
        _require_export_access(storage, data, user)
        return data
    except EvidenceUnsupportedError as error:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error


@router.post("/repos/{repo_id:path}/import", response_model=Dict[str, Any])
def import_repository(
    request: Request,
    repo_id: str,
    payload: Dict[str, Any],
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    # Graph exports carry historical trust tiers, so ordinary HTTP write access
    # cannot authorize importing them unchanged.
    require_owner_or_admin(user)
    require_repo_writable(storage, repo_id, user)
    try:
        data = validate_import_data(payload)
        _validate_import_scope(data, repo_id)
        result = import_memory_data(storage, data, default_repo_id=repo_id)
        return result if result is not None else {"status": "completed"}
    except EvidenceUnsupportedError as error:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, detail=str(error)) from error
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error


@router.get("/memories/{memory_id}/attestation", response_model=Dict[str, Any])
def get_authority_attestation(
    request: Request,
    memory_id: str,
    repo_id: str = None,
    user: UserContext = Depends(get_current_user),
):
    storage = request.app.state.storage
    lookup = getattr(storage, "peek_memory", None) or storage.get_memory
    memory = require_scoped_record_access(
        storage, lookup(memory_id), user,
        scope_field="metadata", not_found_detail="Memory not found",
    )
    if repo_id is not None and memory.get("repo_id") != repo_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Memory not found")
    require_repo_scope_access(storage, memory.get("repo_id"), user)
    get_attestation = getattr(storage, "get_authority_attestation", None)
    if not callable(get_attestation):
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            detail="Storage backend does not support authority attestations",
        )
    attestation = get_attestation(memory_id)
    if attestation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Attestation not found")
    return attestation
