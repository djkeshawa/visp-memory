"""Write-time reconciliation for semantic memory.

Inserting every new fact unconditionally bloats the store with near-duplicates,
which costs recall precision and tokens. Following the Mem0 write-path model,
each candidate fact is reconciled against the most similar existing memories and
resolved to one of four actions:

- ``add``       — genuinely new knowledge; store it.
- ``noop``      — an existing memory already says this; reinforce it instead.
- ``update``    — an existing memory says this less completely; create an
  evidence-backed successor and supersede the older belief.
- ``supersede`` — decided by the caller when an (LLM-confirmed) contradiction is
  found; the old memory is invalidated non-destructively.

The decision here is deterministic (normalized-exact and lexical-overlap checks),
so it is safe on every backend and with the noop embedding provider. Contradiction
detection stays in :mod:`visp_memory.quality.conflict` because it needs an LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from visp_memory.core.ranking import _LEXICAL_STOPWORDS
from visp_memory.quality.secrets import redact_for_storage

DEFAULT_NOOP_THRESHOLD = 0.95
DEFAULT_UPDATE_THRESHOLD = 0.8
_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")


def _normalize(text: str) -> str:
    return " ".join(str(text).casefold().split())


def _tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in _TOKEN_RE.findall(str(text))
        if token.lower() not in _LEXICAL_STOPWORDS
    }


def content_overlap(a: str, b: str) -> float:
    """Symmetric lexical overlap (Jaccard over non-stopword tokens)."""
    if _normalize(a) == _normalize(b):
        return 1.0
    tokens_a = _tokens(a)
    tokens_b = _tokens(b)
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


@dataclass(frozen=True)
class ReconcileDecision:
    """Outcome of reconciling a candidate fact against existing memories."""

    action: str  # "add" | "noop" | "update"
    target_id: Optional[str]
    similarity: float
    reason: str


class Reconciler:
    """Decide whether a new fact should be added, dropped, or folded into an
    existing memory."""

    def __init__(
        self,
        storage,
        noop_threshold: float = DEFAULT_NOOP_THRESHOLD,
        update_threshold: float = DEFAULT_UPDATE_THRESHOLD,
        candidate_limit: int = 10,
    ):
        self.storage = storage
        self.noop_threshold = max(0.0, min(1.0, float(noop_threshold)))
        self.update_threshold = max(0.0, min(self.noop_threshold, float(update_threshold)))
        self.candidate_limit = max(1, int(candidate_limit))

    def decide(
        self,
        content: str,
        layer: str = "semantic",
        repo_id: str = None,
        category: str = None,
    ) -> ReconcileDecision:
        content, _ = redact_for_storage(content, None)
        new_tokens = _tokens(content)
        best: dict[str, Any] | None = None
        best_overlap = 0.0
        best_extends = False
        for candidate in self._candidates(content, layer, repo_id):
            candidate_content = str(candidate.get("content", ""))
            overlap = content_overlap(content, candidate_content)
            # A strict extension (the existing memory's tokens all appear in the
            # new content) is an update signal even at moderate Jaccard, because
            # Jaccard punishes added detail by construction.
            candidate_tokens = _tokens(candidate_content)
            extends = bool(candidate_tokens) and candidate_tokens <= new_tokens
            if overlap > best_overlap or (overlap == best_overlap and extends and not best_extends):
                best = candidate
                best_overlap = overlap
                best_extends = extends

        extension_threshold = self.update_threshold / 2
        if best is None or (
            best_overlap < self.update_threshold
            and not (best_extends and best_overlap >= extension_threshold)
        ):
            return ReconcileDecision("add", None, best_overlap, "No sufficiently similar memory.")

        # Cross-category matches are left alone: a warning and a convention with
        # overlapping words are different kinds of knowledge.
        if category and best.get("category") and str(best["category"]) != str(category):
            return ReconcileDecision(
                "add", None, best_overlap, "Similar content exists in a different category."
            )

        new_is_longer = len(_normalize(content)) > len(_normalize(str(best.get("content", ""))))

        if best_overlap >= self.noop_threshold and not (best_extends and new_is_longer):
            return ReconcileDecision(
                "noop",
                best["id"],
                best_overlap,
                "An existing memory already states this.",
            )

        if new_is_longer:
            return ReconcileDecision(
                "update",
                best["id"],
                best_overlap,
                "New content restates an existing memory with more detail.",
            )

        return ReconcileDecision(
            "noop",
            best["id"],
            best_overlap,
            "Existing memory already covers this in equal or greater detail.",
        )

    def _candidates(self, content: str, layer: str, repo_id: str) -> list[dict[str, Any]]:
        try:
            return self.storage.search_memories(
                query=content,
                layer=layer,
                repo_id=repo_id,
                limit=self.candidate_limit,
                status="active",
            )
        except TypeError:
            return self.storage.search_memories(
                query=content, layer=layer, repo_id=repo_id, limit=self.candidate_limit
            )
        except Exception:
            # Reconciliation is an optimization; a failed candidate lookup must
            # never block a write.
            return []
