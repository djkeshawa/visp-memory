"""PATCH /memories/{id} must not let an ordinary writer change a record's trust tier."""

import pytest

from visp_memory.core.trust import Provenance, provenance_of
from visp_memory.server.app import app
from visp_memory.server.auth import UserContext
from visp_memory.server.provenance_guard import pin_provenance


def _writer_token():
    account = app.state.auth_store.create_account(
        username="plain-writer",
        password="correct-horse-battery-staple",
        role="user",
        team_id="alpha",
    )
    _, token = app.state.auth_store.create_token(
        user_id=account["id"],
        name="writer",
        scopes=["memory:read", "memory:write"],
        repo_ids=["repo-a"],
    )
    return {"Authorization": f"Bearer {token}"}


def _external_memory():
    storage = app.state.storage
    storage.store_repository({"id": "repo-a", "name": "repo-a", "team_id": "alpha"})
    return storage.store_memory(
        "Imported note awaiting review",
        repo_id="repo-a",
        tags=["provenance:external", "imported"],
        source="external",
        auto_link=False,
    )


@pytest.mark.asyncio
async def test_writer_cannot_promote_a_record_through_patch(client):
    memory_id = _external_memory()

    response = await client.patch(
        f"/memories/{memory_id}",
        json={"tags": ["provenance:authored", "reviewed"], "source": "authored"},
        headers=_writer_token(),
    )

    assert response.status_code == 200
    stored = app.state.storage.get_memory(memory_id)
    assert provenance_of(stored) is Provenance.EXTERNAL
    assert stored["source"] == "external"
    # The other tags are still the writer's to edit.
    assert "reviewed" in stored["tags"] and "imported" not in stored["tags"]


@pytest.mark.asyncio
async def test_administrator_can_reapprove_a_record(client):
    memory_id = _external_memory()

    response = await client.patch(
        f"/memories/{memory_id}",
        json={"tags": ["provenance:authored"], "source": "authored"},
        headers={"X-API-KEY": "test_key"},
    )

    assert response.status_code == 200
    stored = app.state.storage.get_memory(memory_id)
    assert provenance_of(stored) is Provenance.AUTHORED
    assert stored["source"] == "authored"


def test_owner_token_holder_may_change_provenance_and_others_may_not():
    existing = {"tags": ["provenance:external", "a"], "source": "external"}
    owner = UserContext(user_id="anonymous", username="anonymous", owner_maintenance=True)
    plain = UserContext(user_id="u", username="u", auth_type="session")

    owner_update = {"tags": ["provenance:authored"], "source": "authored"}
    pin_provenance(owner_update, existing, owner)
    assert owner_update == {"tags": ["provenance:authored"], "source": "authored"}

    plain_update = {"tags": ["provenance:authored", "b"], "source": "authored"}
    pin_provenance(plain_update, existing, plain)
    assert plain_update == {"tags": ["b", "provenance:external"], "source": "external"}

    untouched = {"importance": 0.9}
    pin_provenance(untouched, existing, plain)
    assert untouched == {"importance": 0.9}
