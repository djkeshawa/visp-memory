import json

import pytest

from visp_memory.server.app import app
from visp_memory.server.auth import UserContext, get_current_user


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_intent", [False, True])
async def test_task_brief_cannot_use_an_inaccessible_intent(client, monkeypatch, explicit_intent):
    storage = app.state.storage
    storage.store_repository({"id": "brief-audit", "name": "Brief", "team_id": "alpha"})
    hidden = storage.set_intent(
        "Review secure login", repo_id="brief-audit", priority=3,
        context={"team_id": "beta", "constraints": ["Private beta constraint"]},
    )

    async def current_user():
        return UserContext(user_id="alice", username="alice", team_id="alpha")

    monkeypatch.setitem(app.dependency_overrides, get_current_user, current_user)
    payload = {"task": "Review secure login", "repo_id": "brief-audit"}
    if explicit_intent:
        payload["intent_id"] = hidden

    response = await client.post("/context/brief", json=payload)

    assert response.status_code == 200
    assert response.json()["intent"] is None
    assert "Private beta constraint" not in json.dumps(response.json())
    assert hidden not in json.dumps(response.json())
