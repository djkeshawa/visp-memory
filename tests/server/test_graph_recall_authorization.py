import json

import pytest

from visp_memory.server.app import app
from visp_memory.server.auth import UserContext, get_current_user


@pytest.fixture
def scoped_graph(client, monkeypatch):
    storage = app.state.storage
    storage.store_repository({"id": "graph-audit", "name": "Graph", "team_id": "alpha"})

    async def current_user():
        return UserContext(user_id="alice", username="alice", team_id="alpha")

    monkeypatch.setitem(app.dependency_overrides, get_current_user, current_user)

    def store(content, team, **kwargs):
        return storage.store_memory(
            content, repo_id="graph-audit", auto_link=False,
            source="authored", tags=["provenance:authored"],
            metadata={"team_id": team}, **kwargs,
        )

    source = store("database migration seed", "alpha", importance=0.9)
    hidden = store("Private bridge", "beta", importance=0.9)
    target = store("Visible unrelated destination", "alpha", importance=0.5)
    storage.add_relationship(source, hidden, "supports")
    storage.add_relationship(hidden, target, "supports")
    return source, hidden, target


@pytest.mark.asyncio
async def test_graph_neighbors_cannot_cross_an_inaccessible_bridge(client, scoped_graph):
    source, hidden, target = scoped_graph

    response = await client.post(
        "/graph-recall/neighbors",
        json={"memory_id": source, "repo_id": "graph-audit", "depth": 2},
    )

    assert response.status_code == 200
    assert [node["id"] for node in response.json()["nodes"]] == [source]
    assert response.json()["edges"] == []
    assert hidden not in json.dumps(response.json())
    assert target not in json.dumps(response.json())


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["path", "why-relevant"])
async def test_graph_explanations_do_not_use_inaccessible_paths(client, scoped_graph, endpoint):
    source, hidden, target = scoped_graph
    payload = {"repo_id": "graph-audit"}
    if endpoint == "path":
        payload.update(source_id=source, target_id=target)
    else:
        payload.update(query="database migration seed", memory_id=target, limit=1)

    response = await client.post(f"/graph-recall/{endpoint}", json=payload)

    assert response.status_code == 200
    result = response.json()
    assert result["edges"] == []
    assert any(item["type"] == "path" for item in result["omitted"])
    assert hidden not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("rejection", ["quarantine", "expired"])
async def test_graph_diagnostics_do_not_disclose_inaccessible_records(
    client, scoped_graph, rejection
):
    source, hidden, _ = scoped_graph
    if rejection == "quarantine":
        app.state.storage.update_memory(hidden, source="external", tags=["provenance:external"])
    else:
        app.state.storage.update_memory(
            hidden, metadata={"team_id": "beta", "valid_to": "2020-01-01T00:00:00+00:00"}
        )

    response = await client.post(
        "/graph-recall/neighbors",
        json={"memory_id": source, "repo_id": "graph-audit", "depth": 2},
    )

    assert response.status_code == 200
    assert hidden not in json.dumps(response.json())


@pytest.mark.asyncio
async def test_graph_trace_does_not_seed_from_inaccessible_records(client, scoped_graph):
    response = await client.post(
        "/graph-recall/trace",
        json={"query": "Private bridge", "repo_id": "graph-audit", "limit": 1},
    )

    assert response.status_code == 200
    assert response.json()["nodes"] == []
