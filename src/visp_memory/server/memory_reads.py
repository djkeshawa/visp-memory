"""Page visible memory rows without letting hidden records consume the result limit."""

from itertools import islice
from typing import Any, Iterator

from visp_memory.server.auth import UserContext
from visp_memory.server.authorization import can_access_scoped_record

PAGE_SIZE = 200


def iter_visible_memories(
    storage: Any,
    *,
    repo_id: str,
    user: UserContext,
    layer: str = None,
    category: str = None,
    status: str = "active",
    order_by: str = "created_at DESC",
    after_id: str = None,
) -> Iterator[dict]:
    offset = 0
    while True:
        page = storage.list_memories(
            limit=PAGE_SIZE, offset=offset, repo_id=repo_id, layer=layer,
            category=category, status=status, order_by=order_by, after_id=after_id,
        )
        yield from (
            memory for memory in page
            if can_access_scoped_record(storage, memory, user, scope_field="metadata")
        )
        if len(page) < PAGE_SIZE:
            return
        offset += PAGE_SIZE


def visible_memory_page(storage: Any, *, offset: int = 0, limit: int = 50, **filters) -> list[dict]:
    return list(islice(iter_visible_memories(storage, **filters), offset, offset + limit))
