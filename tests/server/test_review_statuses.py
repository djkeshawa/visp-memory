"""REST accepts the reviewed-lifecycle statuses without widening recall.

Client mode needs to create a proposal quarantined and to review it, so the
REST schema accepts "quarantined" and "rejected". Neither is ever served: a
plain HTTP caller that writes one has only hidden its own row.
"""

import pytest

HEADERS = {"X-API-KEY": "test_key"}
REPO = "repo-a"
PHRASE = "unreviewed proposal about the retry budget"


async def _create(client, status: str) -> str:
    response = await client.post(
        "/memories",
        json={"content": PHRASE, "repo_id": REPO, "status": status},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == status
    return response.json()["id"]


async def _recalled_ids(client) -> set:
    response = await client.post(
        "/recall",
        json={"query": PHRASE, "repo_id": REPO, "min_score": 0.0},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()}


async def _listed_ids(client, **params) -> set:
    response = await client.get(
        "/memories", params={"repo_id": REPO, **params}, headers=HEADERS
    )
    assert response.status_code == 200, response.text
    return {item["id"] for item in response.json()}


@pytest.mark.asyncio
async def test_a_quarantined_row_is_listed_for_review_but_never_recalled(client):
    memory_id = await _create(client, "quarantined")

    assert memory_id not in await _recalled_ids(client)
    assert memory_id not in await _listed_ids(client)
    assert memory_id in await _listed_ids(client, status="quarantined")


@pytest.mark.asyncio
async def test_recall_cannot_be_asked_for_review_statuses(client):
    for status in ("quarantined", "rejected"):
        response = await client.post(
            "/recall",
            json={"query": PHRASE, "repo_id": REPO, "status": status},
            headers=HEADERS,
        )
        assert response.status_code == 422, status


@pytest.mark.asyncio
async def test_review_transitions_are_settable_over_rest(client):
    rejected_id = await _create(client, "quarantined")
    accepted_id = await _create(client, "quarantined")

    for memory_id, status in ((rejected_id, "rejected"), (accepted_id, "active")):
        response = await client.patch(
            f"/memories/{memory_id}", json={"status": status}, headers=HEADERS
        )
        assert response.status_code == 200, response.text
        fetched = await client.get(f"/memories/{memory_id}", headers=HEADERS)
        assert fetched.json()["status"] == status

    assert await _recalled_ids(client) == {accepted_id}
