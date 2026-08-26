import pytest

from visp_memory.server.app import app


@pytest.mark.asyncio
async def test_plural_memory_revision_endpoint_creates_successor(client):
    storage = app.state.storage
    old_evidence = storage.store_evidence("Observed old behavior", repo_id="repo-a")
    old_id = storage.store_memory(
        "The old behavior",
        layer="semantic",
        category="fact",
        repo_id="repo-a",
        evidence_ids=[old_evidence],
        auto_link=False,
    )
    new_evidence = storage.store_evidence("Observed new behavior", repo_id="repo-a")

    response = await client.post(
        f"/memories/{old_id}/revisions",
        headers={"X-API-KEY": "test_key"},
        json={
            "content": "The new behavior",
            "evidence_ids": [new_evidence],
        },
    )

    assert response.status_code == 200
    successor = response.json()
    assert successor["id"] != old_id
    assert successor["metadata"]["revision_of"] == old_id
    assert storage.get_memory(old_id)["status"] == "superseded"
