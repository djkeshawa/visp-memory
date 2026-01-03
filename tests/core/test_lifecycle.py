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
