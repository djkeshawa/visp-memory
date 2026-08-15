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

import hashlib
import math
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


def _configured_provider(text: str) -> List[float]:
    """Stand-in for visp-memory's *configured* embedding provider.

    Deterministic and content-addressed: identical text embeds to an identical
    unit vector, different text to a near-orthogonal one.
    """
    digest = hashlib.sha256(text.encode()).digest()
    raw = [(digest[i % len(digest)] / 255.0) - 0.5 for i in range(8)]
    norm = math.sqrt(sum(value * value for value in raw)) or 1.0
    return [value / norm for value in raw]


def _foreign_provider(text: str) -> List[float]:
    """Stand-in for the *collection's own* embedding function.

    Chroma attaches one whether or not visp-memory asked for it, and it is not
    the provider that wrote the stored vectors. Same dimension, different space.
    """
    digest = hashlib.blake2b(text.encode(), digest_size=8).digest()
    raw = [(digest[i] / 255.0) - 0.5 for i in range(8)]
    norm = math.sqrt(sum(value * value for value in raw)) or 1.0
    return [value / norm for value in raw]


class _ChromaStorage:
    """LocalStorage shape: exposes `_get_collection`, which may return None,
    and `_embedding_fn`, the provider that wrote every stored vector."""

    def __init__(self, collection=None, embedding_fn=_configured_provider):
        self._collection = collection
        self._embedding_fn = embedding_fn
        self._uses_noop_embeddings = False

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
        self.last_query_kwargs = None

    def get(self, include=None):
        if self._get_error:
            raise self._get_error
        return self._data

    def query(self, **kwargs):
        self.last_query_kwargs = kwargs
        if self._query_error:
            raise self._query_error
        return self._query_result


class _VectorCollection:
    """Faithful to Chroma's contract on the one point that matters here.

    Vectors are written by whoever calls ``add``. A query supplying
    ``query_embeddings`` is compared against them directly; a query supplying
    only ``query_texts`` is embedded *by the collection's own* embedding
    function first. Those are two different vector spaces whenever the caller's
    provider is not the collection's, and Chroma cannot detect the difference
    when the dimensions happen to agree.
    """

    def __init__(self, records, embedding_function=_foreign_provider):
        self._records = list(records)  # (id, text, stored_vector)
        self._embedding_function = embedding_function

    def query(self, query_texts=None, query_embeddings=None, n_results=5, **_kwargs):
        if query_embeddings:
            vector = query_embeddings[0]
        elif query_texts:
            vector = self._embedding_function(query_texts[0])
        else:
            raise ValueError("query requires query_texts or query_embeddings")

        scored = sorted(
            (
                (1.0 - sum(a * b for a, b in zip(vector, stored)), mem_id)
                for mem_id, _text, stored in self._records
            ),
        )[:n_results]
        return {
            "ids": [[mem_id for _distance, mem_id in scored]],
            "distances": [[distance for distance, _mem_id in scored]],
        }


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


# ---------------------------------------------------------------------------
# The content path: whose embedder asks the question
# ---------------------------------------------------------------------------
#
# The batch path compares stored vector against stored vector, so both sides
# come from the configured provider. The content path had to *make* a query
# vector, and it made it by handing raw text to Chroma — which embeds it with
# the collection's own embedding function, a thing visp-memory never configured
# and never used to write a single stored vector. When the two happen to share
# a dimension Chroma raises nothing, the distances are meaningless, nothing
# clears the threshold, and the report is `determined=True, is_clean=True` over
# a layer of byte-identical duplicates. That is the same false green just
# removed from the fallback branches, still live on the main branch.


DUPLICATE_TEXT = "The auth token refresh runs on a 15 minute timer."


def _layer_of_three_identical_memories(**kwargs):
    return _VectorCollection(
        [(mem_id, DUPLICATE_TEXT, _configured_provider(DUPLICATE_TEXT)) for mem_id in "abc"],
        **kwargs,
    )


def test_identical_memories_are_found_not_reported_clean():
    """Three byte-identical memories. Any answer but 'found' is wrong."""
    storage = _ChromaStorage(_layer_of_three_identical_memories())

    report = Deduplicator(storage).find_duplicates(content=DUPLICATE_TEXT, threshold=0.9)

    assert report.determined is True
    assert report.is_clean is False, (
        "reported a clean layer over three identical memories — the query was "
        "embedded by the collection instead of the configured provider"
    )
    assert [memory["id"] for memory in report.duplicates] == ["a", "b", "c"]


def test_the_query_is_embedded_by_the_configured_provider():
    """Pins the mechanism, not just the symptom: raw text must never be the query.

    Handing `query_texts` to Chroma delegates the embedding to whatever function
    the collection carries. The comparison is only meaningful against the
    provider that wrote the stored vectors.
    """
    collection = _Collection(query_result={"ids": [[]], "distances": [[]]})

    Deduplicator(_ChromaStorage(collection)).find_duplicates(content=DUPLICATE_TEXT)

    assert collection.last_query_kwargs["query_texts"] is None
    assert collection.last_query_kwargs["query_embeddings"] == [
        _configured_provider(DUPLICATE_TEXT)
    ]


def test_an_explicit_embedding_is_used_verbatim():
    """A caller that already has the vector must not be re-embedded."""
    collection = _Collection(query_result={"ids": [[]], "distances": [[]]})
    given = [0.5] * 8

    Deduplicator(_ChromaStorage(collection)).find_duplicates(
        content=DUPLICATE_TEXT, embedding=given
    )

    assert collection.last_query_kwargs["query_embeddings"] == [given]
    assert collection.last_query_kwargs["query_texts"] is None


def test_content_dedup_without_a_configured_provider_is_undetermined():
    """No provider means no query vector. That is a check that cannot run."""
    storage = _ChromaStorage(_layer_of_three_identical_memories(), embedding_fn=None)

    report = Deduplicator(storage).find_duplicates(content=DUPLICATE_TEXT)

    assert report.determined is False
    assert report.is_clean is False
    assert "embedding provider" in report.reason


def test_content_dedup_with_noop_embeddings_is_undetermined():
    """Noop vectors are identical for every text; a 'duplicate' verdict from them
    is noise, and a clean one is a lie."""
    storage = _ChromaStorage(_layer_of_three_identical_memories())
    storage._uses_noop_embeddings = True

    report = Deduplicator(storage).find_duplicates(content=DUPLICATE_TEXT)

    assert report.determined is False
    assert "embedding provider" in report.reason


def test_a_provider_that_raises_is_undetermined_not_clean():
    def broken(_text):
        raise RuntimeError("model not loaded")

    storage = _ChromaStorage(_layer_of_three_identical_memories(), embedding_fn=broken)

    report = Deduplicator(storage).find_duplicates(content=DUPLICATE_TEXT)

    assert report.determined is False
    assert "model not loaded" in report.reason


def test_results_without_distances_are_undetermined_not_perfect_matches():
    """A missing `distances` key used to be read as distance 0 — similarity 1.0 —
    so every returned id was reported as a duplicate on no evidence at all."""
    collection = _Collection(query_result={"ids": [["a", "b"]]})

    report = Deduplicator(_ChromaStorage(collection)).find_duplicates(content=DUPLICATE_TEXT)

    assert report.determined is False
    assert "distance" in report.reason


def test_a_result_set_with_no_ids_is_undetermined_not_clean():
    """`ids: [[]]` is a collection that answered. A missing `ids` is silence."""
    collection = _Collection(query_result={})

    report = Deduplicator(_ChromaStorage(collection)).find_duplicates(content=DUPLICATE_TEXT)

    assert report.determined is False
    assert report.is_clean is False


def test_a_collection_with_no_neighbours_is_a_real_clean_result():
    collection = _Collection(query_result={"ids": [[]], "distances": [[]]})

    report = Deduplicator(_ChromaStorage(collection)).find_duplicates(content=DUPLICATE_TEXT)

    assert report.determined is True
    assert report.is_clean is True


def test_a_vector_embedding_does_not_have_to_be_a_list():
    """numpy arrays have no truth value; `if embedding:` used to decide the path."""
    numpy = pytest.importorskip("numpy")
    given = numpy.array([0.5] * 8)
    collection = _Collection(query_result={"ids": [[]], "distances": [[]]})

    report = Deduplicator(_ChromaStorage(collection)).find_duplicates(embedding=given)

    assert report.determined is True
    assert collection.last_query_kwargs["query_embeddings"] == [list(given)]


def test_the_same_layer_is_found_by_the_batch_path_too():
    """Cross-check: the batch path never had this bug, and must still agree."""
    collection = _Collection(
        data={
            "ids": ["a", "b", "c"],
            "embeddings": [_configured_provider(DUPLICATE_TEXT)] * 3,
        }
    )

    report = Deduplicator(_ChromaStorage(collection)).find_duplicates(threshold=0.9)

    assert report.found_any is True
    assert [memory["id"] for memory in report.duplicates[0]] == ["a", "b", "c"]
