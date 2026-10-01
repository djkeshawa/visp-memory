"""Preserve record attribution when REST clients replace metadata or context."""

from typing import Any, Mapping

from visp_memory.core.attribution import WRITTEN_BY_KEY


def preserve_written_by(metadata: dict[str, Any], existing: Mapping[str, Any]) -> None:
    """Ignore body attribution while retaining the original record's writer."""
    if WRITTEN_BY_KEY in existing:
        metadata[WRITTEN_BY_KEY] = existing[WRITTEN_BY_KEY]
    else:
        metadata.pop(WRITTEN_BY_KEY, None)
