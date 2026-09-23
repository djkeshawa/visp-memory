"""Recoverable memory merge, deletion, purge, and retention workflows."""

from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterator, Optional

from visp_memory.core.clock import parse_utc, utc_now, utc_now_iso
from visp_memory.core.tokens import estimate_tokens
from visp_memory.quality.secrets import redact_for_storage


class LifecycleError(ValueError):
    """Raised when a requested lifecycle transition is unsafe or invalid."""


class MemoryLifecycleManager:
    """Coordinate lifecycle operations consistently across storage backends."""

    def __init__(self, storage, operation_db: Path):
        self.storage = storage
        self.operation_db = Path(operation_db)
        self.operation_db.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.operation_db, timeout=30.0)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout=30000")
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._db() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS memory_merge_operations (
                    id TEXT PRIMARY KEY,
                    repo_id TEXT,
                    target_id TEXT NOT NULL,
                    actor_id TEXT,
                    status TEXT NOT NULL,
                    snapshot TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    undone_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_merge_target
                    ON memory_merge_operations(target_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_merge_repo
                    ON memory_merge_operations(repo_id, created_at);
                """
            )
            connection.commit()

    @staticmethod
    def _snapshot(memory: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": memory["id"],
            "layer": memory.get("layer"),
            "content": memory.get("content", ""),
            "importance": memory.get("importance", 0.5),
            "tags": memory.get("tags") or [],
            "metadata": memory.get("metadata") or {},
            "status": memory.get("status", "active"),
        }

    def _load_group(
        self, memory_ids: list[str], target_id: Optional[str] = None
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        unique_ids = list(dict.fromkeys(memory_ids))
        if len(unique_ids) < 2:
            raise LifecycleError("Select at least two distinct memories")
        memories = []
        for memory_id in unique_ids:
            memory = self.storage.get_memory(memory_id)
            if not memory:
                raise LifecycleError(f"Memory not found: {memory_id}")
            memories.append(memory)
        selected_target = target_id or unique_ids[0]
        target = next((memory for memory in memories if memory["id"] == selected_target), None)
        if not target:
            raise LifecycleError("The canonical memory must be one of the selected memories")
        return memories, target

    def preview_merge(
        self,
        memory_ids: list[str],
        *,
        target_id: Optional[str] = None,
        target_content: Optional[str] = None,
    ) -> dict[str, Any]:
        memories, target = self._load_group(memory_ids, target_id)
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
        relationships = self.storage.get_all_relationships(repo_id=target.get("repo_id"))
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

    def merge(
        self,
        memory_ids: list[str],
        *,
        actor_id: str,
        target_id: Optional[str] = None,
        target_content: Optional[str] = None,
    ) -> dict[str, Any]:
        preview = self.preview_merge(
            memory_ids, target_id=target_id, target_content=target_content
        )
        if preview["validation_errors"]:
            raise LifecycleError("; ".join(preview["validation_errors"]))
        memories, target = self._load_group(memory_ids, preview["target_id"])
        operation_id = f"mrg_{secrets.token_hex(10)}"
        snapshots = {memory["id"]: self._snapshot(memory) for memory in memories}
        source_ids = [memory["id"] for memory in memories if memory["id"] != target["id"]]
        created_relationship_ids: list[str] = []
        snapshot = {
            "memories": snapshots,
            "created_relationship_ids": created_relationship_ids,
        }
        self._record_operation(
            operation_id,
            repo_id=target.get("repo_id"),
            target_id=target["id"],
            actor_id=actor_id,
            status="pending",
            snapshot=snapshot,
        )

        try:
            created_relationship_ids.extend(
                self._retarget_relationships(target["id"], source_ids, target.get("repo_id"))
            )
            target_metadata = dict(target.get("metadata") or {})
            target_metadata["merged_source_ids"] = list(
                dict.fromkeys(
                    [
                        *(target_metadata.get("merged_source_ids") or []),
                        *preview["merged_source_ids"],
                    ]
                )
            )
            target_metadata["merge_operation_ids"] = [
                *(target_metadata.get("merge_operation_ids") or []),
                operation_id,
            ]
            target_updates = {
                "importance": max(float(memory.get("importance", 0.5)) for memory in memories),
                "tags": preview["merged_tags"],
                "metadata": target_metadata,
                "status": "active",
            }
            if target.get("layer") != "semantic":
                target_updates["content"] = preview["target_content"]
            self.storage.update_memory(target["id"], **target_updates)
            for memory in memories:
                if memory["id"] == target["id"]:
                    continue
                metadata = dict(memory.get("metadata") or {})
                metadata.update(
                    {
                        "merged_into": target["id"],
                        "merge_operation_id": operation_id,
                        "merged_at": utc_now_iso(),
                    }
                )
                self.storage.update_memory(memory["id"], status="merged", metadata=metadata)
            snapshot["post_merge"] = {
                memory["id"]: self._snapshot(self.storage.get_memory(memory["id"]))
                for memory in memories
            }
        except Exception as error:
            self._restore_snapshots(snapshots)
            for relationship_id in created_relationship_ids:
                self.storage.delete_relationship(relationship_id)
            self._update_operation(operation_id, status="failed", snapshot=snapshot)
            raise LifecycleError("Merge failed and was rolled back") from error

        self._update_operation(operation_id, status="completed", snapshot=snapshot)
        return {**preview, "operation_id": operation_id, "status": "completed"}

    def undo_merge(self, operation_id: str, *, actor_id: str) -> dict[str, Any]:
        operation = self.get_operation(operation_id)
        if not operation:
            raise LifecycleError("Merge operation not found")
        if operation["status"] != "completed":
            raise LifecycleError("Only completed merge operations can be undone")
        snapshot = operation["snapshot"]
        target_id = operation["target_id"]
        for memory_id in snapshot["memories"]:
            current = self.storage.get_memory(memory_id)
            if not current:
                raise LifecycleError("Merge cannot be undone after a memory has been purged")
            expected = (snapshot.get("post_merge") or {}).get(memory_id)
            if expected and self._snapshot(current) != expected:
                role = "target" if memory_id == target_id else "source"
                raise LifecycleError(f"Merge cannot be undone after its {role} has changed")
            metadata = current.get("metadata") or {}
            if memory_id == target_id:
                if operation_id not in (metadata.get("merge_operation_ids") or []):
                    raise LifecycleError("Merge cannot be undone after its target has changed")
            elif (
                current.get("status") != "merged"
                or metadata.get("merge_operation_id") != operation_id
                or metadata.get("merged_into") != target_id
            ):
                raise LifecycleError("Merge cannot be undone after a source has changed")
        self._restore_snapshots(snapshot["memories"])
        for relationship_id in snapshot.get("created_relationship_ids", []):
            self.storage.delete_relationship(relationship_id)
        self._update_operation(operation_id, status="undone", undone=True)
        return {
            "operation_id": operation_id,
            "status": "undone",
            "actor_id": actor_id,
            "memory_ids": list(snapshot["memories"]),
        }

    def soft_delete(self, memory_id: str, *, actor_id: str, reason: str) -> bool:
        memory = self.storage.get_memory(memory_id)
        if not memory:
            return False
        metadata = dict(memory.get("metadata") or {})
        metadata["deletion"] = {
            "deleted_at": utc_now_iso(),
            "deleted_by": actor_id,
            "reason": reason,
            "previous_status": memory.get("status", "active"),
        }
        return self.storage.update_memory(memory_id, status="deleted", metadata=metadata)

    def restore(self, memory_id: str) -> bool:
        memory = self.storage.get_memory(memory_id)
        if not memory or memory.get("status") != "deleted":
            return False
        metadata = dict(memory.get("metadata") or {})
        deletion = metadata.pop("deletion", {})
        previous_status = deletion.get("previous_status", "active")
        if previous_status not in {"active", "archived"}:
            previous_status = "active"
        return self.storage.update_memory(memory_id, status=previous_status, metadata=metadata)

    def purge_preview(self, memory_ids: list[str]) -> dict[str, Any]:
        requested = list(dict.fromkeys(memory_ids))
        memories = [self.storage.get_memory(memory_id) for memory_id in requested]
        missing = [memory_id for memory_id, memory in zip(requested, memories) if not memory]
        present = [memory for memory in memories if memory]
        blocked: list[dict[str, str]] = []
        active = self.storage.list_memories(status="active", limit=100000)
        for memory in present:
            metadata = memory.get("metadata") or {}
            if memory.get("status") == "active":
                blocked.append({"id": memory["id"], "reason": "Active memories cannot be purged"})
            elif metadata.get("pinned") or metadata.get("hold"):
                blocked.append({"id": memory["id"], "reason": "Memory is pinned or held"})

        purgeable = [
            memory["id"]
            for memory in present
            if memory["id"] not in {item["id"] for item in blocked}
        ]

        # Being cited as provenance used to block the purge outright, which made
        # a legitimate deletion request simply fail while the content stayed
        # (MG-032). A derivative is not a reason to keep the source — it is a
        # consequence of removing it. So the purge proceeds and the derived
        # beliefs are revoked with it: their basis is gone, so they can no longer
        # be asserted, but they stay visible and auditable rather than being
        # silently destroyed alongside content nobody asked to delete.
        purging = set(purgeable)
        cascade = [
            {
                "id": candidate["id"],
                "derived_from": sorted(
                    purging.intersection(candidate.get("source_ids") or [])
                ),
                "action": "revoke",
            }
            for candidate in active
            if purging.intersection(candidate.get("source_ids") or [])
        ]

        return {
            "requested_ids": requested,
            "purgeable_ids": purgeable,
            "missing_ids": missing,
            "blocked": blocked,
            "cascade": cascade,
            "estimated_content_bytes": sum(
                len(memory.get("content", "").encode("utf-8"))
                for memory in present
                if memory["id"] in purgeable
            ),
        }

    def purge(self, memory_ids: list[str]) -> dict[str, Any]:
        preview = self.purge_preview(memory_ids)
        if preview["blocked"]:
            raise LifecycleError("One or more memories are not safe to purge")

        # Revoke derivatives before removing what they were derived from, so no
        # window exists where a belief is still asserted while its basis is gone.
        revoked = [
            entry["id"]
            for entry in preview.get("cascade", [])
            if self._revoke_derived(entry["id"], entry["derived_from"])
        ]

        # Prefer a backend's explicit purge. On remote storage `delete_memory`
        # is a soft delete, so purging through it left the content on the server
        # while reporting success (MG-035). Local backends have no separate
        # purge because their delete already removes the row.
        purge_one = getattr(self.storage, "purge_memory", None) or self.storage.delete_memory

        purged = [
            memory_id for memory_id in preview["purgeable_ids"] if purge_one(memory_id)
        ]
        return {**preview, "purged_ids": purged, "revoked_ids": revoked}

    def _revoke_derived(self, memory_id: str, derived_from: list[str]) -> bool:
        """Mark a belief unsupported because the memory it came from was purged.

        Revoked rather than deleted: the caller asked to remove the source, not
        everything downstream of it. Destroying derivatives would delete content
        nobody named, and keeping them asserted would leave a claim standing on a
        basis that no longer exists.
        """
        from visp_memory.core.beliefs import EpistemicStatus

        memory = self.storage.get_memory(memory_id)
        if not memory:
            return False

        metadata = dict(memory.get("metadata") or {})
        metadata["revocation"] = {
            "reason": "source memory was purged",
            "purged_sources": list(derived_from),
        }
        return self.storage.update_memory(
            memory_id,
            epistemic_status=EpistemicStatus.REVOKED.value,
            metadata=metadata,
        )

    def retention_preview(self, repo_id: str, *, retention_days: int = 30) -> dict[str, Any]:
        cutoff = utc_now() - timedelta(days=max(30, retention_days))
        candidates: list[str] = []
        for memory in self.storage.list_memories(repo_id=repo_id, status="all", limit=100000):
            if memory.get("status") not in {"deleted", "merged", "superseded"}:
                continue
            metadata = memory.get("metadata") or {}
            timestamp = (
                (metadata.get("deletion") or {}).get("deleted_at")
                or metadata.get("merged_at")
                or metadata.get("invalid_at")
                or memory.get("created_at")
            )
            parsed = parse_utc(timestamp)
            if parsed and parsed <= cutoff:
                candidates.append(memory["id"])
        return {
            "repo_id": repo_id,
            "retention_days": max(30, retention_days),
            **self.purge_preview(candidates),
        }

    def execute_retention(self, repo_id: str, *, retention_days: int = 30) -> dict[str, Any]:
        preview = self.retention_preview(repo_id, retention_days=retention_days)
        if preview["blocked"]:
            raise LifecycleError("Retention candidates include protected provenance")
        result = self.purge(preview["purgeable_ids"])
        return {**preview, "purged_ids": result["purged_ids"]}

    def verify_consistency(self, repo_id: Optional[str] = None) -> dict[str, Any]:
        memories = self.storage.list_memories(repo_id=repo_id, status="all", limit=100000)
        memory_by_id = {memory["id"]: memory for memory in memories}
        orphan_relationships = []
        for relationship in self.storage.get_all_relationships(repo_id=repo_id):
            if (
                relationship.get("source_id") not in memory_by_id
                or relationship.get("target_id") not in memory_by_id
            ):
                orphan_relationships.append(relationship.get("id"))
        broken_merge_lineage = []
        for memory in memories:
            if memory.get("status") != "merged":
                continue
            target_id = (memory.get("metadata") or {}).get("merged_into")
            if not target_id or target_id not in memory_by_id:
                broken_merge_lineage.append(memory["id"])
        active_ids = {
            memory["id"]
            for memory in self.storage.list_memories(
                repo_id=repo_id, status="active", limit=100000
            )
        }
        inactive_in_active_list = [
            memory_id
            for memory_id in active_ids
            if memory_by_id.get(memory_id, {}).get("status") != "active"
        ]
        return {
            "repo_id": repo_id,
            "checked_memories": len(memories),
            "orphan_relationship_ids": orphan_relationships,
            "broken_merge_lineage_ids": broken_merge_lineage,
            "inactive_in_active_list_ids": inactive_in_active_list,
            "healthy": not (
                orphan_relationships or broken_merge_lineage or inactive_in_active_list
            ),
            "checked_at": utc_now_iso(),
        }

    def get_operation(self, operation_id: str) -> Optional[dict[str, Any]]:
        with self._db() as connection:
            row = connection.execute(
                "SELECT * FROM memory_merge_operations WHERE id = ?", (operation_id,)
            ).fetchone()
        if not row:
            return None
        return {
            **dict(row),
            "snapshot": json.loads(row["snapshot"]),
        }

    def _record_operation(self, operation_id: str, **values: Any) -> None:
        with self._db() as connection:
            connection.execute(
                """
                INSERT INTO memory_merge_operations (
                    id, repo_id, target_id, actor_id, status, snapshot, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    values.get("repo_id"),
                    values["target_id"],
                    values.get("actor_id"),
                    values["status"],
                    json.dumps(values["snapshot"]),
                    utc_now_iso(),
                ),
            )
            connection.commit()

    def _update_operation(
        self,
        operation_id: str,
        *,
        status: str,
        snapshot: Optional[dict[str, Any]] = None,
        undone: bool = False,
    ) -> None:
        with self._db() as connection:
            connection.execute(
                """
                UPDATE memory_merge_operations
                SET status = ?, snapshot = COALESCE(?, snapshot), undone_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    json.dumps(snapshot) if snapshot is not None else None,
                    utc_now_iso() if undone else None,
                    operation_id,
                ),
            )
            connection.commit()

    def _restore_snapshots(self, snapshots: dict[str, dict[str, Any]]) -> None:
        for memory_id, snapshot in snapshots.items():
            updates = {
                "importance": snapshot["importance"],
                "tags": snapshot["tags"],
                "metadata": snapshot["metadata"],
                "status": snapshot["status"],
            }
            # Semantic belief text is append-only. Exact duplicate merges do not
            # change it, so undo only restores mutable lifecycle fields and never
            # routes the original content through the generic update API.
            if snapshot.get("layer") != "semantic":
                updates["content"] = snapshot["content"]
            self.storage.update_memory(memory_id, **updates)

    def _retarget_relationships(
        self, target_id: str, source_ids: list[str], repo_id: Optional[str]
    ) -> list[str]:
        source_set = set(source_ids)
        created: list[str] = []
        seen: set[tuple[str, str, str]] = set()
        for relationship in self.storage.get_all_relationships(repo_id=repo_id):
            source_id = relationship.get("source_id")
            related_id = relationship.get("target_id")
            if source_id not in source_set and related_id not in source_set:
                continue
            new_source = target_id if source_id in source_set else source_id
            new_target = target_id if related_id in source_set else related_id
            relation = relationship.get("relationship", "related_to")
            key = (new_source, new_target, relation)
            if not new_source or not new_target or new_source == new_target or key in seen:
                continue
            seen.add(key)
            try:
                relationship_id = self.storage.add_relationship(
                    new_source,
                    new_target,
                    relation,
                    strength=relationship.get("strength", 1.0),
                    evidence=relationship.get("evidence"),
                )
            except TypeError:
                relationship_id = self.storage.add_relationship(
                    new_source,
                    new_target,
                    relation,
                    strength=relationship.get("strength", 1.0),
                )
            created.append(relationship_id)
        return created
