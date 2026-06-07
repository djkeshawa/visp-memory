import builtins
import re
from pathlib import Path

import pytest

from llm_memory.config import MemoryConfig
from llm_memory.core.arcadedb_storage import (
    ARCADEDB_INSTALL_MESSAGE,
    ArcadeDbDependencyError,
    ArcadeDbStorage,
    load_arcadedb_driver,
)


class FakeArcadeDbModule:
    def __init__(self):
        self.db = FakeArcadeDb()
        self.paths = []

    def create_database(self, path):
        self.paths.append(Path(path))
        return self.db


class FakeArcadeDb:
    def __init__(self):
        self.commands = []
        self.memories = {}
        self.relationships = {}
        self.sessions = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def transaction(self):
        return self

    def command(self, language, sql, *params):
        assert language == "sql"
        sql = " ".join(sql.split())
        self.commands.append(sql)
        if sql.startswith("CREATE EDGE MemoryRelationship"):
            fields = _edge_fields(sql)
            self.relationships[params[2]] = dict(zip(fields, params[2:]))
            return None
        if sql.startswith("CREATE "):
            return None
        if sql.startswith("INSERT INTO Memory SET"):
            fields = _insert_fields(sql)
            self.memories[params[0]] = dict(zip(fields, params))
            return None
        if sql.startswith("UPDATE Memory SET"):
            fields = _update_fields(sql)
            memory_id = params[-1]
            if memory_id in self.memories:
                self.memories[memory_id].update(dict(zip(fields, params[:-1])))
            return None
        if sql.startswith("DELETE FROM Memory WHERE id = ?"):
            self.memories.pop(params[0], None)
            return None
        if sql.startswith("INSERT INTO Session SET"):
            fields = _insert_fields(sql)
            self.sessions[params[0]] = dict(zip(fields, params))
            return None
        if sql.startswith("UPDATE Session"):
            session_id = params[-1]
            if session_id in self.sessions:
                self.sessions[session_id].update(
                    {"summary": params[0], "memory_ids": params[1], "ended_at": params[2]}
                )
            return None
        raise AssertionError(f"Unhandled SQL command: {sql}")

    def query(self, language, sql, *params):
        assert language == "sql"
        sql = " ".join(sql.split())
        if sql == "SELECT FROM Memory WHERE id = ?":
            memory = self.memories.get(params[0])
            return [memory] if memory else []
        if sql == "SELECT FROM MemoryRelationship":
            return list(self.relationships.values())
        if sql.startswith("SELECT FROM Memory"):
            return self._query_memories(sql, params)
        raise AssertionError(f"Unhandled SQL query: {sql}")

    def _query_memories(self, sql, params):
        rows = list(self.memories.values())
        param_index = 0
        for field in ("layer", "repo_id", "category", "status"):
            if f"{field} = ?" in sql:
                expected = params[param_index]
                rows = [row for row in rows if row.get(field) == expected]
                param_index += 1

        order_match = re.search(r"ORDER BY ([a-z_]+) (ASC|DESC)", sql)
        if order_match:
            field, direction = order_match.groups()
            rows.sort(key=lambda row: row.get(field) or "", reverse=direction == "DESC")

        limit = params[-1] if params else len(rows)
        return rows[:limit]


def _insert_fields(sql):
    body = sql.split(" SET ", 1)[1]
    return [part.split(" = ?", 1)[0].strip() for part in body.split(",")]


def _update_fields(sql):
    body = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
    return [part.split(" = ?", 1)[0].strip() for part in body.split(",")]


def _edge_fields(sql):
    body = sql.split(" SET ", 1)[1]
    return [part.split(" = ?", 1)[0].strip() for part in body.split(",")]


@pytest.fixture
def fake_arcadedb(monkeypatch):
    driver = FakeArcadeDbModule()
    monkeypatch.setattr("llm_memory.core.arcadedb_storage.load_arcadedb_driver", lambda: driver)
    return driver


def test_storage_config_accepts_arcadedb_backend():
    config = MemoryConfig()

    assert config.storage.backend == "sqlite"

    config.storage.backend = "arcadedb"

    assert config.storage.backend == "arcadedb"


def test_arcadedb_optional_extra_declared():
    try:
        import tomllib
    except ImportError:
        pytest.skip("tomllib is unavailable on this Python version")

    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    extras = pyproject["project"]["optional-dependencies"]

    assert extras["arcadedb"] == ["arcadedb-embedded>=26.4.2,<27"]
    assert "arcadedb" in extras["all"][0]


def test_load_arcadedb_driver_missing_dependency_message(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "arcadedb_embedded":
            raise ImportError("missing arcadedb")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ArcadeDbDependencyError) as excinfo:
        load_arcadedb_driver()

    assert ARCADEDB_INSTALL_MESSAGE in str(excinfo.value)
    assert "llm-memory[arcadedb]" in str(excinfo.value)


def test_arcadedb_storage_uses_configured_data_dir(monkeypatch, tmp_path):
    driver = FakeArcadeDbModule()
    monkeypatch.setattr("llm_memory.core.arcadedb_storage.load_arcadedb_driver", lambda: driver)

    storage = ArcadeDbStorage(tmp_path)

    assert storage.data_dir == tmp_path / "arcadedb"
    assert storage.data_dir.exists()
    assert storage._arcadedb is driver
    assert driver.paths[-1] == tmp_path / "arcadedb"


def test_arcadedb_memory_crud_list_search_stats_and_projects(fake_arcadedb, tmp_path):
    storage = ArcadeDbStorage(tmp_path)

    repo_a = storage.store_memory(
        "ArcadeDB supports embedded local graph storage",
        layer="semantic",
        repo_id="repo-a",
        category="backend",
        importance=0.9,
        tags=["graph"],
        metadata={"source": "test"},
    )
    repo_b = storage.store_memory(
        "SQLite remains the default backend",
        layer="episodic",
        repo_id="repo-b",
        category="backend",
        importance=0.4,
    )

    memory = storage.get_memory(repo_a)

    assert memory["id"] == repo_a
    assert memory["repo_id"] == "repo-a"
    assert memory["tags"] == ["graph"]
    assert memory["metadata"] == {"source": "test"}
    assert memory["access_count"] == 1

    assert [item["id"] for item in storage.list_memories(repo_id="repo-a")] == [repo_a]

    results = storage.search_memories("embedded graph", repo_id="repo-a")
    assert [item["id"] for item in results] == [repo_a]
    assert results[0]["similarity"] > 0

    assert storage.update_memory(repo_b, status="archived") is True
    assert storage.list_memories(repo_id="repo-b") == []
    assert [item["id"] for item in storage.list_memories(repo_id="repo-b", status="all")] == [
        repo_b
    ]

    assert storage.get_stats() == {
        "memories_by_layer": {"semantic": 1},
        "memories_by_category": {"backend": 1},
        "total_memories": 1,
        "active_intents": 0,
        "total_relationships": 0,
    }
    assert storage.list_project_ids() == ["repo-a", "repo-b"]

    assert storage.delete_memory(repo_b) is True
    assert storage.delete_memory(repo_b) is False
    assert fake_arcadedb.paths[-1] == tmp_path / "arcadedb"


def test_arcadedb_sessions_round_trip(fake_arcadedb, tmp_path):
    storage = ArcadeDbStorage(tmp_path)

    session_id = storage.start_session()
    storage.end_session(session_id, "Finished backend selection", ["mem-a", "mem-b"])

    session = fake_arcadedb.db.sessions[session_id]
    assert session["summary"] == "Finished backend selection"
    assert storage._json_deserialize(session["memory_ids"]) == ["mem-a", "mem-b"]


def test_arcadedb_get_collection_is_none_for_conservative_vector_v1(fake_arcadedb, tmp_path):
    storage = ArcadeDbStorage(tmp_path)

    assert storage.get_collection("semantic") is None


def test_arcadedb_schema_uses_stable_vertex_and_edge_types(fake_arcadedb, tmp_path):
    ArcadeDbStorage(tmp_path)

    commands = fake_arcadedb.db.commands

    for vertex_type in (
        "Memory",
        "Intent",
        "Session",
        "Repository",
        "User",
        "Team",
        "AuditLog",
        "RecallFeedback",
    ):
        assert f"CREATE VERTEX TYPE {vertex_type} IF NOT EXISTS" in commands

    for edge_type in ("MemoryRelationship", "RepoDependency", "TeamMember"):
        assert f"CREATE EDGE TYPE {edge_type} IF NOT EXISTS" in commands


def test_arcadedb_relationships_store_kind_as_property_and_round_trip_evidence(
    fake_arcadedb, tmp_path
):
    storage = ArcadeDbStorage(tmp_path)
    source_id = storage.store_memory("ArcadeDB selected for local graph", repo_id="repo-a")
    target_id = storage.store_memory("Neo4j remains the mature team backend", repo_id="repo-a")
    other_repo_id = storage.store_memory("Other repo memory", repo_id="repo-b")

    relationship_id = storage.add_relationship(
        source_id,
        target_id,
        "supports'; DROP EDGE TYPE Unsafe",
        strength=0.82,
        evidence={
            "confidence": "manual",
            "confidence_score": 0.91,
            "source": "test",
            "source_file": "plan.md",
            "source_location": "REQ003",
            "reason": "The plan requires stable edge types.",
            "created_by": "pytest",
        },
    )

    create_edge_commands = [
        command
        for command in fake_arcadedb.db.commands
        if command.startswith("CREATE EDGE MemoryRelationship")
    ]
    assert len(create_edge_commands) == 1
    assert "DROP EDGE TYPE Unsafe" not in create_edge_commands[0]

    stored_edge = fake_arcadedb.db.relationships[relationship_id]
    assert stored_edge["relationship"] == "supports'; DROP EDGE TYPE Unsafe"
    assert stored_edge["source_id"] == source_id
    assert stored_edge["target_id"] == target_id

    relationships = storage.get_all_relationships(repo_id="repo-a")
    assert relationships == [
        {
            "id": relationship_id,
            "source_id": source_id,
            "target_id": target_id,
            "relationship": "supports'; DROP EDGE TYPE Unsafe",
            "strength": 0.82,
            "created_at": relationships[0]["created_at"],
            "evidence": {
                "confidence": "manual",
                "confidence_score": 0.91,
                "source": "test",
                "source_file": "plan.md",
                "source_location": "REQ003",
                "reason": "The plan requires stable edge types.",
                "created_by": "pytest",
                "created_at": relationships[0]["created_at"],
            },
        }
    ]

    assert storage.get_all_relationships(repo_id="repo-b") == []

    related = storage.get_related_memories(source_id)
    assert [item["id"] for item in related] == [target_id]
    assert related[0]["relationship"] == "supports'; DROP EDGE TYPE Unsafe"
    assert related[0]["relationship_evidence"]["source_file"] == "plan.md"

    assert storage.get_related_memories(other_repo_id) == []


def test_arcadedb_relationships_reject_missing_or_cross_repo_memories(fake_arcadedb, tmp_path):
    storage = ArcadeDbStorage(tmp_path)
    source_id = storage.store_memory("Source", repo_id="repo-a")
    target_id = storage.store_memory("Target", repo_id="repo-b")

    with pytest.raises(ValueError, match="cannot cross repository"):
        storage.add_relationship(source_id, target_id, "related_to")

    with pytest.raises(ValueError, match="must both exist"):
        storage.add_relationship(source_id, "missing", "related_to")


def test_real_arcadedb_memory_smoke_skips_without_extra(tmp_path):
    try:
        import arcadedb_embedded  # noqa: F401
    except ImportError:
        pytest.skip("arcadedb_embedded is not installed")

    storage = ArcadeDbStorage(tmp_path)
    memory_id = storage.store_memory("Real ArcadeDB smoke", repo_id="repo-real", auto_link=False)

    assert storage.get_memory(memory_id)["content"] == "Real ArcadeDB smoke"
