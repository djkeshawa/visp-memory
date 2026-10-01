"""Migration checks scope and downgrades trust before changing client config."""

import copy

import pytest

from tests.cli.test_connect import _RemoteStorage, runner
from visp_memory import Memory, MemoryConfig
from visp_memory.core.remote.errors import RemoteStorageError
from visp_memory.interfaces.cli import app


class MigrationRemote(_RemoteStorage):
    graph = {}
    imports = []

    def export_graph(self, repo_id):
        return copy.deepcopy(self.graph)

    def import_graph(self, graph, *, default_repo_id):
        self.imports.append(copy.deepcopy(graph))
        type(self).graph = copy.deepcopy(graph)
        return {"status": "completed"}


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.delenv("VISP_MEMORY_CONFIG", raising=False)
    monkeypatch.setattr("visp_memory.interfaces.connect.RemoteStorage", MigrationRemote)
    MigrationRemote.repositories = {}
    MigrationRemote.imports = []
    MigrationRemote.graph = {}
    config = MemoryConfig(repo_id="old")
    config.storage.data_dir = tmp_path / "data"
    config.embedding.provider = "noop"
    config.save(tmp_path / "visp-memory.yaml")
    return config


def _seed(config, repo="old", tier="external", kind="memory"):
    with Memory(config=config) as memory:
        if kind == "evidence":
            return memory._storage.store_evidence("standalone evidence", repo_id=repo)
        if kind == "intent":
            intent_id = memory._storage.set_intent("archived goal", repo_id=repo)
            memory._storage.update_intent(intent_id, status="completed")
            return intent_id
        memory_id = memory._storage.store_memory(
            "local record", repo_id=repo, source=tier, tags=[f"provenance:{tier}"],
        )
        if tier == "authored":
            memory._storage.update_memory(memory_id, approved_by="reviewer")
        return memory_id


def _connect(*args):
    return runner.invoke(app, [
        "connect", "--server-url", "http://127.0.0.1:9123", "--migrate-local", *args,
    ])


@pytest.mark.parametrize("kind", ["memory", "evidence", "intent"])
def test_refuses_other_repo_before_overwriting_config(project, tmp_path, kind):
    _seed(project, kind=kind)
    path = tmp_path / "visp-memory.yaml"
    before = path.read_bytes()
    result = _connect("--repo", "NEW")
    assert result.exit_code == 1, result.output
    assert "old" in result.output and "NEW" in result.output
    assert "--repo old" in result.output
    assert path.read_bytes() == before
    assert not MigrationRemote.imports
    assert not MigrationRemote.repositories


def test_mixed_repo_store_is_refused_even_when_target_has_records(project):
    _seed(project)
    _seed(project, repo="other")
    result = _connect()
    assert result.exit_code == 1, result.output
    assert "other" in result.output and "old" in result.output
    assert not MigrationRemote.imports


@pytest.mark.parametrize("tier", ["authored", "derived", "assisted"])
def test_higher_trust_is_downgraded_without_confirmation(project, tier):
    memory_id = _seed(project, tier=tier)
    result = _connect()
    assert result.exit_code == 0, result.output
    assert str(project.storage.data_dir) in result.output
    # store_memory also captures one immutable Evidence record in this tier.
    assert f"{tier}: 2" in result.output, result.output
    assert "imported as external" in result.output
    assert "PATCH /memories/{id}" in result.output
    assert "provenance:authored" in result.output
    row = MigrationRemote.imports[0]["memories"]["episodic"][0]
    assert row["source"] == "external"
    assert row["tags"] == ["provenance:external"]
    assert row["approved_by"] is None
    assert MigrationRemote.imports[0]["evidence"][0]["provenance"] == "external"
    with Memory(config=project) as local:
        assert local._storage.peek_memory(memory_id)["source"] == tier


@pytest.mark.parametrize("tier", ["external", "unknown", "authored", "derived", "assisted"])
def test_rerun_reports_imported_and_already_present_after_downgrade(project, tier):
    _seed(project, tier=tier)
    first = _connect()
    assert first.exit_code == 0, first.output
    assert "Imported 2 local records; 0 already present" in first.output, first.output
    row = MigrationRemote.imports[0]["memories"]["episodic"][0]
    assert row["source"] == "external"
    assert row["tags"] == ["provenance:external"]
    assert row["approved_by"] is None
    assert MigrationRemote.imports[0]["evidence"][0]["provenance"] == "external"
    second = _connect()
    assert second.exit_code == 0, second.output
    assert "Imported 0 local records; 2 already present" in second.output


def test_standalone_higher_trust_evidence_is_downgraded(project):
    with Memory(config=project) as memory:
        memory._storage.store_evidence("authored evidence", repo_id="old", provenance="authored")
    result = _connect()
    assert result.exit_code == 0, result.output
    assert "authored: 1" in result.output
    assert MigrationRemote.imports[0]["evidence"][0]["provenance"] == "external"


def test_standalone_evidence_in_unconfigured_repo_is_refused(project):
    _seed(project, repo="unregistered", kind="evidence")
    result = _connect()
    assert result.exit_code == 1, result.output
    assert "unregistered" in result.output
    assert not MigrationRemote.imports


def test_partial_rerun_counts_each_record_kind(project):
    _seed(project)
    first = _connect()
    assert first.exit_code == 0, first.output
    MigrationRemote.graph["evidence"] = []
    second = _connect()
    assert second.exit_code == 0, second.output
    assert "Imported 1 local records; 1 already present" in second.output


def test_source_and_tiers_are_printed_before_import_failure(project, tmp_path, monkeypatch):
    _seed(project)
    path = tmp_path / "visp-memory.yaml"
    before = path.read_bytes()

    def refuse_import(*_args, **_kwargs):
        raise RemoteStorageError("import refused")

    monkeypatch.setattr(MigrationRemote, "import_graph", refuse_import)
    result = _connect()
    assert result.exit_code == 1, result.output
    assert str(project.storage.data_dir) in result.output
    assert "external: 2" in result.output
    assert result.output.index("Trust tiers:") < result.output.index("import refused")
    assert path.read_bytes() == before


def test_connect_help_has_no_unused_yes_flag():
    result = runner.invoke(app, ["connect", "--help"], color=False)
    assert result.exit_code == 0, result.output
    assert "--yes" not in result.output
