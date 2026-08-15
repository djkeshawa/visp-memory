"""Real ChromaDB, real LocalStorage: whose embedder answers the duplicate check.

The unit tests in ``test_dedup_report.py`` model Chroma's contract with a fake.
A reviewer can reasonably ask whether the false green they pin is a property of
the product or of the fake. This file answers that with the real library and the
real storage backend, so the question does not have to be re-litigated.

The defect: ``Deduplicator._find_similar_to_new`` handed ``query_texts`` to the
collection. Chroma then embeds that text with the *collection's* embedding
function — a ``DefaultEmbeddingFunction`` that Chroma attaches on its own, which
visp-memory never configures and never uses to write a single stored vector.
The stored vectors come from the configured provider. Two unrelated spaces, and
because ``DefaultEmbeddingFunction`` is 384-dimensional, any configured provider
that is also 384-dimensional makes the dimensions agree, so Chroma raises
nothing. The distances are meaningless, nothing clears the threshold, and three
identical memories are reported as a clean layer.

Skipped where chromadb (or its default model) is unavailable; the hermetic
version of the same assertions always runs.
"""

import hashlib
import math

import pytest

from visp_memory.quality.dedup import Deduplicator

chromadb = pytest.importorskip("chromadb", reason="the Chroma query path needs chromadb")

DUPLICATE_TEXT = "The auth token refresh runs on a 15 minute timer in session.py."


class Substitute384Provider:
    """A configured provider that is not Chroma's default, at Chroma's default
    dimension. The dimensions agree, so nothing raises; the spaces do not."""

    dimension = 384

    def embed(self, text: str):
        digest = hashlib.sha256(text.encode()).digest()
        raw = [(digest[i % len(digest)] / 255.0) - 0.5 for i in range(self.dimension)]
        norm = math.sqrt(sum(value * value for value in raw)) or 1.0
        return [value / norm for value in raw]


@pytest.fixture
def layer_of_three_identical_memories(tmp_path):
    """A real LocalStorage whose episodic layer holds three identical memories."""
    from visp_memory.core.storage import LocalStorage

    provider = Substitute384Provider()
    storage = LocalStorage(tmp_path / "data", embedding_fn=provider.embed)

    for _ in range(3):
        storage.store_memory(content=DUPLICATE_TEXT, layer="episodic", auto_link=False)

    collection = storage._get_collection("episodic")
    if collection is None:
        pytest.skip("no vector collection: embeddings disabled in this environment")
    return storage, provider, collection


def test_the_collection_embeds_with_a_function_visp_memory_never_configured(
    layer_of_three_identical_memories,
):
    """The premise, checked directly and without needing to embed anything.

    If this ever stops holding — because storage starts passing its provider to
    Chroma as the collection's embedding function — the ``query_texts`` path
    would become merely redundant rather than wrong, and this file can go.
    """
    _storage, provider, collection = layer_of_three_identical_memories

    assert collection.count() == 3
    assert collection._embedding_function is not provider.embed
    assert type(collection._embedding_function).__name__ == "DefaultEmbeddingFunction"


def test_identical_memories_are_not_reported_as_a_clean_layer(
    layer_of_three_identical_memories,
):
    """The end-to-end claim: three identical memories, and the check must say so."""
    storage, provider, collection = layer_of_three_identical_memories

    try:
        collection.query(query_texts=[DUPLICATE_TEXT], n_results=3)
    except Exception as exc:  # pragma: no cover - environment, not behaviour
        pytest.skip(f"Chroma's default embedding model is unavailable here: {exc}")

    report = Deduplicator(storage).find_duplicates(
        layer="episodic", content=DUPLICATE_TEXT, threshold=0.9
    )

    assert report.determined is True
    assert report.is_clean is False, (
        "a clean all-clear over three byte-identical memories: the query was "
        "embedded by the collection instead of by the configured provider"
    )
    assert report.count == 3
    assert all(memory["similarity"] > 0.99 for memory in report.duplicates)


def test_the_collections_own_embedder_would_have_missed_them(
    layer_of_three_identical_memories,
):
    """Pins the size of the error, so a future reader need not take it on trust.

    Same three identical memories, same threshold, two query vectors: the
    configured provider's own, and the one Chroma makes from raw text.
    """
    _storage, provider, collection = layer_of_three_identical_memories

    try:
        by_collection = collection.query(query_texts=[DUPLICATE_TEXT], n_results=3)
    except Exception as exc:  # pragma: no cover - environment, not behaviour
        pytest.skip(f"Chroma's default embedding model is unavailable here: {exc}")

    by_provider = collection.query(query_embeddings=[provider.embed(DUPLICATE_TEXT)], n_results=3)

    provider_similarity = [1 - d for d in by_provider["distances"][0]]
    collection_similarity = [1 - d for d in by_collection["distances"][0]]

    # Identical text, identical stored vector: the configured provider recovers it.
    assert all(similarity > 0.99 for similarity in provider_similarity)
    # The collection's own embedder puts the same memories nowhere near the
    # 0.9 threshold, which is exactly how a duplicate layer read as clean.
    assert all(similarity < 0.9 for similarity in collection_similarity)
