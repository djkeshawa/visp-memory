"""Storage backends share safe, explicit document/query embedding bindings."""

import pytest

from visp_memory.core.embedding_binding import bind_embeddings
from visp_memory.core.embeddings import NoOpProvider


def test_plain_callable_preserves_both_directions_and_explicit_dimension():
    def embed(text):
        return [1.0, 0.0]

    binding = bind_embeddings(embed, 2)
    assert binding.document is embed and binding.query is embed
    assert binding.dimension == 2 and binding.space is None
    assert not binding.is_noop


def test_noop_and_disabled_bindings_remain_distinct():
    noop = bind_embeddings(NoOpProvider(dimension=3).embed)
    disabled = bind_embeddings(None)
    assert noop.is_noop and noop.dimension == 3
    assert disabled.document is None and disabled.query is None
    assert not disabled.is_noop


@pytest.mark.parametrize("space", ["bad`index", "../other", "bad-index", "x" * 65])
def test_unsafe_provider_namespaces_are_rejected(space):
    class Provider:
        retrieval_space = space

        def embed(self, text):
            raise AssertionError("Binding must never execute the provider")

    with pytest.raises(ValueError, match="namespace"):
        bind_embeddings(Provider().embed)


def test_versioned_provider_requires_both_embedding_directions():
    class Provider:
        retrieval_space = "version_one"

        def embed(self, text):
            raise AssertionError("Binding must never execute the provider")

    with pytest.raises(ValueError, match="document and query"):
        bind_embeddings(Provider().embed)
