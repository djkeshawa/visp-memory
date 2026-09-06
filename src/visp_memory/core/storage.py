"""
Storage layer for Visp Memory.

Combines:
- SQLite for structured data (memories, metadata, relationships)
- ChromaDB for vector embeddings (semantic search)
"""

import hashlib
import json
import logging
import re
import sqlite3
import threading
import uuid
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import timedelta
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional

from visp_memory.core.beliefs import (
    HYPOTHESIS_TTL_DAYS,
    EpistemicStatus,
    migrate_legacy_belief_fields,
    normalize_belief_type,
    normalize_epistemic_status,
)
from visp_memory.core.clock import parse_utc, utc_now
from visp_memory.core.eligibility import UNSCOPED_REPO_ID
from visp_memory.core.indexing import EmbeddingIndexReport, ReindexResult, ReindexScope
from visp_memory.core.ranking import (
    clamp_score,
    normalize_distance_score,
    rank_memory_results,
    relationship_score,
    text_similarity,
    utility_rank_adjustment,
)
from visp_memory.quality.secrets import SecretBearingContentError, redact_for_storage

try:
    import chromadb
    from chromadb.config import Settings

    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False


logger = logging.getLogger(__name__)


MemoryLayer = Literal["raw", "episodic", "semantic", "intent"]
MemoryStatus = Literal["active", "pending", "archived", "superseded", "merged", "deleted"]

# Statuses that take a memory out of every serving path. A memory here has been
# deleted, folded into another, or replaced by a newer belief — returning it from
# any read is how content someone deleted keeps reappearing.
#
# `archived` and `pending` are deliberately absent: those are still real,
# retrievable memories, just not prominent ones.
NON_SERVABLE_STATUSES: frozenset[str] = frozenset({"deleted", "merged", "superseded"})

RecallEventType = Literal["surfaced", "used", "dismissed", "task_linked", "outcome_linked"]

RECALL_EVENT_WEIGHTS: dict[str, float] = {
    "surfaced": 0.03,
    "used": 0.30,
    "dismissed": -0.25,
    "task_linked": 0.20,
    "outcome_linked": 0.25,
}
# Events that signal a memory was actually *used* (not merely surfaced) trigger
# retrieval reinforcement: the memory's access count grows and its last-access time
# refreshes. This strengthens future recall (via base-level activation) and resets
# decay, implementing the "use it or lose it" principle. Surfaced/dismissed events
# deliberately do not reinforce, to avoid popularity bias from mere exposure.
REINFORCING_RECALL_EVENTS = frozenset({"used", "task_linked", "outcome_linked"})
SENSITIVE_RECALL_METADATA_KEYS = {"prompt", "response", "query", "content", "messages"}
STORAGE_SCHEMA_VERSION = 5
_STORAGE_TABLE_NAMES = frozenset(
    {
        "audit_logs",
        "authority_attestations",
        "belief_evidence",
        "evidence",
        "intents",
        "memories",
        "recall_events",
        "relationships",
        "repositories",
        "repository_dependencies",
        "sessions",
        "team_members",
        "teams",
        "users",
    }
)
_V4_REQUIRED_COLUMNS = {
    "schema_migrations": {"version", "applied_at"},
    "memories": {
        "id", "content", "layer", "category", "belief_type",
        "epistemic_status", "importance", "repo_id", "access_count", "tags",
        "metadata", "source_ids", "status", "approved_by", "approved_at",
        "archived_at", "source", "quality_flags", "last_quality_checked_at",
        "created_at", "accessed_at", "compressed_at",
    },
    "evidence": {
        "id", "content", "content_hash", "repo_id", "evidence_type",
        "provenance", "metadata", "created_at",
    },
    "belief_evidence": {"belief_id", "evidence_id", "created_at"},
    "authority_attestations": {
        "digest", "belief_id", "key_id", "nonce", "envelope", "created_at",
    },
    "intents": {
        "id", "description", "priority", "status", "context", "created_at",
        "updated_at", "repo_id",
    },
    "relationships": {
        "id", "source_id", "target_id", "relationship", "strength",
        "confidence", "confidence_score", "source", "source_file",
        "source_location", "reason", "created_by", "created_at",
    },
    "sessions": {"id", "summary", "memory_ids", "started_at", "ended_at"},
    "repositories": {
        "id", "name", "url", "description", "tech_stack", "team_id",
        "metadata", "status", "archived_at", "created_at",
    },
    "users": {
        "id", "username", "email", "display_name", "metadata", "created_at",
        "last_active",
    },
    "teams": {"id", "name", "description", "metadata", "created_at"},
    "team_members": {"team_id", "user_id", "role", "joined_at"},
    "repository_dependencies": {
        "id", "source_repo_id", "target_repo_id", "dependency_type", "version",
        "notes", "created_at",
    },
    "audit_logs": {
        "id", "event_type", "actor_id", "repo_id", "target_type", "target_id",
        "metadata", "created_at",
    },
    "recall_events": {
        "id", "memory_id", "event_type", "repo_id", "query_hash", "task_id",
        "outcome", "metadata", "created_at",
    },
}
_V5_REQUIRED_COLUMNS = {
    **_V4_REQUIRED_COLUMNS,
    "sessions": _V4_REQUIRED_COLUMNS["sessions"]
    | {"owner_id", "team_id", "repo_id"},
}


class SessionCompletionStatus(str, Enum):
    """Result of atomically completing a persisted work session."""

    COMPLETED = "completed"
    NOT_FOUND = "not_found"
    ALREADY_COMPLETED = "already_completed"


#: Marks a repositories row the store created for itself because a write named
#: that project scope. It carries no owning team and no description, and explicit
#: registration is allowed to take it over — see ``LocalStorage.store_repository``.
IMPLICIT_REGISTRATION_KEY = "registration"
IMPLICIT_REGISTRATION_VALUE = "implicit"
IMPLICIT_REGISTRATION_METADATA = json.dumps(
    {IMPLICIT_REGISTRATION_KEY: IMPLICIT_REGISTRATION_VALUE}
)


def is_implicitly_registered(repo: Optional[Dict[str, Any]]) -> bool:
    """Whether a repository row exists only because a write named its scope."""
    if not repo:
        return False
    metadata = repo.get("metadata") or {}
    if not isinstance(metadata, dict):
        return False
    return metadata.get(IMPLICIT_REGISTRATION_KEY) == IMPLICIT_REGISTRATION_VALUE


class EvidenceError(ValueError):
    """Base error for evidence contract violations."""


class EvidenceReferenceError(EvidenceError):
    """Raised when a belief cites invalid or out-of-scope evidence."""


class EvidenceImmutableError(EvidenceError):
    """Raised when immutable evidence would be changed."""


class EvidenceUnsupportedError(EvidenceError):
    """Raised when a backend cannot represent Evidence separately."""


class SemanticMemoryImmutableError(EvidenceError):
    """Raised when a semantic belief is edited instead of revised."""


class StorageMigrationRequired(RuntimeError):  # noqa: N818 - public compatibility name
    """Raised when an existing store needs an explicit, backed-up migration."""


class GraphImportRollbackIncompleteError(RuntimeError):
    """Raised when a failed graph import may have left residual vector records."""

    def __init__(self, residual_ids: Iterable[str]):
        self.residual_ids = tuple(dict.fromkeys(residual_ids))
        joined = ", ".join(self.residual_ids)
        super().__init__(
            "Graph import rolled back relational data, but vector compensation failed "
            f"for {joined}; manual vector cleanup is required before retrying"
        )


# Repository purges must be bounded per read.  This is deliberately much smaller
# than the historical 100,000-row cap: a purge may contain more rows than that and
# must make progress without materialising an unbounded backend response.
REPOSITORY_PURGE_PAGE_SIZE = 1_000
_REPOSITORY_MUTATION_STATE_LOCK = threading.Lock()


class RepositoryPurgedError(RuntimeError):
    """Raised when a write races with a completed repository purge."""


def _repository_mutation_state(storage: Any) -> tuple[dict[str, threading.RLock], set[str]]:
    """Return the per-instance locks and completed-purge tombstones.

    Storage subclasses historically do not call a shared ``BaseStorage.__init__``.
    Initialise this state lazily under one module lock so the guard can cover all
    built-in backends without changing their construction contracts.
    """
    with _REPOSITORY_MUTATION_STATE_LOCK:
        locks = getattr(storage, "_repository_mutation_locks", None)
        if locks is None:
            locks = {}
            setattr(storage, "_repository_mutation_locks", locks)
        purged = getattr(storage, "_purged_repository_ids", None)
        if purged is None:
            purged = set()
            setattr(storage, "_purged_repository_ids", purged)
    return locks, purged


def _repository_mutation_lock(storage: Any, repo_id: str) -> threading.RLock:
    locks, _ = _repository_mutation_state(storage)
    with _REPOSITORY_MUTATION_STATE_LOCK:
        return locks.setdefault(repo_id, threading.RLock())


def repository_memory_write(func):
    """Serialize a memory write with purge for its repository scope."""

    @wraps(func)
    def guarded(storage, *args, **kwargs):
        repo_id = kwargs.get("repo_id")
        if repo_id is None and len(args) > 2:
            repo_id = args[2]
        repo_id = repo_id or UNSCOPED_REPO_ID
        if repo_id == UNSCOPED_REPO_ID:
            return func(storage, *args, **kwargs)

        lock = _repository_mutation_lock(storage, repo_id)
        with lock:
            _, purged = _repository_mutation_state(storage)
            if repo_id in purged:
                raise RepositoryPurgedError(
                    f"Repository {repo_id!r} was purged; register it before writing"
                )
            return func(storage, *args, **kwargs)

    return guarded


def repository_registration(func):
    """Allow explicit registration to recreate a previously purged repository."""

    @wraps(func)
    def guarded(storage, repo, *args, **kwargs):
        repo_id = repo.get("id") or storage._generate_id(repo["name"])
        lock = _repository_mutation_lock(storage, repo_id)
        with lock:
            result = func(storage, repo, *args, **kwargs)
            _, purged = _repository_mutation_state(storage)
            purged.discard(repo_id)
            return result

    return guarded


def iter_repository_memories(
    storage: Any,
    repo_id: str,
    *,
    page_size: int = REPOSITORY_PURGE_PAGE_SIZE,
):
    """Yield every memory in a repository using an ID keyset cursor.

    All built-in backends accept ``after_id`` for this internal, deterministic
    ordering.  The small compatibility fallback supports third-party storage
    implementations that have not added the optional keyword yet, provided their
    first page is short; a full page without a cursor is rejected rather than
    silently truncating a purge.
    """
    page_size = max(1, int(page_size))
    cursor = None
    used_legacy_call = False
    seen_ids: set[str] = set()

    while True:
        kwargs = {
            "repo_id": repo_id,
            "status": "all",
            "limit": page_size,
            "order_by": "id ASC",
        }
        if cursor is not None:
            kwargs["after_id"] = cursor
        try:
            page = storage.list_memories(**kwargs)
        except TypeError:
            if cursor is not None or used_legacy_call:
                raise
            # Preserve compatibility with external backends that implement the
            # pre-pagination signature.  A short page is safe; a full page is
            # unsafe because there is no way to advance without an offset/cursor.
            used_legacy_call = True
            page = storage.list_memories(
                repo_id=repo_id,
                status="all",
                limit=page_size,
            )

        if not page:
            return

        page_ids = []
        for memory in page:
            memory_id = memory.get("id") if isinstance(memory, dict) else None
            if not isinstance(memory_id, str) or not memory_id:
                raise ValueError("Repository memory pagination returned an invalid ID")
            if memory_id in seen_ids:
                raise RuntimeError(
                    f"Repository memory pagination did not advance after {memory_id!r}"
                )
            seen_ids.add(memory_id)
            page_ids.append(memory_id)
            yield memory

        if len(page) < page_size:
            return
        if used_legacy_call:
            raise RuntimeError(
                "Storage backend returned a full repository purge page without a cursor"
            )
        cursor = page_ids[-1]


def _purge_repository(storage: Any, repo_id: str) -> Dict[str, Any]:
    """Serialize purge with repository writes and retain a success tombstone."""
    lock = _repository_mutation_lock(storage, repo_id)
    with lock:
        report = _purge_repository_locked(storage, repo_id)
        if report["status"] == "purged":
            _, purged = _repository_mutation_state(storage)
            purged.add(repo_id)
        return report


def _purge_repository_locked(storage: Any, repo_id: str) -> Dict[str, Any]:
    """Run the portable, truthful repository purge protocol.

    Backend-specific implementations provide only the child cleanup and final
    repository-row deletion hooks.  Memory deletion remains per-record so a
    vector or child failure is observed and the repository can be retained for a
    retry instead of being reported as successfully gone.
    """
    report: Dict[str, Any] = {
        "repo_id": repo_id,
        "status": "incomplete",
        "purged_memory_count": 0,
        "purged_memory_ids": [],
        "failed_memory_ids": [],
        "residual": {},
        "errors": [],
    }

    if storage.get_repository(repo_id) is None:
        report["status"] = "not_found"
        return report

    memory_ids: list[str] = []
    memory_listing_failed = False
    try:
        memory_ids = [
            memory["id"]
            for memory in iter_repository_memories(storage, repo_id)
        ]
    except Exception as exc:
        memory_listing_failed = True
        report["errors"].append(
            {"kind": "memory_listing", "error": exc.__class__.__name__}
        )

    if not memory_listing_failed:
        purge_one = getattr(storage, "purge_memory", None) or storage.delete_memory
        for memory_id in memory_ids:
            try:
                deleted = bool(purge_one(memory_id))
            except Exception as exc:
                deleted = False
                report["errors"].append(
                    {
                        "kind": "memory",
                        "id": memory_id,
                        "error": exc.__class__.__name__,
                    }
                )
            if deleted:
                report["purged_memory_count"] += 1
                report["purged_memory_ids"].append(memory_id)
            else:
                report["failed_memory_ids"].append(memory_id)

        # Relationships are memory children in every backend.  Delete them
        # through the public per-edge operation as a second line of defence for
        # stores whose vertex delete does not detach edges automatically.
        try:
            relationships = storage.get_all_relationships(repo_id=repo_id) or []
            delete_relationship = getattr(storage, "delete_relationship", None)
            for relationship in relationships:
                relationship_id = (
                    relationship.get("id") if isinstance(relationship, dict) else None
                )
                if not relationship_id or not callable(delete_relationship):
                    report["errors"].append(
                        {
                            "kind": "relationship",
                            "id": relationship_id,
                            "error": "unsupported_delete",
                        }
                    )
                    continue
                try:
                    if not delete_relationship(relationship_id):
                        report["errors"].append(
                            {
                                "kind": "relationship",
                                "id": relationship_id,
                                "error": "delete_failed",
                            }
                        )
                except Exception as exc:
                    report["errors"].append(
                        {
                            "kind": "relationship",
                            "id": relationship_id,
                            "error": exc.__class__.__name__,
                        }
                    )
        except Exception as exc:
            report["errors"].append(
                {
                    "kind": "relationship",
                    "error": exc.__class__.__name__,
                }
            )

        child_cleanup = getattr(storage, "_purge_repository_children", None)
        if callable(child_cleanup):
            try:
                try:
                    child_errors = child_cleanup(
                        repo_id,
                        memory_ids=report["purged_memory_ids"],
                    ) or []
                except TypeError:
                    # Keep third-party hooks written against the initial one-arg
                    # extension point working while built-ins can clean detached
                    # edge records by the IDs they actually removed.
                    child_errors = child_cleanup(repo_id) or []
                if isinstance(child_errors, dict):
                    report["errors"].append(child_errors)
                else:
                    report["errors"].extend(child_errors)
            except Exception as exc:
                report["errors"].append(
                    {"kind": "children", "error": exc.__class__.__name__}
                )

    def residual_ids(kind: str, values: Any) -> None:
        ids = []
        for value in values or []:
            if isinstance(value, dict):
                value_id = value.get("id")
            else:
                value_id = value
            if value_id:
                ids.append(str(value_id))
        if ids:
            report["residual"][kind] = ids

    try:
        residual_ids(
            "memories",
            [memory for memory in iter_repository_memories(storage, repo_id)],
        )
    except Exception as exc:
        report["errors"].append(
            {"kind": "memory_verification", "error": exc.__class__.__name__}
        )

    try:
        residual_ids(
            "intents",
            storage.get_active_intents(repo_id=repo_id, status="all"),
        )
    except Exception as exc:
        report["errors"].append(
            {"kind": "intent_verification", "error": exc.__class__.__name__}
        )

    try:
        residual_ids("relationships", storage.get_all_relationships(repo_id=repo_id))
    except Exception as exc:
        report["errors"].append(
            {"kind": "relationship_verification", "error": exc.__class__.__name__}
        )

    try:
        residual_ids("dependencies", storage.get_repo_dependencies(repo_id))
    except Exception as exc:
        report["errors"].append(
            {"kind": "dependency_verification", "error": exc.__class__.__name__}
        )

    list_evidence = getattr(storage, "list_evidence", None)
    if callable(list_evidence):
        try:
            residual_ids("evidence", list_evidence(repo_id=repo_id))
        except EvidenceUnsupportedError:
            pass
        except Exception as exc:
            report["errors"].append(
                {"kind": "evidence_verification", "error": exc.__class__.__name__}
            )

    if report["failed_memory_ids"] or report["errors"] or report["residual"]:
        return report

    delete_repository_record = getattr(storage, "_delete_repository_record", None)
    if not callable(delete_repository_record):
        report["errors"].append(
            {"kind": "repository", "error": "unsupported_backend_hook"}
        )
        return report
    try:
        if not delete_repository_record(repo_id):
            report["errors"].append(
                {"kind": "repository", "id": repo_id, "error": "delete_failed"}
            )
    except Exception as exc:
        report["errors"].append(
            {"kind": "repository", "id": repo_id, "error": exc.__class__.__name__}
        )

    if storage.get_repository(repo_id) is not None:
        report["residual"]["repository"] = [repo_id]
    if not report["errors"] and not report["residual"]:
        report["status"] = "purged"
    return report


@dataclass(frozen=True)
class StorageCapabilities:
    """Feature contract advertised by each storage backend."""

    graph: bool = True
    vector_search: bool = False
    repositories: bool = True
    teams: bool = True
    sessions: bool = True
    audit_log: bool = False
    reindex: bool = False
    complete_graph_export: bool = False
    atomic_graph_import: bool = False

    def to_dict(self) -> Dict[str, bool]:
        return asdict(self)


class BaseStorage(ABC):
    """Abstract interface for memory storage."""

    def close(self) -> None:
        """Release any resources held by the backend (connection pools, sessions).

        Default is a no-op so backends without long-lived handles need not override
        it. Backends that own such resources (e.g. Neo4j drivers, HTTP sessions,
        ChromaDB clients) override this and must make it idempotent.
        """

    def get_capabilities(self) -> StorageCapabilities:
        """Return the backend's supported optional feature set."""
        return StorageCapabilities()

    def get_schema_status(self) -> Dict[str, Any]:
        """Return non-secret schema compatibility information."""
        return {
            "current_version": STORAGE_SCHEMA_VERSION,
            "stored_version": STORAGE_SCHEMA_VERSION,
            "status": "ready",
        }

    def _resolve_recall_memory_scope(
        self, memory_id: str, repo_id: str = None
    ) -> tuple[Dict[str, Any], str | None]:
        """Resolve a memory's repository before reading or writing recall events.

        Recall utility events describe how a particular memory was used.  The
        repository on that memory is therefore authoritative; accepting a
        caller-provided repository here would let a malformed or stale caller
        attribute the event to another repository.  Prefer a no-access read for
        backends that provide one, and fall back to the backend's ordinary read
        for portable implementations.
        """
        peek = getattr(self, "peek_memory", None)
        if callable(peek):
            memory = peek(memory_id)
        else:
            query = getattr(self, "_query_memory", None)
            memory = query(memory_id) if callable(query) else self.get_memory(memory_id)
        if memory is None:
            raise ValueError(f"Memory not found: {memory_id}")

        canonical_repo_id = memory.get("repo_id")
        if repo_id is not None and repo_id != canonical_repo_id:
            raise ValueError(
                "Recall utility repository mismatch for memory "
                f"{memory_id!r}: memory belongs to {canonical_repo_id!r}, "
                f"caller supplied {repo_id!r}"
            )
        return memory, canonical_repo_id

    def store_evidence(self, content: str, repo_id: str, **kwargs) -> str:
        raise EvidenceUnsupportedError(
            f"{self.__class__.__name__} does not support separate Evidence records"
        )

    def get_evidence(self, evidence_id: str) -> Optional[Dict[str, Any]]:
        raise EvidenceUnsupportedError(
            f"{self.__class__.__name__} does not support separate Evidence records"
        )

    def list_evidence(self, repo_id: str, **kwargs) -> List[Dict[str, Any]]:
        raise EvidenceUnsupportedError(
            f"{self.__class__.__name__} does not support separate Evidence records"
        )

    def update_evidence(self, evidence_id: str, **kwargs) -> bool:
        raise EvidenceImmutableError(f"Evidence {evidence_id!r} is immutable")

    def attach_evidence(
        self, belief_id: str, evidence_ids: List[str], *, repo_id: str
    ) -> None:
        raise EvidenceUnsupportedError(
            f"{self.__class__.__name__} does not support belief Evidence references"
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    @abstractmethod
    def store_memory(
        self, content: str, layer: MemoryLayer = "episodic", repo_id: str = None, **kwargs
    ) -> str:
        """Store a memory."""
        pass

    @abstractmethod
    def get_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Get a memory by ID."""
        pass

    @abstractmethod
    def search_memories(self, query: str, repo_id: str = None, **kwargs) -> List[Dict[str, Any]]:
        """Search across memories."""
        pass

    @abstractmethod
    def list_memories(self, repo_id: str = None, **kwargs) -> List[Dict[str, Any]]:
        """List memories."""
        pass

    @abstractmethod
    def update_memory(self, memory_id: str, **kwargs) -> bool:
        """Update a memory."""
        pass

    def revise_memory(
        self,
        memory_id: str,
        content: str,
        *,
        evidence_ids: List[str],
        authority_attestation: str = None,
        metadata: Dict[str, Any] = None,
        quality_flags: List[str] = None,
        reason: str = None,
        importance: float = None,
        tags: List[str] = None,
    ) -> str:
        """Create an evidence-backed successor for an immutable semantic belief.

        The implementation is deliberately expressed in terms of the portable storage
        contract so RemoteStorage and graph backends get the same governance behavior.
        Backends that cannot store governed Evidence fail through ``store_memory``.
        """
        if not isinstance(content, str) or not content:
            raise ValueError("Revised semantic content must be a non-empty string")
        resolved_evidence_ids = list(dict.fromkeys(evidence_ids or []))
        if not resolved_evidence_ids:
            raise EvidenceReferenceError(
                "A semantic revision requires at least one evidence record"
            )

        peek = getattr(self, "peek_memory", None)
        existing = peek(memory_id) if callable(peek) else self.get_memory(memory_id)
        if existing is None:
            raise ValueError(f"Memory not found: {memory_id}")
        if existing.get("layer") != "semantic":
            raise ValueError("Only semantic memories can be revised")
        if existing.get("status") in NON_SERVABLE_STATUSES:
            raise ValueError(
                f"Cannot revise a {existing.get('status')} semantic memory"
            )
        existing_evidence_ids = set(existing.get("evidence_ids") or [])
        if not any(
            evidence_id not in existing_evidence_ids
            for evidence_id in resolved_evidence_ids
        ):
            raise EvidenceReferenceError(
                "A semantic revision requires new evidence not already linked to the original"
            )

        try:
            sanitized_content, sanitized_flags = redact_for_storage(
                content,
                [*(existing.get("quality_flags") or []), *(quality_flags or [])],
                reject_if_redacted=authority_attestation is not None,
            )
        except SecretBearingContentError as exc:
            from visp_memory.core.authority import ProhibitionAuthorityError

            raise ProhibitionAuthorityError(str(exc)) from exc

        belief_type = existing.get("belief_type") or existing.get("category")
        if belief_type == "prohibition" and authority_attestation is None:
            from visp_memory.core.authority import ProhibitionAuthorityError

            raise ProhibitionAuthorityError(
                "prohibition revision requires a new authority attestation"
            )
        if belief_type != "prohibition" and authority_attestation is not None:
            raise ValueError(
                "authority attestation applies only to a prohibition revision"
            )

        revision_reason = reason or "Evidence-backed semantic revision"
        revision_reason, _ = redact_for_storage(revision_reason, None)
        revision_importance = existing.get("importance", 0.5)
        if importance is not None:
            revision_importance = max(float(revision_importance or 0.0), float(importance))
        successor_metadata = {
            **(existing.get("metadata") or {}),
            **(metadata or {}),
            "revision_of": memory_id,
        }
        successor_id = self.store_memory(
            sanitized_content,
            layer="semantic",
            repo_id=existing.get("repo_id"),
            category=belief_type,
            importance=revision_importance,
            tags=list(existing.get("tags") or []) if tags is None else list(tags),
            metadata=successor_metadata,
            evidence_ids=resolved_evidence_ids,
            status="active",
            authority_attestation=authority_attestation,
            replaces_belief_id=memory_id if belief_type == "prohibition" else None,
            source=existing.get("source"),
            quality_flags=sanitized_flags,
            auto_link=False,
        )

        try:
            self.add_relationship(
                successor_id,
                memory_id,
                "supersedes",
                evidence={
                    "confidence": "observed",
                    "source": "semantic_revision",
                    "reason": revision_reason,
                },
            )
            old_metadata = {
                **(existing.get("metadata") or {}),
                "superseded_by": successor_id,
                "superseded_reason": revision_reason,
                "invalid_at": utc_now().isoformat(),
            }
            if not self.update_memory(
                memory_id, status="superseded", metadata=old_metadata
            ):
                raise RuntimeError(
                    f"Could not mark revised memory {memory_id!r} as superseded"
                )
        except Exception:
            try:
                self.delete_memory(successor_id)
            except Exception:
                logger.exception(
                    "Failed to remove incomplete semantic revision %s", successor_id
                )
            raise

        return successor_id

    @abstractmethod
    def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory."""
        pass

    @abstractmethod
    def get_collection(self, layer: str):
        """Get underlying vector collection (if applicable)."""
        pass

    # Intent Operations
    @abstractmethod
    def set_intent(
        self,
        description: str,
        priority: int = 0,
        context: Dict[str, Any] = None,
        repo_id: str = None,
    ) -> str:
        """Set a new intent."""
        pass

    @abstractmethod
    def get_active_intents(
        self, repo_id: str = None, status: str = "active"
    ) -> List[Dict[str, Any]]:
        """Get intents filtered by status."""
        pass

    @abstractmethod
    def complete_intent(self, intent_id: str) -> bool:
        """Deprecated compatibility surface; intent status is externally owned."""
        pass

    @abstractmethod
    def update_intent(self, intent_id: str, **kwargs) -> bool:
        """Update an intent."""
        pass

    def append_intent_outcome(
        self, intent_id: str, outcome: Dict[str, Any]
    ) -> bool:
        """Atomically append one non-authoritative outcome history entry."""
        raise NotImplementedError

    def report_intent_workflow(self, intent_id: str, report, *, actor_id: str, channel: str):
        """Mirror an explicit external report when supported by the backend."""
        raise NotImplementedError("External workflow reports currently require SQLite storage")

    # Relationship Operations
    @abstractmethod
    def add_relationship(
        self, source_id: str, target_id: str, relationship: str, strength: float = 1.0
    ) -> str:
        """Add a relationship."""
        pass

    @abstractmethod
    def get_related_memories(
        self, memory_id: str, relationship: str = None
    ) -> List[Dict[str, Any]]:
        """Get related memories."""
        pass

    # Session Operations
    @abstractmethod
    def start_session(
        self,
        *,
        owner_id: str = None,
        team_id: str = None,
        repo_id: str = None,
    ) -> str:
        """Start a session."""
        pass

    @abstractmethod
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Return persisted session metadata when supported."""
        pass

    @abstractmethod
    def end_session(
        self, session_id: str, summary: str, memory_ids: List[str]
    ) -> SessionCompletionStatus:
        """End a session."""
        pass

    @abstractmethod
    def get_all_relationships(self, repo_id: str = None) -> List[Dict[str, Any]]:
        """Get all relationships."""
        pass

    def delete_relationship(self, relationship_id: str) -> bool:
        """Delete a relationship by ID when supported."""
        return False

    # Stats
    @abstractmethod
    def get_stats(self, repo_id: str = None) -> Dict[str, Any]:
        """Get statistics."""
        pass

    # Repository operations (Phase 3.2)
    @abstractmethod
    def store_repository(self, repo: Dict[str, Any]) -> str:
        """Store a repository."""
        pass

    @abstractmethod
    def get_repository(self, repo_id: str) -> Optional[Dict[str, Any]]:
        """Get repository by ID."""
        pass

    @abstractmethod
    def list_repositories(self, team_id: str = None) -> List[Dict[str, Any]]:
        """List all repositories."""
        pass

    def update_repository(self, repo_id: str, **kwargs) -> bool:
        """Update repository lifecycle or display fields."""
        return False

    def delete_repository(self, repo_id: str) -> bool:
        """Permanently remove a repository and its scoped records."""
        return self.purge_repository(repo_id)["status"] == "purged"

    def purge_repository(self, repo_id: str) -> Dict[str, Any]:
        """Permanently remove a repository and return a truthful retry report.

        ``delete_repository`` remains the historical boolean surface.  Callers
        that need to distinguish a missing repository from an incomplete purge
        should use this report-producing method.
        """
        return _purge_repository(self, repo_id)

    def _purge_repository_children(
        self, repo_id: str, *, memory_ids: Iterable[str] = ()
    ) -> list[Dict[str, Any]]:
        """Delete backend-specific repository children and return failures."""
        return []

    def _delete_repository_record(self, repo_id: str) -> bool:
        """Delete only the repository row after all children are verified gone."""
        return False

    @abstractmethod
    def list_project_ids(self) -> List[str]:
        """List repository/project IDs referenced by stored data."""
        pass

    @abstractmethod
    def add_repo_dependency(
        self, source_id: str, target_id: str, dep_type: str, version: str = None, notes: str = None
    ) -> str:
        """Add dependency between repos."""
        pass

    @abstractmethod
    def get_repo_dependencies(self, repo_id: str) -> List[Dict[str, Any]]:
        """Get repository dependencies."""
        pass

    # Team and User operations (Phase 3.3)
    @abstractmethod
    def store_user(self, user: Dict[str, Any]) -> str:
        """Store a user."""
        pass

    @abstractmethod
    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user by ID."""
        pass

    @abstractmethod
    def store_team(self, team: Dict[str, Any]) -> str:
        """Store a team."""
        pass

    @abstractmethod
    def get_team(self, team_id: str) -> Optional[Dict[str, Any]]:
        """Get team by ID."""
        pass

    @abstractmethod
    def add_team_member(self, team_id: str, user_id: str) -> bool:
        """Add member to team."""
        pass

    @abstractmethod
    def get_user_teams(self, user_id: str) -> List[Dict[str, Any]]:
        """Get all teams for a user."""
        pass


class LocalStorage(BaseStorage):
    """Unified storage for structured data and vector embeddings (Local SQLite + Chroma)."""

    AUTO_LINK_RELATIONSHIP = "related_to"
    SOURCE_LINK_RELATIONSHIP = "derived_from"
    DEFAULT_AUTO_LINK_LIMIT = 3
    DEFAULT_AUTO_LINK_MIN_SCORE = 0.53
    RELATIONSHIP_CONFIDENCE_VALUES = {"observed", "inferred", "ambiguous", "manual"}
    LEGACY_RELATIONSHIP_EVIDENCE_REASON = "Legacy relationship without evidence metadata."
    UNSPECIFIED_RELATIONSHIP_EVIDENCE_REASON = "Relationship created without evidence metadata."

    def __init__(self, data_dir: Path, embedding_fn=None):
        """
        Initialize storage.

        Args:
            data_dir: Directory for all data files
            embedding_fn: Optional function to generate embeddings.
                         If None, ChromaDB's default will be used.
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = self.data_dir / "memories.db"
        self.chroma_path = self.data_dir / "vectors"

        self._embedding_fn = embedding_fn
        embedding_owner = getattr(embedding_fn, "__self__", None)
        embedding_owner_name = embedding_owner.__class__.__name__.lower() if embedding_owner else ""
        self._uses_noop_embeddings = embedding_owner_name == "noopprovider"
        self._embedding_dimension = getattr(embedding_owner, "dimension", None)
        self._chroma_client = None
        self._collections = {}

        self._init_sqlite()

    def _init_sqlite(self):
        """Initialize SQLite schema."""
        with self._get_db() as conn:
            existing_tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            }
            stored_version = 0
            if "schema_migrations" in existing_tables:
                row = conn.execute(
                    "SELECT MAX(version) AS version FROM schema_migrations"
                ).fetchone()
                stored_version = int(row["version"] or 0)

            if stored_version == 0 and existing_tables & _STORAGE_TABLE_NAMES:
                raise StorageMigrationRequired(
                    "Unversioned legacy storage requires an explicit migration; "
                    "back up the database before converting it to schema "
                    f"{STORAGE_SCHEMA_VERSION}"
                )
            if stored_version > STORAGE_SCHEMA_VERSION:
                raise RuntimeError(
                    "Storage schema is newer than this visp-memory build "
                    f"({stored_version} > {STORAGE_SCHEMA_VERSION})"
                )
            if stored_version and stored_version < STORAGE_SCHEMA_VERSION:
                raise StorageMigrationRequired(
                    f"Storage schema {stored_version} requires explicit migration to "
                    f"{STORAGE_SCHEMA_VERSION}; create a backup and run "
                    "LocalStorage.migrate_schema(...)"
                )
            if stored_version == STORAGE_SCHEMA_VERSION:
                self._validate_v5_schema(conn)

            # Enable WAL only after compatibility checks: it persists in the
            # database header and legacy stores must remain byte-for-byte unchanged.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            # Main memories table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    layer TEXT NOT NULL DEFAULT 'episodic',
                    category TEXT DEFAULT 'general',
                    belief_type TEXT DEFAULT NULL,
                    epistemic_status TEXT DEFAULT NULL,
                    importance REAL DEFAULT 0.5,
                    repo_id TEXT DEFAULT NULL,
                    access_count INTEGER DEFAULT 0,
                    tags TEXT DEFAULT '[]',
                    metadata TEXT DEFAULT '{}',
                    source_ids TEXT DEFAULT '[]',
                    status TEXT DEFAULT 'active',
                    approved_by TEXT DEFAULT NULL,
                    approved_at TIMESTAMP DEFAULT NULL,
                    archived_at TIMESTAMP DEFAULT NULL,
                    source TEXT DEFAULT NULL,
                    quality_flags TEXT DEFAULT '[]',
                    last_quality_checked_at TIMESTAMP DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    compressed_at TIMESTAMP DEFAULT NULL
                )
            """)

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS evidence (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    repo_id TEXT DEFAULT NULL,
                    evidence_type TEXT NOT NULL DEFAULT 'observation',
                    provenance TEXT NOT NULL DEFAULT 'unknown',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS belief_evidence (
                    belief_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (belief_id, evidence_id),
                    FOREIGN KEY (belief_id) REFERENCES memories(id) ON DELETE CASCADE,
                    FOREIGN KEY (evidence_id) REFERENCES evidence(id) ON DELETE RESTRICT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS authority_attestations (
                    digest TEXT PRIMARY KEY,
                    belief_id TEXT NOT NULL UNIQUE,
                    key_id TEXT NOT NULL,
                    nonce TEXT NOT NULL,
                    envelope TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (key_id, nonce),
                    FOREIGN KEY (belief_id) REFERENCES memories(id) ON DELETE CASCADE
                )
                """
            )

            # Migration: Check if repo_id column exists in memories
            try:
                conn.execute("SELECT repo_id FROM memories LIMIT 1")
            except sqlite3.OperationalError:
                # Column doesn't exist, add it
                conn.execute("ALTER TABLE memories ADD COLUMN repo_id TEXT DEFAULT NULL")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_repo ON memories(repo_id)")

            memory_migrations = {
                "created_at": (
                    "ALTER TABLE memories ADD COLUMN created_at TIMESTAMP DEFAULT NULL"
                ),
                "status": "ALTER TABLE memories ADD COLUMN status TEXT DEFAULT 'active'",
                "approved_by": "ALTER TABLE memories ADD COLUMN approved_by TEXT DEFAULT NULL",
                "approved_at": "ALTER TABLE memories ADD COLUMN approved_at TIMESTAMP DEFAULT NULL",
                "archived_at": "ALTER TABLE memories ADD COLUMN archived_at TIMESTAMP DEFAULT NULL",
                "source": "ALTER TABLE memories ADD COLUMN source TEXT DEFAULT NULL",
                "quality_flags": "ALTER TABLE memories ADD COLUMN quality_flags TEXT DEFAULT '[]'",
                "last_quality_checked_at": (
                    "ALTER TABLE memories ADD COLUMN last_quality_checked_at TIMESTAMP DEFAULT NULL"
                ),
            }
            for column, statement in memory_migrations.items():
                try:
                    conn.execute(f"SELECT {column} FROM memories LIMIT 1")
                except sqlite3.OperationalError:
                    conn.execute(statement)

            # Intent tracking (current direction/goals)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS intents (
                    id TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    priority INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'active',
                    context TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Migration: Check if repo_id column exists in intents
            try:
                conn.execute("SELECT repo_id FROM intents LIMIT 1")
            except sqlite3.OperationalError:
                # Column doesn't exist, add it
                conn.execute("ALTER TABLE intents ADD COLUMN repo_id TEXT DEFAULT NULL")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_intents_repo ON intents(repo_id)")

            # Knowledge graph (relationships between memories)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS relationships (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    relationship TEXT NOT NULL,
                    strength REAL DEFAULT 1.0,
                    confidence TEXT DEFAULT 'ambiguous',
                    confidence_score REAL DEFAULT NULL,
                    source TEXT DEFAULT 'legacy',
                    source_file TEXT DEFAULT NULL,
                    source_location TEXT DEFAULT NULL,
                    reason TEXT DEFAULT 'Legacy relationship without evidence metadata.',
                    created_by TEXT DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (source_id) REFERENCES memories(id),
                    FOREIGN KEY (target_id) REFERENCES memories(id)
                )
            """)

            relationship_migrations = {
                "confidence": (
                    "ALTER TABLE relationships ADD COLUMN confidence TEXT DEFAULT 'ambiguous'"
                ),
                "confidence_score": (
                    "ALTER TABLE relationships ADD COLUMN confidence_score REAL DEFAULT NULL"
                ),
                "source": "ALTER TABLE relationships ADD COLUMN source TEXT DEFAULT 'legacy'",
                "source_file": (
                    "ALTER TABLE relationships ADD COLUMN source_file TEXT DEFAULT NULL"
                ),
                "source_location": (
                    "ALTER TABLE relationships ADD COLUMN source_location TEXT DEFAULT NULL"
                ),
                "reason": (
                    "ALTER TABLE relationships ADD COLUMN reason TEXT DEFAULT "
                    "'Legacy relationship without evidence metadata.'"
                ),
                "created_by": (
                    "ALTER TABLE relationships ADD COLUMN created_by TEXT DEFAULT NULL"
                ),
            }
            for column, statement in relationship_migrations.items():
                try:
                    conn.execute(f"SELECT {column} FROM relationships LIMIT 1")
                except sqlite3.OperationalError:
                    conn.execute(statement)

            # Session tracking (for compression)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT DEFAULT NULL,
                    team_id TEXT DEFAULT NULL,
                    repo_id TEXT DEFAULT NULL,
                    summary TEXT,
                    memory_ids TEXT DEFAULT '[]',
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ended_at TIMESTAMP DEFAULT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(owner_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_repo ON sessions(repo_id)")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS repositories (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    url TEXT,
                    description TEXT,
                    tech_stack TEXT DEFAULT '[]',
                    team_id TEXT,
                    metadata TEXT DEFAULT '{}',
                    status TEXT DEFAULT 'active',
                    archived_at TIMESTAMP DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            repository_migrations = {
                "status": "ALTER TABLE repositories ADD COLUMN status TEXT DEFAULT 'active'",
                "archived_at": (
                    "ALTER TABLE repositories ADD COLUMN archived_at TIMESTAMP DEFAULT NULL"
                ),
            }
            for column, statement in repository_migrations.items():
                try:
                    conn.execute(f"SELECT {column} FROM repositories LIMIT 1")
                except sqlite3.OperationalError:
                    conn.execute(statement)

            # Team management (Phase 3.3)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    email TEXT,
                    display_name TEXT,
                    metadata TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS teams (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    metadata TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS team_members (
                    team_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT DEFAULT 'member',
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (team_id, user_id),
                    FOREIGN KEY (team_id) REFERENCES teams(id),
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS repository_dependencies (
                    id TEXT PRIMARY KEY,
                    source_repo_id TEXT NOT NULL,
                    target_repo_id TEXT NOT NULL,
                    dependency_type TEXT NOT NULL,
                    version TEXT,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (source_repo_id) REFERENCES repositories(id),
                    FOREIGN KEY (target_repo_id) REFERENCES repositories(id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    actor_id TEXT,
                    repo_id TEXT,
                    target_type TEXT,
                    target_id TEXT,
                    metadata TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS recall_events (
                    id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    repo_id TEXT DEFAULT NULL,
                    query_hash TEXT DEFAULT NULL,
                    task_id TEXT DEFAULT NULL,
                    outcome TEXT DEFAULT NULL,
                    metadata TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (memory_id) REFERENCES memories(id)
                )
            """)

            # Indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_layer ON memories(layer)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_repo ON memories(repo_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_evidence_repo ON evidence(repo_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_belief_evidence_evidence "
                "ON belief_evidence(evidence_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_repo_status_created "
                "ON memories(repo_id, status, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_repo_layer_status "
                "ON memories(repo_id, layer, status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_intents_status ON intents(status)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_intents_repo_status_priority "
                "ON intents(repo_id, status, priority)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_relationships_source ON relationships(source_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_relationships_target ON relationships(target_id)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_repos_team ON repositories(team_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_repo_deps_source "
                "ON repository_dependencies(source_repo_id)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_event ON audit_logs(event_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_logs(actor_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_repo ON audit_logs(repo_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_recall_events_memory "
                "ON recall_events(memory_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_recall_events_repo ON recall_events(repo_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_recall_events_type "
                "ON recall_events(event_type)"
            )

            self._register_repositories_for_existing_scopes(conn)

            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)",
                (STORAGE_SCHEMA_VERSION,),
            )

            conn.commit()

    @staticmethod
    def _register_repositories_for_existing_scopes(conn: sqlite3.Connection) -> None:
        """Give every project scope that holds records a repositories row.

        A repository is not something the user declares; it is implied by having
        memories in it. Nothing ever wrote this table — `repos register` is frozen —
        so every repo-scoped join in the API resolved against an empty table and the
        server answered with rows it could not attribute to anything.

        Writes create the row from now on (see ``_register_repository``); this repairs
        stores written before that. It is an idempotent data repair rather than a
        schema change, so it carries no migration version: the shape of the table is
        unchanged and running it twice does nothing.
        """
        conn.execute(
            """
            INSERT OR IGNORE INTO repositories (id, name, metadata)
            SELECT id, id, ? FROM (
                SELECT DISTINCT repo_id AS id FROM memories
                UNION
                SELECT DISTINCT repo_id AS id FROM intents
            )
            WHERE id IS NOT NULL AND id != '' AND id != ?
            """,
            (IMPLICIT_REGISTRATION_METADATA, UNSCOPED_REPO_ID),
        )

    @staticmethod
    def _connect_readonly(data_dir: Path) -> Optional[sqlite3.Connection]:
        """Open the store read-only, or return None when there is no store yet.

        Shared by the diagnostics that must observe a store without touching it.
        Opening a ``LocalStorage`` runs the schema step, which writes; a
        diagnostic that repairs what it measures can never report it.
        """
        db_path = Path(data_dir) / "memories.db"
        if not db_path.exists():
            return None

        # as_uri() rather than an f-string: a Windows path is backslash-separated
        # and drive-lettered, which is not a URI, and read-only mode is only
        # reachable through the URI form.
        return sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True, timeout=30.0)

    @classmethod
    def inspect_intent_usage(cls, data_dir: Path) -> Dict[str, Any]:
        """Count active memories and intents without opening or creating the store.

        ``total_intents`` counts every row, not only active ones: an intent whose
        status moved on was still an intent that was *set*, and the question this
        answers is whether the lifecycle has ever been used at all.
        """
        conn = cls._connect_readonly(data_dir)
        if conn is None:
            return {"exists": False, "memories": 0, "active_intents": 0, "total_intents": 0}

        try:
            memories = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE status = 'active'"
            ).fetchone()[0]
            active_intents = conn.execute(
                "SELECT COUNT(*) FROM intents WHERE status = 'active'"
            ).fetchone()[0]
            total_intents = conn.execute("SELECT COUNT(*) FROM intents").fetchone()[0]
        finally:
            conn.close()

        return {
            "exists": True,
            "memories": memories,
            "active_intents": active_intents,
            "total_intents": total_intents,
        }

    @classmethod
    def inspect_repository_registration(cls, data_dir: Path) -> Dict[str, Any]:
        """Report project scopes holding records that have no repositories row.

        Deliberately a classmethod over the file rather than a method on an open
        store: opening a ``LocalStorage`` runs the schema step, which registers the
        missing rows, and a diagnostic that repairs what it measures can never
        report it.
        """
        conn = cls._connect_readonly(data_dir)
        if conn is None:
            return {"exists": False, "project_scopes": [], "unregistered_scopes": []}

        try:
            scopes = [
                row[0]
                for row in conn.execute(
                    """
                    SELECT DISTINCT repo_id FROM memories
                    UNION
                    SELECT DISTINCT repo_id FROM intents
                    """
                )
                if row[0] and row[0] != UNSCOPED_REPO_ID
            ]
            registered = {row[0] for row in conn.execute("SELECT id FROM repositories")}
        finally:
            conn.close()

        return {
            "exists": True,
            "project_scopes": sorted(scopes),
            "unregistered_scopes": sorted(scope for scope in scopes if scope not in registered),
        }

    @staticmethod
    def _register_repository(conn: sqlite3.Connection, repo_id: str) -> None:
        """Record the project scope a write is landing in, if it is not recorded yet.

        Deliberately leaves ``team_id`` NULL: storage has no principal, so an
        implicit row must carry no tenant and must stay indistinguishable from an
        absent row for authorization. Explicit registration through the API still
        sets the owning team.

        The reserved unscoped bucket is not a repository and never gets a row.
        """
        if not repo_id or repo_id == UNSCOPED_REPO_ID:
            return
        conn.execute(
            "INSERT OR IGNORE INTO repositories (id, name, metadata) VALUES (?, ?, ?)",
            (repo_id, repo_id, IMPLICIT_REGISTRATION_METADATA),
        )

    @classmethod
    def _validate_v4_schema(cls, conn: sqlite3.Connection) -> None:
        """Refuse a malformed declared-v4 governed graph without repairing it."""
        try:
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            for table, expected in _V4_REQUIRED_COLUMNS.items():
                if table not in tables:
                    raise ValueError(f"missing {table} table")
                columns = {
                    row["name"]: row for row in conn.execute(f"PRAGMA table_info({table})")
                }
                missing = expected - columns.keys()
                if missing:
                    raise ValueError(
                        f"{table} is missing columns {', '.join(sorted(missing))}"
                    )

            for row in conn.execute(
                "SELECT id, layer, category, belief_type, epistemic_status FROM memories"
            ):
                if row["layer"] == "semantic":
                    belief_type = normalize_belief_type(row["belief_type"])
                    normalize_epistemic_status(row["epistemic_status"])
                    if row["category"] != belief_type:
                        raise ValueError(
                            f"semantic memory {row['id']!r} has divergent category and type"
                        )

                elif row["belief_type"] is not None or row["epistemic_status"] is not None:
                    raise ValueError(
                        f"non-semantic memory {row['id']!r} carries semantic belief fields"
                    )

            link_columns = {
                row["name"]: row
                for row in conn.execute("PRAGMA table_info(belief_evidence)")
            }
            if (
                link_columns["belief_id"]["pk"] != 1
                or link_columns["evidence_id"]["pk"] != 2
            ):
                raise ValueError("belief_evidence has an invalid primary key")

            foreign_keys = {
                (
                    row["from"],
                    row["table"],
                    row["to"],
                    str(row["on_delete"]).upper(),
                )
                for row in conn.execute("PRAGMA foreign_key_list(belief_evidence)")
            }
            expected_foreign_keys = {
                ("belief_id", "memories", "id", "CASCADE"),
                ("evidence_id", "evidence", "id", "RESTRICT"),
            }
            if not expected_foreign_keys.issubset(foreign_keys):
                raise ValueError("belief_evidence has invalid foreign keys")

            for row in conn.execute("SELECT id, content, content_hash FROM evidence"):
                if cls._evidence_hash(row["content"]) != row["content_hash"]:
                    raise ValueError(f"Evidence {row['id']!r} has an invalid content hash")
                try:
                    cls._require_secret_free_evidence(
                        row["content"], context="opening secret-bearing Evidence"
                    )
                except EvidenceError as exc:
                    raise ValueError(
                        f"Evidence {row['id']!r} contains secret-bearing Evidence content"
                    ) from exc

            invalid_links = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM belief_evidence be
                LEFT JOIN memories m ON m.id = be.belief_id
                LEFT JOIN evidence e ON e.id = be.evidence_id
                WHERE m.id IS NULL OR e.id IS NULL OR m.repo_id != e.repo_id
                """
            ).fetchone()["count"]
            if invalid_links:
                raise ValueError("belief_evidence contains dangling or cross-repository links")

            missing_semantic_links = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM memories m
                WHERE m.layer = 'semantic'
                AND NOT EXISTS (
                    SELECT 1 FROM belief_evidence be WHERE be.belief_id = m.id
                )
                """
            ).fetchone()["count"]
            if missing_semantic_links:
                raise ValueError("semantic memories are missing Evidence links")
        except (sqlite3.DatabaseError, KeyError, TypeError, ValueError) as exc:
            raise StorageMigrationRequired(
                f"Storage schema 4 is malformed: {exc}; restore a valid backup or "
                "run a supported migration"
            ) from exc

    @classmethod
    def _validate_v5_schema(cls, conn: sqlite3.Connection) -> None:
        """Refuse a malformed declared-v5 graph without repairing it."""
        cls._validate_v4_schema(conn)
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(sessions)")
        }
        missing = _V5_REQUIRED_COLUMNS["sessions"] - columns
        if missing:
            raise StorageMigrationRequired(
                "Declared schema v5 sessions table is missing columns: "
                + ", ".join(sorted(missing))
            )

    def import_graph(
        self, data: Dict[str, Any], *, default_repo_id: str
    ) -> Dict[str, Any]:
        """Prevalidate and import one format-3 graph in a single SQLite transaction."""
        from visp_memory.core.authority import (
            ProhibitionAuthorityError,
            verify_prohibition_attestation,
        )
        evidence_items = list(data.get("evidence") or [])
        memories_by_layer = data.get("memories") or {}
        memory_items = [
            {**item, "layer": item.get("layer") or layer}
            for layer, items in memories_by_layer.items()
            for item in (items or [])
        ]
        intent_items = list(data.get("intents") or [])
        relationship_items = list(data.get("relationships") or [])
        authority_items = list(data.get("authority_attestations") or [])
        authority_link_items = list(data.get("belief_authority") or [])
        legacy_format2 = bool(data.get("_legacy_format2"))

        def indexed(items: List[Dict[str, Any]], kind: str) -> Dict[str, Dict[str, Any]]:
            result: Dict[str, Dict[str, Any]] = {}
            for item in items:
                item_id = item.get("id")
                if not isinstance(item_id, str) or not item_id:
                    raise ValueError(f"Every imported {kind} requires a stable id")
                if item_id in result:
                    raise ValueError(f"Duplicate imported {kind} id {item_id!r}")
                result[item_id] = item
            return result

        evidence_by_id = indexed(evidence_items, "Evidence")
        memories_by_id = indexed(memory_items, "memory")
        intents_by_id = indexed(intent_items, "intent")
        relationships_by_id = indexed(relationship_items, "relationship")
        authority_by_id = indexed(authority_items, "AuthorityAttestation")
        authority_links_by_id = indexed(authority_link_items, "BeliefAuthority")
        attested_memory_ids = {
            item.get("belief_id")
            for item in [*authority_items, *authority_link_items]
            if item.get("belief_id")
        }

        portable_evidence_ids = {
            evidence_id
            for item in memory_items
            if not item.get("repo_id")
            for evidence_id in (item.get("evidence_ids") or [])
        }
        for evidence_id in portable_evidence_ids:
            if evidence_id in evidence_by_id:
                evidence_by_id[evidence_id]["repo_id"] = default_repo_id

        for evidence_id, item in evidence_by_id.items():
            content = item.get("content")
            repo_id = item.get("repo_id") or default_repo_id
            if not isinstance(content, str) or not content or not repo_id:
                raise ValueError(f"Imported Evidence {evidence_id!r} is malformed")
            self._require_secret_free_evidence(
                content, context="stable imported Evidence"
            )
            supplied_hash = item.get("content_hash")
            if not legacy_format2 and not supplied_hash:
                raise ValueError(
                    f"Imported Evidence {evidence_id!r} requires an exact content hash"
                )
            if supplied_hash and supplied_hash != self._evidence_hash(content):
                raise ValueError(f"Imported Evidence {evidence_id!r} has an invalid hash")
            item["repo_id"] = repo_id
            item["content_hash"] = supplied_hash or self._evidence_hash(content)

        for memory_id, item in memories_by_id.items():
            content = item.get("content")
            layer = item.get("layer")
            repo_id = item.get("repo_id") or default_repo_id
            if not isinstance(content, str) or layer not in {
                "raw",
                "episodic",
                "semantic",
                "intent",
            }:
                raise ValueError(f"Imported memory {memory_id!r} is malformed")
            original_content = content
            try:
                content, imported_flags = redact_for_storage(
                    content,
                    item.get("quality_flags") or [],
                    reject_if_redacted=(
                        memory_id in attested_memory_ids
                        or item.get("authority_attestation") is not None
                        or item.get("belief_type") == "prohibition"
                    ),
                )
            except SecretBearingContentError as exc:
                from visp_memory.core.authority import ProhibitionAuthorityError

                raise ProhibitionAuthorityError(str(exc)) from exc
            if legacy_format2 and content != original_content:
                raise SecretBearingContentError(
                    "Legacy format-2 memory "
                    f"{memory_id!r} contains secret-bearing content; import refused "
                    "without rewriting the historical record"
                )
            item["content"] = content
            item["quality_flags"] = imported_flags or []
            item["repo_id"] = repo_id
            belief_type = item.get("belief_type")
            epistemic_status = item.get("epistemic_status")
            if layer == "semantic":
                try:
                    belief_type = normalize_belief_type(belief_type)
                    epistemic_status = normalize_epistemic_status(epistemic_status)
                except ValueError as exc:
                    raise ValueError(
                        f"Imported semantic memory {memory_id!r} has invalid governed fields"
                    ) from exc
                if item.get("category") != belief_type:
                    raise ValueError(
                        f"Imported semantic memory {memory_id!r} category/type mismatch"
                    )
                if belief_type == "hypothesis":
                    if epistemic_status != "hypothesized":
                        raise ValueError("Imported hypothesis must be hypothesized")
                    created_at = parse_utc(item.get("created_at"))
                    valid_to = parse_utc((item.get("metadata") or {}).get("valid_to"))
                    if (
                        created_at is None
                        or valid_to is None
                        or valid_to <= created_at
                        or valid_to
                        > created_at + timedelta(days=HYPOTHESIS_TTL_DAYS)
                    ):
                        raise ValueError(
                            f"Imported hypothesis {memory_id!r} has invalid TTL"
                        )
                if belief_type == "prohibition" and epistemic_status != "observed":
                    raise ValueError("Imported prohibition must be observed")
                item["belief_type"] = belief_type
                item["epistemic_status"] = epistemic_status
            elif belief_type is not None or epistemic_status is not None:
                raise ValueError(
                    f"Imported non-semantic memory {memory_id!r} has governed fields"
                )
            for evidence_id in item.get("evidence_ids") or []:
                evidence = evidence_by_id.get(evidence_id)
                if evidence is None:
                    raise ValueError(
                        f"Imported memory {memory_id!r} references missing Evidence "
                        f"{evidence_id!r}"
                    )
                if evidence["repo_id"] != repo_id:
                    raise ValueError("Imported Evidence references cannot cross repositories")

        resolved_cache: Dict[str, List[str]] = {}

        def resolve_evidence(memory_id: str, stack: set[str] | None = None) -> List[str]:
            if memory_id in resolved_cache:
                return resolved_cache[memory_id]
            stack = set(stack or ())
            if memory_id in stack:
                raise ValueError("Imported memory lineage contains a cycle")
            stack.add(memory_id)
            item = memories_by_id[memory_id]
            resolved = list(dict.fromkeys(item.get("evidence_ids") or []))
            for source_id in dict.fromkeys(item.get("source_ids") or []):
                source = memories_by_id.get(source_id)
                if source is None:
                    raise ValueError(
                        f"Imported memory {memory_id!r} references missing lineage "
                        f"source {source_id!r}"
                    )
                if source["repo_id"] != item["repo_id"]:
                    raise ValueError("Imported lineage cannot cross repositories")
                if source.get("status") == "deleted":
                    raise ValueError("Imported lineage cannot reference a deleted memory")
                resolved.extend(resolve_evidence(source_id, stack))
            resolved = list(dict.fromkeys(resolved))
            if item["layer"] == "semantic" and not resolved:
                raise ValueError(
                    f"Imported semantic memory {memory_id!r} has no retrievable Evidence"
                )
            resolved_cache[memory_id] = resolved
            return resolved

        for memory_id in memories_by_id:
            resolve_evidence(memory_id)

        authority_link_by_belief: Dict[str, Dict[str, Any]] = {}
        linked_attestations: set[str] = set()
        for link_id, link in authority_links_by_id.items():
            belief_id = link.get("belief_id")
            attestation_id = link.get("attestation_id")
            if (
                belief_id not in memories_by_id
                or attestation_id not in authority_by_id
                or belief_id in authority_link_by_belief
                or attestation_id in linked_attestations
            ):
                raise ValueError(
                    f"Imported BeliefAuthority {link_id!r} is dangling or duplicated"
                )
            authority_link_by_belief[belief_id] = link
            linked_attestations.add(attestation_id)

        seen_nonces: Dict[tuple[str, str], str] = {}
        for attestation_id, attestation in authority_by_id.items():
            belief_id = attestation.get("belief_id")
            belief = memories_by_id.get(belief_id)
            if (
                belief is None
                or belief.get("belief_type") != "prohibition"
                or attestation_id not in linked_attestations
            ):
                raise ValueError(
                    f"Imported AuthorityAttestation {attestation_id!r} is unlinked"
                )
            evidence_claim = [
                {
                    "id": evidence_id,
                    "content_hash": evidence_by_id[evidence_id]["content_hash"],
                }
                for evidence_id in sorted(resolve_evidence(belief_id))
            ]
            verified = verify_prohibition_attestation(
                attestation.get("envelope"),
                content=belief["content"],
                repo_id=belief["repo_id"],
                metadata=belief.get("metadata") or {},
                evidence=evidence_claim,
            )
            if (
                attestation_id != f"att-{verified.digest}"
                or attestation.get("digest") != verified.digest
                or attestation.get("key_id") != verified.key_id
                or attestation.get("nonce") != verified.nonce
            ):
                raise ProhibitionAuthorityError(
                    f"Imported AuthorityAttestation {attestation_id!r} fields mismatch"
                )
            link = authority_link_by_belief[belief_id]
            if (
                link.get("id") != f"ba-{belief_id}-{attestation_id}"
                or link.get("created_at") != attestation.get("created_at")
            ):
                raise ValueError(
                    f"Imported BeliefAuthority for {belief_id!r} has mismatched history"
                )
            nonce_key = (verified.key_id, verified.nonce)
            if nonce_key in seen_nonces:
                raise ProhibitionAuthorityError(
                    "Imported prohibition authority nonce is duplicated"
                )
            seen_nonces[nonce_key] = verified.digest

        for memory_id, item in memories_by_id.items():
            has_authority = memory_id in authority_link_by_belief
            if (item.get("belief_type") == "prohibition") != has_authority:
                raise ProhibitionAuthorityError(
                    f"Imported prohibition authority graph is incomplete for {memory_id!r}"
                )

        for relationship_id, item in relationships_by_id.items():
            source = memories_by_id.get(item.get("source_id"))
            target = memories_by_id.get(item.get("target_id"))
            if source is None or target is None:
                raise ValueError(
                    f"Imported relationship {relationship_id!r} has a dangling endpoint"
                )
            if source["repo_id"] != target["repo_id"]:
                raise ValueError("Imported relationships cannot cross repositories")

        vector_payloads: Dict[str, Dict[str, Any]] = {}
        vector_collections: Dict[str, Any] = {}
        if self._embedding_fn is not None and not self._uses_noop_embeddings:
            for item in memory_items:
                try:
                    embedding = self._embedding_fn(item["content"])
                except Exception as exc:
                    raise RuntimeError(
                        f"Could not compute vector for imported memory {item['id']!r}"
                    ) from exc
                layer = item["layer"]
                collection = vector_collections.get(layer)
                if collection is None:
                    collection = self._get_collection(layer)
                    if collection is None:
                        raise RuntimeError(
                            f"Vector collection for imported {layer} memories is unavailable"
                        )
                    vector_collections[layer] = collection
                payload = vector_payloads.setdefault(
                    layer,
                    {"ids": [], "documents": [], "metadatas": [], "embeddings": []},
                )
                payload["ids"].append(item["id"])
                payload["documents"].append(item["content"])
                payload["metadatas"].append(
                    {
                        "category": item.get("category") or "general",
                        "importance": item.get("importance", 0.5),
                        "tags": self._json_serialize(
                            item.get("tags") or []
                        ),
                        "repo_id": item["repo_id"],
                        "status": item.get("status") or "active",
                    }
                )
                payload["embeddings"].append(embedding)

        vector_write_payloads: Dict[str, Dict[str, Any]] = {}
        with self._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for layer, payload in vector_payloads.items():
                    placeholders = ", ".join("?" for _ in payload["ids"])
                    relational_ids = {
                        row["id"]
                        for row in conn.execute(
                            f"SELECT id FROM memories WHERE id IN ({placeholders})",
                            payload["ids"],
                        )
                    }
                    collection = vector_collections[layer]
                    vector_result = collection.get(ids=payload["ids"], include=[])
                    vector_ids = set((vector_result or {}).get("ids") or [])
                    orphan_collisions = vector_ids - relational_ids
                    if orphan_collisions:
                        collision = sorted(orphan_collisions)[0]
                        raise ValueError(
                            f"Imported memory {collision!r} collides with a pre-existing vector"
                        )
                    write_indexes = [
                        index
                        for index, memory_id in enumerate(payload["ids"])
                        if memory_id not in vector_ids
                    ]
                    if write_indexes:
                        vector_write_payloads[layer] = {
                            key: [values[index] for index in write_indexes]
                            for key, values in payload.items()
                        }

                for evidence_id, item in evidence_by_id.items():
                    self._insert_evidence(
                        conn,
                        content=item["content"],
                        repo_id=item["repo_id"],
                        evidence_type=item.get("evidence_type") or "observation",
                        provenance=item.get("provenance") or "unknown",
                        metadata=item.get("metadata") or {},
                        evidence_id=evidence_id,
                        created_at=item.get("created_at") or utc_now().isoformat(),
                        compare_created_at=item.get("created_at") is not None,
                    )

                for memory_id, item in memories_by_id.items():
                    tags = item.get("tags") or []
                    metadata = item.get("metadata") or {}
                    existing = conn.execute(
                        "SELECT * FROM memories WHERE id = ?", (memory_id,)
                    ).fetchone()
                    created_at = (
                        item.get("created_at")
                        or (existing["created_at"] if existing is not None else None)
                        or utc_now().isoformat()
                    )
                    values = {
                        "id": memory_id,
                        "content": item["content"],
                        "layer": item["layer"],
                        "repo_id": item["repo_id"],
                        "category": item.get("category") or "general",
                        "belief_type": item.get("belief_type"),
                        "epistemic_status": item.get("epistemic_status"),
                        "importance": item.get("importance", 0.5),
                        "tags": self._json_serialize(tags),
                        "metadata": self._json_serialize(metadata),
                        "source_ids": self._json_serialize(item.get("source_ids") or []),
                        "status": item.get("status") or "active",
                        # Preserve an explicit null rather than coercing it, and
                        # default only when the field is genuinely absent. `or`
                        # could not tell those apart, so importing a record whose
                        # source was null rewrote it to "unknown" and no export /
                        # import / re-export cycle could compare equal. Both
                        # values assess to the same provenance tier, so this
                        # changes fidelity without changing trust.
                        "source": item["source"] if "source" in item else "unknown",
                        "quality_flags": self._json_serialize(
                            item.get("quality_flags") or []
                        ),
                        "created_at": created_at,
                        "accessed_at": item.get("accessed_at")
                        or item.get("created_at")
                        or (existing["accessed_at"] if existing is not None else None)
                        or utc_now().isoformat(),
                        "access_count": int(item.get("access_count") or 0),
                        "approved_by": item.get("approved_by"),
                        "approved_at": item.get("approved_at"),
                        "archived_at": item.get("archived_at"),
                        "compressed_at": item.get("compressed_at"),
                        "last_quality_checked_at": item.get(
                            "last_quality_checked_at"
                        ),
                    }
                    if existing is not None:
                        identity_and_lifecycle = {
                            key: value
                            for key, value in values.items()
                            if key not in {"accessed_at", "access_count"}
                        }
                        if any(
                            existing[key] != value
                            for key, value in identity_and_lifecycle.items()
                        ):
                            raise ValueError(
                                f"Imported memory ID collision for {memory_id!r}"
                            )
                    else:
                        columns = list(values)
                        conn.execute(
                            f"INSERT INTO memories ({', '.join(columns)}) VALUES "
                            f"({', '.join('?' for _ in columns)})",
                            [values[column] for column in columns],
                        )
                    link_created_at = item.get("created_at") or utc_now().isoformat()
                    for evidence_id in resolve_evidence(memory_id):
                        conn.execute(
                            "INSERT OR IGNORE INTO belief_evidence "
                            "(belief_id, evidence_id, created_at) VALUES (?, ?, ?)",
                            (memory_id, evidence_id, link_created_at),
                        )

                for attestation_id, item in authority_by_id.items():
                    values = (
                        item["digest"],
                        item["belief_id"],
                        item["key_id"],
                        item["nonce"],
                        item["envelope"],
                        item["created_at"],
                    )
                    existing = conn.execute(
                        "SELECT digest, belief_id, key_id, nonce, envelope, created_at "
                        "FROM authority_attestations WHERE digest = ? OR belief_id = ? "
                        "OR (key_id = ? AND nonce = ?)",
                        (
                            item["digest"],
                            item["belief_id"],
                            item["key_id"],
                            item["nonce"],
                        ),
                    ).fetchone()
                    if existing is not None and tuple(existing) != values:
                        raise ProhibitionAuthorityError(
                            f"Imported AuthorityAttestation collision for {attestation_id!r}"
                        )
                    if existing is None:
                        conn.execute(
                            "INSERT INTO authority_attestations "
                            "(digest, belief_id, key_id, nonce, envelope, created_at) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            values,
                        )

                for intent_id, item in intents_by_id.items():
                    repo_id = item.get("repo_id") or default_repo_id
                    values = (
                        intent_id,
                        item["description"],
                        int(item.get("priority") or 0),
                        item.get("status") or "active",
                        self._json_serialize(item.get("context") or {}),
                        item.get("created_at") or utc_now().isoformat(),
                        item.get("updated_at") or item.get("created_at") or utc_now().isoformat(),
                        repo_id,
                    )
                    existing = conn.execute(
                        "SELECT id, description, priority, status, context, created_at, "
                        "updated_at, repo_id FROM intents WHERE id = ?", (intent_id,)
                    ).fetchone()
                    if existing is not None and tuple(existing) != values:
                        raise ValueError(f"Imported intent ID collision for {intent_id!r}")
                    if existing is None:
                        conn.execute(
                            "INSERT INTO intents (id, description, priority, status, context, "
                            "created_at, updated_at, repo_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            values,
                        )

                for relationship_id, item in relationships_by_id.items():
                    evidence = self._normalize_relationship_evidence(
                        item.get("evidence"),
                        strength=item.get("strength"),
                        created_at=item.get("created_at"),
                        legacy=False,
                    )
                    values = (
                        relationship_id,
                        item["source_id"],
                        item["target_id"],
                        item["relationship"],
                        float(item.get("strength", 1.0)),
                        evidence["confidence"],
                        evidence["confidence_score"],
                        evidence["source"],
                        evidence["source_file"],
                        evidence["source_location"],
                        evidence["reason"],
                        evidence["created_by"],
                        evidence["created_at"] or utc_now().isoformat(),
                    )
                    existing = conn.execute(
                        "SELECT id, source_id, target_id, relationship, strength, confidence, "
                        "confidence_score, source, source_file, source_location, reason, "
                        "created_by, created_at FROM relationships WHERE id = ?",
                        (relationship_id,),
                    ).fetchone()
                    if existing is not None and tuple(existing) != values:
                        raise ValueError(
                            f"Imported relationship ID collision for {relationship_id!r}"
                        )
                    if existing is None:
                        conn.execute(
                            "INSERT INTO relationships (id, source_id, target_id, relationship, "
                            "strength, confidence, confidence_score, source, source_file, "
                            "source_location, reason, created_by, created_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            values,
                        )
                for layer, payload in vector_write_payloads.items():
                    vector_collections[layer].upsert(**payload)
                conn.commit()
            except Exception as exc:
                conn.rollback()
                residual_ids: List[str] = []
                for layer, payload in vector_write_payloads.items():
                    try:
                        vector_collections[layer].delete(ids=payload["ids"])
                    except Exception:
                        logger.error(
                            "Vector compensation failed for imported %s memory IDs",
                            len(payload["ids"]),
                        )
                        residual_ids.extend(payload["ids"])
                if residual_ids:
                    raise GraphImportRollbackIncompleteError(residual_ids) from exc
                raise
        return {
            "status": "completed",
            "memories": len(memories_by_id),
            "evidence": len(evidence_by_id),
            "vectors": "disabled" if not vector_payloads else "reconciled",
        }

    @classmethod
    def migrate_schema(cls, data_dir: Path, *, backup_path: Path) -> Dict[str, Any]:
        """Explicitly migrate a backed-up SQLite v2/v3/v4 store to schema v5."""
        data_dir = Path(data_dir)
        db_path = data_dir / "memories.db"
        backup_path = Path(backup_path)
        if not db_path.is_file():
            raise FileNotFoundError(f"Storage database does not exist: {db_path}")
        if backup_path.resolve() == db_path.resolve():
            raise ValueError("backup_path must differ from the live storage database")

        with sqlite3.connect(db_path) as probe:
            row = probe.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
            stored_version = int((row or [0])[0] or 0)
        if stored_version > STORAGE_SCHEMA_VERSION:
            raise RuntimeError(
                "Storage schema is newer than this visp-memory build "
                f"({stored_version} > {STORAGE_SCHEMA_VERSION})"
            )
        if stored_version == STORAGE_SCHEMA_VERSION:
            return {
                "from_version": stored_version,
                "to_version": STORAGE_SCHEMA_VERSION,
                "status": "already_current",
            }
        if stored_version not in {2, 3, 4}:
            raise StorageMigrationRequired(
                f"Only schema versions 2, 3 and 4 can be migrated to "
                f"{STORAGE_SCHEMA_VERSION}; "
                f"found {stored_version}"
            )

        with sqlite3.connect(db_path) as validation_conn:
            validation_conn.row_factory = sqlite3.Row
            if stored_version == 2:
                cls._prevalidate_legacy_evidence_content(validation_conn)
            elif stored_version == 3:
                cls._prevalidate_v3_graph(validation_conn)
            else:
                cls._validate_v4_schema(validation_conn)

        if backup_path.exists():
            raise FileExistsError(f"Migration backup already exists: {backup_path}")
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path) as source_conn, sqlite3.connect(
            backup_path
        ) as backup_conn:
            source_conn.backup(backup_conn)
        conn = sqlite3.connect(db_path, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            if stored_version == 2:
                cls._migrate_v2_to_v3(conn)
                conn.execute("INSERT INTO schema_migrations(version) VALUES (3)")
            missing = conn.execute(
                """
                SELECT COUNT(*) FROM memories m
                WHERE m.layer = 'semantic'
                AND NOT EXISTS (
                    SELECT 1 FROM belief_evidence be WHERE be.belief_id = m.id
                )
                """
            ).fetchone()[0]
            if missing:
                raise RuntimeError(
                    f"Evidence migration integrity check found {missing} unlinked beliefs"
                )
            invalid_hashes = sum(
                hashlib.sha256(row["content"].encode("utf-8")).hexdigest()
                != row["content_hash"]
                for row in conn.execute("SELECT content, content_hash FROM evidence")
            )
            if invalid_hashes:
                raise RuntimeError(
                    f"Evidence migration integrity check found {invalid_hashes} invalid hashes"
                )
            if stored_version < 4:
                cls._migrate_v3_to_v4(conn)
                conn.execute("INSERT INTO schema_migrations(version) VALUES (4)")
            cls._migrate_v4_to_v5(conn)
            conn.execute("INSERT INTO schema_migrations(version) VALUES (5)")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return {
            "from_version": stored_version,
            "to_version": STORAGE_SCHEMA_VERSION,
            "status": "migrated",
        }

    @staticmethod
    def _prevalidate_legacy_evidence_content(conn: sqlite3.Connection) -> None:
        for row in conn.execute("SELECT id, content FROM memories ORDER BY id"):
            content = str(row["content"])
            if LocalStorage._redact_evidence_content(content) != content:
                raise EvidenceError(
                    "Refusing schema-v3 migration: secret-bearing legacy content "
                    f"in memory {row['id']!r} cannot become stable Evidence"
                )

    @staticmethod
    def _prevalidate_v3_graph(conn: sqlite3.Connection) -> None:
        """Validate the P11-07 graph before the migration backup is written."""
        required = {
            "evidence": {
                "id",
                "content",
                "content_hash",
                "repo_id",
                "evidence_type",
                "provenance",
                "metadata",
                "created_at",
            },
            "belief_evidence": {"belief_id", "evidence_id", "created_at"},
        }
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        for table, columns in required.items():
            if table not in tables:
                raise StorageMigrationRequired(
                    f"Storage schema 3 is malformed: missing {table} table"
                )
            actual = {
                row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
            }
            missing = columns - actual
            if missing:
                raise StorageMigrationRequired(
                    "Storage schema 3 is malformed: "
                    f"{table} is missing columns {', '.join(sorted(missing))}"
                )
        for row in conn.execute("SELECT id, content, content_hash FROM evidence"):
            if LocalStorage._evidence_hash(row["content"]) != row["content_hash"]:
                raise StorageMigrationRequired(
                    f"Storage schema 3 is malformed: Evidence {row['id']!r} "
                    "has an invalid content hash"
                )
            LocalStorage._require_secret_free_evidence(
                row["content"], context="migrating schema-v3 Evidence"
            )

    @staticmethod
    def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
        LocalStorage._prevalidate_legacy_evidence_content(conn)
        conn.execute(
            "UPDATE memories SET repo_id = ? "
            "WHERE repo_id IS NULL OR TRIM(repo_id) = ''",
            (UNSCOPED_REPO_ID,),
        )
        intent_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'intents'"
        ).fetchone()
        if intent_table is not None:
            intent_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(intents)")
            }
            if "repo_id" in intent_columns:
                conn.execute(
                    "UPDATE intents SET repo_id = ? "
                    "WHERE repo_id IS NULL OR TRIM(repo_id) = ''",
                    (UNSCOPED_REPO_ID,),
                )
        conn.execute(
            """
            CREATE TABLE evidence (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                repo_id TEXT DEFAULT NULL,
                evidence_type TEXT NOT NULL,
                provenance TEXT NOT NULL,
                metadata TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE belief_evidence (
                belief_id TEXT NOT NULL,
                evidence_id TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (belief_id, evidence_id),
                FOREIGN KEY (belief_id) REFERENCES memories(id) ON DELETE CASCADE,
                FOREIGN KEY (evidence_id) REFERENCES evidence(id) ON DELETE RESTRICT
            )
            """
        )
        rows = conn.execute("SELECT * FROM memories ORDER BY id").fetchall()
        for row in rows:
            content = str(row["content"])
            evidence_id = "ev-legacy-" + hashlib.sha256(
                str(row["id"]).encode("utf-8")
            ).hexdigest()[:24]
            created_at = row["created_at"] or utc_now().isoformat()
            conn.execute(
                """
                INSERT INTO evidence (
                    id, content, content_hash, repo_id, evidence_type,
                    provenance, metadata, created_at
                ) VALUES (?, ?, ?, ?, 'legacy_import', 'unknown', ?, ?)
                """,
                (
                    evidence_id,
                    content,
                    hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    row["repo_id"],
                    json.dumps({"legacy_memory_id": row["id"], "schema_version": 2}),
                    created_at,
                ),
            )
            conn.execute(
                "INSERT INTO belief_evidence (belief_id, evidence_id, created_at) "
                "VALUES (?, ?, ?)",
                (row["id"], evidence_id, created_at),
            )
            tags = LocalStorage._json_deserialize(row["tags"] or "[]") or []
            tags = [tag for tag in tags if not str(tag).startswith("provenance:")]
            tags.append("provenance:unknown")
            quality_flags = LocalStorage._json_deserialize(
                row["quality_flags"] or "[]"
            ) or []
            if "legacy_unreviewed" not in quality_flags:
                quality_flags.append("legacy_unreviewed")
            metadata = LocalStorage._json_deserialize(row["metadata"] or "{}") or {}
            metadata = {
                **metadata,
                "legacy_evidence_migration": True,
                "legacy_evidence_id": evidence_id,
            }
            conn.execute(
                """
                UPDATE memories
                SET tags = ?, metadata = ?, source = 'unknown', quality_flags = ?
                WHERE id = ?
                """,
                (
                    json.dumps(tags),
                    json.dumps(metadata),
                    json.dumps(quality_flags),
                    row["id"],
                ),
            )
        conn.execute("CREATE INDEX idx_evidence_repo ON evidence(repo_id)")
        conn.execute(
            "CREATE INDEX idx_belief_evidence_evidence ON belief_evidence(evidence_id)"
        )

    @staticmethod
    def _migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
        """Add governed semantic fields and conservatively classify legacy rows."""
        conn.execute("ALTER TABLE memories ADD COLUMN belief_type TEXT DEFAULT NULL")
        conn.execute(
            "ALTER TABLE memories ADD COLUMN epistemic_status TEXT DEFAULT NULL"
        )
        conn.execute(
            """
            CREATE TABLE authority_attestations (
                digest TEXT PRIMARY KEY,
                belief_id TEXT NOT NULL UNIQUE,
                key_id TEXT NOT NULL,
                nonce TEXT NOT NULL,
                envelope TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (key_id, nonce),
                FOREIGN KEY (belief_id) REFERENCES memories(id) ON DELETE CASCADE
            )
            """
        )
        LocalStorage._ensure_supporting_v4_schema(conn)
        rows = conn.execute(
            "SELECT id, category, status, tags, metadata, quality_flags "
            "FROM memories WHERE layer = 'semantic' ORDER BY id"
        ).fetchall()
        from visp_memory.core.trust import Provenance, with_provenance

        for row in rows:
            belief_type, epistemic_status = migrate_legacy_belief_fields(
                row["category"], row["status"]
            )
            metadata = LocalStorage._json_deserialize(row["metadata"] or "{}") or {}
            metadata = {**metadata, "legacy_category": row["category"]}
            tags = with_provenance(
                LocalStorage._json_deserialize(row["tags"] or "[]") or [],
                Provenance.UNKNOWN,
            )
            quality_flags = LocalStorage._json_deserialize(
                row["quality_flags"] or "[]"
            ) or []
            if "legacy_unreviewed" not in quality_flags:
                quality_flags.append("legacy_unreviewed")
            conn.execute(
                """
                UPDATE memories
                SET category = ?, belief_type = ?, epistemic_status = ?,
                    metadata = ?, tags = ?, source = 'unknown', quality_flags = ?
                WHERE id = ?
                """,
                (
                    belief_type,
                    belief_type,
                    epistemic_status,
                    json.dumps(metadata),
                    json.dumps(tags),
                    json.dumps(quality_flags),
                    row["id"],
                ),
            )

    @staticmethod
    def _migrate_v4_to_v5(conn: sqlite3.Connection) -> None:
        """Bind new sessions to a principal and repository; legacy rows stay unbound."""
        conn.execute("ALTER TABLE sessions ADD COLUMN owner_id TEXT DEFAULT NULL")
        conn.execute("ALTER TABLE sessions ADD COLUMN team_id TEXT DEFAULT NULL")
        conn.execute("ALTER TABLE sessions ADD COLUMN repo_id TEXT DEFAULT NULL")
        conn.execute("CREATE INDEX idx_sessions_owner ON sessions(owner_id)")
        conn.execute("CREATE INDEX idx_sessions_repo ON sessions(repo_id)")

    @staticmethod
    def _ensure_supporting_v4_schema(conn: sqlite3.Connection) -> None:
        """Make an older complete store structurally current inside migration."""
        statements = (
            """
            CREATE TABLE IF NOT EXISTS intents (
                id TEXT PRIMARY KEY, description TEXT NOT NULL, priority INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active', context TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, repo_id TEXT DEFAULT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS relationships (
                id TEXT PRIMARY KEY, source_id TEXT NOT NULL, target_id TEXT NOT NULL,
                relationship TEXT NOT NULL, strength REAL DEFAULT 1.0,
                confidence TEXT DEFAULT 'ambiguous', confidence_score REAL DEFAULT NULL,
                source TEXT DEFAULT 'legacy', source_file TEXT DEFAULT NULL,
                source_location TEXT DEFAULT NULL,
                reason TEXT DEFAULT 'Legacy relationship without evidence metadata.',
                created_by TEXT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (source_id) REFERENCES memories(id),
                FOREIGN KEY (target_id) REFERENCES memories(id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, summary TEXT, memory_ids TEXT DEFAULT '[]',
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ended_at TIMESTAMP DEFAULT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS repositories (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT, description TEXT,
                tech_stack TEXT DEFAULT '[]', team_id TEXT, metadata TEXT DEFAULT '{}',
                status TEXT DEFAULT 'active', archived_at TIMESTAMP DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, email TEXT,
                display_name TEXT, metadata TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS teams (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT,
                metadata TEXT DEFAULT '{}', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS team_members (
                team_id TEXT NOT NULL, user_id TEXT NOT NULL, role TEXT DEFAULT 'member',
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (team_id, user_id),
                FOREIGN KEY (team_id) REFERENCES teams(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS repository_dependencies (
                id TEXT PRIMARY KEY, source_repo_id TEXT NOT NULL,
                target_repo_id TEXT NOT NULL, dependency_type TEXT NOT NULL,
                version TEXT, notes TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (source_repo_id) REFERENCES repositories(id),
                FOREIGN KEY (target_repo_id) REFERENCES repositories(id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id TEXT PRIMARY KEY, event_type TEXT NOT NULL, actor_id TEXT, repo_id TEXT,
                target_type TEXT, target_id TEXT, metadata TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS recall_events (
                id TEXT PRIMARY KEY, memory_id TEXT NOT NULL, event_type TEXT NOT NULL,
                repo_id TEXT DEFAULT NULL, query_hash TEXT DEFAULT NULL,
                task_id TEXT DEFAULT NULL, outcome TEXT DEFAULT NULL,
                metadata TEXT DEFAULT '{}', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (memory_id) REFERENCES memories(id)
            )
            """,
        )
        for statement in statements:
            conn.execute(statement)

        additive_columns = {
            "intents": {"repo_id": "TEXT DEFAULT NULL"},
            "relationships": {
                "confidence": "TEXT DEFAULT 'ambiguous'",
                "confidence_score": "REAL DEFAULT NULL",
                "source": "TEXT DEFAULT 'legacy'",
                "source_file": "TEXT DEFAULT NULL",
                "source_location": "TEXT DEFAULT NULL",
                "reason": "TEXT DEFAULT 'Legacy relationship without evidence metadata.'",
                "created_by": "TEXT DEFAULT NULL",
            },
            "repositories": {
                "status": "TEXT DEFAULT 'active'",
                "archived_at": "TIMESTAMP DEFAULT NULL",
            },
        }
        for table, columns in additive_columns.items():
            existing = {
                row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
            }
            for column, declaration in columns.items():
                if column not in existing:
                    conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"
                    )

    def get_capabilities(self) -> StorageCapabilities:
        return StorageCapabilities(
            vector_search=bool(
                CHROMADB_AVAILABLE
                and self._embedding_fn is not None
                and not self._uses_noop_embeddings
            ),
            audit_log=True,
            reindex=True,
            complete_graph_export=True,
            atomic_graph_import=True,
        )

    def get_schema_status(self) -> Dict[str, Any]:
        with self._get_db() as conn:
            row = conn.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
        stored_version = int(row["version"] or 0)
        return {
            "current_version": STORAGE_SCHEMA_VERSION,
            "stored_version": stored_version,
            "status": "ready" if stored_version == STORAGE_SCHEMA_VERSION else "migration_required",
        }

    @contextmanager
    def _get_db(self):
        """Get SQLite connection context.

        Each connection is configured for safe concurrent multi-client access
        (server / MCP): WAL journaling so readers do not block writers, a busy
        timeout so competing writers wait instead of raising "database is
        locked", and foreign-key enforcement so referential integrity is
        actually honored.
        """
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            # busy_timeout and foreign_keys are per-connection settings; WAL is a
            # persistent database-level setting applied once in _init_sqlite.
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
        finally:
            conn.close()

    def close(self) -> None:
        """Release the ChromaDB client, if one was created. Idempotent.

        SQLite connections are opened and closed per operation (see ``_get_db``),
        so there is no persistent database handle to release here.
        """
        # ChromaDB's PersistentClient exposes no explicit close; drop references so
        # the underlying system/DB handles can be released by garbage collection.
        self._collections = {}
        self._chroma_client = None

    def _get_chroma(self):
        """Get or create ChromaDB client."""
        if not CHROMADB_AVAILABLE or self._embedding_fn is None or self._uses_noop_embeddings:
            return None

        if self._chroma_client is None:
            self.chroma_path.mkdir(parents=True, exist_ok=True)
            self._chroma_client = chromadb.PersistentClient(
                path=str(self.chroma_path),
                settings=Settings(anonymized_telemetry=False, allow_reset=True),
            )

        return self._chroma_client

    def get_collection(self, layer: str):
        """Public accessor for vector collection."""
        return self._get_collection(layer)

    def _get_collection(self, layer: MemoryLayer):
        """Get or create ChromaDB collection for a layer."""
        client = self._get_chroma()
        if client is None:
            return None

        if layer not in self._collections:
            collection_name = self._collection_name(layer)
            try:
                self._collections[layer] = client.get_collection(collection_name)
            except Exception:
                # Create collection with cosine distance for proper similarity scores
                # ChromaDB uses "hnsw:space" parameter - "cosine", "l2", or "ip" (inner product)
                self._collections[layer] = client.create_collection(
                    name=collection_name,
                    metadata={
                        "description": f"Memory embeddings for {layer} layer",
                        "hnsw:space": "cosine",
                        "embedding_dimension": self._embedding_dimension or 0,
                    },
                )
                self._backfill_collection(layer, self._collections[layer])

        return self._collections[layer]

    def _collection_name(self, layer: MemoryLayer) -> str:
        """Use dimension-specific collections so old noop vectors do not poison search."""
        if self._embedding_dimension:
            return f"memories_{layer}_{self._embedding_dimension}"
        return f"memories_{layer}"

    def _list_vector_collection_names(self) -> List[str]:
        """Return Chroma collection names without creating new collections."""
        client = self._get_chroma()
        if client is None:
            return []
        try:
            collections = client.list_collections()
        except Exception:
            return []
        names = []
        for collection in collections:
            name = getattr(collection, "name", None) or str(collection)
            if name:
                names.append(name)
        return names

    def _count_vector_collection(self, collection_name: str) -> Optional[int]:
        client = self._get_chroma()
        if client is None:
            return None
        try:
            return int(client.get_collection(collection_name).count())
        except Exception:
            return None

    def _memory_filters_from_scope(self, scope: ReindexScope) -> tuple[str, list[Any]]:
        clauses = ["1=1"]
        params: list[Any] = []
        if scope.layer:
            clauses.append("layer = ?")
            params.append(scope.layer)
        if scope.repo_id:
            clauses.append("repo_id = ?")
            params.append(scope.repo_id)
        if scope.category:
            clauses.append("category = ?")
            params.append(scope.category)
        return " AND ".join(clauses), params

    def _list_reindex_candidates(self, scope: ReindexScope) -> List[Dict[str, Any]]:
        where, params = self._memory_filters_from_scope(scope)
        query = f"SELECT * FROM memories WHERE {where} ORDER BY layer ASC, created_at ASC"
        with self._get_db() as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def _count_reindex_candidates(self, scope: ReindexScope) -> int:
        where, params = self._memory_filters_from_scope(scope)
        with self._get_db() as conn:
            cursor = conn.execute(f"SELECT COUNT(*) FROM memories WHERE {where}", params)
            return int(cursor.fetchone()[0])

    def _embedding_collection_summary(
        self, scope: ReindexScope
    ) -> tuple[list[str], list[str], Optional[int]]:
        layers = [scope.layer] if scope.layer else ["raw", "episodic", "semantic", "intent"]
        active = [self._collection_name(layer) for layer in layers]
        existing = set(self._list_vector_collection_names())
        legacy = []
        for name in sorted(existing):
            for layer in layers:
                if name == f"memories_{layer}" and name != self._collection_name(layer):
                    legacy.append(name)
                    break
                dimension_collection = re.fullmatch(rf"memories_{re.escape(layer)}_\d+", name)
                if dimension_collection and name != self._collection_name(layer):
                    legacy.append(name)
                    break

        indexed_total = 0
        indexed_known = False
        for name in active:
            count = self._count_vector_collection(name)
            if count is not None:
                indexed_total += count
                indexed_known = True

        return active, sorted(set(legacy)), indexed_total if indexed_known else None

    def inspect_embedding_index(
        self,
        *,
        storage_backend: str,
        provider: str,
        effective_provider: str = None,
        model: str = None,
        dimension: int = None,
        scope: ReindexScope = None,
    ) -> EmbeddingIndexReport:
        """Inspect whether the active embedding index needs maintenance."""
        scope = scope or ReindexScope()
        matched = self._count_reindex_candidates(scope)
        active, legacy, indexed = self._embedding_collection_summary(scope)

        if self._uses_noop_embeddings:
            status = "disabled"
            message = "Noop embeddings are active; vector index search is disabled."
        elif self._embedding_fn is None:
            status = "not_configured"
            message = "No embedding function is configured; text fallback is used."
        elif not CHROMADB_AVAILABLE:
            status = "not_configured"
            message = "ChromaDB is not installed, so vector indexes cannot be rebuilt."
        elif dimension or self._embedding_dimension:
            status = "available"
            message = "Embedding index is available for the active provider dimension."
        else:
            status = "unknown"
            message = "Embedding provider is active but its vector dimension is unknown."

        needs_reindex = bool(
            matched
            and status == "available"
            and (legacy or indexed is None or indexed < matched)
        )
        if legacy:
            message = (
                "Legacy embedding collections were found for a different dimension; "
                "run a dry-run and rebuild after provider changes."
            )
        elif indexed is not None and indexed < matched and status == "available":
            message = "The active embedding index has fewer vectors than matching memories."

        return EmbeddingIndexReport(
            storage_backend=storage_backend,
            provider=provider,
            effective_provider=effective_provider,
            model=model,
            dimension=dimension or self._embedding_dimension,
            status=status,
            message=message,
            scope=scope.as_filter_dict(),
            matched_memories=matched,
            indexed_memories=indexed,
            active_collections=active,
            legacy_collections=legacy,
            needs_reindex=needs_reindex,
        )

    def rebuild_embedding_index(
        self, *, scope: ReindexScope = None, dry_run: bool = True
    ) -> ReindexResult:
        """Rebuild active-dimension vector entries for matching memories."""
        scope = scope or ReindexScope()
        candidates = self._list_reindex_candidates(scope)
        active, legacy, _ = self._embedding_collection_summary(scope)

        if self._uses_noop_embeddings:
            return ReindexResult(
                dry_run=dry_run,
                status="disabled",
                message="Noop embeddings are active; there is no vector index to rebuild.",
                scope=scope.as_filter_dict(),
                matched_memories=len(candidates),
                dimension=self._embedding_dimension,
                active_collections=active,
                legacy_collections=legacy,
            )
        if self._embedding_fn is None or not CHROMADB_AVAILABLE:
            return ReindexResult(
                dry_run=dry_run,
                status="not_configured",
                message="A real embedding function and ChromaDB are required to rebuild vectors.",
                scope=scope.as_filter_dict(),
                matched_memories=len(candidates),
                dimension=self._embedding_dimension,
                active_collections=active,
                legacy_collections=legacy,
            )
        if dry_run:
            return ReindexResult(
                dry_run=True,
                status="ready",
                message="Dry run complete; no embeddings were changed.",
                scope=scope.as_filter_dict(),
                matched_memories=len(candidates),
                dimension=self._embedding_dimension,
                active_collections=active,
                legacy_collections=legacy,
            )

        grouped: dict[str, dict[str, list[Any]]] = {}
        errors: list[dict[str, str]] = []
        for memory in candidates:
            try:
                safe_content, _ = redact_for_storage(memory["content"], None)
                embedding = self._embedding_fn(safe_content)
            except Exception as exc:
                errors.append({"id": memory["id"], "error": exc.__class__.__name__})
                continue

            metadata = {
                "category": memory.get("category") or "general",
                "importance": memory.get("importance") or 0.5,
                "tags": self._json_serialize(memory.get("tags") or []),
                "status": memory.get("status") or "active",
            }
            if memory.get("repo_id"):
                metadata["repo_id"] = memory["repo_id"]

            layer = memory["layer"]
            layer_group = grouped.setdefault(
                layer, {"ids": [], "documents": [], "metadatas": [], "embeddings": []}
            )
            layer_group["ids"].append(memory["id"])
            layer_group["documents"].append(safe_content)
            layer_group["metadatas"].append(metadata)
            layer_group["embeddings"].append(embedding)

        reindexed = 0
        for layer, payload in grouped.items():
            collection = self._get_collection(layer)
            if collection is None:
                errors.extend(
                    {"id": memory_id, "error": "CollectionUnavailable"}
                    for memory_id in payload["ids"]
                )
                continue
            try:
                collection.upsert(**payload)
                reindexed += len(payload["ids"])
            except Exception as exc:
                errors.extend(
                    {"id": memory_id, "error": exc.__class__.__name__}
                    for memory_id in payload["ids"]
                )

        failed = len(errors)
        status = "completed" if failed == 0 else "partial_failure"
        message = (
            f"Rebuilt {reindexed} embedding vectors."
            if failed == 0
            else f"Rebuilt {reindexed} embedding vectors; {failed} failed."
        )
        return ReindexResult(
            dry_run=False,
            status=status,
            message=message,
            scope=scope.as_filter_dict(),
            matched_memories=len(candidates),
            reindexed_memories=reindexed,
            failed_memories=failed,
            dimension=self._embedding_dimension,
            active_collections=active,
            legacy_collections=legacy,
            errors=errors[:50],
        )

    def _backfill_collection(self, layer: MemoryLayer, collection) -> None:
        """Populate a newly-created vector collection from SQLite memory rows."""
        if self._embedding_fn is None:
            return

        memories = self.list_memories(layer=layer, limit=10000, order_by="created_at ASC")
        if not memories:
            return

        ids = []
        documents = []
        metadatas = []
        embeddings = []

        for memory in memories:
            try:
                safe_content, _ = redact_for_storage(memory["content"], None)
                embedding = self._embedding_fn(safe_content)
            except Exception:
                continue

            metadata = {
                "category": memory.get("category") or "general",
                "importance": memory.get("importance") or 0.5,
                "tags": self._json_serialize(memory.get("tags") or []),
                "status": memory.get("status") or "active",
            }
            if memory.get("repo_id"):
                metadata["repo_id"] = memory["repo_id"]

            ids.append(memory["id"])
            documents.append(safe_content)
            metadatas.append(metadata)
            embeddings.append(embedding)

        if ids:
            collection.upsert(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
                embeddings=embeddings,
            )

    @staticmethod
    def _generate_id(content: str) -> str:
        """Generate a unique ID.

        Includes a random nonce in addition to the timestamp so that rapid
        successive calls with identical content (which can share a microsecond
        timestamp, especially under WAL's faster writes) do not collide on the
        truncated hash. IDs are not content-addressed, so the extra entropy is
        behavior-preserving.
        """
        timestamp = utc_now().isoformat()
        nonce = uuid.uuid4().hex
        return hashlib.sha256(f"{content}{timestamp}{nonce}".encode()).hexdigest()[:16]

    @staticmethod
    def _json_serialize(data: Any) -> str:
        """Serialize data to JSON."""
        return json.dumps(data)

    @staticmethod
    def _json_deserialize(data: str) -> Any:
        """Deserialize data from JSON."""
        if not data:
            return None
        try:
            return json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return data

    @staticmethod
    def _evidence_hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def _redact_evidence_content(content: str) -> str:
        redacted, _quality_flags = redact_for_storage(content, None)
        return redacted

    @classmethod
    def _require_secret_free_evidence(cls, content: str, *, context: str) -> None:
        if cls._redact_evidence_content(content) != content:
            raise EvidenceError(
                f"Refusing {context}: secret-bearing Evidence content cannot be "
                "persisted or exported"
            )

    @classmethod
    def _insert_evidence(
        cls,
        conn: sqlite3.Connection,
        *,
        content: str,
        repo_id: str,
        evidence_type: str,
        provenance: str,
        metadata: Dict[str, Any],
        evidence_id: str,
        created_at: str,
        compare_created_at: bool = False,
    ) -> str:
        content_hash = cls._evidence_hash(content)
        existing = conn.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,)).fetchone()
        if existing is not None:
            comparable = {
                "content": content,
                "content_hash": content_hash,
                "repo_id": repo_id,
                "evidence_type": evidence_type,
                "provenance": provenance,
                "metadata": cls._json_serialize(metadata),
            }
            if compare_created_at:
                comparable["created_at"] = created_at
            if any(existing[key] != value for key, value in comparable.items()):
                raise EvidenceImmutableError(
                    f"Evidence ID collision would mutate immutable record {evidence_id!r}"
                )
            return evidence_id
        conn.execute(
            """
            INSERT INTO evidence (
                id, content, content_hash, repo_id, evidence_type,
                provenance, metadata, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                content,
                content_hash,
                repo_id,
                evidence_type,
                provenance,
                cls._json_serialize(metadata),
                created_at,
            ),
        )
        return evidence_id

    def store_evidence(
        self,
        content: str,
        repo_id: str,
        evidence_type: str = "observation",
        provenance: str = "unknown",
        metadata: Dict[str, Any] = None,
        evidence_id: str = None,
        created_at: str = None,
    ) -> str:
        """Store one immutable Evidence record, idempotently by explicit ID."""
        if not isinstance(content, str) or not content:
            raise ValueError("Evidence content must be a non-empty string")
        if not isinstance(repo_id, str) or not repo_id.strip():
            raise EvidenceReferenceError("Evidence requires a non-empty repository ID")
        content = self._redact_evidence_content(content)
        if repo_id == UNSCOPED_REPO_ID:
            provenance = "unknown"
        evidence_id = evidence_id or f"ev-{uuid.uuid4().hex}"
        compare_created_at = created_at is not None
        created_at = created_at or utc_now().isoformat()
        with self._get_db() as conn:
            self._insert_evidence(
                conn,
                content=content,
                repo_id=repo_id,
                evidence_type=evidence_type,
                provenance=provenance,
                metadata=metadata or {},
                evidence_id=evidence_id,
                created_at=created_at,
                compare_created_at=compare_created_at,
            )
            conn.commit()
        return evidence_id

    @staticmethod
    def _evidence_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        evidence = dict(row)
        LocalStorage._require_secret_free_evidence(
            evidence["content"], context="reading secret-bearing Evidence"
        )
        if LocalStorage._evidence_hash(evidence["content"]) != evidence["content_hash"]:
            raise EvidenceError("Refusing to read Evidence with an invalid content hash")
        evidence["metadata"] = LocalStorage._json_deserialize(evidence.get("metadata")) or {}
        evidence["record_type"] = "evidence"
        return evidence

    def get_evidence(self, evidence_id: str) -> Optional[Dict[str, Any]]:
        with self._get_db() as conn:
            row = conn.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,)).fetchone()
        return self._evidence_row_to_dict(row) if row is not None else None

    def list_evidence(
        self, repo_id: str, *, limit: int = 10000
    ) -> List[Dict[str, Any]]:
        with self._get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM evidence WHERE repo_id = ? ORDER BY created_at, id LIMIT ?",
                (repo_id, limit),
            ).fetchall()
        return [self._evidence_row_to_dict(row) for row in rows]

    def get_authority_attestation(self, belief_id: str) -> Optional[Dict[str, Any]]:
        """Return the persisted public attestation for one prohibition belief."""
        with self._get_db() as conn:
            row = conn.execute(
                "SELECT digest, belief_id, key_id, nonce, envelope, created_at "
                "FROM authority_attestations WHERE belief_id = ?",
                (belief_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def update_evidence(self, evidence_id: str, **kwargs) -> bool:
        raise EvidenceImmutableError(f"Evidence {evidence_id!r} is immutable")

    def attach_evidence(
        self, belief_id: str, evidence_ids: List[str], *, repo_id: str
    ) -> None:
        with self._get_db() as conn:
            belief = conn.execute(
                "SELECT id, layer, repo_id FROM memories WHERE id = ?", (belief_id,)
            ).fetchone()
            if belief is None or belief["layer"] != "semantic":
                raise EvidenceReferenceError(
                    f"Evidence can attach only to an existing semantic belief: {belief_id!r}"
                )
            if belief["repo_id"] != repo_id:
                raise EvidenceReferenceError("Belief and Evidence must share a repository")
            resolved = self._validate_belief_references(
                conn,
                memory_id=belief_id,
                layer="semantic",
                repo_id=repo_id,
                source_ids=[],
                evidence_ids=evidence_ids,
            )
            created_at = utc_now().isoformat()
            for evidence_id in resolved:
                conn.execute(
                    "INSERT OR IGNORE INTO belief_evidence "
                    "(belief_id, evidence_id, created_at) VALUES (?, ?, ?)",
                    (belief_id, evidence_id, created_at),
                )
            conn.commit()

    @staticmethod
    def _belief_evidence_ids(conn: sqlite3.Connection, belief_id: str) -> List[str]:
        return [
            row["evidence_id"]
            for row in conn.execute(
                "SELECT evidence_id FROM belief_evidence "
                "WHERE belief_id = ? ORDER BY created_at, evidence_id",
                (belief_id,),
            ).fetchall()
        ]

    @classmethod
    def _validate_belief_references(
        cls,
        conn: sqlite3.Connection,
        *,
        memory_id: str,
        layer: str,
        repo_id: str,
        source_ids: List[str],
        evidence_ids: List[str],
    ) -> List[str]:
        if memory_id in evidence_ids:
            raise EvidenceReferenceError("A belief cannot cite itself as Evidence")

        resolved = list(dict.fromkeys(evidence_ids))
        for source_id in dict.fromkeys(source_ids):
            source = conn.execute(
                "SELECT id, repo_id, status FROM memories WHERE id = ?", (source_id,)
            ).fetchone()
            if source is None or source["status"] == "deleted":
                raise EvidenceReferenceError(
                    f"Lineage source {source_id!r} is missing or deleted"
                )
            if source["repo_id"] != repo_id:
                raise EvidenceReferenceError("Lineage sources must belong to the same repository")
            resolved.extend(cls._belief_evidence_ids(conn, source_id))

        resolved = list(dict.fromkeys(resolved))
        if layer == "semantic" and not resolved:
            raise EvidenceReferenceError(
                "A semantic belief requires at least one evidence record"
            )
        for evidence_id in resolved:
            evidence = conn.execute(
                "SELECT repo_id FROM evidence WHERE id = ?", (evidence_id,)
            ).fetchone()
            if evidence is None:
                raise EvidenceReferenceError(
                    f"Evidence {evidence_id!r} is missing or is not an Evidence record"
                )
            if evidence["repo_id"] != repo_id:
                raise EvidenceReferenceError("Evidence must belong to the same repository")
        return resolved

    @repository_memory_write
    def store_memory(
        self,
        content: str,
        layer: MemoryLayer = "episodic",
        repo_id: str = None,
        category: str = None,
        importance: float = 0.5,
        tags: List[str] = None,
        metadata: Dict[str, Any] = None,
        source_ids: List[str] = None,
        evidence_ids: List[str] = None,
        status: MemoryStatus = "active",
        epistemic_status: str = None,
        authority_attestation: str = None,
        replaces_belief_id: str = None,
        source: str = None,
        quality_flags: List[str] = None,
        embedding: List[float] = None,
        auto_link: bool = True,
        auto_link_limit: int = DEFAULT_AUTO_LINK_LIMIT,
        auto_link_min_score: float = DEFAULT_AUTO_LINK_MIN_SCORE,
        memory_id: str = None,
        created_at: str = None,
    ) -> str:
        """
        Store a memory in both SQLite and vector DB.

        Args:
            content: The memory content
            layer: Memory layer (raw, episodic, semantic, intent)
            repo_id: Repository identifier for context
            category: Category for organization
            importance: Importance score (0.0 to 1.0)
            tags: List of tags
            metadata: Additional metadata
            source_ids: IDs of source memories (for compression tracking)
            embedding: Pre-computed embedding (optional)
            auto_link: Whether to create graph relationships to related memories
            auto_link_limit: Maximum inferred similarity links to create
            auto_link_min_score: Minimum similarity score required for inferred links

        Returns:
            Memory ID
        """
        # Enforce the secrets policy at the single choke point every write path funnels
        # through, so a new caller cannot opt out. See visp_memory.quality.secrets.
        try:
            content, quality_flags = redact_for_storage(
                content,
                quality_flags,
                reject_if_redacted=authority_attestation is not None,
            )
        except SecretBearingContentError as exc:
            from visp_memory.core.authority import ProhibitionAuthorityError

            raise ProhibitionAuthorityError(str(exc)) from exc
        memory_id = memory_id or self._generate_id(content)
        repo_id = repo_id or UNSCOPED_REPO_ID
        tags = tags or []
        metadata = metadata or {}
        source_ids = source_ids or []
        evidence_ids = evidence_ids or []
        quality_flags = quality_flags or []
        created_at = created_at or utc_now().isoformat()
        belief_type = None
        if layer == "semantic":
            category = category or "fact"
            belief_type = normalize_belief_type(category)
            category = belief_type
            if belief_type == "hypothesis":
                if epistemic_status not in (None, EpistemicStatus.HYPOTHESIZED.value):
                    raise ValueError(
                        "a hypothesis must begin with hypothesized epistemic status"
                    )
                epistemic_status = EpistemicStatus.HYPOTHESIZED.value
                created_time = parse_utc(created_at)
                if created_time is None:
                    raise ValueError("created_at must be a valid timestamp")
                maximum_valid_to = created_time + timedelta(days=7)
                supplied_valid_to = metadata.get("valid_to")
                if supplied_valid_to is None:
                    metadata = {**metadata, "valid_to": maximum_valid_to.isoformat()}
                else:
                    valid_to = parse_utc(supplied_valid_to)
                    if valid_to is None:
                        raise ValueError("hypothesis valid_to must be a valid timestamp")
                    if valid_to > maximum_valid_to:
                        raise ValueError(
                            "hypothesis valid_to exceeds the seven-day maximum TTL"
                        )
                    metadata = {**metadata, "valid_to": valid_to.isoformat()}
            elif belief_type == "prohibition":
                if epistemic_status not in (None, EpistemicStatus.OBSERVED.value):
                    raise ValueError(
                        "a verified prohibition must begin with observed epistemic status"
                    )
                epistemic_status = EpistemicStatus.OBSERVED.value
            else:
                epistemic_status = normalize_epistemic_status(
                    epistemic_status or EpistemicStatus.INFERRED.value
                )
            if belief_type != "prohibition" and authority_attestation is not None:
                raise ValueError(
                    "authority attestation applies only to a prohibition belief"
                )
            if replaces_belief_id is not None and belief_type != "prohibition":
                raise ValueError(
                    "replaces_belief_id applies only to a prohibition belief"
                )
        elif epistemic_status is not None:
            raise ValueError(
                "epistemic status applies only to a semantic belief"
            )
        else:
            category = category or "general"
        if repo_id == UNSCOPED_REPO_ID:
            from visp_memory.core.trust import Provenance, with_provenance

            tags = with_provenance(tags, Provenance.UNKNOWN)
            source = Provenance.UNKNOWN.value

        if embedding is None and self._embedding_fn is not None:
            try:
                embedding = self._embedding_fn(content)
            except Exception as exc:
                logger.warning("Embedding computation failed for new memory: %s", exc)
                embedding = None

        # Store in SQLite
        with self._get_db() as conn:
            if layer in ("episodic", "raw") and not evidence_ids:
                captured_evidence_id = f"ev-{uuid.uuid4().hex}"
                self._insert_evidence(
                    conn,
                    content=content,
                    repo_id=repo_id,
                    evidence_type="observation" if layer == "episodic" else "raw",
                    provenance=source or "unknown",
                    metadata={"captured_memory_id": memory_id, "exact_input": True},
                    evidence_id=captured_evidence_id,
                    created_at=created_at,
                )
                evidence_ids = [captured_evidence_id]
            resolved_evidence_ids = self._validate_belief_references(
                conn,
                memory_id=memory_id,
                layer=layer,
                repo_id=repo_id,
                source_ids=source_ids,
                evidence_ids=evidence_ids,
            )
            verified_attestation = None
            if belief_type == "prohibition":
                from visp_memory.core.authority import (
                    ProhibitionAuthorityError,
                    verify_prohibition_attestation,
                )

                if authority_attestation is None:
                    raise ProhibitionAuthorityError(
                        "prohibition belief requires an authority attestation"
                    )
                evidence_claim = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT id, content_hash FROM evidence "
                        f"WHERE id IN ({', '.join('?' for _ in resolved_evidence_ids)}) "
                        "ORDER BY id",
                        resolved_evidence_ids,
                    ).fetchall()
                ]
                verified_attestation = verify_prohibition_attestation(
                    authority_attestation,
                    content=content,
                    repo_id=repo_id,
                    metadata=metadata,
                    evidence=evidence_claim,
                    expected_replaces_belief_id=replaces_belief_id,
                )
                nonce_row = conn.execute(
                    "SELECT digest, belief_id FROM authority_attestations "
                    "WHERE key_id = ? AND nonce = ?",
                    (verified_attestation.key_id, verified_attestation.nonce),
                ).fetchone()
                if nonce_row is not None:
                    if nonce_row["digest"] != verified_attestation.digest:
                        raise ProhibitionAuthorityError(
                            "prohibition authority nonce was reused with different bytes"
                        )
                    if nonce_row["belief_id"] != memory_id:
                        raise ProhibitionAuthorityError(
                            "prohibition attestation replay targets a different belief"
                        )
                    existing = conn.execute(
                        "SELECT content, layer, repo_id, belief_type, epistemic_status, "
                        "metadata, status FROM memories WHERE id = ?",
                        (memory_id,),
                    ).fetchone()
                    existing_evidence = self._belief_evidence_ids(conn, memory_id)
                    if (
                        existing is not None
                        and existing["content"] == content
                        and existing["layer"] == layer
                        and existing["repo_id"] == repo_id
                        and existing["belief_type"] == belief_type
                        and existing["epistemic_status"] == epistemic_status
                        and existing["metadata"] == self._json_serialize(metadata)
                        and existing["status"] == status
                        and existing_evidence == resolved_evidence_ids
                    ):
                        return memory_id
                    raise ProhibitionAuthorityError(
                        "prohibition attestation replay does not match the stored belief"
                    )
            self._register_repository(conn, repo_id)
            conn.execute(
                """
                INSERT INTO memories (
                    id, content, layer, repo_id, category, belief_type,
                    epistemic_status, importance, tags, metadata, source_ids,
                    status, source, quality_flags, created_at, accessed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    memory_id,
                    content,
                    layer,
                    repo_id,
                    category,
                    belief_type,
                    epistemic_status,
                    importance,
                    self._json_serialize(tags),
                    self._json_serialize(metadata),
                    self._json_serialize(source_ids),
                    status,
                    source,
                    self._json_serialize(quality_flags),
                    created_at,
                    created_at,
                ),
            )
            for evidence_id in resolved_evidence_ids:
                conn.execute(
                    "INSERT INTO belief_evidence (belief_id, evidence_id, created_at) "
                    "VALUES (?, ?, ?)",
                    (memory_id, evidence_id, created_at),
                )
            if verified_attestation is not None:
                conn.execute(
                    "INSERT INTO authority_attestations "
                    "(digest, belief_id, key_id, nonce, envelope, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        verified_attestation.digest,
                        memory_id,
                        verified_attestation.key_id,
                        verified_attestation.nonce,
                        verified_attestation.envelope,
                        created_at,
                    ),
                )
            conn.commit()

        # Store in vector DB
        collection = self._get_collection(layer)
        if collection is not None:
            metadata_dict = {
                "category": category,
                "importance": importance,
                "tags": self._json_serialize(tags),
                "status": status,
            }
            if repo_id:
                metadata_dict["repo_id"] = repo_id

            add_kwargs = {"ids": [memory_id], "documents": [content], "metadatas": [metadata_dict]}

            if embedding is not None:
                add_kwargs["embeddings"] = [embedding]

            # The SQLite row is already committed above. If the vector write
            # fails we keep the structured row (it is the source of truth) but
            # log loudly so the divergence is visible; rebuild_embedding_index
            # / inspect_embedding_index can reconcile the stale vector index.
            try:
                collection.upsert(**add_kwargs)
            except Exception as exc:
                logger.warning(
                    "Vector upsert failed for memory %s; SQLite row persisted but the "
                    "vector index is now stale (run rebuild_embedding_index to "
                    "reconcile): %s",
                    memory_id,
                    exc,
                )

        self._auto_link_memory(
            memory_id=memory_id,
            content=content,
            repo_id=repo_id,
            source_ids=source_ids,
            enabled=auto_link,
            limit=auto_link_limit,
            min_score=auto_link_min_score,
        )

        return memory_id

    def _auto_link_memory(
        self,
        memory_id: str,
        content: str,
        repo_id: str = None,
        source_ids: List[str] = None,
        enabled: bool = True,
        limit: int = DEFAULT_AUTO_LINK_LIMIT,
        min_score: float = DEFAULT_AUTO_LINK_MIN_SCORE,
    ) -> None:
        """Create provenance and conservative similarity links for a new memory."""
        source_ids = list(dict.fromkeys(source_ids or []))
        excluded_ids = {memory_id, *source_ids}

        for source_id in source_ids:
            if source_id == memory_id:
                continue
            try:
                self.add_relationship(
                    source_id=source_id,
                    target_id=memory_id,
                    relationship=self.SOURCE_LINK_RELATIONSHIP,
                    strength=1.0,
                )
            except ValueError:
                # Source IDs can come from imports or legacy data. Invalid cross-repo or
                # missing sources should not block storing the memory itself.
                continue

        if not enabled or limit <= 0:
            return

        candidates = self.search_memories(
            query=content,
            repo_id=repo_id,
            limit=max(limit * 4, limit + len(excluded_ids) + 1),
        )

        created = 0
        threshold = clamp_score(min_score)
        for candidate in candidates:
            candidate_id = candidate.get("id")
            if not candidate_id or candidate_id in excluded_ids:
                continue

            score = relationship_score(
                candidate.get("similarity"),
                content,
                str(candidate.get("content", "")),
            )
            if score < threshold:
                continue

            try:
                self.add_relationship(
                    source_id=memory_id,
                    target_id=candidate_id,
                    relationship=self.AUTO_LINK_RELATIONSHIP,
                    strength=score,
                )
            except ValueError:
                continue

            created += 1
            if created >= limit:
                break

    def _get_memory_row(self, memory_id: str, *, track_access: bool) -> Optional[Dict[str, Any]]:
        """Get a memory by ID, optionally updating explicit access metrics."""
        with self._get_db() as conn:
            cursor = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,))
            row = cursor.fetchone()

            if row is None:
                return None

            if track_access:
                conn.execute(
                    """
                    UPDATE memories
                    SET access_count = access_count + 1, accessed_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """,
                    (memory_id,),
                )
                conn.commit()

            memory = self._row_to_dict(row)
            memory["evidence_ids"] = self._belief_evidence_ids(conn, memory_id)
            return memory

    def get_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Get a memory by ID and record an explicit access."""
        return self._get_memory_row(memory_id, track_access=True)

    def peek_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Read a memory without counting it as an access.

        For machinery that reads a row to serialise or inspect it rather than
        because someone recalled it — export being the case that exposed this.
        Exporting through ``get_memory`` incremented ``access_count`` and rewrote
        ``accessed_at`` on every belief it touched, so the act of exporting
        changed what a second export would produce and no round trip could ever
        compare equal.
        """
        return self._get_memory_row(memory_id, track_access=False)

    def search_memories(
        self,
        query: str,
        layer: MemoryLayer = None,
        repo_id: str = None,
        category: str = None,
        limit: int = 10,
        min_importance: float = 0.0,
        status: str = "active",
        **_kwargs,
    ) -> List[Dict[str, Any]]:
        """
        Semantic search across memories.

        Args:
            query: Search query (natural language)
            layer: Filter by layer
            repo_id: Filter by repository context
            category: Filter by category
            limit: Maximum results
            min_importance: Minimum importance threshold

        Returns:
            List of matching memories with similarity scores
        """
        query, _ = redact_for_storage(query, None)
        results = []
        seen_ids = set()

        # Search each relevant layer's vector collection
        layers_to_search = [layer] if layer else ["episodic", "semantic", "intent"]

        for search_layer in layers_to_search:
            if self._uses_noop_embeddings:
                continue

            collection = self._get_collection(search_layer)
            if collection is None:
                continue

            # Build where clause. Filtering status at the vector layer (rather
            # than only post-fetch in Python) ensures active matches ranked
            # beyond the top-N non-active hits are not silently dropped.
            where = {}
            if category:
                where["category"] = category
            if repo_id:
                where["repo_id"] = repo_id
            if min_importance > 0:
                where["importance"] = {"$gte": min_importance}
            if status and status != "all":
                where["status"] = status

            try:
                query_kwargs = {
                    "n_results": limit,
                    "where": where if where else None,
                }
                if self._embedding_fn is not None:
                    query_kwargs["query_embeddings"] = [self._embedding_fn(query)]
                else:
                    query_kwargs["query_texts"] = [query]

                search_results = collection.query(**query_kwargs)

                if search_results["ids"] and search_results["ids"][0]:
                    for i, mem_id in enumerate(search_results["ids"][0]):
                        distance = (
                            search_results["distances"][0][i]
                            if search_results.get("distances")
                            else 0
                        )
                        similarity = normalize_distance_score(distance)

                        memory = self._get_memory_row(mem_id, track_access=False)
                        if not memory:
                            continue
                        status_matches = (
                            not status or status == "all" or memory.get("status") == status
                        )
                        if status_matches:
                            seen_ids.add(mem_id)
                            memory["similarity"] = similarity
                            memory["retrieval_method"] = "semantic"
                            results.append(memory)

            except Exception as exc:
                # Collection might be empty / not yet populated; fall back to
                # text search below. Logged at debug to avoid noise.
                logger.debug("Vector search failed on layer %s: %s", search_layer, exc)

        if len(results) < limit:
            results.extend(
                self._text_search_memories(
                    query=query,
                    layers=layers_to_search,
                    repo_id=repo_id,
                    category=category,
                    limit=limit - len(results),
                    min_importance=min_importance,
                    exclude_ids=seen_ids,
                    status=status,
                )
            )

        self._attach_recall_utility_scores(results)
        return rank_memory_results(results, query=query, limit=limit)

    def _text_search_memories(
        self,
        query: str,
        layers: List[str],
        repo_id: str = None,
        category: str = None,
        limit: int = 10,
        min_importance: float = 0.0,
        exclude_ids: set[str] = None,
        status: str = "active",
    ) -> List[Dict[str, Any]]:
        """Fallback SQLite search used when vector search is unavailable or incomplete."""
        query, _ = redact_for_storage(query, None)
        exclude_ids = exclude_ids or set()
        terms = [term.lower() for term in query.split() if term.strip()]
        sql = "SELECT * FROM memories WHERE importance >= ?"
        params: list[Any] = [min_importance]

        if layers:
            placeholders = ", ".join("?" for _ in layers)
            sql += f" AND layer IN ({placeholders})"
            params.extend(layers)

        if repo_id:
            sql += " AND repo_id = ?"
            params.append(repo_id)

        if category:
            sql += " AND category = ?"
            params.append(category)

        if status and status != "all":
            sql += " AND status = ?"
            params.append(status)

        if terms:
            sql += " AND ("
            sql += " OR ".join("lower(content) LIKE ?" for _ in terms)
            sql += ")"
            params.extend(f"%{term}%" for term in terms)

        sql += " ORDER BY importance DESC, created_at DESC LIMIT ?"
        params.append(limit + len(exclude_ids))

        with self._get_db() as conn:
            cursor = conn.execute(sql, params)
            rows = []
            for row in cursor.fetchall():
                memory = self._row_to_dict(row)
                memory["evidence_ids"] = self._belief_evidence_ids(conn, memory["id"])
                rows.append(memory)

        results = []
        for row in rows:
            if row["id"] in exclude_ids:
                continue
            row["similarity"] = text_similarity(query, row["content"])
            row["retrieval_method"] = "keyword"
            results.append(row)
            if len(results) >= limit:
                break

        return results

    def list_memories(
        self,
        layer: MemoryLayer = None,
        repo_id: str = None,
        category: str = None,
        status: str = "active",
        limit: int = 50,
        offset: int = 0,
        order_by: str = "created_at DESC",
        after_id: str = None,
    ) -> List[Dict[str, Any]]:
        """List memories with optional filtering."""
        query = "SELECT * FROM memories WHERE 1=1"
        params = []

        if layer:
            query += " AND layer = ?"
            params.append(layer)

        if repo_id:
            query += " AND repo_id = ?"
            params.append(repo_id)

        if category:
            query += " AND category = ?"
            params.append(category)

        if after_id is not None:
            query += " AND id > ?"
            params.append(after_id)

        if status and status != "all":
            query += " AND status = ?"
            params.append(status)

        allowed_order_by = {
            "created_at DESC",
            "created_at ASC",
            "importance DESC",
            "importance ASC",
            "accessed_at DESC",
            "accessed_at ASC",
            "id ASC",
        }
        if order_by not in allowed_order_by:
            order_by = "created_at DESC"

        # Deterministic tiebreaker on insertion order (rowid) so rows that share
        # a timestamp/importance are not returned in arbitrary order, which makes
        # "latest"-style queries (limit=1) flaky under same-microsecond writes.
        tiebreak = "rowid ASC" if order_by.endswith("ASC") else "rowid DESC"
        query += f" ORDER BY {order_by}, {tiebreak} LIMIT ? OFFSET ?"
        params.extend((limit, max(0, offset)))

        with self._get_db() as conn:
            cursor = conn.execute(query, params)
            memories = []
            for row in cursor.fetchall():
                memory = self._row_to_dict(row)
                memory["evidence_ids"] = self._belief_evidence_ids(conn, memory["id"])
                memories.append(memory)
            return memories

    def update_memory(
        self,
        memory_id: str,
        content: str = None,
        importance: float = None,
        tags: List[str] = None,
        metadata: Dict[str, Any] = None,
        status: MemoryStatus = None,
        approved_by: str = None,
        approved_at: str = None,
        archived_at: str = None,
        source: str = None,
        quality_flags: List[str] = None,
        epistemic_status: str = None,
    ) -> bool:
        """Update an existing memory."""
        updates = []
        params = []

        if content is not None:
            existing = self._get_memory_row(memory_id, track_access=False)
            if existing is None:
                return False
            if existing.get("layer") == "semantic":
                raise SemanticMemoryImmutableError(
                    "Semantic belief content is immutable; create an evidence-backed "
                    "successor with revise_memory"
                )
            existing_flags = existing.get("quality_flags") or []
            original_content = content
            content, redaction_flags = redact_for_storage(
                content,
                quality_flags if quality_flags is not None else existing_flags,
            )
            updates.append("content = ?")
            params.append(content)
            if content != original_content:
                quality_flags = redaction_flags

        if importance is not None:
            updates.append("importance = ?")
            params.append(importance)

        if tags is not None:
            updates.append("tags = ?")
            params.append(self._json_serialize(tags))

        if metadata is not None:
            updates.append("metadata = ?")
            params.append(self._json_serialize(metadata))

        if status is not None:
            updates.append("status = ?")
            params.append(status)
            if status == "archived":
                updates.append("archived_at = COALESCE(archived_at, CURRENT_TIMESTAMP)")
            elif status == "active":
                updates.append("archived_at = NULL")

        if epistemic_status is not None:
            # Normalised on the way in, exactly as a write is: a lifecycle
            # transition must not be able to introduce a state the governed
            # vocabulary does not define.
            updates.append("epistemic_status = ?")
            params.append(normalize_epistemic_status(epistemic_status))

        if approved_by is not None:
            updates.append("approved_by = ?")
            params.append(approved_by)

        if approved_at is not None:
            updates.append("approved_at = ?")
            params.append(approved_at)

        if archived_at is not None:
            updates.append("archived_at = ?")
            params.append(archived_at)

        if source is not None:
            updates.append("source = ?")
            params.append(source)

        if quality_flags is not None:
            updates.append("quality_flags = ?")
            params.append(self._json_serialize(quality_flags))

        if not updates:
            return False

        params.append(memory_id)

        with self._get_db() as conn:
            cursor = conn.execute(f"UPDATE memories SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()
            updated = cursor.rowcount > 0

        if updated and any(value is not None for value in (content, importance, tags, status)):
            memory = self._get_memory_row(memory_id, track_access=False)
            if memory:
                collection = self._get_collection(memory["layer"])
                if collection is not None:
                    try:
                        metadata_dict = {
                            "category": memory.get("category", "general"),
                            "importance": memory.get("importance", 0.5),
                            "tags": self._json_serialize(memory.get("tags", [])),
                            "status": memory.get("status", "active"),
                        }
                        if memory.get("repo_id"):
                            metadata_dict["repo_id"] = memory["repo_id"]
                        update_kwargs = {
                            "ids": [memory_id],
                            "metadatas": [metadata_dict],
                        }
                        if content is not None:
                            update_kwargs["documents"] = [content]
                            if self._embedding_fn is not None:
                                try:
                                    update_kwargs["embeddings"] = [self._embedding_fn(content)]
                                except Exception as exc:
                                    logger.warning(
                                        "Embedding computation failed during update of "
                                        "memory %s: %s",
                                        memory_id,
                                        exc,
                                    )
                        collection.update(**update_kwargs)
                    except Exception as exc:
                        logger.warning(
                            "Vector update failed for memory %s; vector index may be "
                            "stale (run rebuild_embedding_index to reconcile): %s",
                            memory_id,
                            exc,
                        )

        return updated

    def _drop_deleted_source_reference(self, conn, memory_id: str) -> None:
        """Remove a hard-deleted memory from every survivor's ``source_ids``.

        ``source_ids`` is JSON, so nothing in the schema enforces it the way a
        foreign key enforces ``relationships``. Deleting a memory therefore left
        derived beliefs citing an id that no longer resolved (MG-033) — a
        provenance trail pointing at nothing, which reads as "this was derived
        from something" while being unable to say what.

        Runs inside the caller's transaction so a survivor is never left citing a
        row that has already gone.
        """
        # LIKE narrows the scan; the JSON parse below is what actually decides,
        # since a substring match can hit an unrelated id that contains this one.
        candidates = conn.execute(
            "SELECT id, source_ids FROM memories WHERE id != ? AND source_ids LIKE ?",
            (memory_id, f"%{memory_id}%"),
        ).fetchall()

        for row in candidates:
            try:
                current = json.loads(row["source_ids"] or "[]")
            except (TypeError, ValueError):
                continue
            if not isinstance(current, list) or memory_id not in current:
                continue
            remaining = [source for source in current if source != memory_id]
            conn.execute(
                "UPDATE memories SET source_ids = ? WHERE id = ?",
                (self._json_serialize(remaining), row["id"]),
            )

    def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory from both stores."""
        # Get layer first for vector DB cleanup
        memory = self._get_memory_row(memory_id, track_access=False)
        if not memory:
            return False

        # The vector store is not transactional with SQLite.  Remove it first so
        # a vector failure leaves the structured row and all of its child rows in
        # place for a truthful retry; deleting the row first would create an
        # orphan embedding that the caller could no longer address.
        collection = self._get_collection(memory["layer"])
        if collection:
            try:
                collection.delete(ids=[memory_id])
            except Exception as exc:
                logger.error(
                    "Vector delete failed for memory %s; the row remains for retry: %s",
                    memory_id,
                    exc,
                )
                return False

        # Delete from SQLite. Child rows (which carry FK references to
        # memories.id) must be removed before the parent row so that
        # foreign_keys=ON enforcement does not reject the parent delete.
        with self._get_db() as conn:
            conn.execute("DELETE FROM recall_events WHERE memory_id = ?", (memory_id,))
            conn.execute(
                "DELETE FROM relationships WHERE source_id = ? OR target_id = ?",
                (memory_id, memory_id),
            )
            self._drop_deleted_source_reference(conn, memory_id)
            conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            conn.commit()

        return True

    def set_intent(
        self,
        description: str,
        priority: int = 0,
        context: Dict[str, Any] = None,
        repo_id: str = None,
    ) -> str:
        """Set a new intent (goal/direction)."""
        from visp_memory.core.intent_workflow import WORKFLOW_CONTEXT_KEYS

        intent_id = self._generate_id(description)
        context = {key: value for key, value in (context or {}).items()
                   if key not in WORKFLOW_CONTEXT_KEYS}
        # Intents must obey the same scope invariant as memories: never NULL.
        #
        # `store_memory` has applied this default since the column existed, and
        # `_migrate_v2_to_v3` explicitly rewrites NULL/empty intent scopes to the
        # sentinel so the column can be queried uniformly — and then this method
        # went on inserting NULLs, re-breaking the invariant the migration had
        # just established. A NULL-scoped intent matches no scoped query at all,
        # so `visp-memory goal` reported success for a goal nothing could list.
        repo_id = repo_id or UNSCOPED_REPO_ID

        with self._get_db() as conn:
            self._register_repository(conn, repo_id)
            conn.execute(
                """
                INSERT INTO intents (id, description, priority, context, repo_id)
                VALUES (?, ?, ?, ?, ?)
            """,
                (intent_id, description, priority, self._json_serialize(context), repo_id),
            )
            conn.commit()

        return intent_id

    def get_active_intents(
        self, repo_id: str = None, status: str = "active"
    ) -> List[Dict[str, Any]]:
        """Get intents ordered by priority."""
        query = "SELECT * FROM intents WHERE 1=1"
        params = []

        if status and status != "all":
            query += " AND status = ?"
            params.append(status)

        if repo_id:
            query += " AND repo_id = ?"
            params.append(repo_id)

        query += " ORDER BY priority DESC, created_at DESC"

        with self._get_db() as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def report_intent_workflow(self, intent_id: str, report, *, actor_id: str, channel: str):
        from visp_memory.core.intent_workflow import IntentWorkflowReport, apply_workflow_report

        parsed = IntentWorkflowReport.model_validate(report)
        with self._get_db() as connection:
            return apply_workflow_report(connection, intent_id, parsed, actor_id, channel)

    def complete_intent(self, intent_id: str) -> bool:
        """Keep the legacy surface without changing externally owned status."""
        return False

    def update_intent(
        self,
        intent_id: str,
        description: str = None,
        priority: int = None,
        status: str = None,
        context: Dict[str, Any] = None,
    ) -> bool:
        """Update mutable intent fields."""
        updates = []
        params = []

        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if priority is not None:
            updates.append("priority = ?")
            params.append(priority)
        # Status changes require the dedicated external workflow report path.
        # Generic edits retain this argument for compatibility.
        if context is not None:
            updates.append("context = ?")
            params.append(self._json_serialize(context))

        if not updates:
            return False

        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(intent_id)

        with self._get_db() as conn:
            if context is not None:
                from visp_memory.core.intent_workflow import WORKFLOW_CONTEXT_KEYS

                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT context, status FROM intents WHERE id = ?", (intent_id,)
                ).fetchone()
                existing = self._json_deserialize(row["context"]) if row else {}
                merged = dict(context)
                if row and row["status"] != "active":
                    merged.pop("completion_evaluation", None)
                for key in WORKFLOW_CONTEXT_KEYS:
                    merged.pop(key, None)
                    if key in (existing or {}):
                        merged[key] = existing[key]
                params[updates.index("context = ?")] = self._json_serialize(merged)
            cursor = conn.execute(
                f"""
                UPDATE intents
                SET {", ".join(updates)}
                WHERE id = ?
                """,
                params,
            )
            conn.commit()
            return cursor.rowcount > 0

    def append_intent_outcome(
        self, intent_id: str, outcome: Dict[str, Any]
    ) -> bool:
        """Append an outcome under a write transaction so concurrent writers cannot race."""
        with self._get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT context FROM intents WHERE id = ?", (intent_id,)
            ).fetchone()
            if row is None:
                conn.rollback()
                return False
            context = self._json_deserialize(row["context"]) or {}
            history = context.get("outcome_history")
            history = list(history) if isinstance(history, list) else []
            history.append(dict(outcome))
            context["outcome_history"] = history
            cursor = conn.execute(
                """
                UPDATE intents
                SET context = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (self._json_serialize(context), intent_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def add_relationship(
        self,
        source_id: str,
        target_id: str,
        relationship: str,
        strength: float = 1.0,
        evidence: Dict[str, Any] = None,
    ) -> str:
        """Add a relationship between memories."""
        source = self._get_memory_row(source_id, track_access=False)
        target = self._get_memory_row(target_id, track_access=False)
        if source is None or target is None:
            raise ValueError("Relationship source and target memories must both exist")
        if source.get("repo_id") != target.get("repo_id"):
            raise ValueError("Memory relationships cannot cross repository boundaries")

        rel_id = self._generate_id(f"{source_id}-{target_id}-{relationship}")
        evidence_data = self._normalize_relationship_evidence(
            evidence,
            strength=strength,
            created_at=None,
            legacy=False,
        )

        with self._get_db() as conn:
            if relationship != self.AUTO_LINK_RELATIONSHIP:
                conn.execute(
                    """
                    DELETE FROM relationships
                    WHERE relationship = ?
                    AND (
                        (source_id = ? AND target_id = ?)
                        OR (source_id = ? AND target_id = ?)
                    )
                """,
                    (
                        self.AUTO_LINK_RELATIONSHIP,
                        source_id,
                        target_id,
                        target_id,
                        source_id,
                    ),
                )
            conn.execute(
                """
                INSERT OR REPLACE INTO relationships (
                    id, source_id, target_id, relationship, strength,
                    confidence, confidence_score, source, source_file,
                    source_location, reason, created_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    rel_id,
                    source_id,
                    target_id,
                    relationship,
                    strength,
                    evidence_data["confidence"],
                    evidence_data["confidence_score"],
                    evidence_data["source"],
                    evidence_data["source_file"],
                    evidence_data["source_location"],
                    evidence_data["reason"],
                    evidence_data["created_by"],
                ),
            )
            conn.commit()

        return rel_id

    def get_related_memories(
        self, memory_id: str, relationship: str = None
    ) -> List[Dict[str, Any]]:
        """Get memories related to a given memory."""
        query = """
            SELECT m.*, r.relationship, r.strength,
                r.confidence AS relationship_confidence,
                r.confidence_score AS relationship_confidence_score,
                r.source AS relationship_evidence_source,
                r.source_file AS relationship_source_file,
                r.source_location AS relationship_source_location,
                r.reason AS relationship_reason,
                r.created_by AS relationship_created_by,
                r.created_at AS relationship_created_at
            FROM memories m
            JOIN relationships r ON (m.id = r.target_id OR m.id = r.source_id)
            WHERE (r.source_id = ? OR r.target_id = ?)
            AND m.id != ?
        """
        params = [memory_id, memory_id, memory_id]

        # A relationship outlives the memory at its other end, so without this the
        # traversal happily returned deleted, merged and superseded targets: the
        # edge still pointed at them and nothing here asked whether they were
        # still servable (MG-030). Sorted for a stable query string.
        non_servable = sorted(NON_SERVABLE_STATUSES)
        query += (
            " AND (m.status IS NULL OR m.status NOT IN "
            f"({', '.join('?' for _ in non_servable)}))"
        )
        params.extend(non_servable)

        if relationship:
            query += " AND r.relationship = ?"
            params.append(relationship)

        source = self._get_memory_row(memory_id, track_access=False)
        if source is None:
            return []
        if source.get("repo_id") is None:
            query += " AND m.repo_id IS NULL"
        else:
            query += " AND m.repo_id = ?"
            params.append(source["repo_id"])

        with self._get_db() as conn:
            cursor = conn.execute(query, params)
            related = []
            seen_ids = set()
            for row in cursor.fetchall():
                item = self._row_to_dict(row)
                evidence_data = {
                    "confidence": item.pop("relationship_confidence", None),
                    "confidence_score": item.pop("relationship_confidence_score", None),
                    "source": item.pop("relationship_evidence_source", None),
                    "source_file": item.pop("relationship_source_file", None),
                    "source_location": item.pop("relationship_source_location", None),
                    "reason": item.pop("relationship_reason", None),
                    "created_by": item.pop("relationship_created_by", None),
                    "created_at": item.pop("relationship_created_at", None),
                }
                item["relationship_evidence"] = self._normalize_relationship_evidence(
                    evidence_data,
                    strength=item.get("strength"),
                    created_at=evidence_data["created_at"],
                    legacy=evidence_data["source"] == "legacy",
                )
                if item["id"] in seen_ids:
                    continue
                seen_ids.add(item["id"])
                related.append(item)
            return related

    def get_all_relationships(self, repo_id: str = None) -> List[Dict[str, Any]]:
        """Get all relationships."""
        query = "SELECT r.* FROM relationships r"
        params = []
        if repo_id:
            query += """
                JOIN memories source ON source.id = r.source_id
                JOIN memories target ON target.id = r.target_id
                WHERE source.repo_id = ? AND target.repo_id = ?
            """
            params.extend([repo_id, repo_id])

        with self._get_db() as conn:
            cursor = conn.execute(query, params)
            return [self._relationship_row_to_dict(row) for row in cursor.fetchall()]

    def delete_relationship(self, relationship_id: str) -> bool:
        with self._get_db() as conn:
            result = conn.execute("DELETE FROM relationships WHERE id = ?", (relationship_id,))
            conn.commit()
        return result.rowcount > 0

    def start_session(
        self,
        *,
        owner_id: str = None,
        team_id: str = None,
        repo_id: str = None,
    ) -> str:
        """Start a new session for tracking."""
        session_id = self._generate_id("session")

        with self._get_db() as conn:
            conn.execute(
                "INSERT INTO sessions (id, owner_id, team_id, repo_id) VALUES (?, ?, ?, ?)",
                (session_id, owner_id, team_id, repo_id),
            )
            conn.commit()

        return session_id

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._get_db() as conn:
            row = conn.execute(
                "SELECT id, owner_id, team_id, repo_id, summary, memory_ids, "
                "started_at, ended_at FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        session = dict(row)
        session["memory_ids"] = self._json_deserialize(session.get("memory_ids")) or []
        return session

    def end_session(
        self, session_id: str, summary: str, memory_ids: List[str]
    ) -> SessionCompletionStatus:
        """Atomically complete an open session."""
        with self._get_db() as conn:
            result = conn.execute(
                """
                UPDATE sessions
                SET summary = ?, memory_ids = ?, ended_at = CURRENT_TIMESTAMP
                WHERE id = ? AND ended_at IS NULL
            """,
                (summary, self._json_serialize(memory_ids), session_id),
            )
            conn.commit()
            if result.rowcount:
                return SessionCompletionStatus.COMPLETED
            exists = conn.execute(
                "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return (
            SessionCompletionStatus.ALREADY_COMPLETED
            if exists
            else SessionCompletionStatus.NOT_FOUND
        )

    def get_stats(self, repo_id: str = None) -> Dict[str, Any]:
        """Get storage statistics."""
        with self._get_db() as conn:
            stats = {}

            # Build WHERE clause for repo filtering
            repo_filter = " WHERE status = 'active'"
            repo_params = []
            if repo_id:
                repo_filter += " AND repo_id = ?"
                repo_params = [repo_id]

            # Memory counts by layer
            cursor = conn.execute(
                f"""
                SELECT layer, COUNT(*) as count
                FROM memories
                {repo_filter}
                GROUP BY layer
            """,
                repo_params,
            )
            stats["memories_by_layer"] = dict(cursor.fetchall())

            # Memory counts by category
            cursor = conn.execute(
                f"""
                SELECT category, COUNT(*) as count
                FROM memories
                {repo_filter}
                GROUP BY category
            """,
                repo_params,
            )
            stats["memories_by_category"] = dict(cursor.fetchall())

            # Total counts
            cursor = conn.execute(f"SELECT COUNT(*) FROM memories{repo_filter}", repo_params)
            stats["total_memories"] = cursor.fetchone()[0]

            intent_query = "SELECT COUNT(*) FROM intents WHERE status = 'active'"
            intent_params = []
            if repo_id:
                intent_query += " AND repo_id = ?"
                intent_params.append(repo_id)

            cursor = conn.execute(intent_query, intent_params)
            stats["active_intents"] = cursor.fetchone()[0]

            rel_query = "SELECT COUNT(*) FROM relationships r"
            rel_params = []
            if repo_id:
                rel_query += """
                    JOIN memories source ON source.id = r.source_id
                    JOIN memories target ON target.id = r.target_id
                    WHERE source.repo_id = ? AND target.repo_id = ?
                """
                rel_params.extend([repo_id, repo_id])

            cursor = conn.execute(rel_query, rel_params)
            stats["total_relationships"] = cursor.fetchone()[0]

            return stats

    def log_recall_event(
        self,
        memory_id: str,
        event_type: RecallEventType,
        repo_id: str = None,
        query: str = None,
        task_id: str = None,
        outcome: str = None,
        metadata: Dict[str, Any] = None,
    ) -> str:
        """Record a privacy-conscious recall utility event."""
        normalized_type = self._normalize_recall_event_type(event_type)
        _, canonical_repo_id = self._resolve_recall_memory_scope(memory_id, repo_id)

        event_id = self._generate_id(f"{memory_id}:{normalized_type}")
        with self._get_db() as conn:
            conn.execute(
                """
                INSERT INTO recall_events (
                    id, memory_id, event_type, repo_id, query_hash, task_id,
                    outcome, metadata, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    memory_id,
                    normalized_type,
                    canonical_repo_id,
                    self._hash_recall_query(query),
                    task_id,
                    outcome,
                    self._json_serialize(self._sanitize_recall_metadata(metadata)),
                    utc_now().isoformat(),
                ),
            )
            if normalized_type in REINFORCING_RECALL_EVENTS:
                # Retrieval-induced strengthening: a used memory becomes easier to
                # recall and resets its decay clock.
                conn.execute(
                    """
                    UPDATE memories
                    SET access_count = access_count + 1, accessed_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (memory_id,),
                )
            conn.commit()
        return event_id

    def inspect_recall_utility(
        self,
        memory_id: str = None,
        repo_id: str = None,
        event_type: str = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Return recall utility signals and recent sanitized events."""
        canonical_repo_id = None
        if memory_id is not None:
            _, canonical_repo_id = self._resolve_recall_memory_scope(memory_id, repo_id)
        if memory_id is None and repo_id is not None:
            where = (
                "WHERE memory_id IN (SELECT id FROM memories WHERE repo_id = ?) "
                "AND recall_events.repo_id = ?"
            )
            params: list[Any] = [repo_id, repo_id]
            if event_type:
                where += " AND event_type = ?"
                params.append(self._normalize_recall_event_type(event_type))
        else:
            where, params = self._recall_event_filters(
                memory_id,
                canonical_repo_id,
                event_type,
                repo_id_is_null=memory_id is not None and canonical_repo_id is None,
            )
        with self._get_db() as conn:
            count_cursor = conn.execute(
                f"""
                SELECT event_type, COUNT(*) AS count
                FROM recall_events
                {where}
                GROUP BY event_type
                """,
                params,
            )
            by_event_type = {row["event_type"]: row["count"] for row in count_cursor.fetchall()}

            signal_cursor = conn.execute(
                f"""
                SELECT memory_id, repo_id, event_type, COUNT(*) AS count,
                       MAX(created_at) AS last_event_at
                FROM recall_events
                {where}
                GROUP BY memory_id, repo_id, event_type
                ORDER BY MAX(created_at) DESC
                """,
                params,
            )
            signals_by_memory: dict[str, dict[str, Any]] = {}
            for row in signal_cursor.fetchall():
                signal = signals_by_memory.setdefault(
                    row["memory_id"],
                    {
                        "memory_id": row["memory_id"],
                        "repo_id": row["repo_id"],
                        "counts": {},
                        "total_events": 0,
                        "last_event_at": row["last_event_at"],
                    },
                )
                signal["counts"][row["event_type"]] = row["count"]
                signal["total_events"] += row["count"]
                signal["last_event_at"] = max(
                    signal["last_event_at"] or "", row["last_event_at"] or ""
                )

            events_cursor = conn.execute(
                f"""
                SELECT id, memory_id, event_type, repo_id, query_hash, task_id,
                       outcome, metadata, created_at
                FROM recall_events
                {where}
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                [*params, max(0, int(limit))],
            )
            events = [
                self._recall_event_row_to_dict(row) for row in events_cursor.fetchall()
            ]

        signals = []
        for signal in signals_by_memory.values():
            utility_score = self._recall_utility_score_from_counts(signal["counts"])
            signal["utility_score"] = utility_score
            signal["utility_rank_adjustment"] = utility_rank_adjustment(utility_score)
            signals.append(signal)

        signals.sort(
            key=lambda item: (
                item.get("utility_score", 0.0),
                item.get("last_event_at") or "",
            ),
            reverse=True,
        )
        return {
            "summary": {
                "total_events": sum(by_event_type.values()),
                "by_event_type": by_event_type,
                "memories": len(signals),
            },
            "signals": signals,
            "events": events,
            "verification": self.verify_recall_utility(
                memory_id=memory_id,
                repo_id=repo_id,
                event_type=event_type,
            ),
        }

    def verify_recall_utility(
        self,
        memory_id: str = None,
        repo_id: str = None,
        event_type: str = None,
    ) -> Dict[str, Any]:
        """Verify historical recall event repository attribution without rewriting it."""
        if memory_id is not None:
            self._resolve_recall_memory_scope(memory_id, repo_id)

        conditions = ["1=1"]
        params: list[Any] = []
        if memory_id is not None:
            conditions.append("e.memory_id = ?")
            params.append(memory_id)
        elif repo_id is not None:
            conditions.append(
                "EXISTS (SELECT 1 FROM memories scoped WHERE scoped.id = e.memory_id "
                "AND scoped.repo_id = ?)"
            )
            params.append(repo_id)
        if event_type:
            conditions.append("e.event_type = ?")
            params.append(self._normalize_recall_event_type(event_type))

        with self._get_db() as conn:
            cursor = conn.execute(
                f"""
                SELECT e.id, e.memory_id, e.repo_id AS event_repo_id,
                       m.id AS matched_memory_id, m.repo_id AS memory_repo_id
                FROM recall_events e
                LEFT JOIN memories m ON m.id = e.memory_id
                WHERE {' AND '.join(conditions)}
                ORDER BY e.created_at ASC, e.rowid ASC
                """,
                params,
            )
            rows = cursor.fetchall()

        violations = []
        for row in rows:
            if row["matched_memory_id"] is None:
                continue
            if row["event_repo_id"] == row["memory_repo_id"]:
                continue
            violations.append(
                {
                    "event_id": row["id"],
                    "memory_id": row["memory_id"],
                    "event_repo_id": row["event_repo_id"],
                    "memory_repo_id": row["memory_repo_id"],
                }
            )

        return {
            "valid": not violations,
            "checked_events": len(rows),
            "cross_repository_events": len(violations),
            "violations": violations,
        }

    def reset_recall_utility(
        self,
        memory_id: str = None,
        repo_id: str = None,
        event_type: str = None,
    ) -> int:
        """Delete recall utility events matching optional filters."""
        canonical_repo_id = None
        if memory_id is not None:
            _, canonical_repo_id = self._resolve_recall_memory_scope(memory_id, repo_id)
        if memory_id is None and repo_id is not None:
            where = (
                "WHERE memory_id IN (SELECT id FROM memories WHERE repo_id = ?) "
                "AND recall_events.repo_id = ?"
            )
            params: list[Any] = [repo_id, repo_id]
            if event_type:
                where += " AND event_type = ?"
                params.append(self._normalize_recall_event_type(event_type))
        else:
            where, params = self._recall_event_filters(
                memory_id,
                canonical_repo_id,
                event_type,
                repo_id_is_null=memory_id is not None and canonical_repo_id is None,
            )
        with self._get_db() as conn:
            cursor = conn.execute(f"DELETE FROM recall_events {where}", params)
            conn.commit()
            return cursor.rowcount

    def _attach_recall_utility_scores(self, memories: List[Dict[str, Any]]) -> None:
        """Attach aggregate recall utility scores to memory rows in-place."""
        memory_ids = [memory.get("id") for memory in memories if memory.get("id")]
        if not memory_ids:
            return

        placeholders = ", ".join("?" for _ in memory_ids)
        with self._get_db() as conn:
            cursor = conn.execute(
                f"""
                SELECT e.memory_id, e.event_type, COUNT(*) AS count
                FROM recall_events e
                JOIN memories m ON m.id = e.memory_id
                WHERE e.memory_id IN ({placeholders})
                  AND (e.repo_id = m.repo_id
                       OR (e.repo_id IS NULL AND m.repo_id IS NULL))
                GROUP BY e.memory_id, e.event_type
                """,
                memory_ids,
            )
            counts_by_memory: dict[str, dict[str, int]] = {}
            for row in cursor.fetchall():
                counts_by_memory.setdefault(row["memory_id"], {})[row["event_type"]] = row[
                    "count"
                ]

        for memory in memories:
            counts = counts_by_memory.get(memory.get("id"), {})
            utility_score = self._recall_utility_score_from_counts(counts)
            memory["utility_score"] = utility_score
            memory["utility_signal"] = {
                "counts": counts,
                "total_events": sum(counts.values()),
                "rank_adjustment": utility_rank_adjustment(utility_score),
            }

    @classmethod
    def _normalize_recall_event_type(cls, event_type: str) -> str:
        normalized = str(event_type or "").strip().lower().replace("-", "_")
        if normalized not in RECALL_EVENT_WEIGHTS:
            allowed = ", ".join(sorted(RECALL_EVENT_WEIGHTS))
            raise ValueError(f"Invalid recall event type: {event_type}. Expected one of: {allowed}")
        return normalized

    @classmethod
    def _recall_utility_score_from_counts(cls, counts: Dict[str, int]) -> float:
        score = 0.0
        for event_type, count in counts.items():
            score += RECALL_EVENT_WEIGHTS.get(event_type, 0.0) * int(count)
        return max(-1.0, min(1.0, score))

    @classmethod
    def _sanitize_recall_metadata(cls, metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        if not isinstance(metadata, dict):
            return {}

        safe: dict[str, Any] = {}
        for key, value in metadata.items():
            normalized_key = str(key).lower()
            if any(sensitive in normalized_key for sensitive in SENSITIVE_RECALL_METADATA_KEYS):
                continue
            if isinstance(value, str) and len(value) > 500:
                value = f"{value[:500]}..."
            safe[str(key)] = value
        return json.loads(json.dumps(safe, default=str))

    @classmethod
    def _hash_recall_query(cls, query: str = None) -> str | None:
        if not query:
            return None
        return hashlib.sha256(str(query).encode("utf-8")).hexdigest()

    @classmethod
    def _recall_event_filters(
        cls,
        memory_id: str = None,
        repo_id: str = None,
        event_type: str = None,
        *,
        repo_id_is_null: bool = False,
    ) -> tuple[str, list[Any]]:
        where = "WHERE 1=1"
        params: list[Any] = []
        if memory_id is not None:
            where += " AND memory_id = ?"
            params.append(memory_id)
        if repo_id_is_null:
            where += " AND repo_id IS NULL"
        elif repo_id is not None:
            where += " AND repo_id = ?"
            params.append(repo_id)
        if event_type:
            where += " AND event_type = ?"
            params.append(cls._normalize_recall_event_type(event_type))
        return where, params

    @classmethod
    def _recall_event_row_to_dict(cls, row: sqlite3.Row) -> Dict[str, Any]:
        event = dict(row)
        event["metadata"] = cls._json_deserialize(event.get("metadata") or "{}") or {}
        return event

    def list_project_ids(self) -> List[str]:
        """List distinct repository/project IDs referenced by stored data."""
        with self._get_db() as conn:
            cursor = conn.execute(
                """
                SELECT repo_id AS id FROM memories WHERE repo_id IS NOT NULL AND repo_id != ''
                UNION
                SELECT repo_id AS id FROM intents WHERE repo_id IS NOT NULL AND repo_id != ''
                UNION
                SELECT id FROM repositories WHERE id IS NOT NULL AND id != ''
                ORDER BY id
                """
            )
            return [row[0] for row in cursor.fetchall()]

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        """Convert SQLite row to dictionary."""
        d = dict(row)

        # Parse JSON fields
        for field in [
            "tags",
            "metadata",
            "source_ids",
            "quality_flags",
            "memory_ids",
            "context",
            "tech_stack",
        ]:
            if field in d and d[field]:
                d[field] = LocalStorage._json_deserialize(d[field])

        return d

    @classmethod
    def _normalize_relationship_evidence(
        cls,
        evidence: Dict[str, Any] = None,
        *,
        strength: float = None,
        created_at: str = None,
        legacy: bool = True,
    ) -> Dict[str, Any]:
        """Return relationship evidence with safe, contract-compatible defaults."""
        evidence = evidence or {}
        confidence = evidence.get("confidence") or "ambiguous"
        if confidence not in cls.RELATIONSHIP_CONFIDENCE_VALUES:
            raise ValueError(
                "Relationship confidence must be one of: "
                + ", ".join(sorted(cls.RELATIONSHIP_CONFIDENCE_VALUES))
            )

        score = evidence.get("confidence_score")
        if score is None:
            score = strength if strength is not None else 0.5

        default_reason = (
            cls.LEGACY_RELATIONSHIP_EVIDENCE_REASON
            if legacy
            else cls.UNSPECIFIED_RELATIONSHIP_EVIDENCE_REASON
        )

        return {
            "confidence": confidence,
            "confidence_score": clamp_score(score),
            "source": evidence.get("source") or ("legacy" if legacy else "unspecified"),
            "source_file": evidence.get("source_file"),
            "source_location": evidence.get("source_location"),
            "reason": evidence.get("reason") or default_reason,
            "created_by": evidence.get("created_by"),
            "created_at": evidence.get("created_at") or created_at,
        }

    @classmethod
    def _relationship_row_to_dict(cls, row: sqlite3.Row) -> Dict[str, Any]:
        """Convert a relationship row to its public shape with nested evidence."""
        d = dict(row)
        evidence_data = {
            "confidence": d.pop("confidence", None),
            "confidence_score": d.pop("confidence_score", None),
            "source": d.pop("source", None),
            "source_file": d.pop("source_file", None),
            "source_location": d.pop("source_location", None),
            "reason": d.pop("reason", None),
            "created_by": d.pop("created_by", None),
            "created_at": d.get("created_at"),
        }
        d["evidence"] = cls._normalize_relationship_evidence(
            evidence_data,
            strength=d.get("strength"),
            created_at=d.get("created_at"),
            legacy=evidence_data["source"] == "legacy",
        )
        return d

    def append_audit_log(
        self,
        event_type: str,
        actor_id: str = None,
        repo_id: str = None,
        target_type: str = None,
        target_id: str = None,
        metadata: Dict[str, Any] = None,
    ) -> str:
        """Append a non-secret audit event."""
        audit_id = self._generate_id(f"{event_type}:{target_id or ''}")
        with self._get_db() as conn:
            conn.execute(
                """
                INSERT INTO audit_logs (
                    id, event_type, actor_id, repo_id, target_type, target_id, metadata
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_id,
                    event_type,
                    actor_id,
                    repo_id,
                    target_type,
                    target_id,
                    self._json_serialize(metadata or {}),
                ),
            )
            conn.commit()
        return audit_id

    def list_audit_logs(
        self,
        actor_id: str = None,
        repo_id: str = None,
        event_type: str = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List audit log entries with optional filters."""
        query = "SELECT * FROM audit_logs WHERE 1=1"
        params: list[Any] = []
        if actor_id:
            query += " AND actor_id = ?"
            params.append(actor_id)
        if repo_id:
            query += " AND repo_id = ?"
            params.append(repo_id)
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        query += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        params.append(limit)

        with self._get_db() as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    # Repository operations
    @repository_registration
    def store_repository(self, repo: Dict[str, Any]) -> str:
        repo_id = repo.get("id") or self._generate_id(repo["name"])

        with self._get_db() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO repositories (
                        id, name, url, description, tech_stack, team_id, metadata, status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        repo_id,
                        repo["name"],
                        repo.get("url"),
                        repo.get("description"),
                        self._json_serialize(repo.get("tech_stack", [])),
                        repo.get("team_id"),
                        self._json_serialize(repo.get("metadata", {})),
                        repo.get("status", "active"),
                    ),
                )
            except sqlite3.IntegrityError as e:
                # A row the store created for itself is a placeholder, not a
                # registration, so explicit registration takes it over instead of
                # colliding with it. Without this, writing one memory would make
                # `repos register` for that project permanently impossible.
                existing = conn.execute(
                    "SELECT * FROM repositories WHERE id = ?", (repo_id,)
                ).fetchone()
                if not is_implicitly_registered(
                    self._row_to_dict(existing) if existing else None
                ):
                    raise ValueError(f"Repository already exists: {repo_id}") from e
                conn.execute(
                    """
                    UPDATE repositories
                    SET name = ?, url = ?, description = ?, tech_stack = ?,
                        team_id = ?, metadata = ?, status = ?
                    WHERE id = ?
                    """,
                    (
                        repo["name"],
                        repo.get("url"),
                        repo.get("description"),
                        self._json_serialize(repo.get("tech_stack", [])),
                        repo.get("team_id"),
                        self._json_serialize(repo.get("metadata", {})),
                        repo.get("status", "active"),
                        repo_id,
                    ),
                )
            conn.commit()
        return repo_id

    def get_repository(self, repo_id: str) -> Optional[Dict[str, Any]]:
        with self._get_db() as conn:
            cursor = conn.execute("SELECT * FROM repositories WHERE id = ?", (repo_id,))
            row = cursor.fetchone()
            return self._row_to_dict(row) if row else None

    def list_repositories(
        self, team_id: str = None, status: str = "active"
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM repositories WHERE 1=1"
        params = []
        if team_id:
            query += " AND team_id = ?"
            params.append(team_id)
        if status and status != "all":
            query += " AND status = ?"
            params.append(status)

        with self._get_db() as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def update_repository(self, repo_id: str, **kwargs) -> bool:
        allowed = {"name", "url", "description", "tech_stack", "metadata", "status"}
        updates = []
        params: list[Any] = []
        for field, value in kwargs.items():
            if field not in allowed or value is None:
                continue
            if field in {"tech_stack", "metadata"}:
                value = self._json_serialize(value)
            updates.append(f"{field} = ?")
            params.append(value)
        if "status" in kwargs:
            if kwargs["status"] == "archived":
                updates.append("archived_at = CURRENT_TIMESTAMP")
            elif kwargs["status"] == "active":
                updates.append("archived_at = NULL")
        if not updates:
            return False
        params.append(repo_id)
        with self._get_db() as conn:
            result = conn.execute(
                f"UPDATE repositories SET {', '.join(updates)} WHERE id = ?", params
            )
            conn.commit()
        return result.rowcount > 0

    def _purge_repository_children(self, repo_id: str) -> list[Dict[str, Any]]:
        """Remove repository-owned rows that are not memory nodes.

        Audit rows intentionally survive a purge so the destructive operation is
        itself auditable.  Every content-bearing child is deleted in one
        transaction and residual verification in ``BaseStorage`` decides whether
        the repository may be removed.
        """
        try:
            with self._get_db() as conn:
                conn.execute("DELETE FROM intents WHERE repo_id = ?", (repo_id,))
                conn.execute("DELETE FROM evidence WHERE repo_id = ?", (repo_id,))
                conn.execute("DELETE FROM sessions WHERE repo_id = ?", (repo_id,))
                conn.execute("DELETE FROM recall_events WHERE repo_id = ?", (repo_id,))
                conn.execute(
                    "DELETE FROM repository_dependencies "
                    "WHERE source_repo_id = ? OR target_repo_id = ?",
                    (repo_id, repo_id),
                )
                conn.commit()
        except Exception as exc:
            return [{"kind": "children", "error": exc.__class__.__name__}]
        return []

    def _delete_repository_record(self, repo_id: str) -> bool:
        with self._get_db() as conn:
            result = conn.execute("DELETE FROM repositories WHERE id = ?", (repo_id,))
            conn.commit()
        return result.rowcount > 0

    def add_repo_dependency(
        self,
        source_id: str,
        target_id: str,
        dep_type: str,
        version: str = None,
        notes: str = None,
    ) -> str:
        if self.get_repository(source_id) is None:
            raise ValueError(f"Repository not found: {source_id}")
        if self.get_repository(target_id) is None:
            raise ValueError(f"Repository not found: {target_id}")

        dep_id = self._generate_id(f"{source_id}-{target_id}-{dep_type}")

        with self._get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO repository_dependencies (
                    id, source_repo_id, target_repo_id, dependency_type, version, notes
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (dep_id, source_id, target_id, dep_type, version, notes),
            )
            conn.commit()
        return dep_id

    def get_repo_dependencies(self, repo_id: str) -> List[Dict[str, Any]]:
        with self._get_db() as conn:
            cursor = conn.execute(
                """
                SELECT target_repo_id as target_id, dependency_type as type, version, notes
                FROM repository_dependencies
                WHERE source_repo_id = ?
            """,
                (repo_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    # Team and User operations
    def store_user(self, user: Dict[str, Any]) -> str:
        user_id = user["id"]
        with self._get_db() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO users (id, username, email, display_name, metadata)
                    VALUES (?, ?, ?, ?, ?)
                """,
                    (
                        user_id,
                        user["username"],
                        user.get("email"),
                        user.get("display_name"),
                        self._json_serialize(user.get("metadata", {})),
                    ),
                )
            except sqlite3.IntegrityError as e:
                raise ValueError(f"User already exists: {user_id}") from e
            conn.commit()
        return user_id

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self._get_db() as conn:
            cursor = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            return self._row_to_dict(row) if row else None

    def store_team(self, team: Dict[str, Any]) -> str:
        team_id = team["id"]
        with self._get_db() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO teams (id, name, description, metadata)
                    VALUES (?, ?, ?, ?)
                """,
                    (
                        team_id,
                        team["name"],
                        team.get("description"),
                        self._json_serialize(team.get("metadata", {})),
                    ),
                )
            except sqlite3.IntegrityError as e:
                raise ValueError(f"Team already exists: {team_id}") from e
            conn.commit()
        return team_id

    def get_team(self, team_id: str) -> Optional[Dict[str, Any]]:
        with self._get_db() as conn:
            cursor = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,))
            row = cursor.fetchone()
            return self._row_to_dict(row) if row else None

    def add_team_member(self, team_id: str, user_id: str) -> bool:
        if self.get_team(team_id) is None or self.get_user(user_id) is None:
            return False

        with self._get_db() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO team_members (team_id, user_id)
                VALUES (?, ?)
            """,
                (team_id, user_id),
            )
            conn.commit()
        return True

    def get_user_teams(self, user_id: str) -> List[Dict[str, Any]]:
        with self._get_db() as conn:
            cursor = conn.execute(
                """
                SELECT t.* FROM teams t
                JOIN team_members tm ON t.id = tm.team_id
                WHERE tm.user_id = ?
            """,
                (user_id,),
            )
            return [self._row_to_dict(row) for row in cursor.fetchall()]
