import base64
import builtins
import copy
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from visp_memory import Memory
from visp_memory.config import MemoryConfig
from visp_memory.core.arcadedb_storage import (
    ARCADEDB_INSTALL_MESSAGE,
    ArcadeDbDependencyError,
    ArcadeDbStorage,
    load_arcadedb_driver,
)
from visp_memory.core.authority import (
    PROHIBITION_AUTHORITY_KEYS_ENV,
    ProhibitionAuthorityError,
    build_prohibition_claim,
    sign_prohibition_attestation,
)
from visp_memory.core.ranking import utility_rank_adjustment
from visp_memory.core.storage import (
    STORAGE_SCHEMA_VERSION,
    EvidenceError,
    EvidenceImmutableError,
    EvidenceReferenceError,
    LocalStorage,
    SessionCompletionStatus,
    StorageMigrationRequired,
)
from visp_memory.core.trust import Provenance, provenance_tag


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
            "Evidence": {},
            "Intent": {},
            "Session": {},
            "Repository": {},
            "User": {},
            "Team": {},
            "AuditLog": {},
            "RecallFeedback": {},
            "SchemaVersion": {},
            "AuthorityAttestation": {},
        }
        self.edges = {
            "MemoryRelationship": {},
            "BeliefEvidence": {},
            "RepoDependency": {},
            "TeamMember": {},
            "BeliefAuthority": {},
        }
        self.memories = self.records["Memory"]
        self.relationships = self.edges["MemoryRelationship"]
        self.sessions = self.records["Session"]
        self.vertex_types = set()
        self.edge_types = set()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def transaction(self):
        return FakeArcadeTransaction(self)

    def command(self, language, sql, *params):
        assert language == "sql"
        sql = " ".join(sql.split())
        self.commands.append(sql)
        if sql.startswith("CREATE EDGE MemoryRelationship"):
            self._create_edge("MemoryRelationship", sql, params)
            return None
        if sql.startswith("CREATE EDGE BeliefEvidence"):
            self._create_edge("BeliefEvidence", sql, params)
            return None
        if sql.startswith("CREATE EDGE BeliefAuthority"):
            self._create_edge("BeliefAuthority", sql, params)
            return None
        if sql.startswith("CREATE EDGE RepoDependency"):
            self._create_edge("RepoDependency", sql, params)
            return None
        if sql.startswith("CREATE EDGE TeamMember"):
            self._create_edge("TeamMember", sql, params)
            return None
        if sql.startswith("CREATE VERTEX TYPE "):
            self.vertex_types.add(sql.split()[3])
            return None
        if sql.startswith("CREATE EDGE TYPE "):
            self.edge_types.add(sql.split()[3])
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
                if "ended_at IS NULL" in sql and record.get("ended_at") is not None:
                    return []
                param_values = iter(params[:-1])
                for field, spec in _update_assignments(sql):
                    if spec == "increment":
                        record[field] = int(record.get(field) or 0) + 1
                    else:
                        record[field] = next(param_values)
                if "RETURN AFTER" in sql:
                    return [record]
            return [] if "RETURN AFTER" in sql else None
        if sql.startswith("DELETE FROM "):
            type_name = sql.split()[2]
            if "WHERE memory_id = ?" in sql:
                memory_id = params[0]
                for record_id, record in list(self.records[type_name].items()):
                    if record.get("memory_id") == memory_id:
                        self.records[type_name].pop(record_id, None)
            else:
                self.records[type_name].pop(params[0], None)
            return None
        if sql.startswith("DELETE EDGE "):
            edge_type = sql.split()[2]
            self.edges[edge_type].pop(params[0], None)
            return None
        raise AssertionError(f"Unhandled SQL command: {sql}")

    def query(self, language, sql, *params):
        assert language == "sql"
        sql = " ".join(sql.split())
        if sql == "SELECT name, type, records FROM schema:types":
            rows = []
            for type_name in sorted(self.vertex_types):
                records = self.records.get(type_name, self.edges.get(type_name, {}))
                rows.append(
                    {"name": type_name, "type": "VERTEX", "records": len(records)}
                )
            for type_name in sorted(self.edge_types):
                records = self.edges[type_name]
                rows.append(
                    {"name": type_name, "type": "EDGE", "records": len(records)}
                )
            return rows
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
                condition = condition.strip()
                if condition == "(layer IS NULL OR layer <> 'raw')":
                    # Paramless literal filter (exclude_raw); consumes no parameter.
                    rows = [
                        row
                        for row in rows
                        if row.get("layer") is None or row.get("layer") != "raw"
                    ]
                    continue
                field = condition.split(" = ?", 1)[0]
                expected = params[param_index]
                rows = [row for row in rows if row.get(field) == expected]
                param_index += 1

        order_match = re.search(r"ORDER BY ([a-z_]+) (ASC|DESC)", sql)
        if order_match:
            field, direction = order_match.groups()
            rows.sort(key=lambda row: row.get(field) or "", reverse=direction == "DESC")

        limit = params[-1] if params else len(rows)
        skip = params[-2] if " SKIP ? " in sql else 0
        return rows[skip : skip + limit]


class FakeArcadeTransaction:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        self.snapshot = (
            copy.deepcopy(self.db.records),
            copy.deepcopy(self.db.edges),
            set(self.db.vertex_types),
            set(self.db.edge_types),
        )
        return self.db

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is not None:
            records, edges, vertex_types, edge_types = self.snapshot
            self.db.records = records
            self.db.edges = edges
            self.db.memories = records["Memory"]
            self.db.relationships = edges["MemoryRelationship"]
            self.db.sessions = records["Session"]
            self.db.vertex_types = vertex_types
            self.db.edge_types = edge_types
        return False


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
    monkeypatch.setattr("visp_memory.core.arcadedb_storage.load_arcadedb_driver", lambda: driver)
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
    assert "visp-memory[arcadedb]" in str(excinfo.value)


def test_arcadedb_storage_uses_configured_data_dir(monkeypatch, tmp_path):
    driver = FakeArcadeDbModule()
    monkeypatch.setattr("visp_memory.core.arcadedb_storage.load_arcadedb_driver", lambda: driver)

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
    semantic_evidence = storage.store_evidence("ArcadeDB backend fixture", repo_id="repo-a")

    repo_a = storage.store_memory(
        "ArcadeDB supports embedded local graph storage",
        layer="semantic",
        repo_id="repo-a",
        category="fact",
        importance=0.9,
        tags=["graph"],
        metadata={"source": "test"},
        evidence_ids=[semantic_evidence],
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

    results = storage.search_memories(
        "embedded graph",
        repo_id="repo-a",
        environment=["prod"],
        task_type="deploy",
        as_of="2026-01-15T12:00:00+00:00",
    )
    assert [item["id"] for item in results] == [repo_a]
    assert results[0]["similarity"] > 0

    assert storage.update_memory(repo_b, status="archived") is True
    assert storage.list_memories(repo_id="repo-b") == []
    assert [item["id"] for item in storage.list_memories(repo_id="repo-b", status="all")] == [
        repo_b
    ]

    assert storage.get_stats() == {
        "memories_by_layer": {"semantic": 1},
        "memories_by_category": {"fact": 1},
        "total_memories": 1,
        "active_intents": 0,
        "total_relationships": 0,
    }
    assert storage.list_project_ids() == ["repo-a", "repo-b"]

    assert storage.delete_memory(repo_b) is True
    assert storage.delete_memory(repo_b) is False
    assert fake_arcadedb.paths[-1] == tmp_path / "arcadedb"


def test_arcadedb_memory_listing_supports_offsets(fake_arcadedb, tmp_path):
    storage = ArcadeDbStorage(tmp_path)
    memory_ids = [
        storage.store_memory(f"memory-{index}", repo_id="repo-a")
        for index in range(3)
    ]

    page = storage.list_memories(
        repo_id="repo-a", limit=2, offset=1, order_by="created_at ASC"
    )

    assert [item["id"] for item in page] == memory_ids[1:]


def test_arcadedb_persists_evidence_separately_and_validates_beliefs_atomically(
    fake_arcadedb, tmp_path
):
    storage = ArcadeDbStorage(tmp_path)
    evidence_id = storage.store_evidence(
        "ArcadeDB exact tool output",
        repo_id="repo-a",
        evidence_type="tool_output",
        provenance="derived",
    )

    belief_id = storage.store_memory(
        "ArcadeDB evidence-backed belief",
        layer="semantic",
        repo_id="repo-a",
        evidence_ids=[evidence_id],
    )

    assert storage.get_evidence(evidence_id)["record_type"] == "evidence"
    assert storage.get_memory(belief_id)["evidence_ids"] == [evidence_id]
    assert evidence_id not in {item["id"] for item in storage.list_memories(repo_id="repo-a")}
    with pytest.raises(EvidenceImmutableError):
        storage.update_evidence(evidence_id, content="changed")

    other = storage.store_evidence("Other repository evidence", repo_id="repo-b")
    with pytest.raises(EvidenceReferenceError):
        storage.store_memory(
            "Must not persist",
            layer="semantic",
            repo_id="repo-a",
            evidence_ids=[other],
        )
    assert all(
        item["content"] != "Must not persist"
        for item in storage.list_memories(repo_id="repo-a")
    )

    additional = storage.store_evidence("Reconciliation observation", repo_id="repo-a")
    storage.attach_evidence(belief_id, [additional], repo_id="repo-a")
    assert storage.get_memory(belief_id)["evidence_ids"] == sorted(
        [evidence_id, additional]
    )


def test_arcadedb_governed_belief_fields_round_trip_and_unknown_refuses_prewrite(
    fake_arcadedb, tmp_path
):
    storage = ArcadeDbStorage(tmp_path)
    evidence_id = storage.store_evidence("Observed backend behavior", repo_id="repo-a")

    belief_id = storage.store_memory(
        "ArcadeDB uses governed fields",
        layer="semantic",
        category="procedure",
        repo_id="repo-a",
        evidence_ids=[evidence_id],
        auto_link=False,
    )

    belief = storage.get_memory(belief_id)
    assert belief["category"] == "procedure"
    assert belief["belief_type"] == "procedure"
    assert belief["epistemic_status"] == "inferred"
    commands_before = list(fake_arcadedb.db.commands)
    with pytest.raises(ValueError, match="semantic belief type"):
        storage.store_memory(
            "Legacy semantic type",
            layer="semantic",
            category="invariant",
            repo_id="repo-a",
            evidence_ids=[evidence_id],
            auto_link=False,
        )
    assert fake_arcadedb.db.commands == commands_before


def test_arcadedb_verified_prohibition_attestation_round_trip_and_replay(
    fake_arcadedb, tmp_path, monkeypatch
):
    now = datetime(2026, 8, 2, 6, tzinfo=timezone.utc)
    private_key = Ed25519PrivateKey.generate()
    private_raw = private_key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public_raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    monkeypatch.setenv(
        PROHIBITION_AUTHORITY_KEYS_ENV,
        json.dumps({"owner-2026": base64.b64encode(public_raw).decode("ascii")}),
    )
    monkeypatch.setattr("visp_memory.core.authority.utc_now", lambda: now)
    storage = ArcadeDbStorage(tmp_path)
    content = "Never bypass review"
    evidence_id = storage.store_evidence(content, repo_id="repo-a")
    evidence = storage.get_evidence(evidence_id)
    claim = build_prohibition_claim(
        content=content,
        repo_id="repo-a",
        evidence=[{"id": evidence_id, "content_hash": evidence["content_hash"]}],
    )

    def signed(issued_at):
        return sign_prohibition_attestation(
            claim,
            key_id="owner-2026",
            private_key=base64.b64encode(private_raw).decode("ascii"),
            nonce="nonce-1",
            issued_at=issued_at,
        )

    envelope = signed(now)
    belief_id = storage.store_memory(
        content,
        layer="semantic",
        category="prohibition",
        repo_id="repo-a",
        evidence_ids=[evidence_id],
        authority_attestation=envelope,
        auto_link=False,
    )
    record_counts = (
        len(fake_arcadedb.db.records["AuthorityAttestation"]),
        len(fake_arcadedb.db.edges["BeliefAuthority"]),
    )

    belief = storage.get_memory(belief_id)
    assert belief["belief_type"] == "prohibition"
    assert belief["epistemic_status"] == "observed"
    assert belief["authority_attestation"] == envelope
    assert storage.store_memory(
        content,
        layer="semantic",
        category="prohibition",
        repo_id="repo-a",
        evidence_ids=[evidence_id],
        authority_attestation=envelope,
        auto_link=False,
    ) == belief_id
    assert record_counts == (
        len(fake_arcadedb.db.records["AuthorityAttestation"]),
        len(fake_arcadedb.db.edges["BeliefAuthority"]),
    )
    with pytest.raises(ProhibitionAuthorityError, match="nonce"):
        storage.store_memory(
            content,
            layer="semantic",
            category="prohibition",
            repo_id="repo-a",
            evidence_ids=[evidence_id],
            authority_attestation=signed(now + timedelta(minutes=1)),
            auto_link=False,
        )


def test_arcadedb_repository_purge_removes_authority_graph_only_for_purged_memories(
    fake_arcadedb, tmp_path
):
    storage = ArcadeDbStorage(tmp_path)
    storage.store_repository({"id": "repo-a", "name": "Repo A"})
    storage.store_repository({"id": "repo-b", "name": "Repo B"})
    purged_belief_id = storage.store_memory(
        "Repo A prohibition placeholder",
        layer="episodic",
        repo_id="repo-a",
        auto_link=False,
    )
    retained_belief_id = storage.store_memory(
        "Repo B prohibition placeholder",
        layer="episodic",
        repo_id="repo-b",
        auto_link=False,
    )

    for belief_id, suffix in (
        (purged_belief_id, "a"),
        (retained_belief_id, "b"),
    ):
        attestation_id = f"att-{suffix}"
        fake_arcadedb.db.records["AuthorityAttestation"][attestation_id] = {
            "id": attestation_id,
            "belief_id": belief_id,
            "key_id": f"key-{suffix}",
            "nonce": f"nonce-{suffix}",
            "digest": f"digest-{suffix}",
            "envelope": f"envelope-{suffix}",
            "created_at": "2026-08-27T00:00:00+00:00",
        }
        fake_arcadedb.db.edges["BeliefAuthority"][f"ba-{suffix}"] = {
            "id": f"ba-{suffix}",
            "belief_id": belief_id,
            "attestation_id": attestation_id,
            "created_at": "2026-08-27T00:00:00+00:00",
        }

    report = storage.purge_repository("repo-a")

    assert report["status"] == "purged"
    assert "att-a" not in fake_arcadedb.db.records["AuthorityAttestation"]
    assert "ba-a" not in fake_arcadedb.db.edges["BeliefAuthority"]
    assert "att-b" in fake_arcadedb.db.records["AuthorityAttestation"]
    assert "ba-b" in fake_arcadedb.db.edges["BeliefAuthority"]


def test_arcadedb_repository_purge_retains_repository_on_authority_cleanup_failure(
    fake_arcadedb, tmp_path, monkeypatch
):
    storage = ArcadeDbStorage(tmp_path)
    storage.store_repository({"id": "repo-a", "name": "Repo A"})
    belief_id = storage.store_memory(
        "Repo A authority cleanup failure",
        layer="episodic",
        repo_id="repo-a",
        auto_link=False,
    )
    fake_arcadedb.db.records["AuthorityAttestation"]["att-a"] = {
        "id": "att-a",
        "belief_id": belief_id,
        "key_id": "key-a",
        "nonce": "nonce-a",
        "digest": "digest-a",
        "envelope": "envelope-a",
        "created_at": "2026-08-27T00:00:00+00:00",
    }
    fake_arcadedb.db.edges["BeliefAuthority"]["ba-a"] = {
        "id": "ba-a",
        "belief_id": belief_id,
        "attestation_id": "att-a",
        "created_at": "2026-08-27T00:00:00+00:00",
    }

    original_command = fake_arcadedb.db.command

    def fail_authority_edge_delete(language, sql, *params):
        if sql.startswith("DELETE EDGE BeliefAuthority"):
            raise RuntimeError("authority edge store unavailable")
        return original_command(language, sql, *params)

    monkeypatch.setattr(fake_arcadedb.db, "command", fail_authority_edge_delete)

    report = storage.purge_repository("repo-a")

    assert report["status"] == "incomplete"
    assert storage.get_repository("repo-a") is not None
    assert "ba-a" in fake_arcadedb.db.edges["BeliefAuthority"]
    assert "att-a" in fake_arcadedb.db.records["AuthorityAttestation"]
    assert any(
        error["kind"] == "BeliefAuthority" and error["id"] == "ba-a"
        for error in report["errors"]
    )


def test_arcadedb_v2_constructor_refuses_without_schema_or_marker_drift(
    fake_arcadedb, tmp_path
):
    database_path = tmp_path / "arcadedb"
    fake_arcadedb.existing_paths.add(database_path)
    fake_arcadedb.db.records["SchemaVersion"]["storage"] = {
        "id": "storage",
        "component": "storage",
        "version": 2,
        "applied_at": "2026-01-01T00:00:00+00:00",
    }
    fake_arcadedb.db.vertex_types = {"Memory", "SchemaVersion"}
    fake_arcadedb.db.edge_types = {"MemoryRelationship"}

    with pytest.raises(StorageMigrationRequired):
        ArcadeDbStorage(tmp_path)

    assert fake_arcadedb.db.records["SchemaVersion"]["storage"]["version"] == 2
    assert fake_arcadedb.db.vertex_types == {"Memory", "SchemaVersion"}
    assert fake_arcadedb.db.edge_types == {"MemoryRelationship"}
    assert fake_arcadedb.db.commands == []


def test_arcadedb_markerless_nonempty_constructor_refuses_before_type_mutation(
    fake_arcadedb, tmp_path
):
    database_path = tmp_path / "arcadedb"
    fake_arcadedb.existing_paths.add(database_path)
    fake_arcadedb.db.vertex_types = {"Memory"}
    fake_arcadedb.db.records["Memory"]["legacy"] = {
        "id": "legacy",
        "content": "Markerless legacy record",
        "layer": "episodic",
    }
    records_before = copy.deepcopy(fake_arcadedb.db.records)

    with pytest.raises(StorageMigrationRequired, match="[Uu]nversioned"):
        ArcadeDbStorage(tmp_path)

    assert fake_arcadedb.db.records == records_before
    assert fake_arcadedb.db.vertex_types == {"Memory"}
    assert fake_arcadedb.db.edge_types == set()
    assert fake_arcadedb.db.commands == []


def _configure_current_arcadedb(fake_arcadedb, tmp_path):
    database_path = tmp_path / "arcadedb"
    fake_arcadedb.existing_paths.add(database_path)
    fake_arcadedb.db.vertex_types = set(ArcadeDbStorage.VERTEX_TYPES)
    fake_arcadedb.db.edge_types = set(ArcadeDbStorage.EDGE_TYPES)
    fake_arcadedb.db.records["SchemaVersion"]["storage"] = {
        "id": "storage",
        "component": "storage",
        "version": STORAGE_SCHEMA_VERSION,
        "applied_at": "2026-01-01T00:00:00+00:00",
    }


def test_arcadedb_v4_marker_upgrade_preserves_unbound_legacy_session(
    fake_arcadedb, tmp_path
):
    _configure_current_arcadedb(fake_arcadedb, tmp_path)
    fake_arcadedb.db.records["SchemaVersion"]["storage"]["version"] = 4
    fake_arcadedb.db.records["Session"]["legacy-session"] = {
        "id": "legacy-session",
        "started_at": "2026-01-01T00:00:00+00:00",
    }

    storage = ArcadeDbStorage(tmp_path)

    assert fake_arcadedb.db.records["SchemaVersion"]["storage"]["version"] == 5
    assert storage.get_session("legacy-session")["owner_id"] is None
    assert storage.get_session("legacy-session")["repo_id"] is None


def _add_current_arcadedb_evidence_graph(fake_arcadedb):
    fake_arcadedb.db.records["Memory"]["belief-1"] = {
        "id": "belief-1",
        "content": "Evidence-backed belief",
        "layer": "semantic",
        "repo_id": "repo-a",
        "category": "fact",
        "belief_type": "fact",
        "epistemic_status": "inferred",
    }
    fake_arcadedb.db.records["Evidence"]["evidence-1"] = {
        "id": "evidence-1",
        "content": "Exact observation",
        "content_hash": LocalStorage._evidence_hash("Exact observation"),
        "repo_id": "repo-a",
        "evidence_type": "observation",
        "provenance": "unknown",
        "metadata": {},
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    fake_arcadedb.db.edges["BeliefEvidence"]["link-1"] = {
        "id": "link-1",
        "belief_id": "belief-1",
        "evidence_id": "evidence-1",
        "created_at": "2026-01-01T00:00:00+00:00",
    }


def test_arcadedb_current_marker_missing_required_type_refuses_without_mutation(
    fake_arcadedb, tmp_path
):
    _configure_current_arcadedb(fake_arcadedb, tmp_path)
    fake_arcadedb.db.vertex_types.remove("Evidence")
    types_before = (
        set(fake_arcadedb.db.vertex_types),
        set(fake_arcadedb.db.edge_types),
    )

    with pytest.raises(StorageMigrationRequired, match="required types"):
        ArcadeDbStorage(tmp_path)

    assert types_before == (
        fake_arcadedb.db.vertex_types,
        fake_arcadedb.db.edge_types,
    )
    assert fake_arcadedb.db.commands == []


@pytest.mark.parametrize(
    "defect",
    ["dangling_belief", "missing_evidence", "cross_repo", "no_edge"],
)
def test_arcadedb_current_marker_invalid_evidence_graph_refuses_without_mutation(
    fake_arcadedb, tmp_path, defect
):
    _configure_current_arcadedb(fake_arcadedb, tmp_path)
    _add_current_arcadedb_evidence_graph(fake_arcadedb)
    if defect == "dangling_belief":
        fake_arcadedb.db.edges["BeliefEvidence"]["link-1"]["belief_id"] = "missing"
    elif defect == "missing_evidence":
        fake_arcadedb.db.records["Evidence"].clear()
    elif defect == "cross_repo":
        fake_arcadedb.db.records["Evidence"]["evidence-1"]["repo_id"] = "repo-b"
    else:
        fake_arcadedb.db.edges["BeliefEvidence"].clear()
    records_before = copy.deepcopy(fake_arcadedb.db.records)
    edges_before = copy.deepcopy(fake_arcadedb.db.edges)

    with pytest.raises(StorageMigrationRequired, match="Evidence graph"):
        ArcadeDbStorage(tmp_path)

    assert fake_arcadedb.db.records == records_before
    assert fake_arcadedb.db.edges == edges_before
    assert fake_arcadedb.db.commands == []


def test_arcadedb_current_marker_valid_evidence_graph_proceeds(
    fake_arcadedb, tmp_path
):
    _configure_current_arcadedb(fake_arcadedb, tmp_path)
    _add_current_arcadedb_evidence_graph(fake_arcadedb)

    storage = ArcadeDbStorage(tmp_path)

    assert storage.get_memory("belief-1")["evidence_ids"] == ["evidence-1"]
    assert not any(
        command.startswith("INSERT INTO SchemaVersion")
        for command in fake_arcadedb.db.commands
    )


def test_arcadedb_current_marker_missing_governed_field_refuses_before_mutation(
    fake_arcadedb, tmp_path
):
    _configure_current_arcadedb(fake_arcadedb, tmp_path)
    _add_current_arcadedb_evidence_graph(fake_arcadedb)
    fake_arcadedb.db.records["Memory"]["belief-1"].pop("epistemic_status")

    with pytest.raises(StorageMigrationRequired, match="governed"):
        ArcadeDbStorage(tmp_path)

    assert fake_arcadedb.db.commands == []


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

    session_id = storage.start_session(
        owner_id="alice", team_id="team-a", repo_id="repo-a"
    )
    assert (
        storage.end_session(session_id, "Finished backend selection", ["mem-a", "mem-b"])
        is SessionCompletionStatus.COMPLETED
    )

    session = fake_arcadedb.db.sessions[session_id]
    assert session["owner_id"] == "alice"
    assert session["repo_id"] == "repo-a"
    assert session["summary"] == "Finished backend selection"
    assert storage._json_deserialize(session["memory_ids"]) == ["mem-a", "mem-b"]
    assert storage.get_session(session_id)["team_id"] == "team-a"
    assert (
        storage.end_session(session_id, "Again", [])
        is SessionCompletionStatus.ALREADY_COMPLETED
    )
    assert session["summary"] == "Finished backend selection"
    completion_updates = [
        command
        for command in fake_arcadedb.db.commands
        if command.startswith("UPDATE Session SET summary")
    ]
    assert completion_updates
    assert "ended_at IS NULL" in completion_updates[0]
    assert "RETURN AFTER" in completion_updates[0]


def test_arcadedb_get_collection_is_none_for_conservative_vector_v1(fake_arcadedb, tmp_path):
    storage = ArcadeDbStorage(tmp_path)

    assert storage.get_collection("semantic") is None


def test_arcadedb_evidence_idempotency_compares_only_explicit_created_at(
    fake_arcadedb, tmp_path
):
    storage = ArcadeDbStorage(tmp_path / "arcade")
    first_created_at = "2026-01-01T00:00:00+00:00"
    storage.store_evidence(
        "Timestamped evidence",
        repo_id="repo-a",
        evidence_id="evidence-timestamped",
        created_at=first_created_at,
    )

    assert storage.store_evidence(
        "Timestamped evidence",
        repo_id="repo-a",
        evidence_id="evidence-timestamped",
    ) == "evidence-timestamped"
    with pytest.raises(EvidenceImmutableError, match="collision"):
        storage.store_evidence(
            "Timestamped evidence",
            repo_id="repo-a",
            evidence_id="evidence-timestamped",
            created_at="2026-01-02T00:00:00+00:00",
        )

    assert storage.get_evidence("evidence-timestamped")["created_at"] == first_created_at


def test_arcadedb_evidence_redacts_secret_before_hash_and_persistence(
    fake_arcadedb, tmp_path
):
    storage = ArcadeDbStorage(tmp_path)
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"

    evidence_id = storage.store_evidence(
        f"Observed credential {secret}", repo_id="repo-a"
    )

    evidence = storage.get_evidence(evidence_id)
    assert secret not in evidence["content"]
    assert "[REDACTED:openai-key]" in evidence["content"]
    assert evidence["content_hash"] == LocalStorage._evidence_hash(evidence["content"])


@pytest.mark.parametrize("defect", ["invalid_hash", "missing_field"])
def test_arcadedb_current_marker_corrupt_evidence_refuses_without_command(
    fake_arcadedb, tmp_path, defect
):
    _configure_current_arcadedb(fake_arcadedb, tmp_path)
    _add_current_arcadedb_evidence_graph(fake_arcadedb)
    if defect == "invalid_hash":
        fake_arcadedb.db.records["Evidence"]["evidence-1"]["content_hash"] = "bad"
    else:
        fake_arcadedb.db.records["Evidence"]["evidence-1"].pop("evidence_type")

    with pytest.raises(StorageMigrationRequired, match="Evidence graph"):
        ArcadeDbStorage(tmp_path)

    assert fake_arcadedb.db.commands == []


def test_arcadedb_corrupt_secret_evidence_read_refuses(fake_arcadedb, tmp_path):
    storage = ArcadeDbStorage(tmp_path)
    evidence_id = storage.store_evidence("Initially safe", repo_id="repo-a")
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"
    record = fake_arcadedb.db.records["Evidence"][evidence_id]
    record["content"] = f"Corrupted credential {secret}"
    record["content_hash"] = LocalStorage._evidence_hash(record["content"])

    with pytest.raises(EvidenceError, match="secret-bearing Evidence"):
        storage.get_evidence(evidence_id)


@pytest.mark.parametrize("defect", ["invalid_hash", "secret"])
def test_arcadedb_post_open_corrupt_evidence_list_and_export_refuse(
    fake_arcadedb, tmp_path, defect
):
    config = MemoryConfig(project_name="arcadedb-corrupt-export", repo_id="repo-a")
    config.storage.backend = "arcadedb"
    config.storage.data_dir = tmp_path / "data"
    config.embedding.provider = "noop"
    memory = Memory(config=config)
    evidence_id = memory._storage.store_evidence("Initially safe", repo_id="repo-a")
    record = fake_arcadedb.db.records["Evidence"][evidence_id]
    if defect == "invalid_hash":
        record["content_hash"] = "invalid"
    else:
        secret = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"
        record["content"] = f"Corrupted credential {secret}"
        record["content_hash"] = LocalStorage._evidence_hash(record["content"])
    export_path = tmp_path / f"must-not-export-{defect}.json"

    with pytest.raises(EvidenceError, match="Evidence"):
        memory._storage.list_evidence("repo-a")
    with pytest.raises(EvidenceError, match="Evidence"):
        memory.export(export_path)

    assert not export_path.exists()


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
        "AuthorityAttestation",
    ):
        assert f"CREATE VERTEX TYPE {vertex_type} IF NOT EXISTS" in commands

    for edge_type in (
        "MemoryRelationship",
        "RepoDependency",
        "TeamMember",
        "BeliefAuthority",
    ):
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

    assert storage.complete_intent(intent_id) is False
    assert storage.update_intent(intent_id, status="completed") is False
    assert storage.update_intent(
        intent_id,
        description="Updated ArcadeDB description",
        status="completed",
    ) is True
    assert storage.get_active_intents(repo_id="repo-intent")[0]["id"] == intent_id
    assert storage.get_active_intents(repo_id="repo-intent")[0]["status"] == "active"


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


def test_arcadedb_delete_memory_removes_recall_feedback(fake_arcadedb, tmp_path):
    """Deleting a memory purges its feedback just like LocalStorage."""
    storage = ArcadeDbStorage(tmp_path)
    deleted_id = storage.store_memory("ArcadeDB feedback to purge", repo_id="repo-a")
    retained_id = storage.store_memory("ArcadeDB feedback to retain", repo_id="repo-a")
    deleted_event = storage.log_recall_event(deleted_id, "used")
    retained_event = storage.log_recall_event(retained_id, "used")

    assert storage.delete_memory(deleted_id) is True
    assert deleted_event not in fake_arcadedb.db.records["RecallFeedback"]
    assert retained_event in fake_arcadedb.db.records["RecallFeedback"]
    assert storage.inspect_recall_utility(repo_id="repo-a")["summary"] == {
        "total_events": 1,
        "by_event_type": {"used": 1},
        "memories": 1,
    }


def test_arcadedb_search_attaches_canonical_recall_utility_scores(
    fake_arcadedb, tmp_path
):
    """ArcadeDB search ranks the same canonical utility signals as SQLite."""
    storage = ArcadeDbStorage(tmp_path)
    memory_id = storage.store_memory("ArcadeDB utility ranking parity", repo_id="repo-a")
    storage.log_recall_event(memory_id, "used")
    storage._insert_record(
        "RecallFeedback",
        {
            "id": "cross-repo-search-event",
            "memory_id": memory_id,
            "event_type": "dismissed",
            "repo_id": "repo-b",
            "query_hash": None,
            "task_id": None,
            "outcome": None,
            "metadata": {},
            "created_at": "2024-01-01T00:00:00+00:00",
        },
        storage.RECALL_EVENT_FIELDS,
        storage.RECORD_JSON_FIELDS["RecallFeedback"],
    )

    results = storage.search_memories("utility ranking", repo_id="repo-a")

    assert results[0]["id"] == memory_id
    assert results[0]["utility_score"] > 0
    assert results[0]["utility_signal"] == {
        "counts": {"used": 1},
        "total_events": 1,
        "rank_adjustment": utility_rank_adjustment(results[0]["utility_score"]),
    }


def test_arcadedb_reinforces_on_use_in_parity_with_local(fake_arcadedb, tmp_path):
    # Backend parity: a used memory must strengthen (access_count++) just like SQLite,
    # while a merely-surfaced one must not.
    storage = ArcadeDbStorage(tmp_path)
    memory_id = storage.store_memory("ArcadeDB reinforce signal", repo_id="repo-a")

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


def test_arcadedb_recall_utility_enforces_scope_and_verifies_history(
    fake_arcadedb, tmp_path
):
    storage = ArcadeDbStorage(tmp_path)
    memory_id = storage.store_memory("ArcadeDB repository-scoped recall", repo_id="repo-a")

    with pytest.raises(ValueError, match="repository mismatch"):
        storage.log_recall_event(memory_id, "used", repo_id="repo-b")
    with pytest.raises(ValueError, match="repository mismatch"):
        storage.inspect_recall_utility(memory_id=memory_id, repo_id="repo-b")
    with pytest.raises(ValueError, match="repository mismatch"):
        storage.reset_recall_utility(memory_id=memory_id, repo_id="repo-b")

    good_id = storage.log_recall_event(memory_id, "used", repo_id="repo-a")
    storage._insert_record(
        "RecallFeedback",
        {
            "id": "legacy-arcade-cross-repo-event",
            "memory_id": memory_id,
            "event_type": "dismissed",
            "repo_id": "repo-b",
            "query_hash": None,
            "task_id": None,
            "outcome": None,
            "metadata": {},
            "created_at": "2024-01-01T00:00:00+00:00",
        },
        storage.RECALL_EVENT_FIELDS,
        storage.RECORD_JSON_FIELDS["RecallFeedback"],
    )

    report = storage.inspect_recall_utility(memory_id=memory_id)
    assert report["summary"]["total_events"] == 1
    assert report["summary"]["by_event_type"] == {"used": 1}
    assert [event["id"] for event in report["events"]] == [good_id]
    assert report["verification"] == {
        "valid": False,
        "checked_events": 2,
        "cross_repository_events": 1,
        "violations": [
            {
                "event_id": "legacy-arcade-cross-repo-event",
                "memory_id": memory_id,
                "event_repo_id": "repo-b",
                "memory_repo_id": "repo-a",
            }
        ],
    }

    assert storage.inspect_recall_utility(repo_id="repo-a")["summary"]["total_events"] == 1
    assert storage.inspect_recall_utility(repo_id="repo-b")["summary"]["total_events"] == 0
    assert storage.reset_recall_utility(repo_id="repo-b") == 0

    assert storage.reset_recall_utility(memory_id=memory_id) == 1
    assert "legacy-arcade-cross-repo-event" in fake_arcadedb.db.records["RecallFeedback"]


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
        tags=[provenance_tag(Provenance.DERIVED)],
        auto_link=False,
    )
    target_id = memory._storage.store_memory(
        "Repository scope evidence explains access controls",
        repo_id="repo-a",
        importance=0.8,
        tags=[provenance_tag(Provenance.DERIVED)],
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
    from visp_memory.core.reporting import MemoryIntelligenceReporter

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
            category="negative",
            repo_id="repo-a",
            importance=0.8,
            tags=["warning", "legacy_category:fragile_area"],
        auto_link=False,
        evidence_ids=[
            memory._storage.store_evidence(
                "Embedded graph backend needs careful packaging", repo_id="repo-a"
            )
        ],
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
    if sys.platform == "win32":
        pytest.skip("arcadedb_embedded native smoke is unstable on Windows")

    try:
        import arcadedb_embedded  # noqa: F401
    except ImportError:
        pytest.skip("arcadedb_embedded is not installed")

    storage = ArcadeDbStorage(tmp_path)
    memory_id = storage.store_memory("Real ArcadeDB smoke", repo_id="repo-real", auto_link=False)

    assert storage.get_memory(memory_id)["content"] == "Real ArcadeDB smoke"


def test_real_arcadedb_memory_listing_supports_offsets(tmp_path):
    if sys.platform == "win32":
        pytest.skip("arcadedb_embedded native smoke is unstable on Windows")

    try:
        import arcadedb_embedded  # noqa: F401
    except ImportError:
        pytest.skip("arcadedb_embedded is not installed")

    storage = ArcadeDbStorage(tmp_path)
    memory_ids = [
        storage.store_memory(
            f"Real ArcadeDB page {index}", repo_id="repo-real", auto_link=False
        )
        for index in range(3)
    ]

    page = storage.list_memories(
        repo_id="repo-real", limit=2, offset=1, order_by="created_at ASC"
    )

    assert [item["id"] for item in page] == memory_ids[1:]
