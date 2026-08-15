"""`visp-memory dedup` said "No duplicates found." on checks that never ran.

`Deduplicator.find_duplicates` returned a bare `[]` from three places that are
not findings at all:

* the analysis dependencies (scikit-learn / numpy) are not installed;
* `_get_collection` handed back `None`, which is what happens whenever ChromaDB
  is absent or embeddings are noop — i.e. the default `sqlite` install;
* the backend exposes no collection to scan, so whole-layer dedup was logged as
  "not supported ... For now, just return empty".

`Memory.deduplicate()` passes no content, so the public API and the CLI always
took one of those paths on a default install and printed a green all-clear. An
operator acting on that all-clear is acting on a check that never happened.

These tests pin the distinction: checked-and-clean, checked-and-found-N, and
could-not-check-because-X are three different answers.
"""

from typing import Any, Dict, List, Optional

import pytest

from visp_memory.quality import dedup as dedup_module
from visp_memory.quality.dedup import Deduplicator, DedupReport


class _CollectionlessStorage:
    """A backend with no vector collection — Neo4j/ArcadeDB shape."""

    def __init__(self, results: Optional[List[Dict[str, Any]]] = None):
        self._results = results or []

    def search_memories(self, **kwargs) -> List[Dict[str, Any]]:
        return self._results


class _ChromaStorage:
    """LocalStorage shape: exposes `_get_collection`, which may return None."""

    def __init__(self, collection=None):
        self._collection = collection

    def _get_collection(self, layer):
        return self._collection

    def get_memory(self, memory_id: str) -> Dict[str, Any]:
        return {"id": memory_id, "content": f"content for {memory_id}"}


class _Collection:
    def __init__(self, data=None, get_error=None, query_result=None, query_error=None):
        self._data = data
        self._get_error = get_error
        self._query_result = query_result
        self._query_error = query_error

    def get(self, include=None):
        if self._get_error:
            raise self._get_error
        return self._data

    def query(self, **kwargs):
        if self._query_error:
            raise self._query_error
        return self._query_result


# ---------------------------------------------------------------------------
# The report type itself
# ---------------------------------------------------------------------------


def test_a_clean_check_and_an_impossible_check_are_not_equal():
    assert DedupReport.clear() != DedupReport.undetermined("chromadb is not installed")


def test_undetermined_is_never_clean():
    report = DedupReport.undetermined("chromadb is not installed")

    assert report.determined is False
    assert report.is_clean is False
    assert report.found_any is False
    assert report.reason == "chromadb is not installed"


def test_clear_is_clean_and_found_nothing():
    report = DedupReport.clear()

    assert report.determined is True
    assert report.is_clean is True
    assert report.found_any is False
    assert report.count == 0
    assert report.reason is None


def test_checked_with_findings_reports_the_count():
    report = DedupReport.checked([{"id": "a"}, {"id": "b"}])

    assert report.determined is True
    assert report.is_clean is False
    assert report.found_any is True
    assert report.count == 2


def test_the_report_cannot_be_mistaken_for_an_empty_list():
    """`if not result:` was the shape that hid the bug. It must not silently work."""
    with pytest.raises(TypeError):
        len(DedupReport.undetermined("nope"))


# ---------------------------------------------------------------------------
# The three silent returns
# ---------------------------------------------------------------------------


def test_missing_analysis_dependencies_is_reported_not_swallowed(monkeypatch):
    monkeypatch.setattr(dedup_module, "SKLEARN_AVAILABLE", False)
    monkeypatch.setattr(dedup_module, "NUMPY_AVAILABLE", False)

    report = Deduplicator(_ChromaStorage(_Collection())).find_duplicates()

    assert report.determined is False
    assert "scikit-learn" in report.reason


def test_no_vector_collection_is_reported_not_swallowed():
    """The default sqlite install: no ChromaDB, or noop embeddings, so no collection."""
    report = Deduplicator(_ChromaStorage(collection=None)).find_duplicates(layer="episodic")

    assert report.determined is False
    assert report.is_clean is False
    assert "episodic" in report.reason
    assert "ChromaDB" in report.reason


def test_backend_without_batch_support_is_reported_not_swallowed():
    report = Deduplicator(_CollectionlessStorage()).find_duplicates()

    assert report.determined is False
    assert "_CollectionlessStorage" in report.reason


# ---------------------------------------------------------------------------
# Failures inside a check that did start
# ---------------------------------------------------------------------------


def test_an_unreadable_collection_is_undetermined():
    collection = _Collection(get_error=RuntimeError("index corrupt"))

    report = Deduplicator(_ChromaStorage(collection)).find_duplicates(layer="semantic")

    assert report.determined is False
    assert "index corrupt" in report.reason


def test_a_collection_returning_no_embedding_data_is_undetermined():
    collection = _Collection(data={"ids": [], "embeddings": None})

    report = Deduplicator(_ChromaStorage(collection)).find_duplicates()

    assert report.determined is False
    assert "no embedding data" in report.reason


def test_a_failing_similarity_query_is_undetermined():
    collection = _Collection(query_error=RuntimeError("dimension mismatch"))

    report = Deduplicator(_ChromaStorage(collection)).find_duplicates(content="hello")

    assert report.determined is False
    assert "dimension mismatch" in report.reason


# ---------------------------------------------------------------------------
# Checks that really did run
# ---------------------------------------------------------------------------


def test_an_empty_collection_is_a_real_clean_result():
    collection = _Collection(data={"ids": [], "embeddings": []})

    report = Deduplicator(_ChromaStorage(collection)).find_duplicates()

    assert report.determined is True
    assert report.is_clean is True


def test_internal_duplicates_are_grouped_and_reported_as_found():
    collection = _Collection(
        data={
            "ids": ["a", "b", "c"],
            "embeddings": [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]],
        }
    )

    report = Deduplicator(_ChromaStorage(collection)).find_duplicates(threshold=0.9)

    assert report.determined is True
    assert report.found_any is True
    assert [memory["id"] for memory in report.duplicates[0]] == ["a", "b"]


def test_search_backed_dedup_with_content_is_a_real_check():
    storage = _CollectionlessStorage(
        [{"id": "a", "similarity": 0.95}, {"id": "b", "similarity": 0.10}]
    )

    report = Deduplicator(storage).find_duplicates(content="hello", threshold=0.9)

    assert report.determined is True
    assert [memory["id"] for memory in report.duplicates] == ["a"]


def test_search_backed_dedup_with_content_and_no_match_is_clean():
    storage = _CollectionlessStorage([{"id": "b", "similarity": 0.10}])

    report = Deduplicator(storage).find_duplicates(content="hello", threshold=0.9)

    assert report.determined is True
    assert report.is_clean is True
