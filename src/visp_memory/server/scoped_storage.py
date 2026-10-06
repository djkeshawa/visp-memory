"""A borrowed storage view that filters content before an MCP request can use it."""

from collections import Counter

from visp_memory.server.authorization import can_access_scoped_record, has_admin_privileges
from visp_memory.server.memory_reads import iter_visible_memories, visible_memory_page
from visp_memory.server.scoped_utility import (
    inspect_visible_utility,
    reset_visible_utility,
    verify_visible_utility,
)

# Writes still pass through the MCP entry point's scope and ID authorization.
# Content reads are explicit methods below; new read methods must be scoped too.
_FORWARDED = frozenset({
    "get_capabilities", "get_schema_status", "supports_retrieval_channel",
    "update_memory", "revise_memory",
    "attach_evidence", "update_evidence", "update_intent", "append_intent_outcome",
    "report_intent_workflow", "add_relationship", "log_recall_event", "get_repo_dependencies",
    "data_dir", "server_url", "embedding_fn", "_embedding_fn", "turn_keys",
})


class ScopedStorageView:
    def __init__(self, storage, user, repo_id):
        self.storage = storage
        self.user = user
        self.repo_id = repo_id

    def __getattr__(self, name):
        if name in _FORWARDED:
            return getattr(self.storage, name)
        raise AttributeError(name)

    def close(self):
        """The original Memory instance owns the backend and its writer lock."""

    def _repo(self, requested):
        if requested is not None and requested != self.repo_id:
            raise ValueError("Storage requests must stay within the authorized repository")
        return self.repo_id

    def _owned_metadata(self, metadata):
        result = dict(metadata or {})
        result["author_id"] = self.user.user_id
        result.pop("team_id", None)
        if self.user.team_id:
            result["team_id"] = self.user.team_id
        return result

    def store_memory(self, content, layer="episodic", repo_id=None, metadata=None, **kwargs):
        return self.storage.store_memory(
            content=content, layer=layer, repo_id=self._repo(repo_id),
            metadata=self._owned_metadata(metadata), **kwargs,
        )

    def store_evidence(self, content, repo_id=None, metadata=None, **kwargs):
        return self.storage.store_evidence(
            content=content, repo_id=self._repo(repo_id),
            metadata=self._owned_metadata(metadata), **kwargs,
        )

    def _visible(self, record, scope_field="metadata"):
        return bool(
            record and record.get("repo_id") == self.repo_id
            and can_access_scoped_record(self.storage, record, self.user, scope_field=scope_field)
        )

    def peek_memory(self, memory_id, repo_id=None):
        self._repo(repo_id)
        peek = getattr(self.storage, "peek_memory", self.storage.get_memory)
        record = peek(memory_id)
        return record if self._visible(record) else None

    def get_memory(self, memory_id):
        # Check visibility without reinforcing a record the caller cannot read.
        return self.storage.get_memory(memory_id) if self.peek_memory(memory_id) else None

    def list_memories(self, repo_id=None, *, limit=50, offset=0, **filters):
        return visible_memory_page(
            self.storage, repo_id=self._repo(repo_id), user=self.user,
            limit=limit, offset=offset, **filters,
        )

    @staticmethod
    def _refill(search, limit, visible, identity):
        if limit <= 0:
            return []
        window, seen = limit, set()
        while True:
            candidates = search(window)
            allowed = [candidate for candidate in candidates if visible(candidate)]
            ids = {identity(candidate) for candidate in candidates}
            if len(allowed) >= limit or len(candidates) < window or not ids.difference(seen):
                return allowed[:limit]
            seen.update(ids)
            window *= 2

    def search_memories(self, query, repo_id=None, *, limit=10, **filters):
        repo_id = self._repo(repo_id)
        return self._refill(
            lambda window: self.storage.search_memories(
                query=query, repo_id=repo_id, limit=window, **filters
            ), limit, self._visible, lambda record: record["id"],
        )

    def search_turn_keys(self, query, *, repo_id=None, limit=30, **filters):
        repo_id = self._repo(repo_id)
        return self._refill(
            lambda window: self.storage.search_turn_keys(
                query, repo_id=repo_id, limit=window, **filters
            ), limit, lambda hit: self._visible(hit["memory"]),
            lambda hit: (hit["memory"]["id"], tuple(hit.get("span") or ())),
        )

    def get_active_intents(self, repo_id=None, status="active"):
        return [
            intent for intent in self.storage.get_active_intents(
                repo_id=self._repo(repo_id), status=status
            ) if self._visible(intent, scope_field="context")
        ]

    def set_intent(self, description, priority=0, context=None, repo_id=None):
        return self.storage.set_intent(
            description=description, priority=priority, context=self._owned_metadata(context),
            repo_id=self._repo(repo_id),
        )

    def get_related_memories(self, memory_id, relationship=None):
        if not self.peek_memory(memory_id):
            return []
        return [
            record for record in self.storage.get_related_memories(memory_id, relationship)
            if self._visible(record)
        ]

    def get_all_relationships(self, repo_id=None):
        visible_ids = {}

        def visible(memory_id):
            if memory_id not in visible_ids:
                visible_ids[memory_id] = bool(self.peek_memory(memory_id))
            return visible_ids[memory_id]

        return [
            edge for edge in self.storage.get_all_relationships(repo_id=self._repo(repo_id))
            if visible(edge["source_id"]) and visible(edge["target_id"])
        ]

    def get_repository(self, repo_id):
        return self.storage.get_repository(repo_id) if repo_id == self.repo_id else None

    def get_evidence(self, evidence_id):
        record = self.storage.get_evidence(evidence_id)
        return record if self._visible(record) else None

    def get_authority_attestation(self, belief_id):
        if not self.peek_memory(belief_id):
            return None
        return self.storage.get_authority_attestation(belief_id)

    def get_stats(self, repo_id=None):
        repo_id = self._repo(repo_id)
        if has_admin_privileges(self.user) or self.user.is_local_owner:
            return self.storage.get_stats(repo_id=repo_id)
        by_layer, by_category = Counter(), Counter()
        for memory in iter_visible_memories(self.storage, repo_id=repo_id, user=self.user):
            by_layer[memory["layer"]] += 1
            by_category[memory["category"]] += 1
        return {
            "memories_by_layer": dict(by_layer), "memories_by_category": dict(by_category),
            "total_memories": sum(by_layer.values()),
            "active_intents": len(self.get_active_intents(repo_id)),
            "total_relationships": len(self.get_all_relationships(repo_id)),
        }

    def _utility_args(self, repo_id, memory_id, event_type):
        repo_id = self._repo(repo_id)
        if memory_id is not None and not self.peek_memory(memory_id):
            raise ValueError("Memory not found")
        return {"repo_id": repo_id, "memory_id": memory_id, "event_type": event_type}

    def inspect_recall_utility(self, memory_id=None, repo_id=None, event_type=None, limit=50):
        return inspect_visible_utility(
            self.storage, self.user, limit=limit,
            **self._utility_args(repo_id, memory_id, event_type),
        )

    def reset_recall_utility(self, memory_id=None, repo_id=None, event_type=None):
        return reset_visible_utility(
            self.storage, self.user, **self._utility_args(repo_id, memory_id, event_type),
        )

    def verify_recall_utility(self, memory_id=None, repo_id=None, event_type=None):
        return verify_visible_utility(
            self.storage, self.user, **self._utility_args(repo_id, memory_id, event_type),
        )
