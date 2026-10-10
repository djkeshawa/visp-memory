"""The entity channel must match a memory's file under any accepted path spelling.

The structural channel normalizes paths; when the entity channel compared raw strings,
"./src/x.py" or "src\\x.py" missed the memory about src/x.py while structure still
admitted its neighbours.
"""

import pytest

from visp_memory.core.code_graph import FileGraph
from visp_memory.core.hybrid_retrieval import HybridRetriever
from visp_memory.core.storage import LocalStorage


@pytest.mark.parametrize(
    "spelling", ["src/auth/tokens.py", "./src/auth/tokens.py", "src\\auth\\tokens.py"]
)
def test_exact_file_memory_is_an_entity_match_for_every_spelling(tmp_path, spelling):
    storage = LocalStorage(tmp_path)
    storage.store_memory(
        "Gotcha: token refresh must hold the session lock.",
        layer="episodic", repo_id="r", metadata={"files": ["src/auth/tokens.py"]},
        auto_link=False, memory_id="about-file",
    )
    storage.store_memory(
        "Session store serializes writes through a queue.",
        layer="episodic", repo_id="r", metadata={"files": ["src/auth/session.py"]},
        auto_link=False, memory_id="neighbour",
    )
    graph = FileGraph(
        snapshot_id="s", head_snapshot_id="s",
        file_paths=("src/auth/session.py", "src/auth/tokens.py"),
        internal_edges={"src/auth/tokens.py": ("src/auth/session.py",)},
    )

    results = HybridRetriever(storage, code_graph=graph).retrieve(
        "unrelated words", repo_id="r", files=[spelling]
    )
    channels = {row["id"]: row["retrieval_channels"] for row in results}

    assert "entity" in channels["about-file"]
    assert results[0]["id"] == "about-file"


def test_memory_side_spelling_is_normalized_too():
    memory = {"metadata": {"files": ["./src\\auth\\tokens.py"]}}

    assert HybridRetriever._matches_entities(memory, files=["src/auth/tokens.py"], symbols=[])
    assert not HybridRetriever._matches_entities(memory, files=["src/auth/other.py"], symbols=[])
