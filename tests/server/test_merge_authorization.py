import pytest

from visp_memory.server.app import app
from visp_memory.server.auth import UserContext, get_current_user


@pytest.fixture
def scoped_memories(client, monkeypatch):
    storage = app.state.storage
    storage.store_repository({"id": "merge-audit", "name": "Merge", "team_id": "alpha"})

    async def current_user():
        return UserContext(user_id="alice", username="alice", team_id="alpha")

    monkeypatch.setitem(app.dependency_overrides, get_current_user, current_user)

    def store(tier):
        return storage.store_memory(
            "Same content", repo_id="merge-audit", auto_link=False,
            source=tier, tags=[f"provenance:{tier}"], metadata={"team_id": "alpha"},
        )

    return store


@pytest.mark.asyncio
async def test_merge_cannot_bypass_the_provenance_update_guard(client, scoped_memories):
    authored, external = scoped_memories("authored"), scoped_memories("external")

    response = await client.post(
        "/memories/merge", json={"memory_ids": [authored, external], "target_id": external}
    )

    assert response.status_code == 400
    assert "provenance" in response.json()["detail"]
    assert app.state.storage.get_memory(external)["tags"] == ["provenance:external"]
    assert app.state.storage.get_memory(authored)["status"] == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_selection", ["duplicate", "target"])
async def test_invalid_merge_selection_is_a_client_error(
    client, scoped_memories, invalid_selection
):
    first, second = scoped_memories("authored"), scoped_memories("authored")
    ids = [first, first] if invalid_selection == "duplicate" else [first, second]
    payload = {"memory_ids": ids}
    if invalid_selection == "target":
        payload["target_id"] = "not-selected"

    response = await client.post("/memories/merge", json=payload)

    assert response.status_code == 400
    assert app.state.storage.get_memory(first)["status"] == "active"
