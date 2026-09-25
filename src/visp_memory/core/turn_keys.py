"""Turn-level search keys for conversation memories.

A conversation memory holds many turns, and its single embedding is dominated by
whatever most of them discuss. A fact stated once in passing ("I used to be a
marketing specialist") is then out of reach for a question about it. Turn keys
embed each user turn separately, pointing back at the parent memory and the span
of the turn, so a precise match retrieves that turn as a cited passage.

Keys are an index, not memories: they carry no content of their own beyond the
parent's verbatim text, inherit the parent's scope and eligibility, and are
removed with it.
"""

from __future__ import annotations

from typing import Any, Iterable

from visp_memory.core.source_passages import attributed_passage, turn_ranges

KEY_LAYERS = ("episodic",)
# Turns shorter than this carry no retrievable fact ("thanks!").
MIN_KEY_CHARS = 20
# Embedding inputs are bounded; a longer turn is keyed by its opening, which is
# where people state the fact they then elaborate on.
MAX_KEY_CHARS = 1500
MAX_KEYS_PER_MEMORY = 64
# How many matching turns a brief adds to its candidate pool. 30 recovered more
# evidence than 10 or 20 on held-out conversations without losing any elsewhere;
# larger candidate pools crowded other evidence out of the budget.
BRIEF_TURN_KEYS = 30


def conversation_keys(content: str) -> list[tuple[int, int]]:
    """Character spans of the user turns worth indexing in ``content``."""
    spans = []
    for start, end in turn_ranges(content or ""):
        text = content[start:end].strip()
        if text.startswith("user:") and len(text) >= MIN_KEY_CHARS:
            spans.append((start, end))
        if len(spans) == MAX_KEYS_PER_MEMORY:
            break
    return spans


def key_text(content: str, span: tuple[int, int]) -> str:
    start, end = span
    return content[start:end][:MAX_KEY_CHARS]


def key_id(memory_id: str, span: tuple[int, int]) -> str:
    return f"{memory_id}#turn:{span[0]}-{span[1]}"


def key_passages(
    hits: Iterable[dict[str, Any]], existing: list[dict[str, Any]], limit: int = BRIEF_TURN_KEYS
) -> list[dict[str, Any]]:
    """Attributed passages for the best turn hits, ranked alongside ``existing``.

    Each hit is ``{"memory": row, "span": (start, end), "similarity": float}``.
    Key similarities are not on the same scale as memory relevance, so a turn at
    rank ``n`` is given the relevance of the existing candidate at rank ``n``:
    it competes as an equal, never as an automatic winner.
    """
    ordered = sorted(hits, key=lambda hit: -float(hit.get("similarity") or 0))
    scale = sorted(
        (float(row.get("relevance_score") or row.get("similarity") or 0) for row in existing),
        reverse=True,
    ) or [0.5]
    seen: set[tuple[str, int, int]] = set()
    passages = []
    for hit in ordered:
        memory, (start, end) = hit["memory"], hit["span"]
        identity = (str(memory["id"]), start, end)
        if identity in seen:
            continue
        seen.add(identity)
        position = min(len(passages), len(scale) - 1)
        passages.append({
            **attributed_passage(memory, start, end),
            "relevance_score": scale[position],
            "retrieval_channels": ["direct", "turn_key"],
        })
        if len(passages) == limit:
            break
    return passages
