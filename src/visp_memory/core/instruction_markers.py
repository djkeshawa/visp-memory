"""Managed instruction regions shared by hook writers and instruction capture."""

import re

CLAUDE_CONTEXT_MARKER = "<!-- LLM-MEMORY -->"
CODEX_CONTEXT_MARKER = "<!-- LLM-MEMORY-CODEX -->"
CURSOR_CONTEXT_MARKER = "# LLM-MEMORY"

_MANAGED_REGIONS = tuple(
    re.compile(rf"{pattern}\s*START.*?{pattern}\s*END", re.DOTALL)
    for marker in (CLAUDE_CONTEXT_MARKER, CODEX_CONTEXT_MARKER, CURSOR_CONTEXT_MARKER)
    for pattern in [r"\s*".join(re.escape(part) for part in marker.split())]
)


def strip_managed_context(text: str) -> str:
    """Keep generated context from becoming a new source on the next ingestion."""
    for pattern in _MANAGED_REGIONS:
        text = pattern.sub("", text)
    return text
