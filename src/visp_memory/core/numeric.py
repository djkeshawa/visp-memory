"""Defensive numeric normalization for persisted and caller-supplied scores."""

from __future__ import annotations

import math
from typing import Any


def bounded_float(
    value: Any,
    *,
    lower: float = 0.0,
    upper: float = 1.0,
    default: float | None = None,
) -> float | None:
    """Return a finite float clamped to ``lower..upper``, or ``default`` if malformed."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return max(lower, min(upper, parsed))
