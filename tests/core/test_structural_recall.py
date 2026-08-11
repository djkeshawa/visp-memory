"""Structural conditioning of recall, at the retriever.

Memory recalls by text, so it finds memories that *sound like* the task. A memory
recorded against `core/ranking.py` scores exactly zero for a task editing
`core/hybrid_retrieval.py`, which imports it, unless the words happen to overlap: both
of Memory's file-aware signals are identity tests, and identity is zero at one
structural hop.

These tests pin the fix and, more importantly, its bounds. The properties in
`TestBounds` are the ones that decide whether this is a signal that informs or a signal
that decides, and each has a named failure it exists to prevent.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from visp_memory.core.code_graph import FileGraph
from visp_memory.core.hybrid_retrieval import (
    ENTITY_EXACT_FLOOR,
    STRUCTURAL_MAX_ADMISSIONS,
    STRUCTURAL_SCORE_CEILING,
    HybridRetriever,
)
from visp_memory.core.storage import LocalStorage

SNAPSHOT = "urn:visp-intel:snapshot:1.0:sha256:" + ("ab" * 32)
REPO = "project-a"


def _graph() -> FileGraph:
    """src/api.py imports src/store.py imports src/config.py; tests/test_api.py covers api."""
    return FileGraph(
        repository_instance_id="urn:visp-intel:repository-instance:1.0:sha256:" + ("cd" * 32),
        snapshot_id=SNAPSHOT,
        head_snapshot_id=SNAPSHOT,
        file_paths=(
            "src/api.py",
            "src/config.py",
            "src/store.py",
            "src/unrelated.py",
            "tests/test_api.py",
        ),
        test_file_paths=("tests/test_api.py",),
        internal_edges={
            "src/api.py": ("src/store.py",),
            "src/store.py": ("src/config.py",),
        },
        test_edges={"src/api.py": ("tests/test_api.py",)},
    )


def _store(storage, content, files, **kwargs):
    evidence_id = storage.store_evidence(content, repo_id=REPO)
    return storage.store_memory(
        content,
        layer="semantic",
        repo_id=REPO,
        evidence_ids=[evidence_id],
        auto_link=False,
        metadata={"files": list(files), "confidence": 0.9},
        **kwargs,
    )


@pytest.fixture
def corpus(tmp_path):
    """One memory per file, written so that no two share vocabulary with the query.

    The query is about the seed file. Nothing in the hop-1 or hop-2 memories repeats a
    word from it, so anything that surfaces them surfaced them structurally.
    """
    storage = LocalStorage(tmp_path)
    ids = {
        "seed": _store(storage, "Pagination cursors are opaque here", ["src/api.py"]),
        "hop1": _store(storage, "Writes must hold the advisory lock", ["src/store.py"]),
        "hop1_test": _store(storage, "Fixtures reset the clock", ["tests/test_api.py"]),
        "hop2": _store(storage, "Defaults resolve last, after env", ["src/config.py"]),
        "far": _store(storage, "Telemetry batches every thirty seconds", ["src/unrelated.py"]),
    }
    return storage, ids


def _ids(results) -> list[str]:
    return [str(item["id"]) for item in results]


def _by_id(results) -> dict[str, dict]:
    return {str(item["id"]): item for item in results}


def _retrieve(storage, graph, *, files=("src/api.py",), query="pagination cursors", limit=80):
    return HybridRetriever(storage, code_graph=graph).retrieve(
        query, repo_id=REPO, files=list(files), limit=limit
    )


# --- what the signal is for -----------------------------------------------------------


def test_a_memory_one_import_hop_away_is_invisible_without_a_graph(corpus):
    """The gap, stated as a test before the fix is applied to it."""
    storage, ids = corpus

    results = _retrieve(storage, None)

    assert ids["hop1"] not in _ids(results)
    assert ids["hop2"] not in _ids(results)


def test_structure_surfaces_the_neighbour_the_words_never_would(corpus):
    storage, ids = corpus

    results = _retrieve(storage, _graph())

    surfaced = _ids(results)
    assert ids["hop1"] in surfaced
    assert ids["hop1_test"] in surfaced
    assert ids["far"] not in surfaced


def test_hop_two_ranks_below_hop_one(corpus):
    storage, ids = corpus

    scored = _by_id(_retrieve(storage, _graph()))

    assert scored[ids["hop2"]]["retrieval_factors"]["structural_proximity"] == 0.25
    assert scored[ids["hop1"]]["retrieval_factors"]["structural_proximity"] == 0.5
    assert scored[ids["hop2"]]["relevance_score"] < scored[ids["hop1"]]["relevance_score"]


# --- the bounds -----------------------------------------------------------------------


class TestBounds:
    def test_absence_is_byte_identical_to_today(self, corpus):
        """Unconfigured, absent, stale, malformed and empty all arrive here as None."""
        storage, _ = corpus

        without = _retrieve(storage, None)
        inert = _retrieve(storage, FileGraph())

        assert without == inert

    def test_a_seed_outside_the_snapshot_changes_nothing(self, corpus):
        """Not "no neighbours" -- no answer. Either way the pack is today's pack."""
        storage, _ = corpus

        without = _retrieve(storage, None, files=("docs/design.md",))
        with_graph = _retrieve(storage, _graph(), files=("docs/design.md",))

        assert without == with_graph

    def test_the_result_is_a_superset_of_the_no_graph_result(self, corpus):
        """Monotonicity. A signal that can take a memory away is deciding what recall
        may not return, and that is the one power this must not have.

        Checked at every limit from 1 up, because the failure only appears at the
        truncation boundary -- one sort and one slice would let an admission push the
        weakest baseline memory off the bottom.
        """
        storage, _ = corpus

        for limit in range(1, 8):
            without = set(_ids(_retrieve(storage, None, limit=limit)))
            with_graph = set(_ids(_retrieve(storage, _graph(), limit=limit)))
            assert without <= with_graph, f"limit {limit} dropped {without - with_graph}"

    def test_no_memory_the_other_channels_found_is_re_scored(self, corpus):
        """Structure adds; it does not touch what was already there.

        Admissions rank in a space disjoint from the entity channel, so no other
        memory's reciprocal-rank term -- and therefore no other memory's score -- moves
        when a projection appears.
        """
        storage, _ = corpus

        without = _by_id(_retrieve(storage, None))
        with_graph = _by_id(_retrieve(storage, _graph()))

        for memory_id, before in without.items():
            after = with_graph[memory_id]
            assert after["relevance_score"] == before["relevance_score"], memory_id
            assert after["retrieval_channels"] == before["retrieval_channels"]
            assert after["retrieval_factors"] == before["retrieval_factors"]

    def test_admissions_are_bounded(self, tmp_path):
        """A hub file must not turn one recall into a repository tour."""
        storage = LocalStorage(tmp_path)
        neighbours = tuple(f"src/n{index:03d}.py" for index in range(20))
        for index, path in enumerate(neighbours):
            _store(storage, f"Unrelated note {index} about throughput", [path])
        _store(storage, "Pagination cursors are opaque here", ["src/hub.py"])

        graph = FileGraph(
            snapshot_id=SNAPSHOT,
            head_snapshot_id=SNAPSHOT,
            file_paths=("src/hub.py",) + neighbours,
            internal_edges={"src/hub.py": neighbours},
        )
        results = _retrieve(storage, graph, files=("src/hub.py",))

        admitted = [
            item for item in results if "structure" in item.get("retrieval_channels", [])
        ]
        assert len(admitted) == STRUCTURAL_MAX_ADMISSIONS

    def test_a_structural_admission_never_outranks_an_identity_match(self, corpus):
        """One import away is never presented as "about this file"."""
        storage, ids = corpus

        results = _retrieve(storage, _graph())
        scored = _by_id(results)

        assert scored[ids["seed"]]["relevance_score"] >= ENTITY_EXACT_FLOOR
        for item in results:
            if "structure" in item["retrieval_channels"]:
                assert item["relevance_score"] <= STRUCTURAL_SCORE_CEILING
                assert item["relevance_score"] < scored[ids["seed"]]["relevance_score"]

    def test_structure_cannot_seed_the_associative_walk(self, corpus):
        """Otherwise structure would move the graph scores of memories it did not admit,
        which is re-scoring results it never found."""
        storage, _ = corpus

        without = _by_id(_retrieve(storage, None))
        with_graph = _by_id(_retrieve(storage, _graph()))

        for memory_id, before in without.items():
            assert (
                with_graph[memory_id]["retrieval_factors"]["graph_score"]
                == before["retrieval_factors"]["graph_score"]
            )

    def test_an_admission_arrives_labelled_with_the_snapshot_that_vouched_for_it(self, corpus):
        """A remembered claim about code that may have moved, injected unlabelled, is
        the confident wrong answer this layer exists to prevent."""
        storage, ids = corpus

        admitted = _by_id(_retrieve(storage, _graph()))[ids["hop1"]]

        factors = admitted["retrieval_factors"]
        assert factors["structural_proximity"] == 0.5
        assert factors["structural_hops"] == 1
        assert factors["code_graph_snapshot"] == SNAPSHOT
        assert factors["entity_rank"] is None
        explanation = " ".join(admitted["ranking_explanation"])
        assert "1 import/test hop" in explanation
        assert SNAPSHOT[-12:] in explanation

    def test_the_admission_floor_excludes_hop_three(self, tmp_path):
        """Two hops was measured; three was not, so three does not reach the prompt."""
        storage = LocalStorage(tmp_path)
        chain = tuple(f"src/f{index}.py" for index in range(5))
        ids = {
            path: _store(storage, f"Isolated note {index} on batching", [path])
            for index, path in enumerate(chain)
        }

        graph = FileGraph(
            snapshot_id=SNAPSHOT,
            head_snapshot_id=SNAPSHOT,
            file_paths=chain,
            internal_edges={chain[index]: (chain[index + 1],) for index in range(len(chain) - 1)},
        )
        results = _by_id(
            _retrieve(storage, graph, files=(chain[0],), query="cursor pagination opaque")
        )

        admitted = {
            memory_id
            for memory_id, item in results.items()
            if "structure" in item["retrieval_channels"]
        }
        assert admitted == {ids["src/f1.py"], ids["src/f2.py"]}
        assert ids["src/f3.py"] not in results
        assert ids["src/f4.py"] not in results

    def test_determinism(self, corpus):
        storage, _ = corpus
        graph = _graph()

        first = _retrieve(storage, graph)
        second = _retrieve(storage, copy.deepcopy(graph))

        assert first == second




# --- the ranking tiers, at Memory's own file factor -----------------------------------


class TestFileFactorTiers:
    """`_file_factor` returned zero at one structural hop. It now returns a tier.

    The order is the whole point: both structural tiers sit strictly below both identity
    tiers, so nothing that ranks above something else today can invert tomorrow.
    Structure only distinguishes memories that used to score a flat zero.
    """

    def test_identity_tiers_are_unchanged(self):
        from visp_memory.core.memory import Memory

        assert Memory._file_factor("see src/api.py", ["src/api.py"]) == (
            1.0,
            "matched file src/api.py",
        )
        score, reason = Memory._file_factor("see api.py", ["src/api.py"])
        assert score == 0.6
        assert "api.py" in reason

    def test_a_structural_hop_scores_below_a_basename_match(self):
        from visp_memory.core.memory import Memory

        proximity = {"src/store.py": 0.5, "src/config.py": 0.25}

        hop_one, reason = Memory._file_factor("see src/store.py", ["src/api.py"], proximity)
        hop_two, _ = Memory._file_factor("see src/config.py", ["src/api.py"], proximity)

        assert hop_one == 0.5
        assert hop_two == 0.25
        assert hop_two < hop_one < 0.6
        assert "1 import/test hop" in reason

    def test_identity_always_wins_over_proximity(self):
        """A memory naming the file in scope keeps 1.0 even when it also names a
        neighbour, so no existing ranking can invert."""
        from visp_memory.core.memory import Memory

        score, _ = Memory._file_factor(
            "src/api.py calls src/store.py", ["src/api.py"], {"src/store.py": 0.5}
        )
        assert score == 1.0

    def test_no_proximity_is_the_old_behaviour(self):
        from visp_memory.core.memory import Memory

        assert Memory._file_factor("see src/store.py", ["src/api.py"]) == (0.0, None)
        assert Memory._file_factor("see src/store.py", ["src/api.py"], {}) == (0.0, None)


class TestConfiguration:
    def test_unset_configuration_is_inert(self):
        from visp_memory.config import CodeGraphConfig

        assert CodeGraphConfig().projection_path_for("any-repo") is None

    def test_a_per_repo_path_wins_over_the_default(self, tmp_path):
        from visp_memory.config import CodeGraphConfig

        config = CodeGraphConfig(
            intel_projection_path=tmp_path / "default.json",
            intel_projection_paths={"repo-b": tmp_path / "b.json"},
        )

        assert config.projection_path_for("repo-b") == tmp_path / "b.json"
        assert config.projection_path_for("repo-a") == tmp_path / "default.json"
        assert config.projection_path_for(None) == tmp_path / "default.json"


class TestTheSeamIsLive:
    """Configuration to compiled context, through the real artifact on disk.

    A mechanism that works only when a test hands it a graph is a mechanism that does
    nothing: the lesson from the round before this one is that the graph arrived and
    stopped one layer short of anything that reads it. These tests start from a
    projection file and a config path and end at a compiled context.
    """

    VECTORS = Path(__file__).resolve().parents[1] / "vectors" / "file-grain-collapse"

    def _configured(self, tmp_path):
        from visp_memory.config import CodeGraphConfig, MemoryConfig
        from visp_memory.core.code_graph import clear_cache

        clear_cache()
        projection = tmp_path / "graph.json"
        projection.write_bytes((self.VECTORS / "projection.json").read_bytes())
        config = MemoryConfig()
        config.code_graph = CodeGraphConfig(intel_projection_path=projection)
        return config, projection

    def test_config_path_to_collapsed_graph(self, tmp_path):
        from visp_memory.core.code_graph import graph_for_repo, load_for_repo

        config, _ = self._configured(tmp_path)

        graph = graph_for_repo(config, REPO)

        assert graph is not None
        assert "src/service/api.ts" in graph.file_paths
        assert load_for_repo(config, REPO).state.value == "loaded"

    def test_a_compiled_context_carries_a_structurally_admitted_memory(self, tmp_path):
        from visp_memory.core.code_graph import graph_for_repo
        from visp_memory.core.context_compiler import ContextCompiler

        config, _ = self._configured(tmp_path)
        storage = LocalStorage(tmp_path / "store")
        _store(storage, "Router owns retry budgets end to end", ["src/service/Router.ts"])
        _store(storage, "Pagination cursors are opaque here", ["src/service/api.ts"])

        compiled = ContextCompiler(storage, code_graph=graph_for_repo(config, REPO)).compile(
            "pagination cursors",
            repo_id=REPO,
            token_budget=4000,
            files=["src/service/api.ts"],
        )

        contents = " ".join(str(item.get("content") or "") for item in compiled["items"])
        assert "retry budgets" in contents

    def test_an_absent_projection_compiles_exactly_what_it_did_before(self, tmp_path):
        from visp_memory.config import CodeGraphConfig, MemoryConfig
        from visp_memory.core.code_graph import graph_for_repo
        from visp_memory.core.context_compiler import ContextCompiler

        storage = LocalStorage(tmp_path / "store")
        _store(storage, "Router owns retry budgets end to end", ["src/service/Router.ts"])
        _store(storage, "Pagination cursors are opaque here", ["src/service/api.ts"])

        unset = MemoryConfig()
        unset.code_graph = CodeGraphConfig()
        missing = MemoryConfig()
        missing.code_graph = CodeGraphConfig(intel_projection_path=tmp_path / "nowhere.json")

        def _stable(compiled):
            # `as_of` is wall-clock and differs between two calls a microsecond apart.
            return {key: value for key, value in compiled.items() if key != "as_of"}

        def compile_with(config):
            return _stable(
                ContextCompiler(storage, code_graph=graph_for_repo(config, REPO)).compile(
                    "pagination cursors",
                    repo_id=REPO,
                    token_budget=4000,
                    files=["src/service/api.ts"],
                )
            )

        no_compiler_argument = _stable(
            ContextCompiler(storage).compile(
                "pagination cursors", repo_id=REPO, token_budget=4000, files=["src/service/api.ts"]
            )
        )

        assert compile_with(unset) == compile_with(missing)
        assert compile_with(unset) == no_compiler_argument

    def test_memory_reports_why_structure_did_nothing(self, tmp_path):
        from visp_memory.config import CodeGraphConfig
        from visp_memory.core.memory import Memory

        memory = Memory(data_dir=tmp_path / "store")
        memory.config.code_graph = CodeGraphConfig(
            intel_projection_path=self.VECTORS / "projection-superseded.json"
        )

        assert memory.code_graph(REPO) is None
        assert memory.last_code_graph_load["state"] == "not_head"
        assert memory.last_code_graph_load["reason"]
