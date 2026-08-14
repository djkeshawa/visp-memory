"""Task-scoped recall — the contract path's route to the code graph.

`Memory.recall` ranks by text, so it finds memories that *sound like* the query.
A coordinator knows more than the words it typed: which task, which files, which
constraints. `hybrid_retrieval` has fused the code graph into retrieval since
0.5.0, but only `ContextCompiler` and its callers could reach it — the machine
contract, the one surface the coordinator actually speaks, could not.

These tests pin the wiring AND its bounds. The bounds matter more: a signal that
can take a memory away, or that quietly lowers the relevance floor when a file
is named, is worse than no signal, because a coordinator cannot tell which of
those happened.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from visp_memory.config import CodeGraphConfig
from visp_memory.core.code_graph import clear_cache
from visp_memory.core.contract_recall import MAX_ADMISSIONS, recall_for_task
from visp_memory.core.memory import Memory

REPO = "project-a"
VECTORS = Path(__file__).resolve().parents[1] / "vectors" / "file-grain-collapse"

# From the vendored conformance vectors: src/service/api.ts imports
# src/core/store.ts, which imports src/core/config.ts.
SEED_FILE = "src/service/api.ts"
HOP_ONE_FILE = "src/core/store.ts"
QUERY = "pagination cursors"


def _store(memory: Memory, content: str, files: list[str], repo_id: str = REPO) -> str:
    evidence_id = memory._storage.store_evidence(content, repo_id=repo_id)
    return memory._storage.store_memory(
        content,
        layer="semantic",
        repo_id=repo_id,
        evidence_ids=[evidence_id],
        auto_link=False,
        metadata={"files": list(files), "confidence": 0.9},
    )


def _ids(results) -> list[str]:
    return [str(item["id"]) for item in results]


@pytest.fixture
def memory(tmp_path) -> Memory:
    clear_cache()
    instance = Memory(data_dir=tmp_path / "store")
    instance.config.code_graph = CodeGraphConfig()
    return instance


@pytest.fixture
def graph_memory(tmp_path, memory) -> Memory:
    projection = tmp_path / "graph.json"
    projection.write_bytes((VECTORS / "projection.json").read_bytes())
    memory.config.code_graph = CodeGraphConfig(intel_projection_path=projection)
    return memory


# --- the text path is untouched -------------------------------------------------------


def test_without_files_the_result_is_exactly_what_recall_returns(memory):
    _store(memory, "Pagination cursors are opaque here", [SEED_FILE])

    outcome = recall_for_task(memory, QUERY, repo_id=REPO)

    assert outcome.strategy == "text"
    assert _ids(outcome.results) == _ids(memory.recall(QUERY, repo_id=REPO, limit=10))
    assert outcome.admissions == []
    assert outcome.diagnostics()["strategy"] == "text"


# --- what naming the task's files buys ------------------------------------------------


def test_a_memory_about_the_task_file_is_admitted_though_the_words_miss(memory):
    """The gap this closes: identity on the file, invisible to the query's words."""
    silent = _store(memory, "Writes must hold the advisory lock", [SEED_FILE])
    _store(memory, "Pagination cursors are opaque here", [SEED_FILE])

    assert silent not in _ids(memory.recall(QUERY, repo_id=REPO, limit=10))

    outcome = recall_for_task(memory, QUERY, repo_id=REPO, files=[SEED_FILE])

    assert silent in _ids(outcome.results)
    assert outcome.strategy == "text+code"


def test_the_result_is_a_superset_of_the_text_recall(memory):
    _store(memory, "Pagination cursors are opaque here", [SEED_FILE])
    _store(memory, "Writes must hold the advisory lock", [SEED_FILE])
    _store(memory, "Telemetry batches every thirty seconds", ["src/isolated/orphan.ts"])

    text_only = memory.recall(QUERY, repo_id=REPO, limit=10)
    outcome = recall_for_task(memory, QUERY, repo_id=REPO, files=[SEED_FILE])

    assert _ids(outcome.results)[: len(text_only)] == _ids(text_only)
    assert set(_ids(text_only)).issubset(set(_ids(outcome.results)))


def test_admissions_are_bounded(memory):
    for index in range(MAX_ADMISSIONS + 4):
        _store(memory, f"Advisory lock rule number {index}", [SEED_FILE])
    _store(memory, "Pagination cursors are opaque here", [SEED_FILE])

    outcome = recall_for_task(memory, QUERY, repo_id=REPO, files=[SEED_FILE])

    assert len(outcome.admissions) <= MAX_ADMISSIONS


def test_naming_a_file_is_not_a_back_door_to_a_lower_relevance_floor(memory):
    """A file-scoped recall must not admit text hits recall itself rejected.

    The retriever's direct channel opens at 0.16 and recall's floor is 0.56. If
    a named file let everything between them through, `--file` would silently be
    a precision switch, and the coordinator would have no way to know.
    """
    weak = _store(memory, "Cursors were discussed at the offsite", [])
    _store(memory, "Pagination cursors are opaque here", [SEED_FILE])

    assert weak not in _ids(memory.recall(QUERY, repo_id=REPO, limit=10))

    outcome = recall_for_task(memory, QUERY, repo_id=REPO, files=[SEED_FILE])

    assert weak not in _ids(outcome.results)


def test_another_repository_is_never_admitted(memory):
    foreign = _store(memory, "Writes must hold the advisory lock", [SEED_FILE], repo_id="other")
    _store(memory, "Pagination cursors are opaque here", [SEED_FILE])

    outcome = recall_for_task(memory, QUERY, repo_id=REPO, files=[SEED_FILE])

    assert foreign not in _ids(outcome.results)


class TestAdmissionsPassTheSameGateAsRecall:
    """The retriever reads storage directly, so it needs the eligibility contract.

    Without it, the admissions would be the only rows in a contract envelope that
    nobody had checked for temporal validity or declared scope — a memory that
    expired last year, arriving because a file name was mentioned.
    """

    def test_an_expired_memory_is_not_admitted(self, memory):
        expired = memory._storage.store_memory(
            "Writes must hold the advisory lock",
            layer="semantic",
            repo_id=REPO,
            evidence_ids=[
                memory._storage.store_evidence("Writes must hold the lock", repo_id=REPO)
            ],
            auto_link=False,
            metadata={
                "files": [SEED_FILE],
                "confidence": 0.9,
                "valid_to": "2020-01-01T00:00:00+00:00",
            },
        )
        _store(memory, "Pagination cursors are opaque here", [SEED_FILE])

        outcome = recall_for_task(memory, QUERY, repo_id=REPO, files=[SEED_FILE])

        assert expired not in _ids(outcome.results)

    def test_a_memory_declared_for_another_environment_is_not_admitted(self, memory):
        production_only = memory._storage.store_memory(
            "Writes must hold the advisory lock",
            layer="semantic",
            repo_id=REPO,
            evidence_ids=[
                memory._storage.store_evidence("Writes must hold the lock", repo_id=REPO)
            ],
            auto_link=False,
            metadata={"files": [SEED_FILE], "confidence": 0.9, "environment": ["production"]},
        )
        _store(memory, "Pagination cursors are opaque here", [SEED_FILE])

        outcome = recall_for_task(
            memory, QUERY, repo_id=REPO, files=[SEED_FILE], environment=["staging"]
        )

        assert production_only not in _ids(outcome.results)


# --- the code graph, when there is one ------------------------------------------------


def test_an_import_neighbour_is_admitted_only_when_a_projection_exists(memory, tmp_path):
    neighbour = _store(memory, "Writes must hold the advisory lock", [HOP_ONE_FILE])
    _store(memory, "Pagination cursors are opaque here", [SEED_FILE])

    without = recall_for_task(memory, QUERY, repo_id=REPO, files=[SEED_FILE])
    assert neighbour not in _ids(without.results)
    assert without.diagnostics()["codeGraph"]["state"] == "unconfigured"

    projection = tmp_path / "graph.json"
    projection.write_bytes((VECTORS / "projection.json").read_bytes())
    memory.config.code_graph = CodeGraphConfig(intel_projection_path=projection)

    with_graph = recall_for_task(memory, QUERY, repo_id=REPO, files=[SEED_FILE])

    assert neighbour in _ids(with_graph.results)
    assert with_graph.diagnostics()["codeGraph"]["state"] == "loaded"


def test_a_structural_admission_says_it_is_structural(graph_memory):
    neighbour = _store(graph_memory, "Writes must hold the advisory lock", [HOP_ONE_FILE])
    _store(graph_memory, "Pagination cursors are opaque here", [SEED_FILE])

    outcome = recall_for_task(graph_memory, QUERY, repo_id=REPO, files=[SEED_FILE])

    admitted = {str(item["id"]): item for item in outcome.admissions}
    assert neighbour in admitted
    factors = admitted[neighbour]["retrieval_factors"]
    assert factors["structural_hops"] == 1
    assert factors["code_graph_snapshot"]
    assert "structure" in admitted[neighbour]["retrieval_channels"]


def test_identity_matches_outrank_import_neighbours(graph_memory):
    neighbour = _store(graph_memory, "Advisory locks are held by writers", [HOP_ONE_FILE])
    same_file = _store(graph_memory, "Retry budgets belong to the caller", [SEED_FILE])
    _store(graph_memory, "Pagination cursors are opaque here", [SEED_FILE])

    outcome = recall_for_task(graph_memory, QUERY, repo_id=REPO, files=[SEED_FILE])

    order = _ids(outcome.admissions)
    assert order.index(same_file) < order.index(neighbour)


# --- the scopes the coordinator forwards ----------------------------------------------


def test_an_unparseable_as_of_is_refused_rather_than_ignored(memory):
    _store(memory, "Pagination cursors are opaque here", [SEED_FILE])

    with pytest.raises(ValueError):
        recall_for_task(memory, QUERY, repo_id=REPO, as_of="not-a-timestamp")
