"""P13 — the HTTP API must not treat the quarantine bucket as a repository.

`__visp_unscoped__` is where rows of unknown provenance live: data from the
v2->v3 migration, and writes that never named a project. `require_repo_id`
refuses to serve it, and that refusal is a trust control — the whole point is
that unattributed content is never handed to a model as project knowledge.

`require_repo_scope_access` (server/authorization.py:30) is the API's gate, and
it only ever checked for a BLANK scope. The reserved name is a perfectly good
non-blank string, so it walks through. Whatever each endpoint does next is then
decided by whether that particular code path happens to call `require_repo_id`
— which is not a security model, it is a coincidence.

Two things must hold, and they are different claims:

  1. No endpoint serves quarantined rows to a caller who asks for them by name.
  2. No endpoint answers with a 500. A rejected scope is a client error; a
     stack trace is the API saying the request was valid and it broke.

The tests are written against those two, not against today's behaviour.
"""


import pytest

from visp_memory.core.eligibility import UNSCOPED_REPO_ID
from visp_memory.server.app import app

HEADERS = {"X-API-KEY": "test_key"}

# Endpoints that accept a repository scope from the caller. Listed explicitly:
# checking one endpoint and assuming the rest agree is how the gate came to be
# bypassed on some paths and not others.
READ_ENDPOINTS = [
    ("GET", "/memories", {"repo_id": UNSCOPED_REPO_ID}),
    ("GET", "/graph", {"repo_id": UNSCOPED_REPO_ID}),
]


def _quarantined_row() -> str:
    """A row in the reserved bucket, as a migration would leave it."""
    return app.state.storage.store_memory(
        "the quarantined secret sauce recipe",
        repo_id=UNSCOPED_REPO_ID,
        auto_link=False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,params", READ_ENDPOINTS)
async def test_reserved_scope_is_refused_not_served(client, method, path, params):
    _quarantined_row()

    response = await client.request(method, path, params=params, headers=HEADERS)

    assert response.status_code != 500, (
        f"{method} {path} returned a server error for a scope the caller is simply not "
        f"allowed to use. That is a 400. Body: {response.text[:300]}"
    )
    assert response.status_code >= 400, (
        f"{method} {path} accepted the reserved quarantine scope (status "
        f"{response.status_code}). Rows of unknown provenance must never be served."
    )
    assert "secret sauce" not in response.text, (
        f"{method} {path} returned a quarantined memory to a caller who named the reserved "
        "bucket. The refusal in require_repo_id is the control that stops unattributed "
        "content reaching a model, and this route goes around it."
    )


@pytest.mark.asyncio
async def test_recall_refuses_the_reserved_scope_cleanly(client):
    _quarantined_row()

    response = await client.post(
        "/recall",
        json={"query": "secret sauce", "repo_id": UNSCOPED_REPO_ID, "min_score": 0.0},
        headers=HEADERS,
    )

    assert response.status_code != 500, (
        "POST /recall raised rather than refusing. The ValueError from require_repo_id "
        f"escaped as a server error. Body: {response.text[:300]}"
    )
    assert response.status_code == 400
    assert "secret sauce" not in response.text


@pytest.mark.asyncio
async def test_writing_into_the_reserved_scope_is_refused(client):
    """The bucket is not a destination a caller may choose.

    Allowing it would let an API client deliberately park content where no
    recall can reach it — the same silent black hole the CLI used to create by
    accident, only on purpose.
    """
    response = await client.post(
        "/memories",
        json={"content": "deliberately unscoped", "repo_id": UNSCOPED_REPO_ID},
        headers=HEADERS,
    )

    assert response.status_code != 500, f"write raised instead of refusing: {response.text[:300]}"
    assert response.status_code >= 400, (
        "The API accepted a write addressed to the reserved quarantine scope."
    )


@pytest.mark.asyncio
async def test_a_real_scope_is_unaffected(client):
    """The converse: refusing the reserved name must not refuse real projects."""
    app.state.storage.store_memory("ordinary project memory", repo_id="repo-a", auto_link=False)

    response = await client.get("/memories", params={"repo_id": "repo-a"}, headers=HEADERS)

    assert response.status_code == 200, response.text
    assert "ordinary project memory" in response.text
