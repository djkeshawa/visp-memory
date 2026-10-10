"""Whole-layer dedup must not pair rows a merge is not allowed to collapse.

The per-layer vector collection holds every repository's rows and every
lifecycle state. ``dedup --merge`` hard-deletes all but the first row of each
group, so grouping on cosine similarity alone deleted another repository's
memory, or deleted an active successor while keeping its superseded
predecessor. Real LocalStorage; only the collection is substituted, so the
vectors are controlled exactly.
"""

import pytest

from visp_memory.core.storage import LocalStorage
from visp_memory.quality.dedup import Deduplicator

TEXT = "Run make test before pushing; CI does not run lint."


class _Collection:
    def __init__(self, ids):
        self.ids = ids

    def get(self, include=None):
        return {
            "ids": list(self.ids),
            "embeddings": [[1.0, 0.0] for _ in self.ids],
            "metadatas": [{} for _ in self.ids],
            "documents": [TEXT for _ in self.ids],
        }

    def delete(self, ids):
        pass


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(tmp_path / "data")


def _store(storage, **kwargs):
    kwargs.setdefault("repo_id", "repo-a")
    return storage.store_memory(content=TEXT, layer="episodic", auto_link=False, **kwargs)


def _groups(storage, ids):
    storage._get_collection = lambda layer: _Collection(ids)
    report = Deduplicator(storage).find_duplicates(layer="episodic", threshold=0.9)
    assert report.determined
    return [[mem["id"] for mem in group] for group in report.duplicates]


def test_identical_rows_in_different_repositories_are_not_grouped(storage):
    a = _store(storage, repo_id="repo-a")
    b = _store(storage, repo_id="repo-b")

    assert _groups(storage, [a, b]) == []


def test_identical_rows_in_different_scopes_are_not_grouped(storage):
    prod = _store(storage, metadata={"environment": ["prod"]})
    dev = _store(storage, metadata={"environment": ["dev"]})

    assert _groups(storage, [prod, dev]) == []


def test_non_active_rows_are_not_grouped(storage):
    superseded = _store(storage, status="superseded")
    active = _store(storage)

    assert _groups(storage, [superseded, active]) == []


def test_same_repo_same_scope_active_rows_are_still_grouped(storage):
    a = _store(storage)
    b = _store(storage)
    other_repo = _store(storage, repo_id="repo-b")

    assert _groups(storage, [a, other_repo, b]) == [[a, b]]


def test_merge_refuses_ids_spanning_repositories_and_deletes_nothing(storage):
    a = _store(storage, repo_id="repo-a")
    b = _store(storage, repo_id="repo-b")

    with pytest.raises(ValueError, match="different repositories"):
        Deduplicator(storage).merge_memories([a, b])

    assert storage.get_memory(b) is not None
    assert storage.get_memory(b)["status"] == "active"


def test_merge_refuses_a_non_active_primary_and_deletes_nothing(storage):
    superseded = _store(storage, status="superseded")
    active = _store(storage)

    with pytest.raises(ValueError, match="not 'active'"):
        Deduplicator(storage).merge_memories([superseded, active])

    assert storage.get_memory(active)["status"] == "active"
