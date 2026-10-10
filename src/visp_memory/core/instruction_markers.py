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


#: Every built-in marker contains this token. U+2011 (non-breaking hyphen) renders
#: the same to a reader but is not the ASCII hyphen the markers are matched on.
_MARKER_TOKEN = "LLM-MEMORY"
_NEUTRAL_TOKEN = "LLM\u2011MEMORY"


def neutralize_markers(text: str, *markers: str) -> str:
    """Make recalled text unable to open or close a managed region.

    Memory content is written between region markers; a stored memory that quotes a
    marker (``<!-- LLM-MEMORY --> END``) would otherwise end the region early, so the
    next update refuses the now-duplicated markers and the remainder leaks into
    instruction capture. ``markers`` covers custom markers without the shared token.
    """
    text = text.replace(_MARKER_TOKEN, _NEUTRAL_TOKEN)
    for marker in markers:
        if marker and len(marker) > 1:
            text = text.replace(marker, f"{marker[0]}\u200b{marker[1:]}")
    return text
