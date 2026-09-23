import pytest

from visp_memory.core.lifecycle import LifecycleError, MemoryLifecycleManager
from visp_memory.core.storage import LocalStorage


def _manager(tmp_path):
    storage = LocalStorage(tmp_path)
    return storage, MemoryLifecycleManager(storage, tmp_path / "lifecycle.db")


def test_merge_is_previewed_recoverable_and_reversible(tmp_path):
    storage, manager = _manager(tmp_path)
    primary = storage.store_memory(
        "Use structured logging",
        repo_id="repo-a",
        tags=["logging"],
        metadata={"source": "decision"},
        auto_link=False,
    )
    duplicate = storage.store_memory(
        "Use structured logging",
        repo_id="repo-a",
        tags=["operations"],
        metadata={"source": "incident"},
        auto_link=False,
    )
    neighbor = storage.store_memory("Log request IDs", repo_id="repo-a", auto_link=False)
    storage.add_relationship(duplicate, neighbor, "supports")

    preview = manager.preview_merge([primary, duplicate])
    assert preview["exact_duplicate"] is True
    assert preview["estimated_tokens_saved"] > 0

    result = manager.merge([primary, duplicate], actor_id="admin")
    assert storage.get_memory(duplicate)["status"] == "merged"
    assert set(storage.get_memory(primary)["tags"]) == {"logging", "operations"}
    assert any(
        relationship["source_id"] == primary and relationship["target_id"] == neighbor
        for relationship in storage.get_all_relationships(repo_id="repo-a")
    )

    manager.undo_merge(result["operation_id"], actor_id="admin")
    assert storage.get_memory(duplicate)["status"] == "active"
    assert storage.get_memory(primary)["tags"] == ["logging"]


def test_merge_rejects_cross_project_and_temporal_conflicts(tmp_path):
    storage, manager = _manager(tmp_path)
    first = storage.store_memory("Current API is v1", repo_id="one", auto_link=False)
    second = storage.store_memory("Current API is v1", repo_id="two", auto_link=False)
    preview = manager.preview_merge([first, second])
    assert preview["validation_errors"]
    with pytest.raises(LifecycleError):
        manager.merge([first, second], actor_id="admin")


def test_merge_requires_distinct_existing_memories_and_selected_target(tmp_path):
    storage, manager = _manager(tmp_path)
    first = storage.store_memory("First", repo_id="repo-a", auto_link=False)
    second = storage.store_memory("Second", repo_id="repo-a", auto_link=False)

    with pytest.raises(LifecycleError, match="at least two distinct"):
        manager.preview_merge([first, first])
    with pytest.raises(LifecycleError, match="Memory not found: missing"):
        manager.preview_merge([first, "missing"])
    with pytest.raises(LifecycleError, match="canonical memory"):
        manager.preview_merge([first, second], target_id="missing")


def test_merge_preview_reports_layer_status_temporal_and_immutable_errors(tmp_path):
    storage, manager = _manager(tmp_path)
    evidence_id = storage.store_evidence("Source fact", repo_id="repo-a")
    semantic = storage.store_memory(
        "Current API is v1",
        layer="semantic",
        repo_id="repo-a",
        evidence_ids=[evidence_id],
        metadata={"valid_from": "2026-01-01T00:00:00+00:00"},
        auto_link=False,
    )
    episodic = storage.store_memory(
        "Current API is v2",
        repo_id="repo-a",
        metadata={"valid_from": "2026-02-01T00:00:00+00:00"},
        auto_link=False,
    )
    assert storage.update_memory(episodic, status="deleted")

    preview = manager.preview_merge(
        [semantic, episodic], target_id=semantic, target_content="Current API is v3"
    )

    assert preview["exact_duplicate"] is False
    assert "Memories from different layers cannot be merged" in preview["validation_errors"]
    assert any("Only active or archived" in error for error in preview["validation_errors"])
    assert any("Temporally different facts" in error for error in preview["validation_errors"])
    assert any("immutable" in error for error in preview["validation_errors"])
    assert preview["warnings"] == ["Semantic merges require explicit human review"]


def test_merge_failure_restores_memories_relationships_and_operation_status(tmp_path, monkeypatch):
    class FailingSourceStorage(LocalStorage):
        fail_id = None

        def update_memory(self, memory_id, **kwargs):
            if memory_id == self.fail_id and kwargs.get("status") == "merged":
                raise RuntimeError("simulated source write failure")
            return super().update_memory(memory_id, **kwargs)

    storage = FailingSourceStorage(tmp_path)
    manager = MemoryLifecycleManager(storage, tmp_path / "lifecycle.db")
    target = storage.store_memory(
        "Same content", repo_id="repo-a", tags=["target"], auto_link=False
    )
    source = storage.store_memory(
        "Same content", repo_id="repo-a", tags=["source"], auto_link=False
    )
    neighbor = storage.store_memory("Neighbor", repo_id="repo-a", auto_link=False)
    original_relationship = storage.add_relationship(source, neighbor, "supports")
    storage.fail_id = source
    monkeypatch.setattr("visp_memory.core.lifecycle.secrets.token_hex", lambda _size: "rollback")

    with pytest.raises(LifecycleError, match="rolled back"):
        manager.merge([target, source], actor_id="admin")

    assert storage.get_memory(target)["status"] == "active"
    assert storage.get_memory(target)["tags"] == ["target"]
    assert storage.get_memory(source)["status"] == "active"
    relationships = storage.get_all_relationships(repo_id="repo-a")
    assert [relationship["id"] for relationship in relationships] == [original_relationship]
    assert manager.get_operation("mrg_rollback")["status"] == "failed"


def test_undo_merge_rejects_missing_pending_and_purged_operations(tmp_path):
    storage, manager = _manager(tmp_path)
    with pytest.raises(LifecycleError, match="not found"):
        manager.undo_merge("missing", actor_id="admin")

    target = storage.store_memory("Same", repo_id="repo-a", auto_link=False)
    source = storage.store_memory("Same", repo_id="repo-a", auto_link=False)
    result = manager.merge([target, source], actor_id="admin")
    manager._update_operation(result["operation_id"], status="pending")
    with pytest.raises(LifecycleError, match="Only completed"):
        manager.undo_merge(result["operation_id"], actor_id="admin")

    manager._update_operation(result["operation_id"], status="completed")
    assert storage.delete_memory(source)
    with pytest.raises(LifecycleError, match="purged"):
        manager.undo_merge(result["operation_id"], actor_id="admin")


def test_restore_handles_non_deleted_rows_and_invalid_previous_status(tmp_path):
    storage, manager = _manager(tmp_path)
    memory_id = storage.store_memory("Restore me", repo_id="repo-a", auto_link=False)

    assert manager.restore("missing") is False
    assert manager.restore(memory_id) is False
    assert manager.soft_delete(memory_id, actor_id="owner", reason="temporary")
    assert storage.update_memory(
        memory_id,
        metadata={"deletion": {"previous_status": "merged"}},
    )

    assert manager.restore(memory_id) is True
    restored = storage.get_memory(memory_id)
    assert restored["status"] == "active"
    assert "deletion" not in restored["metadata"]


def test_purge_preview_cascades_revocation_and_reports_protected_rows(tmp_path):
    storage, manager = _manager(tmp_path)
    source = storage.store_memory("Source", repo_id="repo-a", auto_link=False)
    derivative = storage.store_memory(
        "Derived", repo_id="repo-a", source_ids=[source], auto_link=False
    )
    assert manager.soft_delete(source, actor_id="owner", reason="remove source")

    preview = manager.purge_preview([source])
    assert preview["purgeable_ids"] == [source]
    assert preview["cascade"] == [{"id": derivative, "derived_from": [source], "action": "revoke"}]

    result = manager.purge([source])
    assert result["purged_ids"] == [source]
    assert result["revoked_ids"] == [derivative]
    assert storage.get_memory(source) is None
    assert storage.get_memory(derivative)["epistemic_status"] == "revoked"

    active = storage.store_memory("Still active", repo_id="repo-a", auto_link=False)
    pinned = storage.store_memory(
        "Pinned", repo_id="repo-a", metadata={"pinned": True}, auto_link=False
    )
    held = storage.store_memory(
        "Held", repo_id="repo-a", metadata={"hold": "legal"}, auto_link=False
    )
    blocked_preview = manager.purge_preview([active, pinned, held, "missing"])
    assert blocked_preview["missing_ids"] == ["missing"]
    assert {item["id"] for item in blocked_preview["blocked"]} == {active, pinned, held}


def test_retention_and_consistency_reports_are_actionable(tmp_path):
    from datetime import datetime, timedelta, timezone

    storage, manager = _manager(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    memory_id = storage.store_memory(
        "Expired record",
        repo_id="repo-a",
        created_at=old,
        auto_link=False,
    )
    assert storage.update_memory(memory_id, status="merged", metadata={"merged_at": old})

    preview = manager.retention_preview("repo-a", retention_days=1)
    assert preview["retention_days"] == 30
    assert preview["purgeable_ids"] == [memory_id]
    result = manager.execute_retention("repo-a", retention_days=1)
    assert result["purged_ids"] == [memory_id]

    broken = storage.store_memory("Broken lineage", repo_id="repo-a", auto_link=False)
    assert storage.update_memory(broken, status="merged", metadata={"merged_into": "missing"})
    report = manager.verify_consistency("repo-a")
    assert report["healthy"] is False
    assert report["broken_merge_lineage_ids"] == [broken]


def test_semantic_duplicate_merge_undo_preserves_immutable_content(tmp_path):
    storage, manager = _manager(tmp_path)
    evidence_one = storage.store_evidence("Evidence one", repo_id="repo-a")
    evidence_two = storage.store_evidence("Evidence two", repo_id="repo-a")
    target = storage.store_memory(
        "Immutable fact",
        layer="semantic",
        repo_id="repo-a",
        evidence_ids=[evidence_one],
        tags=["target"],
        auto_link=False,
    )
    source = storage.store_memory(
        "Immutable fact",
        layer="semantic",
        repo_id="repo-a",
        evidence_ids=[evidence_two],
        tags=["source"],
        auto_link=False,
    )

    result = manager.merge([target, source], actor_id="admin")
    assert manager.undo_merge(result["operation_id"], actor_id="admin")["status"] == "undone"
    assert storage.get_memory(target)["content"] == "Immutable fact"
    assert storage.get_memory(source)["content"] == "Immutable fact"
    assert storage.get_memory(source)["status"] == "active"


def test_merge_undo_rejects_sources_changed_after_merge(tmp_path):
    storage, manager = _manager(tmp_path)
    primary = storage.store_memory("Keep this", repo_id="repo-a", auto_link=False)
    duplicate = storage.store_memory("Keep this", repo_id="repo-a", auto_link=False)
    result = manager.merge([primary, duplicate], actor_id="admin")

    storage.update_memory(duplicate, status="archived")

    with pytest.raises(LifecycleError, match="source has changed"):
        manager.undo_merge(result["operation_id"], actor_id="admin")


def test_soft_delete_restore_and_confirmed_purge(tmp_path):
    storage, manager = _manager(tmp_path)
    memory_id = storage.store_memory("Temporary note", repo_id="repo-a", auto_link=False)
    assert manager.soft_delete(memory_id, actor_id="owner", reason="obsolete")
    assert storage.get_memory(memory_id)["status"] == "deleted"
    assert memory_id not in {memory["id"] for memory in storage.list_memories(repo_id="repo-a")}

    assert manager.restore(memory_id)
    assert storage.get_memory(memory_id)["status"] == "active"
    with pytest.raises(LifecycleError):
        manager.purge([memory_id])

    manager.soft_delete(memory_id, actor_id="owner", reason="obsolete")
    result = manager.purge([memory_id])
    assert result["purged_ids"] == [memory_id]
    assert storage.get_memory(memory_id) is None
