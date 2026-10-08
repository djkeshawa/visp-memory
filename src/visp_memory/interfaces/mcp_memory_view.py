"""Bind an HTTP principal to a borrowed Memory without mutating shared state."""

from copy import copy

from visp_memory.server.scoped_storage import ScopedStorageView


def memory_for_principal(memory, principal, repo_id):
    view = copy(memory)
    view.config = memory.config.model_copy(update={"repo_id": repo_id})
    view._storage = ScopedStorageView(memory._storage, principal, repo_id)
    view._writer_lock = None
    for name in (
        "episodic", "semantic", "intent", "repos", "teams", "_compressor",
        "deduplicator", "reconciler", "conflict_detector",
    ):
        component = copy(getattr(memory, name))
        component.storage = view._storage
        setattr(view, name, component)
    return view
