"""Deterministic token accounting for memory payloads.

The whole value proposition of Visp Memory is that injecting a small, pre-formed
memory context lets an assistant skip re-reading files and re-deriving project
knowledge every session. That claim is only credible if it is *measurable*, so
this module provides a single, deterministic way to estimate token counts and to
quantify how many tokens a piece of memory saves versus the raw material it was
built from.

Design choices:

- No hard dependency. ``tiktoken`` is used when it is importable (more accurate),
  otherwise a calibrated character/word heuristic is used. The heuristic is
  deterministic and stable across platforms so report/eval snapshots do not flake.
- Estimates, not guarantees. Savings figures are explicitly framed as estimates
  and are computed conservatively (never negative) so the system never overclaims.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Iterable, Mapping

# Characters per token and words per token for English / source code. These match
# the widely used "~4 chars or ~0.75 words per token" rule of thumb. We average a
# character-based and a word-based estimate because each alone is biased: the
# character estimate under-counts punctuation-heavy code, while the word estimate
# under-counts long identifiers and whitespace.
_CHARS_PER_TOKEN = 4.0
_WORDS_PER_TOKEN = 0.75


@lru_cache(maxsize=1)
def _tiktoken_encoder() -> Any | None:
    """Return a cached tiktoken encoder, or ``None`` when tiktoken is unavailable."""
    try:  # pragma: no cover - exercised only when tiktoken is installed
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


def estimate_tokens(text: Any) -> int:
    """Estimate the number of tokens needed to represent ``text``.

    Uses ``tiktoken`` when available for accuracy, otherwise a deterministic
    heuristic. Always returns a non-negative integer; empty input is ``0``.
    """
    if text is None:
        return 0
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return 0

    encoder = _tiktoken_encoder()
    if encoder is not None:  # pragma: no cover - depends on optional dependency
        return len(encoder.encode(text))

    char_estimate = len(text) / _CHARS_PER_TOKEN
    word_estimate = len(text.split()) / _WORDS_PER_TOKEN
    blended = (char_estimate + word_estimate) / 2
    return max(1, math.ceil(blended))


def estimate_memory_tokens(memory: Mapping[str, Any]) -> int:
    """Estimate the token cost of a single memory's recallable content."""
    if not isinstance(memory, Mapping):
        return estimate_tokens(memory)
    return estimate_tokens(memory.get("content", ""))


def estimate_total_tokens(items: Iterable[Any]) -> int:
    """Estimate the combined token cost of an iterable of strings or memories."""
    total = 0
    for item in items:
        if isinstance(item, Mapping):
            total += estimate_memory_tokens(item)
        else:
            total += estimate_tokens(item)
    return total


@dataclass(frozen=True)
class TokenSavings:
    """A conservative, auditable estimate of tokens saved by a memory operation.

    ``source_tokens`` is the cost of the raw material (e.g. the episodic memories
    or file content the assistant would otherwise have to read), ``result_tokens``
    is the cost of the compact memory that replaces it, and ``saved_tokens`` is the
    non-negative difference. ``ratio`` is the fraction of source tokens avoided.
    """

    source_tokens: int
    result_tokens: int
    saved_tokens: int = field(init=False)
    ratio: float = field(init=False)

    def __post_init__(self) -> None:
        saved = max(0, self.source_tokens - self.result_tokens)
        object.__setattr__(self, "saved_tokens", saved)
        ratio = saved / self.source_tokens if self.source_tokens > 0 else 0.0
        object.__setattr__(self, "ratio", round(ratio, 4))

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_tokens": self.source_tokens,
            "result_tokens": self.result_tokens,
            "saved_tokens": self.saved_tokens,
            "ratio": self.ratio,
        }


def compute_savings(source: Any, result: Any) -> TokenSavings:
    """Compute :class:`TokenSavings` from source and result material.

    ``source`` and ``result`` may each be a string, a memory mapping, or an
    iterable of either. This is used both for consolidation savings (many episodic
    memories compressed into one semantic memory) and for recall compactness
    (a compact context replacing larger source files).
    """
    return TokenSavings(
        source_tokens=_tokens_of(source),
        result_tokens=_tokens_of(result),
    )


def _tokens_of(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str) or isinstance(value, Mapping):
        return estimate_total_tokens([value])
    if isinstance(value, Iterable):
        return estimate_total_tokens(value)
    return estimate_tokens(value)
