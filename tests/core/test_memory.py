"""Basic tests for Visp Memory system."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from visp_memory import Memory, MemoryConfig
from visp_memory.core.indexing import ReindexScope
from visp_memory.core.storage import CHROMADB_AVAILABLE, LocalStorage
from visp_memory.core.trust import Provenance, WriteChannel, assess, provenance_of, provenance_tag


@pytest.fixture
def memory():
    """Create a memory instance with temp directory."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        config = MemoryConfig()
        config.storage.data_dir = Path(tmpdir)
        config.embedding.provider = "noop"
        yield Memory(config=config)


def test_client_mode_does_not_initialize_local_embedding_provider():
    config = MemoryConfig()
    config.storage.mode = "client"
    config.storage.server_url = "http://127.0.0.1:65535"
    config.embedding.provider = "auto"

    with patch("visp_memory.core.embeddings.get_embedding_provider") as get_embedding_provider:
        Memory(config=config)

    get_embedding_provider.assert_not_called()


def test_direct_facade_writes_default_to_unknown_and_replace_self_claims(memory):
    claimed = [provenance_tag(Provenance.AUTHORED)]
    memory_ids = [
        memory.record("Direct record", tags=claimed),
        memory.decision("Direct decision", "Because", tags=claimed),
        memory.learn("Direct knowledge", tags=claimed),
        memory.warn("direct.py", "Direct warning", tags=claimed),
    ]

    for memory_id in memory_ids:
        stored = memory._storage.get_memory(memory_id)
        assert provenance_of(stored) is Provenance.UNKNOWN
        assert provenance_tag(Provenance.AUTHORED) not in stored["tags"]
        assert assess(stored).injectable is False


def test_direct_layer_writes_default_to_unknown_and_replace_self_claims(memory):
    claimed = [provenance_tag(Provenance.AUTHORED)]
    memory_ids = [
        memory.episodic.record("Direct layer event", tags=claimed),
        memory.semantic.establish("Direct layer knowledge", tags=claimed),
    ]

    for memory_id in memory_ids:
        stored = memory._storage.get_memory(memory_id)
        assert provenance_of(stored) is Provenance.UNKNOWN
        assert provenance_tag(Provenance.AUTHORED) not in stored["tags"]
        assert stored["metadata"]["write_channel"] == WriteChannel.LIBRARY.value
        assert assess(stored).injectable is False


def test_direct_layer_convenience_methods_thread_package_channel(memory):
    memory_ids = [
        memory.episodic.bug(
            "MCP bug",
            tags=[provenance_tag(Provenance.AUTHORED)],
            _write_channel=WriteChannel.MCP,
        ),
        memory.semantic.known_issue(
            "MCP issue",
            tags=[provenance_tag(Provenance.AUTHORED)],
            _write_channel=WriteChannel.MCP,
        ),
    ]

    for memory_id in memory_ids:
        stored = memory._storage.get_memory(memory_id)
        assert provenance_of(stored) is Provenance.ASSISTED
        assert stored["metadata"]["write_channel"] == WriteChannel.MCP.value


def test_memory_uses_arcadedb_storage_when_configured(tmp_path, monkeypatch):
    import visp_memory.core.memory as memory_module

    created = {}

    class FakeArcadeDbStorage:
        def __init__(self, data_dir, embedding_fn=None):
            created["data_dir"] = data_dir
            created["embedding_fn"] = embedding_fn

    monkeypatch.setattr(memory_module, "ArcadeDbStorage", FakeArcadeDbStorage)

    config = MemoryConfig()
    config.storage.backend = "arcadedb"
    config.storage.data_dir = tmp_path
    config.embedding.provider = "noop"

    memory = Memory(config=config)

    assert isinstance(memory._storage, FakeArcadeDbStorage)
    assert created["data_dir"] == tmp_path
    assert callable(created["embedding_fn"])


def test_local_storage_uses_dimension_specific_vector_collection(tmp_path):
    if not CHROMADB_AVAILABLE:
        pytest.skip("ChromaDB not installed")

    class FakeEmbeddingProvider:
        dimension = 3

        def embed(self, text):
            return [float(len(text)), 0.0, 1.0]

    provider = FakeEmbeddingProvider()
    storage = LocalStorage(tmp_path, embedding_fn=provider.embed)

    storage.store_memory("Docker uses semantic embeddings", layer="episodic")

    assert "episodic" in storage._collections
    collection = storage._collections["episodic"]
    assert collection.name == "memories_episodic_3"


def test_local_storage_rebuild_embedding_index_scopes_by_repo_and_layer(tmp_path, monkeypatch):
    class FakeEmbeddingProvider:
        dimension = 3

        def embed(self, text):
            return [float(len(text)), 1.0, 0.0]

    class FakeClient:
        def list_collections(self):
            return []

    class FakeCollection:
        def __init__(self):
            self.upserts = []

        def count(self):
            return 0

        def upsert(self, **kwargs):
            self.upserts.append(kwargs)

    provider = FakeEmbeddingProvider()
    storage = LocalStorage(tmp_path, embedding_fn=provider.embed)
    collection = FakeCollection()
    monkeypatch.setattr("visp_memory.core.storage.CHROMADB_AVAILABLE", True)
    monkeypatch.setattr(storage, "_get_chroma", lambda: FakeClient())
    monkeypatch.setattr(storage, "_get_collection", lambda _layer: collection)

    storage.store_memory("Repo A event", layer="episodic", repo_id="repo-a", auto_link=False)
    storage.store_memory("Repo B event", layer="episodic", repo_id="repo-b", auto_link=False)
    storage.store_memory("Repo A fact", layer="semantic", repo_id="repo-a", auto_link=False)
    collection.upserts.clear()

    dry_run = storage.rebuild_embedding_index(
        scope=ReindexScope(repo_id="repo-a", layer="episodic"),
        dry_run=True,
    )
    assert dry_run.status == "ready"
    assert dry_run.matched_memories == 1
    assert collection.upserts == []

    result = storage.rebuild_embedding_index(
        scope=ReindexScope(repo_id="repo-a", layer="episodic"),
        dry_run=False,
    )
    assert result.status == "completed"
    assert result.reindexed_memories == 1
    assert collection.upserts[0]["documents"] == ["Repo A event"]


def test_update_memory_refreshes_vector_embedding(tmp_path, monkeypatch):
    class FakeEmbeddingProvider:
        dimension = 2

        def embed(self, text):
            return [float(len(text)), 0.0]

    class FakeCollection:
        def __init__(self):
            self.updated = None

        def upsert(self, **kwargs):
            pass

        def update(self, **kwargs):
            self.updated = kwargs

    provider = FakeEmbeddingProvider()
    storage = LocalStorage(tmp_path, embedding_fn=provider.embed)
    collection = FakeCollection()
    monkeypatch.setattr(storage, "_get_collection", lambda _layer: collection)

    memory_id = storage.store_memory("Old content", auto_link=False)
    assert storage.update_memory(memory_id, content="New vector content") is True

    assert collection.updated["documents"] == ["New vector content"]
    assert collection.updated["embeddings"] == [[18.0, 0.0]]


def test_archived_memories_are_excluded_from_default_list_and_search(tmp_path):
    storage = LocalStorage(tmp_path)
    active_id = storage.store_memory("Active deploy note", auto_link=False)
    archived_id = storage.store_memory("Archived deploy note", auto_link=False)

    assert storage.update_memory(archived_id, status="archived") is True

    listed_ids = {item["id"] for item in storage.list_memories(status="active")}
    assert active_id in listed_ids
    assert archived_id not in listed_ids

    archived_ids = {item["id"] for item in storage.list_memories(status="archived")}
    assert archived_ids == {archived_id}

    all_ids = {item["id"] for item in storage.list_memories(status="all")}
    assert {active_id, archived_id}.issubset(all_ids)

    search_ids = {item["id"] for item in storage.search_memories("deploy")}
    assert active_id in search_ids
    assert archived_id not in search_ids


def test_memory_status_migration_defaults_old_rows_to_active(tmp_path):
    db_dir = tmp_path / "legacy"
    db_dir.mkdir()
    db_path = db_dir / "memories.db"

    import sqlite3

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                layer TEXT NOT NULL DEFAULT 'episodic',
                category TEXT DEFAULT 'general',
                importance REAL DEFAULT 0.5,
                repo_id TEXT DEFAULT NULL,
                access_count INTEGER DEFAULT 0,
                tags TEXT DEFAULT '[]',
                metadata TEXT DEFAULT '{}',
                source_ids TEXT DEFAULT '[]',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                compressed_at TIMESTAMP DEFAULT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO memories (id, content, layer) VALUES (?, ?, ?)",
            ("legacy-memory", "Legacy active memory", "episodic"),
        )

    storage = LocalStorage(db_dir)
    memory = storage.get_memory("legacy-memory")

    assert memory["status"] == "active"
    assert memory["quality_flags"] == []


def test_local_storage_rejects_repo_dependencies_with_missing_repositories(tmp_path):
    storage = LocalStorage(tmp_path)

    with pytest.raises(ValueError, match="Repository not found: missing-source"):
        storage.add_repo_dependency("missing-source", "missing-target", "depends_on")

    storage.store_repository({"id": "app-repo", "name": "App"})

    with pytest.raises(ValueError, match="Repository not found: missing-target"):
        storage.add_repo_dependency("app-repo", "missing-target", "depends_on")

    assert storage.get_repo_dependencies("app-repo") == []


def test_local_storage_rejects_duplicate_repository_ids(tmp_path):
    storage = LocalStorage(tmp_path)

    storage.store_repository({"id": "app-repo", "name": "App", "description": "Original"})

    with pytest.raises(ValueError, match="Repository already exists: app-repo"):
        storage.store_repository(
            {"id": "app-repo", "name": "Renamed", "description": "Replacement"}
        )

    assert storage.get_repository("app-repo")["description"] == "Original"


def test_local_storage_auto_links_source_ids(tmp_path):
    storage = LocalStorage(tmp_path)
    source_id = storage.store_memory(
        "Release smoke test captured the packaged dashboard behavior",
        repo_id="repo-a",
        auto_link=False,
    )

    target_id = storage.store_memory(
        "Dashboard release knowledge was compressed from the release smoke test",
        layer="semantic",
        repo_id="repo-a",
        source_ids=[source_id],
        auto_link=False,
    )

    relationships = storage.get_all_relationships(repo_id="repo-a")
    relationship = relationships[0]

    assert len(relationships) == 1
    assert relationship["source_id"] == source_id
    assert relationship["target_id"] == target_id
    assert relationship["relationship"] == "derived_from"
    assert relationship["strength"] == 1.0
    assert relationship["evidence"] == {
        "confidence": "ambiguous",
        "confidence_score": 1.0,
        "source": "unspecified",
        "source_file": None,
        "source_location": None,
        "reason": "Relationship created without evidence metadata.",
        "created_by": None,
        "created_at": relationship["created_at"],
    }


def test_local_storage_relationship_evidence_round_trips(tmp_path):
    storage = LocalStorage(tmp_path)
    source_id = storage.store_memory("Incident was caused by cache expiry", auto_link=False)
    target_id = storage.store_memory("Fix refreshed the cache before expiry", auto_link=False)

    rel_id = storage.add_relationship(
        source_id,
        target_id,
        "resolved_by",
        strength=0.75,
        evidence={
            "confidence": "observed",
            "confidence_score": 0.9,
            "source": "test",
            "source_file": "tests/core/test_memory.py",
            "source_location": "test_local_storage_relationship_evidence_round_trips",
            "reason": "The regression fixture directly links the incident and fix.",
            "created_by": "pytest",
        },
    )

    relationship = storage.get_all_relationships()[0]

    assert relationship["id"] == rel_id
    assert relationship["evidence"] == {
        "confidence": "observed",
        "confidence_score": 0.9,
        "source": "test",
        "source_file": "tests/core/test_memory.py",
        "source_location": "test_local_storage_relationship_evidence_round_trips",
        "reason": "The regression fixture directly links the incident and fix.",
        "created_by": "pytest",
        "created_at": relationship["created_at"],
    }

    related = storage.get_related_memories(source_id)
    assert related[0]["relationship_evidence"] == relationship["evidence"]


def test_local_storage_relationship_evidence_defaults_and_bounds(tmp_path):
    storage = LocalStorage(tmp_path)
    source_id = storage.store_memory("A warning surfaced during release", auto_link=False)
    target_id = storage.store_memory("The release task reused the warning", auto_link=False)

    storage.add_relationship(
        source_id,
        target_id,
        "related",
        strength=0.65,
        evidence={"confidence": "manual", "confidence_score": 2.5},
    )

    relationship = storage.get_all_relationships()[0]

    assert relationship["evidence"] == {
        "confidence": "manual",
        "confidence_score": 1.0,
        "source": "unspecified",
        "source_file": None,
        "source_location": None,
        "reason": "Relationship created without evidence metadata.",
        "created_by": None,
        "created_at": relationship["created_at"],
    }

    with pytest.raises(ValueError, match="Relationship confidence"):
        storage.add_relationship(
            source_id,
            target_id,
            "related",
            evidence={"confidence": "trusted"},
        )


def test_local_storage_relationship_evidence_migration_defaults_old_rows(tmp_path):
    db_dir = tmp_path / "legacy-relationships"
    db_dir.mkdir()
    db_path = db_dir / "memories.db"

    import sqlite3

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                layer TEXT NOT NULL DEFAULT 'episodic',
                category TEXT DEFAULT 'general',
                importance REAL DEFAULT 0.5,
                repo_id TEXT DEFAULT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE relationships (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relationship TEXT NOT NULL,
                strength REAL DEFAULT 1.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO memories (id, content, layer, category, importance, repo_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("source-memory", "Legacy source", "episodic", "general", 0.5, "repo-a"),
        )
        conn.execute(
            """
            INSERT INTO memories (id, content, layer, category, importance, repo_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("target-memory", "Legacy target", "episodic", "general", 0.5, "repo-a"),
        )
        conn.execute(
            """
            INSERT INTO relationships (
                id, source_id, target_id, relationship, strength, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-rel",
                "source-memory",
                "target-memory",
                "related",
                0.65,
                "2026-06-06T00:00:00Z",
            ),
        )

    storage = LocalStorage(db_dir)
    relationship = storage.get_all_relationships(repo_id="repo-a")[0]

    assert relationship["id"] == "legacy-rel"
    assert relationship["evidence"] == {
        "confidence": "ambiguous",
        "confidence_score": 0.65,
        "source": "legacy",
        "source_file": None,
        "source_location": None,
        "reason": "Legacy relationship without evidence metadata.",
        "created_by": None,
        "created_at": "2026-06-06T00:00:00Z",
    }


def test_local_storage_auto_links_similar_memories_in_same_repo(tmp_path):
    storage = LocalStorage(tmp_path)
    first_id = storage.store_memory(
        "Authentication token refresh uses mutex locking around the shared cache",
        repo_id="repo-a",
        auto_link=False,
    )
    second_id = storage.store_memory(
        "Authentication token refresh should acquire the mutex before shared cache writes",
        repo_id="repo-a",
    )

    relationships = storage.get_all_relationships(repo_id="repo-a")

    assert [
        (rel["source_id"], rel["target_id"], rel["relationship"])
        for rel in relationships
    ] == [(second_id, first_id, "related_to")]
    assert relationships[0]["strength"] >= 0.60


def test_memory_recall_filters_unrelated_queries_by_default(memory):
    memory.learn(
        "OpenRouter cloud embeddings power project recall",
        category="embeddings",
        importance=0.9,
    )

    assert memory.recall("banana bread recipe") == []


def test_local_storage_auto_links_do_not_cross_repositories(tmp_path):
    storage = LocalStorage(tmp_path)
    storage.store_memory(
        "Authentication token refresh uses mutex locking around the shared cache",
        repo_id="repo-a",
        auto_link=False,
    )
    storage.store_memory(
        "Authentication token refresh uses mutex locking around the shared cache",
        repo_id="repo-b",
    )

    assert storage.get_all_relationships(repo_id="repo-a") == []
    assert storage.get_all_relationships(repo_id="repo-b") == []


def test_local_storage_rejects_duplicate_user_ids(tmp_path):
    storage = LocalStorage(tmp_path)

    storage.store_user({"id": "alice-id", "username": "alice", "display_name": "Alice"})

    with pytest.raises(ValueError, match="User already exists: alice-id"):
        storage.store_user(
            {"id": "alice-id", "username": "renamed", "display_name": "Replacement"}
        )

    assert storage.get_user("alice-id")["display_name"] == "Alice"


def test_local_storage_rejects_duplicate_team_ids(tmp_path):
    storage = LocalStorage(tmp_path)

    storage.store_team({"id": "team-a", "name": "Team A", "description": "Original"})

    with pytest.raises(ValueError, match="Team already exists: team-a"):
        storage.store_team(
            {"id": "team-a", "name": "Renamed", "description": "Replacement"}
        )

    assert storage.get_team("team-a")["description"] == "Original"


def test_local_storage_rejects_team_membership_with_missing_entities(tmp_path):
    storage = LocalStorage(tmp_path)

    assert storage.add_team_member("missing-team", "missing-user") is False

    storage.store_team({"id": "team-a", "name": "Team A"})

    assert storage.add_team_member("team-a", "missing-user") is False
    assert storage.get_user_teams("missing-user") == []


class TestEpisodicMemory:
    """Tests for episodic memory layer."""

    def test_record_event(self, memory):
        """Can record a basic event."""
        mem_id = memory.record("Fixed a bug", category="bug_fixed")
        assert mem_id is not None
        assert len(mem_id) == 16

    def test_record_decision(self, memory):
        """Can record a decision with reasoning."""
        mem_id = memory.decision(
            what="Use PostgreSQL", why="Need ACID compliance", alternatives=["MongoDB", "MySQL"]
        )
        assert mem_id is not None

        # Verify it was stored
        results = memory.recall("PostgreSQL")
        assert len(results) > 0
        assert "PostgreSQL" in results[0]["content"]

    def test_record_bug(self, memory):
        """Can record a bug fix."""
        mem_id = memory.episodic.bug(
            description="Race condition in auth",
            cause="Missing mutex",
            fix="Added lock",
            files=["auth/token.py"],
        )
        assert mem_id is not None


class TestSemanticMemory:
    """Tests for semantic memory layer."""

    def test_establish_knowledge(self, memory):
        """Can establish semantic knowledge."""
        mem_id = memory.learn(
            "Always use mutex locks in auth module", category="invariant", importance=0.9
        )
        assert mem_id is not None

    def test_add_warning(self, memory):
        """Can add warnings for fragile areas."""
        mem_id = memory.warn(
            area="database/migrations", warning="Always backup before running", severity=0.8
        )
        assert mem_id is not None

        warnings = memory.semantic.get_warnings()
        assert len(warnings) > 0

    def test_add_convention(self, memory):
        """Can add conventions."""
        mem_id = memory.semantic.convention(
            rule="All API endpoints return JSON", rationale="Consistency"
        )
        assert mem_id is not None

        conventions = memory.semantic.get_conventions()
        assert len(conventions) > 0


class TestIntentMemory:
    """Tests for intent memory layer."""

    def test_set_goal(self, memory):
        """Can set a goal."""
        intent_id = memory.goal("Implement OAuth2", priority=2, constraints=["No breaking changes"])
        assert intent_id is not None

        intents = memory.intent.get_active()
        assert len(intents) > 0

    def test_working_on(self, memory):
        """Can track current work."""
        intent_id = memory.working_on("Token refresh endpoint", files=["auth/refresh.py"])
        assert intent_id is not None

        current = memory.intent.get_working_on()
        assert current is not None
        assert "Token refresh" in current["description"]

    def test_done_records_outcome_without_clearing_task(self, memory):
        """Done is a compatibility surface, not a workflow authority."""
        intent_id = memory.working_on("Some task")

        assert memory.done() == 1

        current = memory.intent.get_working_on()
        assert current["id"] == intent_id
        outcome = current["context"]["outcome_history"][-1]
        assert outcome["outcome"] == "completed"
        assert outcome["provenance"]["channel"] == "library"
        assert outcome["authoritative"] is False
        assert outcome["status_changed"] is False

    def test_done_only_records_configured_repo_task_outcome(self, tmp_path):
        """Done records history only for current tasks in the configured repository."""
        config = MemoryConfig(project_name="done-scope-test", repo_id="repo-a")
        config.storage.data_dir = tmp_path / "data"
        config.embedding.provider = "noop"
        memory = Memory(config=config)

        memory.working_on("Repo A task")
        memory.working_on("Repo B task", repo_id="repo-b")

        assert memory.done() == 1

        repo_a_task = memory.intent.get_working_on(repo_id="repo-a")
        assert repo_a_task is not None
        assert repo_a_task["status"] == "active"
        assert len(repo_a_task["context"]["outcome_history"]) == 1
        repo_b_task = memory.intent.get_working_on(repo_id="repo-b")
        assert repo_b_task is not None
        assert repo_b_task["description"] == "WORKING ON: Repo B task"
        assert "outcome_history" not in repo_b_task["context"]

    def test_intent_status_update_records_history_without_transition(self, memory):
        intent_id = memory.goal("Ship the release")

        assert memory.intent.update(intent_id, status="completed") is True

        intent = next(item for item in memory.intent.get_active() if item["id"] == intent_id)
        assert intent["status"] == "active"
        outcome = intent["context"]["outcome_history"][-1]
        assert outcome["outcome"] == "completed"
        assert outcome["status_changed"] is False

    def test_complete_close_and_clear_all_are_history_only(self, memory):
        first_id = memory.goal("First goal")
        second_id = memory.goal("Second goal")

        assert memory.intent.complete(first_id) is True
        assert memory.intent.close(second_id) is True
        assert memory.intent.clear_all() == 2

        active = {item["id"]: item for item in memory.intent.get_active()}
        assert {first_id, second_id} <= set(active)
        assert [entry["outcome"] for entry in active[first_id]["context"]["outcome_history"]] == [
            "completed",
            "completed",
        ]
        assert [entry["outcome"] for entry in active[second_id]["context"]["outcome_history"]] == [
            "closed",
            "completed",
        ]

    def test_local_storage_rejects_direct_status_mutations(self, memory):
        intent_id = memory.goal("Preserve backend boundary")

        assert memory._storage.complete_intent(intent_id) is False
        assert memory._storage.update_intent(intent_id, status="completed") is False

        intent = next(item for item in memory.intent.get_active() if item["id"] == intent_id)
        assert intent["status"] == "active"

    def test_local_storage_mixed_update_ignores_status(self, memory):
        intent_id = memory.goal("Update description only")

        assert memory._storage.update_intent(
            intent_id,
            description="Updated description",
            status="completed",
        ) is True

        intent = next(item for item in memory.intent.get_active() if item["id"] == intent_id)
        assert intent["description"] == "Updated description"
        assert intent["status"] == "active"

    def test_external_kit_outcome_appends_provenance_history_only(self, memory):
        intent_id = memory.goal("Wait for Kit verdict")

        recorded = memory.intent.record_outcome(
            intent_id,
            "completed",
            actor_id="kit-run-42",
            channel="kit-contract",
        )

        assert recorded is True
        intent = next(item for item in memory.intent.get_active() if item["id"] == intent_id)
        assert intent["status"] == "active"
        outcome = intent["context"]["outcome_history"][-1]
        assert outcome["actor_id"] == "kit-run-42"
        assert outcome["provenance"] == {
            "source": "kit",
            "channel": "kit-contract",
            "tier": "derived",
        }
        assert outcome["authoritative"] is False
        assert outcome["status_changed"] is False

    def test_outcome_history_rejects_self_declared_channel(self, memory):
        intent_id = memory.goal("Reject invented outcome provenance")

        with pytest.raises(ValueError, match="Unknown memory write channel"):
            memory.intent.record_outcome(
                intent_id,
                "completed",
                actor_id="caller",
                channel="claimed-human",
            )

        intent = next(item for item in memory.intent.get_active() if item["id"] == intent_id)
        assert "outcome_history" not in intent["context"]

    def test_historical_completed_intent_is_preserved(self, memory):
        intent_id = memory.goal("Historical completed intent")
        with memory._storage._get_db() as conn:
            conn.execute("UPDATE intents SET status = 'completed' WHERE id = ?", (intent_id,))
            conn.commit()

        assert memory._storage.complete_intent(intent_id) is False
        historical = memory._storage.get_active_intents(status="completed")
        assert historical[0]["id"] == intent_id
        assert historical[0]["status"] == "completed"


class TestSearch:
    """Tests for search functionality."""

    def test_recall_finds_memories(self, memory):
        """Recall can find memories by content."""
        memory.record("Authentication system updated")
        memory.record("Database migration complete")
        memory.learn("Auth requires special handling")

        results = memory.recall("authentication")
        assert len(results) > 0
        assert "relevance_score" in results[0]

    def test_relevant_for_task(self, memory):
        """Can get relevant memories for a task."""
        memory.learn("Auth module is fragile")
        memory.warn("auth/", "Test thoroughly")
        memory.record("Fixed auth bug")

        relevant = memory.relevant_for(task="fix authentication", files=["auth/login.py"])
        assert "knowledge" in relevant
        assert "warnings" in relevant

    def test_recall_is_read_only_for_access_metrics(self, memory):
        """Search should not count as an explicit memory access."""
        mem_id = memory.record("Authentication system updated")

        memory.recall("authentication")

        stored = memory._storage._get_memory_row(mem_id, track_access=False)
        assert stored["access_count"] == 0

    def test_recall_can_log_surfaced_utility_without_access_metrics(self, memory):
        """Optional recall feedback logs surfaced memories without explicit access."""
        mem_id = memory.record("Authentication system updated")

        results = memory.recall("authentication", log_utility=True, task_id="task-1")

        assert results[0]["id"] == mem_id
        stored = memory._storage._get_memory_row(mem_id, track_access=False)
        assert stored["access_count"] == 0

        report = memory.inspect_utility_signals(memory_id=mem_id)
        assert report["summary"]["by_event_type"] == {"surfaced": 1}
        assert report["signals"][0]["counts"] == {"surfaced": 1}
        assert report["events"][0]["query_hash"]
        assert "query" not in report["events"][0]
        assert report["events"][0]["task_id"] == "task-1"

    def test_utility_feedback_aggregates_sanitizes_and_resets(self, memory):
        """Utility signals are inspectable, sanitized, and resettable."""
        mem_id = memory.record("Authentication system updated")

        memory.record_utility_feedback(
            mem_id,
            "used",
            metadata={
                "source": "test",
                "prompt": "full prompt should not be stored",
                "response": "full response should not be stored",
            },
        )
        memory.record_utility_feedback(mem_id, "dismissed")
        memory.record_utility_feedback(mem_id, "task-linked", task_id="task-1")
        memory.record_utility_feedback(mem_id, "outcome-linked", outcome="outcome-1")

        report = memory.inspect_utility_signals(memory_id=mem_id)

        assert report["summary"]["total_events"] == 4
        assert report["signals"][0]["counts"] == {
            "dismissed": 1,
            "outcome_linked": 1,
            "task_linked": 1,
            "used": 1,
        }
        assert report["signals"][0]["utility_rank_adjustment"] != 0
        assert report["events"][-1]["metadata"] == {"source": "test"}
        assert report["events"][0]["outcome"] == "outcome-1"

        deleted = memory.reset_utility_signals(memory_id=mem_id, event_type="dismissed")
        assert deleted == 1
        report = memory.inspect_utility_signals(memory_id=mem_id)
        assert "dismissed" not in report["summary"]["by_event_type"]

        assert memory.reset_utility_signals(memory_id=mem_id) == 3
        assert memory.inspect_utility_signals(memory_id=mem_id)["summary"]["total_events"] == 0

    def test_recall_uses_and_exposes_intent_aware_factors(self, memory):
        """Contextual recall factors influence ordering and are exposed."""
        memory.goal(
            "Stabilize auth refresh rollout",
            constraints=["No breaking API"],
            repo_id="app",
        )
        memory.working_on("Fix auth refresh flow", files=["auth/refresh.py"], repo_id="app")
        target = memory.record(
            "Auth refresh fix for auth/refresh.py in session s-1 uses api-client "
            "and respects No breaking API",
            importance=0.2,
            repo_id="app",
            context={"files": ["auth/refresh.py"], "session_id": "s-1"},
        )
        other = memory.record(
            "Auth cache note",
            importance=0.9,
            repo_id="app",
        )

        results = memory.recall(
            "auth refresh",
            repo_id="app",
            task="Fix auth refresh flow",
            files=["auth/refresh.py"],
            session_id="s-1",
            dependencies=["api-client"],
            constraints=["No breaking API"],
            min_score=0.0,
            limit=2,
        )

        assert [result["id"] for result in results] == [target, other]
        factors = results[0]["ranking_factors"]
        assert set(factors) == {
            "active_intent",
            "constraint",
            "dependency",
            "file",
            "repo",
            "session",
            "task",
        }
        assert any("file" in item for item in results[0]["ranking_explanation"])

    def test_proactive_file_recall_exposes_file_ranking_factor(self, memory):
        """Proactive recall annotates surfaced memories with context factors."""
        from visp_memory.recall.proactive import ProactiveRecall

        memory.working_on("Fix auth refresh flow", files=["auth/refresh.py"])
        memory.record(
            "Fixed bug in auth/refresh.py",
            category="bug_fixed",
            context={"files": ["auth/refresh.py"]},
            _write_channel=WriteChannel.TEST_CAPTURE,
        )

        result = ProactiveRecall(memory).on_file_open("auth/refresh.py")

        assert result["bugs"][0]["ranking_factors"]["file"]["score"] == 1.0

    def test_recall_does_not_duplicate_layers(self, memory):
        """Layer fan-out should not duplicate results from the fallback search path."""
        memory.record("Authentication system updated")

        results = memory.recall("authentication")

        ids = [result["id"] for result in results]
        assert len(ids) == len(set(ids))

    def test_noop_embeddings_use_text_relevance_for_limited_results(self, memory):
        """Noop vectors should not let arbitrary vector order hide lexical matches."""
        memory.record("Database migration complete", importance=0.9)
        memory.record("Authentication token refresh fixed", importance=0.4)

        results = memory.recall("authentication", limit=1)

        assert [result["content"] for result in results] == ["Authentication token refresh fixed"]

    def test_update_memory_refreshes_vector_metadata_for_filter_fields(self, tmp_path):
        """Importance and tags updates should keep vector-store metadata in sync."""

        class FakeCollection:
            def __init__(self):
                self.update_calls = []

            def update(self, **kwargs):
                self.update_calls.append(kwargs)

        storage = LocalStorage(tmp_path)
        mem_id = storage.store_memory(
            "Indexed memory",
            repo_id="repo-a",
            importance=0.2,
            tags=["old"],
        )
        collection = FakeCollection()
        storage._get_collection = lambda layer: collection

        assert storage.update_memory(mem_id, importance=0.9, tags=["new"]) is True

        assert collection.update_calls == [
            {
                "ids": [mem_id],
                "metadatas": [
                    {
                        "category": "general",
                        "importance": 0.9,
                        "tags": '["new"]',
                        "status": "active",
                        "repo_id": "repo-a",
                    }
                ],
            }
        ]


class TestRepositoryIsolation:
    """Tests for repository isolation behavior."""

    def test_search_filters_by_repo_id(self, memory):
        memory.record("Shared auth convention in repo A", repo_id="repo-a")
        memory.record("Shared auth convention in repo B", repo_id="repo-b")

        results = memory._storage.search_memories("auth", repo_id="repo-a")

        assert {result["repo_id"] for result in results} == {"repo-a"}

    def test_memory_relationships_cannot_cross_repositories(self, memory):
        source = memory.record("Repo A event", repo_id="repo-a")
        target = memory.record("Repo B event", repo_id="repo-b")

        with pytest.raises(ValueError, match="cannot cross repository"):
            memory._storage.add_relationship(source, target, "related")

    def test_related_memories_stay_within_source_repo(self, memory):
        source = memory.record("Repo A event", repo_id="repo-a")
        target = memory.record("Repo A related event", repo_id="repo-a")
        memory._storage.add_relationship(source, target, "related")

        related = memory._storage.get_related_memories(source)

        assert [item["id"] for item in related] == [target]

    def test_relevant_for_uses_configured_repo_id(self, tmp_path):
        config = MemoryConfig(project_name="relevant-scope-test", repo_id="repo-a")
        config.storage.data_dir = tmp_path / "data"
        config.embedding.provider = "noop"
        memory = Memory(config=config)

        memory.learn("Repo A auth knowledge", _write_channel=WriteChannel.CLI)
        memory.learn(
            "Repo B auth knowledge", repo_id="repo-b", _write_channel=WriteChannel.CLI
        )
        memory.warn("auth/login.py", "Repo A warning", _write_channel=WriteChannel.CLI)
        memory.warn(
            "auth/login.py",
            "Repo B warning",
            repo_id="repo-b",
            _write_channel=WriteChannel.CLI,
        )
        memory.record("Repo A auth history", _write_channel=WriteChannel.CLI)
        memory.record(
            "Repo B auth history", repo_id="repo-b", _write_channel=WriteChannel.CLI
        )

        relevant = memory.relevant_for(task="auth", files=["auth/login.py"], limit=10)

        for section in ("knowledge", "warnings", "history"):
            assert relevant[section]
            assert {item["repo_id"] for item in relevant[section]} == {"repo-a"}
            assert all("Repo B" not in item["content"] for item in relevant[section])

    def test_context_uses_configured_repo_id(self, tmp_path):
        config = MemoryConfig(project_name="context-scope-test", repo_id="repo-a")
        config.storage.data_dir = tmp_path / "data"
        config.embedding.provider = "noop"
        memory = Memory(config=config)

        memory.warn("deploy", "Repo A warning", _write_channel=WriteChannel.CLI)
        memory.semantic.convention(
            "Repo A convention", repo_id="repo-a", _write_channel=WriteChannel.CLI
        )
        memory.semantic.known_issue(
            "Repo A issue", repo_id="repo-a", _write_channel=WriteChannel.CLI
        )
        memory.record("Repo A event", _write_channel=WriteChannel.CLI)
        memory.goal("Repo A goal")
        memory.working_on("Repo A task")

        memory.warn(
            "deploy", "Repo B warning", repo_id="repo-b", _write_channel=WriteChannel.CLI
        )
        memory.semantic.convention(
            "Repo B convention", repo_id="repo-b", _write_channel=WriteChannel.CLI
        )
        memory.semantic.known_issue(
            "Repo B issue", repo_id="repo-b", _write_channel=WriteChannel.CLI
        )
        memory.record(
            "Repo B event", repo_id="repo-b", _write_channel=WriteChannel.CLI
        )
        memory.goal("Repo B goal", repo_id="repo-b")
        memory.working_on("Repo B task", repo_id="repo-b")

        context = memory.context(format="json")

        assert context["intent"]["current_task"] == "WORKING ON: Repo A task"
        assert context["intent"]["goals"] == ["WORKING ON: Repo A task", "Repo A goal"]
        assert context["knowledge"] == {
            "warnings": ["WARNING [deploy]: Repo A warning"],
            "conventions": ["Repo A convention"],
            "known_issues": ["Known Issue: Repo A issue"],
        }
        assert [event["event"] for event in context["history"]["recent_events"]] == [
            "Repo A event"
        ]
        assert context["meta"]["stats"]["total_memories"] == 4


class TestContext:
    """Tests for context generation."""

    def test_context_text_format(self, memory):
        """Can generate text context."""
        memory.goal("Test the system")
        memory.warn("tests/", "Flaky tests exist")

        context = memory.context(format="text")
        assert isinstance(context, str)
        assert "Memory Context" in context

    def test_context_json_format(self, memory):
        """Can generate JSON context."""
        memory.goal("Test the system")
        memory.learn("Tests run slowly")

        context = memory.context(format="json")
        assert isinstance(context, dict)
        assert "intent" in context
        assert "knowledge" in context


class TestStats:
    """Tests for statistics."""

    def test_stats_returns_counts(self, memory):
        """Stats returns memory counts."""
        memory.record("Event 1")
        memory.record("Event 2")
        memory.learn("Knowledge 1")

        stats = memory.stats()
        assert stats["total_memories"] >= 3
        assert "memories_by_layer" in stats


class TestImportExport:
    """Tests for import/export behavior."""

    def test_export_filters_to_configured_repo(self, tmp_path):
        config = MemoryConfig(project_name="export-test", repo_id="repo-a")
        config.storage.data_dir = tmp_path / "data"
        config.embedding.provider = "noop"
        memory = Memory(config=config)

        memory.record("Event in repo A")
        memory.learn("Knowledge in repo A")
        memory.goal("Goal in repo A")
        memory.record("Event in repo B", repo_id="repo-b")
        memory.learn("Knowledge in repo B", repo_id="repo-b")
        memory.goal("Goal in repo B", repo_id="repo-b")

        export_file = tmp_path / "repo-a-export.json"
        export_data = memory.export(export_file)

        exported = json.loads(export_file.read_text())
        assert exported["version"] == export_data["version"]
        assert [m["content"] for m in exported["memories"]["episodic"]] == ["Event in repo A"]
        assert [m["content"] for m in exported["memories"]["semantic"]] == ["Knowledge in repo A"]
        assert [i["description"] for i in exported["intents"]] == ["Goal in repo A"]

    def test_export_redacts_config_secrets(self, tmp_path):
        config = MemoryConfig(project_name="export-test", repo_id="repo-a")
        config.storage.data_dir = tmp_path / "data"
        config.embedding.provider = "noop"
        config.embedding.api_key = "embedding-secret"
        config.storage.api_key = "storage-secret"
        config.storage.jwt_token = "jwt-token-secret"
        config.storage.neo4j_password = "neo4j-secret"
        config.server.jwt_secret = "server-secret"
        config.server.api_keys = ["server-api-secret"]
        memory = Memory(config=config)

        export_file = tmp_path / "repo-a-export.json"
        export_data = memory.export(export_file)
        exported = json.loads(export_file.read_text())

        serialized_export = json.dumps(exported)
        for secret in (
            "embedding-secret",
            "storage-secret",
            "jwt-token-secret",
            "neo4j-secret",
            "server-secret",
            "server-api-secret",
        ):
            assert secret not in serialized_export

        assert export_data["config"]["embedding"]["api_key"] == "***REDACTED***"
        assert exported["config"]["storage"]["jwt_token"] == "***REDACTED***"
        assert exported["config"]["server"]["api_keys"] == ["***REDACTED***"]

    def test_export_scrubs_vector_payloads(self, tmp_path, monkeypatch):
        config = MemoryConfig(project_name="export-test", repo_id="repo-a")
        config.storage.data_dir = tmp_path / "data"
        config.embedding.provider = "noop"
        memory = Memory(config=config)

        def fake_list_memories(*, layer, **_kwargs):
            if layer == "episodic":
                return [
                    {
                        "id": "memory-1",
                        "content": "Exported event",
                        "layer": "episodic",
                        "embedding": [1.0, 2.0],
                        "embedding_1536": [3.0, 4.0],
                    }
                ]
            return []

        monkeypatch.setattr(memory._storage, "list_memories", fake_list_memories)

        export_data = memory.export()
        exported_memory = export_data["memories"]["episodic"][0]
        assert "embedding" not in exported_memory
        assert "embedding_1536" not in exported_memory

    def test_export_import_preserves_capture_manifest(self, tmp_path):
        from visp_memory.capture.git import CaptureManifest, capture_content_hash

        source_config = MemoryConfig(project_name="manifest-export", repo_id="repo-a")
        source_config.storage.data_dir = tmp_path / "source"
        source_config.embedding.provider = "noop"
        source = Memory(config=source_config)
        content_hash = capture_content_hash({"source": "abc"})
        CaptureManifest(source).record("git_commit", "abc", content_hash, ["mem-1"])

        export_file = tmp_path / "manifest-export.json"
        source.export(export_file)

        target_config = MemoryConfig(project_name="manifest-import", repo_id="repo-a")
        target_config.storage.data_dir = tmp_path / "target"
        target_config.embedding.provider = "noop"
        target = Memory(config=target_config)
        target.import_memories(export_file)

        status, entry = CaptureManifest(target).check("git_commit", "abc", content_hash)
        assert status == "unchanged"
        assert entry["output_memory_ids"] == ["mem-1"]

    def test_import_uses_configured_repo_when_export_has_no_repo_id(self, tmp_path):
        source_config = MemoryConfig(project_name="export-test", repo_id="source-repo")
        source_config.storage.data_dir = tmp_path / "source"
        source_config.embedding.provider = "noop"
        source = Memory(config=source_config)

        source.record("Source event")
        source.learn("Source knowledge")
        source.goal("Source goal")
        export_data = source.export()

        for layer in ("episodic", "semantic"):
            for item in export_data["memories"][layer]:
                item.pop("repo_id", None)
        for intent in export_data["intents"]:
            intent.pop("repo_id", None)

        export_file = tmp_path / "portable-export.json"
        export_file.write_text(json.dumps(export_data, default=str))

        target_config = MemoryConfig(project_name="export-test", repo_id="target-repo")
        target_config.storage.data_dir = tmp_path / "target"
        target_config.embedding.provider = "noop"
        target = Memory(config=target_config)
        target.import_memories(export_file)

        assert sorted(
            m["content"] for m in target._storage.list_memories(repo_id="target-repo")
        ) == [
            "Source event",
            "Source knowledge",
        ]
        imported_intents = target._storage.get_active_intents(repo_id="target-repo")
        assert [i["description"] for i in imported_intents] == ["Source goal"]

    def test_import_replaces_authored_claim_with_external_quarantine(self, tmp_path):
        config = MemoryConfig(project_name="import-test", repo_id="target-repo")
        config.storage.data_dir = tmp_path / "data"
        config.embedding.provider = "noop"
        memory = Memory(config=config)
        import_file = tmp_path / "claimed-authored.json"
        import_file.write_text(
            json.dumps(
                {
                    "memories": {
                        "episodic": [
                            {
                                "content": "Imported claimed authored content",
                                "tags": ["provenance:authored", "portable"],
                                "source": "authored",
                            }
                        ],
                        "semantic": [],
                    },
                    "intents": [],
                }
            )
        )

        memory.import_memories(import_file)

        imported = memory._storage.list_memories(repo_id="target-repo")[0]
        assert provenance_of(imported) is Provenance.EXTERNAL
        assert "portable" in imported["tags"]
        assert imported["source"] == "external"
        assert imported["metadata"]["write_channel"] == "import"
        assert assess(imported).injectable is False

    def test_import_rejects_non_object_payload(self, tmp_path):
        config = MemoryConfig(project_name="import-test", repo_id="target-repo")
        config.storage.data_dir = tmp_path / "data"
        config.embedding.provider = "noop"
        memory = Memory(config=config)

        import_file = tmp_path / "invalid-export.json"
        import_file.write_text(json.dumps(["not", "an", "export"]))

        with pytest.raises(ValueError, match="Import field 'root' must be an object"):
            memory.import_memories(import_file)

    def test_import_rejects_invalid_nested_payload_fields(self, tmp_path):
        config = MemoryConfig(project_name="import-test", repo_id="target-repo")
        config.storage.data_dir = tmp_path / "data"
        config.embedding.provider = "noop"
        memory = Memory(config=config)

        import_file = tmp_path / "invalid-export.json"
        import_file.write_text(
            json.dumps({"memories": {"episodic": [{"content": "Valid", "tags": "not-list"}]}})
        )

        with pytest.raises(
            ValueError,
            match=r"Import field 'memories\.episodic\[0\]\.tags' has an invalid type",
        ):
            memory.import_memories(import_file)

    def test_import_validates_before_writing_any_memories(self, tmp_path):
        config = MemoryConfig(project_name="import-test", repo_id="target-repo")
        config.storage.data_dir = tmp_path / "data"
        config.embedding.provider = "noop"
        memory = Memory(config=config)

        import_file = tmp_path / "invalid-export.json"
        import_file.write_text(
            json.dumps(
                {
                    "memories": {
                        "episodic": [
                            {"content": "Valid event"},
                            {"metadata": {"missing": "content"}},
                        ]
                    }
                }
            )
        )

        with pytest.raises(
            ValueError,
            match=r"Import field 'memories\.episodic\[1\]\.content' must be a string",
        ):
            memory.import_memories(import_file)

        assert memory._storage.list_memories(repo_id="target-repo") == []


class TestGraphRecall:
    """Tests for evidence-backed graph recall."""

    def test_graph_trace_returns_evidence_and_deterministic_omissions(self, tmp_path):
        config = MemoryConfig(project_name="graph-recall", repo_id="repo-a")
        config.storage.data_dir = tmp_path / "data"
        config.embedding.provider = "noop"
        memory = Memory(config=config)

        source_id = memory.record(
            "Auth route checks repository scope before returning memories",
            importance=0.9,
            _write_channel=WriteChannel.TEST_CAPTURE,
        )
        bug_id = memory.record(
            "Bug fixed where graph route leaked cross repository memories",
            importance=0.8,
            _write_channel=WriteChannel.TEST_CAPTURE,
        )
        knowledge_id = memory.learn(
            "Use require_repo_scope_access before graph recall traversal",
            importance=0.7,
            _write_channel=WriteChannel.TEST_CAPTURE,
        )
        memory._storage.add_relationship(
            source_id,
            bug_id,
            "explains",
            strength=0.8,
            evidence={
                "confidence": "observed",
                "confidence_score": 0.9,
                "source": "test",
                "reason": "The auth route fix explains the graph leak.",
            },
        )
        memory._storage.add_relationship(
            bug_id,
            knowledge_id,
            "mitigated_by",
            strength=0.7,
            evidence={
                "confidence": "manual",
                "confidence_score": 0.8,
                "source": "test",
                "reason": "The access helper mitigates the leak.",
            },
        )

        trace = memory.graph_trace(
            "auth graph repository leak",
            depth=2,
            token_budget=1000,
            limit=2,
        )

        assert trace["mode"] == "trace"
        assert {node["id"] for node in trace["nodes"]} >= {source_id, bug_id}
        assert any(
            edge["reason"] == "The auth route fix explains the graph leak."
            for edge in trace["edges"]
        )
        assert all("relevance_factors" in node for node in trace["nodes"])
        assert all("edge_score" in edge["relevance_factors"] for edge in trace["edges"])

        tiny = memory.graph_trace(
            "auth graph repository leak",
            depth=2,
            token_budget=5,
            limit=3,
        )

        assert len(tiny["nodes"]) == 1
        assert tiny["omitted"][0]["type"] == "depth" or any(
            item["type"] == "token_budget" for item in tiny["omitted"]
        )

    def test_graph_path_returns_shortest_evidence_path(self, tmp_path):
        config = MemoryConfig(project_name="graph-path", repo_id="repo-a")
        config.storage.data_dir = tmp_path / "data"
        config.embedding.provider = "noop"
        memory = Memory(config=config)

        source_id = memory._storage.store_memory(
            "Source alpha",
            repo_id="repo-a",
            tags=[provenance_tag(Provenance.DERIVED)],
            auto_link=False,
        )
        middle_id = memory._storage.store_memory(
            "Bridge beta",
            repo_id="repo-a",
            tags=[provenance_tag(Provenance.DERIVED)],
            auto_link=False,
        )
        target_id = memory._storage.store_memory(
            "Target gamma",
            repo_id="repo-a",
            tags=[provenance_tag(Provenance.DERIVED)],
            auto_link=False,
        )
        memory._storage.add_relationship(source_id, middle_id, "first", strength=0.7)
        memory._storage.add_relationship(middle_id, target_id, "second", strength=0.8)

        result = memory.graph_path(source_id, target_id, max_hops=2)

        assert result["mode"] == "path"
        assert [edge["relationship"] for edge in result["edges"]] == ["first", "second"]
        assert {node["id"] for node in result["nodes"]} == {source_id, middle_id, target_id}

    def test_graph_recall_uses_portable_storage_api(self):
        from visp_memory.recall.graph import GraphRecall

        class FakeStorage:
            memories = {
                "a": {
                    "id": "a",
                    "content": "Portable source memory",
                    "layer": "episodic",
                    "category": "note",
                    "importance": 0.8,
                    "repo_id": "repo-a",
                    "tags": [provenance_tag(Provenance.DERIVED)],
                },
                "b": {
                    "id": "b",
                    "content": "Portable target memory",
                    "layer": "semantic",
                    "category": "fact",
                    "importance": 0.7,
                    "repo_id": "repo-a",
                    "tags": [provenance_tag(Provenance.DERIVED)],
                },
            }

            def get_memory(self, memory_id):
                return self.memories.get(memory_id)

            def get_all_relationships(self, repo_id=None):
                return [
                    {
                        "source_id": "a",
                        "target_id": "b",
                        "relationship": "portable",
                        "strength": 0.9,
                        "evidence": {
                            "confidence": "observed",
                            "confidence_score": 0.9,
                            "reason": "Portable API methods are sufficient.",
                        },
                    }
                ]

            def search_memories(self, query, repo_id=None, limit=10, status="active"):
                return [self.memories["a"]]

        result = GraphRecall(FakeStorage()).trace("portable", repo_id="repo-a")

        assert {node["id"] for node in result["nodes"]} == {"a", "b"}
        assert result["edges"][0]["reason"] == "Portable API methods are sufficient."


class TestMemoryIntelligenceReport:
    """Tests for deterministic memory intelligence reporting."""

    def test_report_handles_empty_database(self, tmp_path):
        from visp_memory.core.reporting import MemoryIntelligenceReporter

        config = MemoryConfig(project_name="empty-report", repo_id="repo-a")
        config.storage.data_dir = tmp_path / "data"
        config.embedding.provider = "noop"
        memory = Memory(config=config)

        report = MemoryIntelligenceReporter(memory._storage).generate(repo_id="repo-a")

        assert report["summary"]["total_memories"] == 0
        assert report["summary"]["total_relationships"] == 0
        assert report["thresholds"]["high_impact_importance"] == 0.75
        assert report["sections"]["high_impact_memories"]["items"] == []
        assert report["sections"]["suggested_questions"]["items"]
        assert "No findings." in MemoryIntelligenceReporter.format_text(report)

    def test_report_sections_are_stable_and_distinguish_inferences(self, tmp_path):
        from visp_memory.core.reporting import MemoryIntelligenceReporter

        config = MemoryConfig(project_name="report", repo_id="repo-a")
        config.storage.data_dir = tmp_path / "data"
        config.embedding.provider = "noop"
        memory = Memory(config=config)

        high_id = memory.record("High impact migration decision", importance=0.9)
        warning_id = memory.warn("auth.py", "Fragile token refresh path")
        related_id = memory.record("Related implementation context")
        memory._storage.add_relationship(
            high_id,
            related_id,
            "related",
            strength=0.4,
            evidence={
                "confidence": "ambiguous",
                "confidence_score": 0.3,
                "reason": "Similarity was weak and needs confirmation.",
            },
        )

        report = MemoryIntelligenceReporter(memory._storage).generate(repo_id="repo-a")

        assert report["sections"]["high_impact_memories"]["kind"] == "stored_fact"
        assert report["sections"]["ambiguous_relationships"]["kind"] == (
            "inferred_recommendation"
        )
        assert report["sections"]["high_impact_memories"]["items"][0]["id"] == high_id
        assert report["sections"]["ambiguous_relationships"]["items"][0]["reason"] == (
            "Similarity was weak and needs confirmation."
        )
        assert report["sections"]["isolated_warnings"]["items"][0]["id"] == warning_id
        assert report["sections"]["suggested_questions"]["items"]
