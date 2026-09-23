"""Regression coverage for truthful, retryable repository purges."""

import threading

from visp_memory.core.remote_storage import RemoteStorage
from visp_memory.core.storage import BaseStorage, LocalStorage, RepositoryPurgedError


class FakePurgeStorage:
    """Small storage double used to exercise the portable purge algorithm."""

    def __init__(self, count=0, *, failed_memory_ids=None, child_failure=False):
        self.repository = {"id": "repo-a"}
        self.memories = {
            f"memory-{index:06d}": {"id": f"memory-{index:06d}", "content": "x"}
            for index in range(count)
        }
        self.failed_memory_ids = set(failed_memory_ids or ())
        self.child_failure = child_failure
        self.deleted_ids = []
        self.page_sizes = []

    def get_repository(self, repo_id):
        return self.repository if repo_id == "repo-a" else None

    def list_memories(self, *, repo_id, status, limit, order_by, after_id=None):
        assert repo_id == "repo-a"
        assert status == "all"
        assert order_by == "id ASC"
        self.page_sizes.append(limit)
        ids = sorted(self.memories)
        if after_id is not None:
            ids = [memory_id for memory_id in ids if memory_id > after_id]
        return [self.memories[memory_id] for memory_id in ids[:limit]]

    def purge_memory(self, memory_id):
        if memory_id in self.failed_memory_ids:
            return False
        self.deleted_ids.append(memory_id)
        self.memories.pop(memory_id, None)
        return True

    def get_active_intents(self, *, repo_id, status):
        assert repo_id == "repo-a"
        assert status == "all"
        return []

    def get_all_relationships(self, *, repo_id):
        assert repo_id == "repo-a"
        return []

    def get_repo_dependencies(self, repo_id):
        assert repo_id == "repo-a"
        return []

    def _purge_repository_children(self, repo_id):
        assert repo_id == "repo-a"
        if self.child_failure:
            return [{"kind": "intent", "id": "intent-1", "error": "injected"}]
        return []

    def _delete_repository_record(self, repo_id):
        assert repo_id == "repo-a"
        self.repository = None
        return True


def test_repository_purge_walks_more_than_100k_records_in_bounded_pages():
    storage = FakePurgeStorage(100_005)

    report = BaseStorage.purge_repository(storage, "repo-a")

    assert report["status"] == "purged"
    assert report["purged_memory_count"] == 100_005
    assert report["residual"] == {}
    assert max(storage.page_sizes) < 100_000
    assert len(storage.deleted_ids) == 100_005
    assert storage.repository is None


def test_repository_purge_keeps_repository_when_memory_or_child_delete_fails():
    storage = FakePurgeStorage(
        3,
        failed_memory_ids={"memory-000001"},
        child_failure=True,
    )

    report = BaseStorage.purge_repository(storage, "repo-a")

    assert report["status"] == "incomplete"
    assert report["failed_memory_ids"] == ["memory-000001"]
    assert report["errors"] == [
        {"kind": "intent", "id": "intent-1", "error": "injected"}
    ]
    assert storage.repository == {"id": "repo-a"}
    assert "memory-000001" in storage.memories


def test_local_repository_purge_keeps_rows_when_vector_delete_fails(tmp_path, monkeypatch):
    storage = LocalStorage(tmp_path)
    storage.store_repository({"id": "repo-a", "name": "Repo A"})
    memory_id = storage.store_memory("retain on vector failure", repo_id="repo-a")

    class FailingCollection:
        def delete(self, *, ids):
            raise RuntimeError("vector unavailable")

    monkeypatch.setattr(storage, "_get_collection", lambda _layer: FailingCollection())

    report = storage.purge_repository("repo-a")

    assert report["status"] == "incomplete"
    assert report["failed_memory_ids"] == [memory_id]
    assert storage.get_memory(memory_id) is not None
    assert storage.get_repository("repo-a") is not None


def test_local_repository_purge_serializes_concurrent_memory_write(tmp_path, monkeypatch):
    storage = LocalStorage(tmp_path)
    storage.store_repository({"id": "repo-a", "name": "Repo A"})
    storage.store_memory("existing", repo_id="repo-a", auto_link=False)
    at_repository_delete = threading.Event()
    write_attempted = threading.Event()
    original_delete = storage._delete_repository_record

    def coordinated_delete(repo_id):
        at_repository_delete.set()
        assert write_attempted.wait(timeout=5)
        return original_delete(repo_id)

    monkeypatch.setattr(storage, "_delete_repository_record", coordinated_delete)
    purge_result = {}
    write_errors = []

    def purge():
        purge_result.update(storage.purge_repository("repo-a"))

    def write():
        assert at_repository_delete.wait(timeout=5)
        write_attempted.set()
        try:
            storage.store_memory("concurrent", repo_id="repo-a", auto_link=False)
        except Exception as exc:  # captured for assertion in the test thread
            write_errors.append(exc)

    purge_thread = threading.Thread(target=purge)
    write_thread = threading.Thread(target=write)
    purge_thread.start()
    write_thread.start()
    purge_thread.join(timeout=10)
    write_thread.join(timeout=10)

    assert not purge_thread.is_alive()
    assert not write_thread.is_alive()
    assert purge_result["status"] == "purged"
    assert len(write_errors) == 1
    assert isinstance(write_errors[0], RepositoryPurgedError)
    assert storage.list_memories(repo_id="repo-a", status="all") == []
    assert storage.get_repository("repo-a") is None

    storage.store_repository({"id": "repo-a", "name": "Repo A recreated"})
    memory_id = storage.store_memory("after recreation", repo_id="repo-a", auto_link=False)
    assert storage.get_memory(memory_id)["repo_id"] == "repo-a"


class _RemoteResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.payload = payload
        self.text = ""

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"unexpected HTTP {self.status_code}")


class _RemoteSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def delete(self, url, params=None):
        self.calls.append((url, params))
        return self.response


def test_remote_repository_purge_preserves_incomplete_server_report():
    storage = RemoteStorage.__new__(RemoteStorage)
    storage.server_url = "http://memory.example"
    storage.session = _RemoteSession(
        _RemoteResponse(
            200,
            {
                "repo_id": "repo-a",
                "status": "incomplete",
                "purged_memory_count": 2,
                "failed_memory_ids": ["memory-3"],
                "residual": {"memories": ["memory-3"]},
                "errors": [],
            },
        )
    )

    report = storage.purge_repository("repo-a")

    assert report["status"] == "incomplete"
    assert report["residual"] == {"memories": ["memory-3"]}
    assert storage.session.calls == [
        (
            "http://memory.example/repos/repo-a",
            {"confirmation": "repo-a"},
        )
    ]
