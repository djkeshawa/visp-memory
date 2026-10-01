"""Keep a memory's trust tier out of reach of an ordinary update.

A record's tier lives in its ``provenance:*`` tags and its ``source``. Every write
path assigns those from the server-owned channel, so a payload can never choose
its own tier. ``PATCH /memories/{id}`` accepted both fields verbatim, which let any
caller with write access promote a record to ``authored`` and have it auto-injected.

Raising or lowering a tier is a review act, so only an administrator or the local
owner (proven by the owner token) may do it. Everyone else may still edit the
other tags; the tier comes from the stored record.
"""

from typing import Any, Dict

from visp_memory.core.trust import PROVENANCE_TAG_PREFIX
from visp_memory.server.auth import UserContext
from visp_memory.server.authorization import has_admin_privileges


def may_change_provenance(user: UserContext) -> bool:
    return has_admin_privileges(user) or user.owner_maintenance


def pin_provenance(update: Dict[str, Any], existing: Dict[str, Any], user: UserContext) -> None:
    """Rewrite ``update`` in place so it cannot change the record's tier."""
    if may_change_provenance(user):
        return
    if "tags" in update:
        stored = [
            tag for tag in existing.get("tags") or [] if str(tag).startswith(PROVENANCE_TAG_PREFIX)
        ]
        requested = [
            tag for tag in update["tags"] or [] if not str(tag).startswith(PROVENANCE_TAG_PREFIX)
        ]
        update["tags"] = requested + stored
    if "source" in update:
        update["source"] = existing.get("source")
