"""Basic tests for LLM Memory system."""

import json
import tempfile
from pathlib import Path

import pytest

from llm_memory import Memory, MemoryConfig


@pytest.fixture
def memory():
    """Create a memory instance with temp directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = MemoryConfig()
        config.storage.data_dir = Path(tmpdir)
        config.embedding.provider = "noop"
        yield Memory(config=config)


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
            what="Use PostgreSQL",
            why="Need ACID compliance",
            alternatives=["MongoDB", "MySQL"]
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
            files=["auth/token.py"]
        )
        assert mem_id is not None


class TestSemanticMemory:
    """Tests for semantic memory layer."""

    def test_establish_knowledge(self, memory):
        """Can establish semantic knowledge."""
        mem_id = memory.learn(
            "Always use mutex locks in auth module",
            category="invariant",
            importance=0.9
        )
        assert mem_id is not None

    def test_add_warning(self, memory):
        """Can add warnings for fragile areas."""
        mem_id = memory.warn(
            area="database/migrations",
            warning="Always backup before running",
            severity=0.8
        )
        assert mem_id is not None

        warnings = memory.semantic.get_warnings()
        assert len(warnings) > 0

    def test_add_convention(self, memory):
        """Can add conventions."""
        mem_id = memory.semantic.convention(
            rule="All API endpoints return JSON",
            rationale="Consistency"
        )
        assert mem_id is not None

        conventions = memory.semantic.get_conventions()
        assert len(conventions) > 0


class TestIntentMemory:
    """Tests for intent memory layer."""

    def test_set_goal(self, memory):
        """Can set a goal."""
        intent_id = memory.goal(
            "Implement OAuth2",
            priority=2,
            constraints=["No breaking changes"]
        )
        assert intent_id is not None

        intents = memory.intent.get_active()
        assert len(intents) > 0

    def test_working_on(self, memory):
        """Can track current work."""
        intent_id = memory.working_on(
            "Token refresh endpoint",
            files=["auth/refresh.py"]
        )
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


class TestSearch:
    """Tests for search functionality."""

    def test_recall_finds_memories(self, memory):
        """Recall can find memories by content."""
        memory.record("Authentication system updated")
        memory.record("Database migration complete")
        memory.learn("Auth requires special handling")

        results = memory.recall("authentication")
        assert len(results) > 0

    def test_relevant_for_task(self, memory):
        """Can get relevant memories for a task."""
        memory.learn("Auth module is fragile")
        memory.warn("auth/", "Test thoroughly")
        memory.record("Fixed auth bug")

        relevant = memory.relevant_for(
            task="fix authentication",
            files=["auth/login.py"]
        )
        assert "knowledge" in relevant
        assert "warnings" in relevant


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

        assert [m["content"] for m in target._storage.list_memories(repo_id="target-repo")] == [
            "Source event",
            "Source knowledge",
        ]
        imported_intents = target._storage.get_active_intents(repo_id="target-repo")
        assert [i["description"] for i in imported_intents] == ["Source goal"]
