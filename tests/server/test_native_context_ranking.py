"""HTTP context requests must not silently discard ranking choices."""

import pytest

from visp_memory.server.app import app


@pytest.mark.asyncio
@pytest.mark.parametrize("path,field", [("compile", "query"), ("brief", "task")])
@pytest.mark.parametrize("selection", ["default", "coverage"])
async def test_native_context_api_uses_hybrid(client, path, field, selection):
    app.state.storage.store_memory(
        "Secure browser authentication uses session cookies.",
        repo_id="repo-a", tags=["provenance:authored"], auto_link=False,
    )
    response = await client.post(
        f"/context/{path}", headers={"X-API-KEY": "test_key"},
        json={field: "secure browser authentication", "repo_id": "repo-a",
              "ranking_strategy": "hybrid", "context_selection": selection},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["retrieval"]["direct_ranking_strategy"] == "hybrid"
    assert not result["abstained"]
    if selection == "coverage":
        assert result["retrieval"]["context_selection"] == "coverage"


@pytest.mark.asyncio
@pytest.mark.parametrize("path,field", [("compile", "query"), ("brief", "task")])
async def test_native_context_api_rejects_unknown_ranking(client, path, field):
    response = await client.post(
        f"/context/{path}", headers={"X-API-KEY": "test_key"},
        json={field: "secure authentication", "repo_id": "repo-a",
              "ranking_strategy": "unknown"},
    )
    assert response.status_code == 422
