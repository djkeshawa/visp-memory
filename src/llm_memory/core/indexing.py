"""Embedding index inspection and rebuild helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReindexScope:
    """Filters that select memories for embedding rebuilds."""

    repo_id: str | None = None
    layer: str | None = None
    category: str | None = None

    def as_filter_dict(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "repo_id": self.repo_id,
                "layer": self.layer,
                "category": self.category,
            }.items()
            if value
        }


@dataclass
class EmbeddingIndexReport:
    """Non-secret embedding index compatibility summary."""

    storage_backend: str
    provider: str
    effective_provider: str | None
    model: str | None
    dimension: int | None
    status: str
    message: str
    scope: dict[str, str] = field(default_factory=dict)
    matched_memories: int = 0
    indexed_memories: int | None = None
    active_collections: list[str] = field(default_factory=list)
    legacy_collections: list[str] = field(default_factory=list)
    needs_reindex: bool = False


@dataclass
class ReindexResult:
    """Result of a dry-run or synchronous embedding rebuild."""

    dry_run: bool
    status: str
    message: str
    scope: dict[str, str]
    matched_memories: int
    reindexed_memories: int = 0
    failed_memories: int = 0
    dimension: int | None = None
    active_collections: list[str] = field(default_factory=list)
    legacy_collections: list[str] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)


def inspect_embedding_index(
    storage: Any,
    *,
    storage_backend: str,
    provider: str,
    effective_provider: str | None,
    model: str | None,
    dimension: int | None,
    scope: ReindexScope | None = None,
) -> EmbeddingIndexReport:
    """Return a storage-aware embedding index report when supported."""
    scope = scope or ReindexScope()
    if hasattr(storage, "inspect_embedding_index"):
        return storage.inspect_embedding_index(
            storage_backend=storage_backend,
            provider=provider,
            effective_provider=effective_provider,
            model=model,
            dimension=dimension,
            scope=scope,
        )

    return EmbeddingIndexReport(
        storage_backend=storage_backend,
        provider=provider,
        effective_provider=effective_provider,
        model=model,
        dimension=dimension,
        status="unknown",
        message="This storage backend does not expose embedding index diagnostics.",
        scope=scope.as_filter_dict(),
    )


def rebuild_embedding_index(
    storage: Any,
    *,
    scope: ReindexScope | None = None,
    dry_run: bool = True,
) -> ReindexResult:
    """Rebuild embeddings for a scoped set of memories when supported."""
    scope = scope or ReindexScope()
    if hasattr(storage, "rebuild_embedding_index"):
        return storage.rebuild_embedding_index(scope=scope, dry_run=dry_run)

    return ReindexResult(
        dry_run=dry_run,
        status="unsupported",
        message="This storage backend does not support embedding rebuilds.",
        scope=scope.as_filter_dict(),
        matched_memories=0,
    )


def to_plain_dict(value: Any) -> dict[str, Any]:
    """Convert dataclass reports into JSON-ready dictionaries."""
    return asdict(value)
