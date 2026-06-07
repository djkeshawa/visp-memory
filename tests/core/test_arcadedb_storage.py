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
        self.memories = {}
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


def test_real_arcadedb_memory_smoke_skips_without_extra(tmp_path):
    try:
        import arcadedb_embedded  # noqa: F401
    except ImportError:
        pytest.skip("arcadedb_embedded is not installed")

    storage = ArcadeDbStorage(tmp_path)
    memory_id = storage.store_memory("Real ArcadeDB smoke", repo_id="repo-real", auto_link=False)

    assert storage.get_memory(memory_id)["content"] == "Real ArcadeDB smoke"
