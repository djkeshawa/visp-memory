"""The one JSON encoding the storage backends persist structured fields with.

SQLite, ArcadeDB and Neo4j keep metadata, tags and similar fields as JSON text.
Anything that decides whether a write matches a stored record compares through
this same encoding, so the comparison sees exactly what would be persisted.
"""

import json
from typing import Any


def to_json_text(data: Any) -> str:
    """Serialize a structured field as the storage backends persist it."""
    return json.dumps(data)
