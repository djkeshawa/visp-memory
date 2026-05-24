"""Basic tests for LLM Memory system."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from llm_memory import Memory, MemoryConfig
from llm_memory.core.indexing import ReindexScope
from llm_memory.core.storage import CHROMADB_AVAILABLE, LocalStorage


@pytest.fixture
def memory():
    """Create a memory instance with temp directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = MemoryConfig()
        config.storage.data_dir = Path(tmpdir)
        config.embedding.provider = "noop"
        yield Memory(config=config)


def test_client_mode_does_not_initialize_local_embedding_provider():
    config = MemoryConfig()
    config.storage.mode = "client"
    config.storage.server_url = "http://127.0.0.1:65535"
    config.embedding.provider = "auto"

    with patch("llm_memory.core.embeddings.get_embedding_provider") as get_embedding_provider:
        Memory(config=config)

    get_embedding_provider.assert_not_called()


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
    monkeypatch.setattr("llm_memory.core.storage.CHROMADB_AVAILABLE", True)
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

    assert relationships == [
        {
            "id": relationships[0]["id"],
            "source_id": source_id,
            "target_id": target_id,
            "relationship": "derived_from",
            "strength": 1.0,
            "created_at": relationships[0]["created_at"],
        }
    ]


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

    def test_done_clears_task(self, memory):
        """Done clears current task."""
        memory.working_on("Some task")
        assert memory.intent.get_working_on() is not None

        memory.done()
        assert memory.intent.get_working_on() is None

    def test_done_only_clears_configured_repo_task(self, tmp_path):
        """Done should not complete current tasks from other repositories."""
        config = MemoryConfig(project_name="done-scope-test", repo_id="repo-a")
        config.storage.data_dir = tmp_path / "data"
        config.embedding.provider = "noop"
        memory = Memory(config=config)

        memory.working_on("Repo A task")
        memory.working_on("Repo B task", repo_id="repo-b")

        assert memory.done() == 1

        assert memory.intent.get_working_on(repo_id="repo-a") is None
        repo_b_task = memory.intent.get_working_on(repo_id="repo-b")
        assert repo_b_task is not None
        assert repo_b_task["description"] == "WORKING ON: Repo B task"


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

        memory.learn("Repo A auth knowledge")
        memory.learn("Repo B auth knowledge", repo_id="repo-b")
        memory.warn("auth/login.py", "Repo A warning")
        memory.warn("auth/login.py", "Repo B warning", repo_id="repo-b")
        memory.record("Repo A auth history")
        memory.record("Repo B auth history", repo_id="repo-b")

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

        memory.warn("deploy", "Repo A warning")
        memory.semantic.convention("Repo A convention", repo_id="repo-a")
        memory.semantic.known_issue("Repo A issue", repo_id="repo-a")
        memory.record("Repo A event")
        memory.goal("Repo A goal")
        memory.working_on("Repo A task")

        memory.warn("deploy", "Repo B warning", repo_id="repo-b")
        memory.semantic.convention("Repo B convention", repo_id="repo-b")
        memory.semantic.known_issue("Repo B issue", repo_id="repo-b")
        memory.record("Repo B event", repo_id="repo-b")
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
