"""Bind storage writes and queries to one compatible embedding instruction space."""

import re
from collections.abc import Callable
from dataclasses import dataclass

EmbeddingFunction = Callable[[str], list[float]]


@dataclass(frozen=True)
class EmbeddingBinding:
    document: EmbeddingFunction | None
    query: EmbeddingFunction | None
    dimension: int | None
    space: str | None
    is_noop: bool


def bind_embeddings(
    embedding_fn: EmbeddingFunction | None, dimension: int | None = None
) -> EmbeddingBinding:
    """Preserve generic callables; version providers with asymmetric instructions.

    Binding does not generate vectors or call a provider. The namespace is used in
    Chroma collection names and Neo4j identifiers, so reject unsafe values before
    a backend can interpolate them into an index definition.
    """
    owner = getattr(embedding_fn, "__self__", None)
    owner_name = owner.__class__.__name__.lower() if owner is not None else ""
    space = getattr(owner, "retrieval_space", None)
    space = space if isinstance(space, str) and space else None
    document = query = embedding_fn
    if space:
        if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", space) is None:
            raise ValueError("retrieval_space must be a safe lowercase index namespace")
        document = getattr(owner, "embed_document", None)
        query = getattr(owner, "embed_query", None)
        if not callable(document) or not callable(query):
            raise ValueError("A versioned retrieval_space requires document and query embedders")
    return EmbeddingBinding(
        document=document,
        query=query,
        dimension=dimension or getattr(owner, "dimension", None),
        space=space,
        is_noop=owner_name == "noopprovider",
    )
