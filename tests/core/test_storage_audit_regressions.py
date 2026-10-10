"""LocalStorage audit regressions: vector batching, lexical search, edges, timestamps."""

import logging
import sqlite3

from visp_memory.core.indexing import ReindexScope
from visp_memory.core.storage import LocalStorage


class _FakeCollection:
    def __init__(self, max_batch, fail_upserts=0):
        self.max_batch = max_batch
        self.fail_upserts = fail_upserts
        self.upserts = []
        self.deletes = []

    def upsert(self, **kwargs):
        assert len(kwargs["ids"]) <= self.max_batch, "write exceeds Chroma max batch size"
        if self.fail_upserts:
            self.fail_upserts -= 1
            raise RuntimeError("vector store unavailable")
        self.upserts.append(kwargs)

    def delete(self, ids):
        assert len(ids) <= self.max_batch, "delete exceeds Chroma max batch size"
        self.deletes.append(ids)

    def upserted_ids(self):
        return [memory_id for call in self.upserts for memory_id in call["ids"]]


class _FakeClient:
    def __init__(self, max_batch=3, fail_upserts=0):
        self.max_batch = max_batch
        self.fail_upserts = fail_upserts
        self.collections = {}
        self.created = 0

    def get_max_batch_size(self):
        return self.max_batch

    def get_collection(self, name):
        return self.collections[name]

    def create_collection(self, name, metadata=None):
        self.created += 1
        collection = _FakeCollection(self.max_batch, fail_upserts=self.fail_upserts)
        self.fail_upserts = 0
        self.collections[name] = collection
        return collection

    def delete_collection(self, name):
        del self.collections[name]


def _embed(text):
    return [float(len(text)), 1.0]


def _store_rows(storage, count):
    return [
        storage.store_memory(f"note {index}", repo_id="repo-a", auto_link=False)
        for index in range(count)
    ]


def test_backfill_pages_every_status_in_chroma_sized_batches(tmp_path, monkeypatch):
    storage = LocalStorage(tmp_path, embedding_fn=_embed)
    ids = _store_rows(storage, 7)
    assert storage.update_memory(ids[0], status="archived") is True
    client = _FakeClient(max_batch=3)
    monkeypatch.setattr(storage, "_get_chroma", lambda: client)

    collection = storage._get_collection("episodic")

    assert collection is not None
    assert sorted(collection.upserted_ids()) == sorted(ids)
    archived = [
        metadata["status"]
        for call in collection.upserts
        for memory_id, metadata in zip(call["ids"], call["metadatas"])
        if memory_id == ids[0]
    ]
    assert archived == ["archived"]


def test_failed_backfill_is_not_cached_and_retries(tmp_path, monkeypatch, caplog):
    storage = LocalStorage(tmp_path, embedding_fn=_embed)
    ids = _store_rows(storage, 2)
    client = _FakeClient(max_batch=10, fail_upserts=1)
    monkeypatch.setattr(storage, "_get_chroma", lambda: client)

    with caplog.at_level(logging.WARNING):
        # The vector failure must not fail the committed SQLite write.
        new_id = storage.store_memory("written anyway", repo_id="repo-a", auto_link=False)
    assert storage.peek_memory(new_id) is not None
    assert "episodic" not in storage._collections
    assert "backfill failed" in caplog.text

    collection = storage._get_collection("episodic")

    assert client.created == 2
    assert sorted(collection.upserted_ids()) == sorted([*ids, new_id])
    assert storage._get_collection("episodic") is collection


def test_rebuild_embedding_index_upserts_in_chroma_sized_batches(tmp_path, monkeypatch):
    storage = LocalStorage(tmp_path, embedding_fn=_embed)
    ids = _store_rows(storage, 7)
    collection = _FakeCollection(max_batch=3)
    monkeypatch.setattr(storage, "_get_chroma", lambda: _FakeClient(max_batch=3))
    monkeypatch.setattr(storage, "_get_collection", lambda _layer: collection)
    monkeypatch.setattr("visp_memory.core.storage.CHROMADB_AVAILABLE", True)

    result = storage.rebuild_embedding_index(
        scope=ReindexScope(layer="episodic"), dry_run=False
    )

    assert result.status == "completed"
    assert result.reindexed_memories == 7
    assert sorted(collection.upserted_ids()) == sorted(ids)
    assert len(collection.upserts) == 3


def test_vector_batch_writer_chunks_upserts_and_deletes(tmp_path, monkeypatch):
    storage = LocalStorage(tmp_path)
    monkeypatch.setattr(storage, "_get_chroma", lambda: _FakeClient(max_batch=2))
    collection = _FakeCollection(max_batch=2)
    payload = {
        "ids": ["a", "b", "c", "d", "e"],
        "documents": ["A", "B", "C", "D", "E"],
    }

    storage._write_vector_batches(collection.upsert, payload)
    storage._write_vector_batches(collection.delete, {"ids": payload["ids"]})

    assert [call["ids"] for call in collection.upserts] == [["a", "b"], ["c", "d"], ["e"]]
    assert [call["documents"] for call in collection.upserts] == [["A", "B"], ["C", "D"], ["E"]]
    assert collection.deletes == [["a", "b"], ["c", "d"], ["e"]]


def test_vector_batch_size_falls_back_without_client(tmp_path):
    assert LocalStorage(tmp_path)._vector_batch_size() == 1000


def test_collection_failures_do_not_fail_store_or_update(tmp_path, monkeypatch):
    storage = LocalStorage(tmp_path, embedding_fn=_embed)

    def unavailable(_layer):
        raise RuntimeError("chroma unavailable")

    monkeypatch.setattr(storage, "_get_collection", unavailable)

    memory_id = storage.store_memory("kept in SQLite", repo_id="repo-a", auto_link=False)
    assert storage.update_memory(memory_id, content="still kept") is True
    assert storage.peek_memory(memory_id)["content"] == "still kept"


def test_lexical_search_folds_non_ascii_case(tmp_path):
    storage = LocalStorage(tmp_path)
    german = storage.store_memory(
        "Über-Konfiguration nutzt Ölpumpe", repo_id="repo-a", auto_link=False
    )
    greek = storage.store_memory("Ελληνικά ΚΕΙΜΕΝΟ test", repo_id="repo-a", auto_link=False)

    def found(query):
        return {
            item["id"]
            for item in storage.search_memories(
                query, repo_id="repo-a", retrieval_channel="lexical"
            )
        }

    assert german in found("über")
    assert german in found("ÖLPUMPE")
    assert greek in found("κειμενο")
    assert greek in found("ΚΕΙΜΕΝΟ")


def test_lexical_search_treats_like_wildcards_literally(tmp_path):
    storage = LocalStorage(tmp_path)
    literal = storage.store_memory("ratio a_b at 100%", repo_id="repo-a", auto_link=False)
    lookalike = storage.store_memory("ratio axb at 1000", repo_id="repo-a", auto_link=False)

    def found(query):
        return {
            item["id"]
            for item in storage.search_memories(
                query, repo_id="repo-a", retrieval_channel="lexical"
            )
        }

    assert found("a_b") == {literal}
    assert found("1%0") == set()
    assert lookalike not in found("100%")


def test_add_relationship_is_idempotent_per_edge(tmp_path):
    storage = LocalStorage(tmp_path)
    source = storage.store_memory("alpha caching fact", repo_id="repo-a", auto_link=False)
    target = storage.store_memory("beta caching fact", repo_id="repo-a", auto_link=False)

    first = storage.add_relationship(source, target, "depends_on", strength=0.4)
    second = storage.add_relationship(
        source,
        target,
        "depends_on",
        strength=0.9,
        evidence={"confidence": "observed", "reason": "Seen in the build graph."},
    )
    other_type = storage.add_relationship(source, target, "supports")
    reverse = storage.add_relationship(target, source, "depends_on")

    assert second == first
    assert len({first, other_type, reverse}) == 3
    edges = storage.get_all_relationships("repo-a")
    assert len(edges) == 3
    (edge,) = [item for item in edges if item["id"] == first]
    assert edge["strength"] == 0.9
    assert edge["evidence"]["confidence"] == "observed"
    assert edge["evidence"]["reason"] == "Seen in the build graph."
    assert storage.get_stats("repo-a")["total_relationships"] == 3


def _raw_column(storage, memory_id, column):
    with sqlite3.connect(storage.db_path) as conn:
        return conn.execute(
            f"SELECT {column} FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()[0]


def test_access_and_archive_timestamps_share_the_iso_format(tmp_path):
    storage = LocalStorage(tmp_path)
    accessed = storage.store_memory("accessed later", repo_id="repo-a", auto_link=False)
    stored = storage.store_memory("stored later", repo_id="repo-a", auto_link=False)
    used = storage.store_memory("used later", repo_id="repo-a", auto_link=False)

    storage.get_memory(accessed)
    assert "T" in _raw_column(storage, accessed, "accessed_at")
    newest = storage.list_memories(repo_id="repo-a", order_by="accessed_at DESC", limit=1)
    assert newest[0]["id"] == accessed

    storage.log_recall_event(used, "used")
    assert "T" in _raw_column(storage, used, "accessed_at")
    newest = storage.list_memories(repo_id="repo-a", order_by="accessed_at DESC", limit=1)
    assert newest[0]["id"] == used

    assert storage.update_memory(stored, status="archived") is True
    assert "T" in _raw_column(storage, stored, "archived_at")
