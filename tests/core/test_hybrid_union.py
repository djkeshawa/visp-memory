"""Opt-in independent lexical/vector candidate discovery."""

from visp_memory.core.hybrid_retrieval import HybridRetriever
from visp_memory.core.storage import LocalStorage


class ChannelStorage:
    def __init__(self, vector, lexical):
        self.vector = vector
        self.lexical = lexical
        self.calls = []

    def supports_retrieval_channel(self, channel):
        return channel in {"vector", "lexical"}

    def search_memories(self, **kwargs):
        channel = kwargs["retrieval_channel"]
        self.calls.append(channel)
        return list(self.vector if channel == "vector" else self.lexical)

    def list_memories(self, **kwargs):
        return []

    def get_all_relationships(self, **kwargs):
        return []


def _row(memory_id, content, similarity, method):
    return {
        "id": memory_id,
        "content": content,
        "similarity": similarity,
        "retrieval_method": method,
        "relevance_score": similarity,
        "importance": 0.5,
        "repo_id": "repo",
        "layer": "episodic",
        "status": "active",
    }


def test_union_recovers_exact_identifier_outside_vector_pool():
    exact = _row("exact", "Invoice IN-2718 total is $417.", 0.35, "keyword")
    vector = _row("generic", "Invoice totals are recorded in the ledger.", 0.95, "semantic")
    storage = ChannelStorage([vector], [exact])

    result = HybridRetriever(storage).retrieve(
        "What is invoice IN-2718 total?", repo_id="repo", ranking_strategy="hybrid_union"
    )

    assert "exact" in {row["id"] for row in result}
    assert storage.calls.count("vector") == 4
    assert storage.calls.count("lexical") == 4


def test_union_fuses_lexical_hit_after_more_than_canonical_hundred_vector_rows():
    vector = [
        _row(f"generic-{index}", "unrelated paraphrase context", 0.95, "semantic")
        for index in range(100)
    ]
    exact = _row("exact", "IN-2718 invoice context", 0.2, "keyword")
    storage = ChannelStorage(vector, [exact])

    result = HybridRetriever(storage).retrieve(
        "IN-2718", repo_id="repo", limit=200, ranking_strategy="hybrid_union"
    )

    assert "exact" in {row["id"] for row in result}


def test_union_keeps_vector_semantic_score_for_duplicate_id():
    vector = _row("same", "The cache uses a bounded TTL.", 0.91, "semantic")
    lexical = _row("same", "The cache uses a bounded TTL.", 0.22, "keyword")
    storage = ChannelStorage([vector], [lexical])

    result = HybridRetriever(storage).retrieve(
        "cache TTL", repo_id="repo", ranking_strategy="hybrid_union"
    )

    same = next(row for row in result if row["id"] == "same")
    assert same["similarity"] == 0.91
    assert same["retrieval_method"] == "semantic"


def test_union_applies_shared_scope_and_trust_filter():
    allowed = _row("allowed", "Invoice IN-2718 total is $417.", 0.35, "keyword")
    foreign = {**allowed, "id": "foreign", "repo_id": "other"}
    quarantined = {**allowed, "id": "quarantined", "tags": ["provenance:external"]}
    storage = ChannelStorage([foreign], [allowed, quarantined])

    def allowed_filter(row):
        return row.get("repo_id") == "repo" and row.get("id") != "quarantined"

    result = HybridRetriever(storage).retrieve(
        "invoice IN-2718", repo_id="repo",
        candidate_filter=allowed_filter,
        ranking_strategy="hybrid_union",
    )

    assert [row["id"] for row in result] == ["allowed"]


def test_union_is_rejected_when_backend_cannot_expose_independent_pools():
    class UnsupportedStorage(ChannelStorage):
        def supports_retrieval_channel(self, channel):
            return False

    storage = UnsupportedStorage([], [])

    try:
        HybridRetriever(storage).retrieve(
            "query", repo_id="repo", ranking_strategy="hybrid_union"
        )
    except ValueError as error:
        assert "independent vector and lexical" in str(error)
    else:
        raise AssertionError("expected unsupported union strategy to be rejected")


def test_local_lexical_channel_uses_sqlite_without_requesting_embeddings(tmp_path):
    storage = LocalStorage(tmp_path)
    try:
        memory_id = storage.store_memory(
            "Invoice IN-2718 total is $417.", repo_id="repo", auto_link=False
        )

        def no_embedding(_query):
            raise AssertionError("lexical channel must not request an embedding")

        storage._query_embedding_fn = no_embedding
        lexical = storage.search_memories(
            "IN-2718", repo_id="repo", retrieval_channel="lexical", limit=5
        )
        vector = storage.search_memories(
            "IN-2718", repo_id="repo", retrieval_channel="vector", limit=5
        )

        assert [row["id"] for row in lexical] == [memory_id]
        assert vector == []
    finally:
        storage.close()


def test_local_union_keeps_repo_status_and_trust_filters_together(tmp_path):
    storage = LocalStorage(tmp_path)
    try:
        allowed = storage.store_memory(
            "Invoice IN-2718 total is $417.", repo_id="repo",
            tags=["provenance:authored"], auto_link=False,
        )
        storage.store_memory(
            "Invoice IN-2718 total is $999.", repo_id="other",
            tags=["provenance:authored"], auto_link=False,
        )
        storage.store_memory(
            "Invoice IN-2718 total is $888.", repo_id="repo", status="deleted",
            tags=["provenance:authored"], auto_link=False,
        )
        storage.store_memory(
            "Invoice IN-2718 total is $123.", repo_id="repo",
            tags=["provenance:external"], auto_link=False,
        )

        result = HybridRetriever(storage).retrieve(
            "IN-2718", repo_id="repo",
            candidate_filter=lambda row: "provenance:external" not in row.get("tags", []),
            ranking_strategy="hybrid_union",
        )

        assert [row["id"] for row in result] == [allowed]
    finally:
        storage.close()


def test_memory_recall_unions_independent_pools_before_limit(tmp_path, monkeypatch):
    from visp_memory import Memory, MemoryConfig
    from visp_memory.config import EmbeddingConfig, StorageConfig

    memory = Memory(
        MemoryConfig(
            repo_id="repo",
            embedding=EmbeddingConfig(provider="none"),
            storage=StorageConfig(data_dir=tmp_path),
        )
    )
    vector = _row("generic", "Invoice totals are recorded in the ledger.", 0.95, "semantic")
    exact = _row("exact", "Invoice IN-2718 total is $417.", 0.35, "keyword")

    def search(**kwargs):
        return [vector] if kwargs["retrieval_channel"] == "vector" else [exact]

    monkeypatch.setattr(memory._storage, "search_memories", search)
    result = memory.recall(
        "IN-2718", layers=["episodic"], limit=1,
        min_score=0, ranking_strategy="hybrid_union", log_utility=False,
    )

    assert [row["id"] for row in result] == ["exact"]
    memory.close()
