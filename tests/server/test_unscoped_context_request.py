"""An unscoped context request is refused with the repair, not with a field name.

LC-124. `POST /context/brief` and `POST /context/compile` resolve their scope as
`payload.repo_id or config.repo_id`. When neither names a project the gate answered
`400 repo_id is required` — accurate, and useless to the person reading it. The
dashboard rendered nothing for it, so the brief page appeared to hang, and the
packaged browser smoke waited for headings that were never going to appear.

The refusal stays a refusal. Answering an unscoped brief would mean compiling one
brief out of every project at once, and retrieval must not cross project scopes —
so these tests also pin that an unscoped request is never served, no matter how
many projects the store holds.
"""

from unittest import mock

import pytest

from visp_memory import MemoryConfig
from visp_memory.server.app import app

HEADERS = {"X-API-KEY": "test_key"}
BARE_REFUSAL = "repo_id is required"


def _without_a_configured_scope():
    """Neither the request nor the config names a project."""
    return mock.patch(
        "visp_memory.server.routers.context.load_config",
        return_value=MemoryConfig(repo_id=None),
    )


async def _post_unscoped(client, path: str, payload: dict):
    with _without_a_configured_scope():
        return await client.post(path, json=payload, headers=HEADERS)


@pytest.mark.asyncio
async def test_an_unscoped_brief_is_refused_with_something_the_caller_can_act_on(client):
    response = await _post_unscoped(client, "/context/brief", {"task": "x"})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail != BARE_REFUSAL
    assert "visp-memory init" in detail
    assert "repo_id" in detail


@pytest.mark.asyncio
async def test_an_unscoped_compile_is_refused_with_something_the_caller_can_act_on(client):
    response = await _post_unscoped(client, "/context/compile", {"query": "x"})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail != BARE_REFUSAL
    assert "visp-memory init" in detail
    assert "repo_id" in detail


@pytest.mark.asyncio
async def test_both_context_routes_refuse_in_the_same_words(client):
    brief = await _post_unscoped(client, "/context/brief", {"task": "x"})
    compiled = await _post_unscoped(client, "/context/compile", {"query": "x"})

    assert brief.json()["detail"] == compiled.json()["detail"]


@pytest.mark.asyncio
async def test_a_blank_scope_is_refused_the_same_way_as_a_missing_one(client):
    response = await _post_unscoped(client, "/context/brief", {"task": "x", "repo_id": "   "})

    assert response.status_code == 400
    assert "visp-memory init" in response.json()["detail"]


@pytest.mark.asyncio
async def test_an_unscoped_brief_is_never_compiled_across_every_project(client):
    storage = app.state.storage
    storage.store_memory("alpha deployment note", repo_id="repo-a", auto_link=False)
    storage.store_memory("beta deployment note", repo_id="repo-b", auto_link=False)

    response = await _post_unscoped(client, "/context/brief", {"task": "deployment"})

    assert response.status_code == 400
    assert "citations" not in response.json()


@pytest.mark.asyncio
async def test_a_named_project_still_compiles_a_brief(client):
    app.state.storage.store_memory("alpha deployment note", repo_id="repo-a", auto_link=False)

    with _without_a_configured_scope():
        response = await client.post(
            "/context/brief",
            json={"task": "deployment", "repo_id": "repo-a", "token_budget": 300},
            headers=HEADERS,
        )

    assert response.status_code == 200
    assert "citations" in response.json()
