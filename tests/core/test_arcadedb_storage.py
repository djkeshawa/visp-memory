import builtins
import re
from pathlib import Path

import pytest

from llm_memory import Memory
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
        self.created_paths = []
        self.opened_paths = []
        self.existing_paths = set()

    def database_exists(self, path):
        return Path(path) in self.existing_paths

    def create_database(self, path):
        db_path = Path(path)
        if db_path in self.existing_paths:
            raise AssertionError(f"Database already exists: {db_path}")
        self.paths.append(db_path)
        self.created_paths.append(db_path)
        self.existing_paths.add(db_path)
        return self.db

    def open_database(self, path):
        db_path = Path(path)
        if db_path not in self.existing_paths:
            raise AssertionError(f"Database does not exist: {db_path}")
        self.paths.append(db_path)
        self.opened_paths.append(db_path)
        return self.db


class FakeArcadeDb:
    def __init__(self):
        self.commands = []
        self.records = {
            "Memory": {},
            "Intent": {},
            "Session": {},
            "Repository": {},
            "User": {},
            "Team": {},
            "AuditLog": {},
            "RecallFeedback": {},
        }
        self.edges = {"MemoryRelationship": {}, "RepoDependency": {}, "TeamMember": {}}
        self.memories = self.records["Memory"]
        self.relationships = self.edges["MemoryRelationship"]
        self.sessions = self.records["Session"]

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
            self._create_edge("MemoryRelationship", sql, params)
            return None
        if sql.startswith("CREATE EDGE RepoDependency"):
            self._create_edge("RepoDependency", sql, params)
            return None
        if sql.startswith("CREATE EDGE TeamMember"):
            self._create_edge("TeamMember", sql, params)
            return None
        if sql.startswith("CREATE "):
            return None
        if sql.startswith("INSERT INTO "):
            type_name = sql.split()[2]
            fields = _insert_fields(sql)
            self.records[type_name][params[0]] = dict(zip(fields, params))
            return None
        if sql.startswith("UPDATE "):
            type_name = sql.split()[1]
            record_id = params[-1]
            if record_id in self.records[type_name]:
                record = self.records[type_name][record_id]
                param_values = iter(params[:-1])
                for field, spec in _update_assignments(sql):
                    if spec == "increment":
                        record[field] = int(record.get(field) or 0) + 1
                    else:
                        record[field] = next(param_values)
            return None
        if sql.startswith("DELETE FROM "):
            type_name = sql.split()[2]
            self.records[type_name].pop(params[0], None)
            return None
        raise AssertionError(f"Unhandled SQL command: {sql}")

    def query(self, language, sql, *params):
        assert language == "sql"
        sql = " ".join(sql.split())
        if sql.startswith("SELECT FROM "):
            type_name = sql.split()[2]
            if type_name in self.edges:
                return list(self.edges[type_name].values())
            if sql == f"SELECT FROM {type_name} WHERE id = ?":
                record = self.records[type_name].get(params[0])
                return [record] if record else []
            return self._query_records(type_name, sql, params)
        raise AssertionError(f"Unhandled SQL query: {sql}")

    def _create_edge(self, edge_type, sql, params):
        fields = _edge_fields(sql)
        values = params[2:]
        edge_id = values[0]
        self.edges[edge_type][edge_id] = dict(zip(fields, values))

    def _query_records(self, type_name, sql, params):
        rows = list(self.records[type_name].values())
        param_index = 0
        where_match = re.search(r" WHERE (.*?) ORDER BY ", sql)
        if where_match:
            for condition in where_match.group(1).split(" AND "):
                field = condition.split(" = ?", 1)[0]
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


def _update_assignments(sql):
    """Parse an UPDATE SET clause into (field, spec) pairs.

    spec is "param" for `field = ?` (consumes a positional parameter) or "increment"
    for an atomic `field = field + 1` self-increment.
    """
    body = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
    assignments = []
    for part in body.split(","):
        field, _, expr = part.partition(" = ")
        field = field.strip()
        expr = expr.strip()
        if expr == f"{field} + 1":
            assignments.append((field, "increment"))
        else:
            assignments.append((field, "param"))
    return assignments


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
    assert storage.data_dir.parent.exists()
    assert storage._arcadedb is driver
    assert driver.paths[-1] == tmp_path / "arcadedb"


def test_arcadedb_storage_opens_existing_database_after_initial_create(fake_arcadedb, tmp_path):
    storage = ArcadeDbStorage(tmp_path)

    storage.store_memory("ArcadeDB opens existing embedded database", repo_id="repo-a")

    assert fake_arcadedb.created_paths == [tmp_path / "arcadedb"]
    assert fake_arcadedb.opened_paths
    assert fake_arcadedb.opened_paths[-1] == tmp_path / "arcadedb"


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


def test_arcadedb_search_excludes_raw_layer_by_default(fake_arcadedb, tmp_path):
    storage = ArcadeDbStorage(tmp_path)

    raw_id = storage.store_memory(
        "shared token appears in raw capture",
        layer="raw",
        repo_id="repo-a",
        importance=0.9,
        auto_link=False,
    )
    episodic_id = storage.store_memory(
        "shared token appears in episodic note",
        layer="episodic",
        repo_id="repo-a",
        importance=0.8,
        auto_link=False,
    )

    # Default search (layer=None) must exclude the raw layer, mirroring SQLite.
    default_ids = [item["id"] for item in storage.search_memories("shared token", repo_id="repo-a")]
    assert episodic_id in default_ids
    assert raw_id not in default_ids

    # An explicit raw-layer search still returns raw memories.
    raw_ids = [
        item["id"]
        for item in storage.search_memories("shared token", repo_id="repo-a", layer="raw")
    ]
    assert raw_ids == [raw_id]

    # list_memories keeps every layer, including raw.
    listed_ids = {item["id"] for item in storage.list_memories(repo_id="repo-a")}
    assert listed_ids == {raw_id, episodic_id}


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


def test_arcadedb_intents_stats_and_project_ids(fake_arcadedb, tmp_path):
    storage = ArcadeDbStorage(tmp_path)
    storage.store_memory("Repo memory", repo_id="repo-a")

    intent_id = storage.set_intent(
        "Ship ArcadeDB backend",
        priority=5,
        context={"phase": "storage"},
        repo_id="repo-intent",
    )

    intents = storage.get_active_intents(repo_id="repo-intent")
    assert intents[0]["id"] == intent_id
    assert intents[0]["context"] == {"phase": "storage"}

    assert storage.update_intent(intent_id, priority=7, context={"phase": "graph"}) is True
    assert storage.get_active_intents(repo_id="repo-intent")[0]["priority"] == 7

    stats = storage.get_stats()
    assert stats["active_intents"] == 1
    assert storage.list_project_ids() == ["repo-a", "repo-intent"]

    assert storage.complete_intent(intent_id) is True
    assert storage.get_active_intents(repo_id="repo-intent") == []


def test_arcadedb_repositories_dependencies_users_and_teams(fake_arcadedb, tmp_path):
    storage = ArcadeDbStorage(tmp_path)

    storage.store_repository(
        {
            "id": "repo-a",
            "name": "Repo A",
            "tech_stack": ["python"],
            "metadata": {"critical": True},
        }
    )
    storage.store_repository({"id": "repo-b", "name": "Repo B"})

    with pytest.raises(ValueError, match="Repository already exists: repo-a"):
        storage.store_repository({"id": "repo-a", "name": "Replacement"})

    assert storage.get_repository("repo-a")["tech_stack"] == ["python"]
    assert [repo["id"] for repo in storage.list_repositories()] == ["repo-a", "repo-b"]

    dependency_id = storage.add_repo_dependency(
        "repo-a", "repo-b", "runtime", version="1.0", notes="uses API"
    )
    assert dependency_id
    assert storage.get_repo_dependencies("repo-a") == [
        {"target_id": "repo-b", "type": "runtime", "version": "1.0", "notes": "uses API"}
    ]

    with pytest.raises(ValueError, match="Repository not found: missing"):
        storage.add_repo_dependency("missing", "repo-b", "runtime")

    storage.store_user({"id": "alice", "username": "alice", "metadata": {"role": "dev"}})
    storage.store_team({"id": "team-a", "name": "Team A", "metadata": {"tier": "platform"}})

    with pytest.raises(ValueError, match="User already exists: alice"):
        storage.store_user({"id": "alice", "username": "renamed"})
    with pytest.raises(ValueError, match="Team already exists: team-a"):
        storage.store_team({"id": "team-a", "name": "Replacement"})

    assert storage.add_team_member("team-a", "alice") is True
    assert storage.add_team_member("team-a", "missing") is False
    assert storage.get_user("alice")["metadata"] == {"role": "dev"}
    assert storage.get_user_teams("alice")[0]["metadata"] == {"tier": "platform"}


def test_arcadedb_audit_and_recall_feedback(fake_arcadedb, tmp_path):
    storage = ArcadeDbStorage(tmp_path)
    memory_id = storage.store_memory("ArcadeDB recall signal", repo_id="repo-a")

    audit_id = storage.append_audit_log(
        "memory.created",
        actor_id="alice",
        repo_id="repo-a",
        target_type="memory",
        target_id=memory_id,
        metadata={"safe": True},
    )
    audit = storage.list_audit_logs(actor_id="alice")[0]
    assert audit["id"] == audit_id
    assert audit["metadata"] == {"safe": True}

    event_id = storage.log_recall_event(
        memory_id,
        "used",
        query="What backend should I use?",
        task_id="T004",
        metadata={"prompt": "secret prompt", "safe": "kept"},
    )
    assert event_id

    utility = storage.inspect_recall_utility(memory_id=memory_id)
    assert utility["summary"]["total_events"] == 1
    assert utility["summary"]["by_event_type"] == {"used": 1}
    assert utility["signals"][0]["utility_score"] > 0
    assert utility["events"][0]["metadata"] == {"safe": "kept"}
    assert utility["events"][0]["query_hash"]

    assert storage.reset_recall_utility(memory_id=memory_id) == 1
    assert storage.inspect_recall_utility(memory_id=memory_id)["summary"]["total_events"] == 0

    with pytest.raises(ValueError, match="Memory not found"):
        storage.log_recall_event("missing", "used")


def test_arcadedb_reinforces_on_use_in_parity_with_local(fake_arcadedb, tmp_path):
    # Backend parity: a used memory must strengthen (access_count++) just like SQLite,
    # while a merely-surfaced one must not.
    storage = ArcadeDbStorage(tmp_path)
    memory_id = storage.store_memory("ArcadeDB reinforce signal")

    def access_count() -> int:
        record = storage._memory_record_to_dict(storage._query_memory(memory_id))
        return int(record.get("access_count") or 0)

    assert access_count() == 0
    storage.log_recall_event(memory_id, "used")
    assert access_count() == 1
    storage.log_recall_event(memory_id, "surfaced")
    assert access_count() == 1  # surfaced/dismissed do not reinforce
    storage.log_recall_event(memory_id, "task_linked")
    assert access_count() == 2


def test_arcadedb_graph_recall_uses_public_memory_contract(fake_arcadedb, tmp_path):
    config = MemoryConfig(project_name="arcadedb-graph", repo_id="repo-a")
    config.storage.backend = "arcadedb"
    config.storage.data_dir = tmp_path / "data"
    config.embedding.provider = "noop"
    memory = Memory(config=config)

    source_id = memory._storage.store_memory(
        "ArcadeDB graph recall auth route memory",
        repo_id="repo-a",
        importance=0.9,
        auto_link=False,
    )
    target_id = memory._storage.store_memory(
        "Repository scope evidence explains access controls",
        repo_id="repo-a",
        importance=0.8,
        auto_link=False,
    )
    memory._storage.add_relationship(
        source_id,
        target_id,
        "supports",
        strength=0.84,
        evidence={
            "confidence": "observed",
            "confidence_score": 0.92,
            "source": "pytest",
            "source_file": "tests/core/test_arcadedb_storage.py",
            "source_location": "test_arcadedb_graph_recall_uses_public_memory_contract",
            "reason": "ArcadeDB relationship evidence supports graph recall.",
            "created_by": "codex",
        },
    )

    trace = memory.graph_trace(
        "arcadedb graph recall auth route memory",
        repo_id="repo-a",
        depth=1,
        token_budget=1000,
        limit=2,
    )

    assert trace["mode"] == "trace"
    assert {node["id"] for node in trace["nodes"]} >= {source_id, target_id}
    assert trace["edges"][0]["relationship"] == "supports"
    assert trace["edges"][0]["reason"] == "ArcadeDB relationship evidence supports graph recall."
    assert trace["edges"][0]["evidence"] == {
        "confidence": "observed",
        "confidence_score": 0.92,
        "source": "pytest",
        "source_file": "tests/core/test_arcadedb_storage.py",
        "source_location": "test_arcadedb_graph_recall_uses_public_memory_contract",
        "reason": "ArcadeDB relationship evidence supports graph recall.",
        "created_by": "codex",
        "created_at": trace["edges"][0]["evidence"]["created_at"],
    }
    assert all("relevance_factors" in node for node in trace["nodes"])
    assert trace["edges"][0]["relevance_factors"]["edge_score"] > 0

    path = memory.graph_path(source_id, target_id, repo_id="repo-a", max_hops=1)
    assert path["mode"] == "path"
    assert [edge["relationship"] for edge in path["edges"]] == ["supports"]

    why = memory.graph_why_relevant(
        "arcadedb graph recall auth route memory",
        target_id,
        repo_id="repo-a",
        depth=1,
    )
    assert why["mode"] == "why_relevant"
    assert why["edges"][0]["reason"] == "ArcadeDB relationship evidence supports graph recall."


def test_arcadedb_memory_intelligence_report_uses_public_storage_contract(
    fake_arcadedb, tmp_path
):
    from llm_memory.core.reporting import MemoryIntelligenceReporter

    config = MemoryConfig(project_name="arcadedb-report", repo_id="repo-a")
    config.storage.backend = "arcadedb"
    config.storage.data_dir = tmp_path / "data"
    config.embedding.provider = "noop"
    memory = Memory(config=config)

    high_id = memory._storage.store_memory(
        "High impact ArcadeDB migration decision",
        repo_id="repo-a",
        importance=0.91,
        auto_link=False,
    )
    related_id = memory._storage.store_memory(
        "ArcadeDB report relationship context",
        repo_id="repo-a",
        importance=0.6,
        auto_link=False,
    )
    warning_id = memory._storage.store_memory(
        "WARNING [arcadedb]: Embedded graph backend needs careful packaging",
        layer="semantic",
        category="fragile_area",
        repo_id="repo-a",
        importance=0.8,
        auto_link=False,
    )
    memory._storage.add_relationship(
        high_id,
        related_id,
        "related",
        strength=0.4,
        evidence={
            "confidence": "ambiguous",
            "confidence_score": 0.3,
            "source": "pytest",
            "reason": "Relationship needs confirmation before relying on graph recall.",
        },
    )

    report = MemoryIntelligenceReporter(memory._storage).generate(repo_id="repo-a")

    assert report["schema_version"] == "1.0"
    assert report["summary"]["total_memories"] == 3
    assert report["summary"]["total_relationships"] == 1
    assert report["sections"]["high_impact_memories"]["items"][0]["id"] == high_id
    assert report["sections"]["ambiguous_relationships"]["items"][0]["reason"] == (
        "Relationship needs confirmation before relying on graph recall."
    )
    assert report["sections"]["isolated_warnings"]["items"][0]["id"] == warning_id
    assert report["sections"]["suggested_questions"]["kind"] == "inferred_recommendation"


def test_real_arcadedb_memory_smoke_skips_without_extra(tmp_path):
    try:
        import arcadedb_embedded  # noqa: F401
    except ImportError:
        pytest.skip("arcadedb_embedded is not installed")

    storage = ArcadeDbStorage(tmp_path)
    memory_id = storage.store_memory("Real ArcadeDB smoke", repo_id="repo-real", auto_link=False)

    assert storage.get_memory(memory_id)["content"] == "Real ArcadeDB smoke"
