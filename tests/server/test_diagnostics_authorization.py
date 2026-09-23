import pytest

from visp_memory.core.indexing import EmbeddingIndexReport
from visp_memory.server.app import app
from visp_memory.server.auth import UserContext, get_current_user


@pytest.mark.asyncio
async def test_embedding_index_diagnostics_requires_administrator(client, monkeypatch):
    async def override_current_user():
        return UserContext(
            user_id="alice",
            username="alice",
            team_id="team-alpha",
            is_admin=False,
        )

    app.dependency_overrides[get_current_user] = override_current_user
    monkeypatch.setattr(
        "visp_memory.server.routers.diagnostics.inspect_embedding_index",
        lambda *args, **kwargs: EmbeddingIndexReport(
            storage_backend="sqlite",
            provider="noop",
            effective_provider="noop",
            model=None,
            dimension=None,
            status="disabled",
            message="Embeddings disabled.",
        ),
    )
    try:
        response = await client.get("/diagnostics/embedding-index")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 403
