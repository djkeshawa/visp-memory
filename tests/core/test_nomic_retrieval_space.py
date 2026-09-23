"""Nomic retrieval uses separate query/document instructions and isolated vectors."""

import sys
from types import SimpleNamespace

import pytest

from visp_memory.core.embeddings import OllamaProvider
from visp_memory.core.storage import LocalStorage


@pytest.mark.parametrize("model", ["nomic-embed-text", "nomic-embed-text:latest"])
def test_nomic_storage_uses_distinct_retrieval_inputs(tmp_path, monkeypatch, model):
    prompts = []

    def embeddings(*, model, prompt):
        prompts.append(prompt)
        return {"embedding": [1.0, 0.0]}

    client = SimpleNamespace(embeddings=embeddings)
    monkeypatch.setitem(sys.modules, "ollama", SimpleNamespace(Client=lambda host: client))
    provider = OllamaProvider(model=model, host="http://local-model")
    storage = LocalStorage(tmp_path, embedding_fn=provider.embed)
    prompts.clear()
    storage.store_memory("Keep verified notes", repo_id="repo-a", auto_link=False)
    assert "search_document: Keep verified notes" in prompts
    prompts.clear()
    collection = SimpleNamespace(query=lambda **kwargs: {"ids": [[]]})
    monkeypatch.setattr(storage, "_get_collection", lambda layer: collection)
    storage.search_memories("How can knowledge be retained?", repo_id="repo-a", layer="episodic")
    assert prompts == ["search_query: How can knowledge be retained?"]
    assert storage._collection_name("episodic") != "memories_episodic_2"


def test_other_ollama_models_keep_existing_embedding_space(tmp_path, monkeypatch):
    prompts = []

    def embeddings(*, model, prompt):
        prompts.append(prompt)
        return {"embedding": [1.0, 0.0]}

    monkeypatch.setitem(
        sys.modules,
        "ollama",
        SimpleNamespace(
            Client=lambda host: SimpleNamespace(embeddings=embeddings),
        ),
    )
    provider = OllamaProvider(model="other-embedding-model", host="http://local-model")
    storage = LocalStorage(tmp_path, embedding_fn=provider.embed)
    prompts.clear()
    storage.store_memory("Keep verified notes", repo_id="repo-a", auto_link=False)
    assert "Keep verified notes" in prompts
    assert not any(prompt.startswith("search_document:") for prompt in prompts)
    assert storage._collection_name("episodic") == "memories_episodic_2"


@pytest.mark.parametrize("indexed,needs_reindex", [(0, True), (1, False)])
def test_retained_legacy_vectors_do_not_force_another_completed_rebuild(
    tmp_path,
    monkeypatch,
    indexed,
    needs_reindex,
):
    storage = LocalStorage(tmp_path, embedding_fn=lambda text: [1.0, 0.0])
    monkeypatch.setattr("visp_memory.core.storage.CHROMADB_AVAILABLE", True)
    monkeypatch.setattr(storage, "_count_reindex_candidates", lambda scope: 1)
    monkeypatch.setattr(
        storage,
        "_embedding_collection_summary",
        lambda scope: (
            ["memories_episodic_2_nomic_search_v1"],
            ["memories_episodic_2"],
            indexed,
        ),
    )
    report = storage.inspect_embedding_index(
        storage_backend="sqlite", provider="ollama", dimension=2
    )
    assert report.needs_reindex is needs_reindex
    assert report.legacy_collections == ["memories_episodic_2"]
