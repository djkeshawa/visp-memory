"""Graph path nodes carry their real hop count from the start of the path.

``path`` and ``why_relevant`` used to build node ids as source, target, then
each edge's stored endpoints, and used the list index as the distance: on the
chain A-B-C-D the target D was reported one hop from A. Edges are traversed
undirected, so the stored source/target order cannot be trusted either; these
chains are stored with edges pointing back towards the start.
"""

import pytest

from visp_memory import Memory, MemoryConfig
from visp_memory.core.trust import WriteChannel
from visp_memory.recall.graph import GraphRecall


@pytest.fixture
def chain(tmp_path):
    config = MemoryConfig(project_name="t", repo_id="repo-a")
    config.storage.data_dir = tmp_path / "data"
    config.embedding.provider = "noop"
    memory = Memory(config=config)
    words = ["ledger rounding", "invoice export", "tax table", "refund queue"]
    ids = [
        memory.record(f"{word} note", _write_channel=WriteChannel.TEST_CAPTURE)
        for word in words
    ]
    storage = memory._storage
    for relationship in storage.get_all_relationships(repo_id="repo-a"):
        storage.delete_relationship(relationship["id"])
    # Stored backwards (B->A, C->B, D->C) so endpoint order disagrees with walk order.
    for near, far in zip(ids, ids[1:]):
        storage.add_relationship(far, near, "related_to")
    return storage, ids


def _distances(result):
    return [(node["id"], node["relevance_factors"]["distance"]) for node in result["nodes"]]


def test_path_reports_hop_count_for_every_node(chain):
    storage, (a, b, c, d) = chain

    result = GraphRecall(storage).path(a, d, repo_id="repo-a")

    assert sorted(_distances(result), key=lambda pair: pair[1]) == [
        (a, 0), (b, 1), (c, 2), (d, 3),
    ]


def test_why_relevant_measures_distance_from_the_seed(chain):
    storage, (a, b, c, d) = chain

    result = GraphRecall(storage).why_relevant(
        "ledger rounding", d, repo_id="repo-a", depth=3, limit=1
    )

    assert result["edges"], result["explanation"]
    assert dict(_distances(result)) == {a: 0, b: 1, c: 2, d: 3}
