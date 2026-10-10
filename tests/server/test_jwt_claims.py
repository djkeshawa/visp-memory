"""A JWT's ``scopes`` and ``repo_ids`` claims bound it exactly as a PAT's do.

They were parsed into the principal and then ignored: every enforcement point
asked ``auth_type == "pat"``, so a JWT minted for one project with read-only
scopes could read and write everything its account could. A JWT without ``exp``
was accepted too, and never expired.
"""

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from visp_memory.server import auth
from visp_memory.server.app import app

SECRET = "test_secret_at_least_32_bytes_long"


@pytest.fixture
def jwt_client(client):
    auth.load_config().server.jwt_secret = SECRET
    storage = app.state.storage
    for repo_id in ("allowed", "other"):
        storage.store_repository({"id": repo_id, "name": repo_id, "team_id": "alpha"})
    return client


def _bearer(*, expires=True, **claims):
    payload = {"sub": "jwt-user", "team_id": "alpha", **claims}
    if expires:
        payload["exp"] = datetime.now(timezone.utc) + timedelta(hours=1)
    return {"Authorization": "Bearer " + jwt.encode(payload, SECRET, algorithm="HS256")}


def _memory(repo_id):
    return app.state.storage.store_memory(
        f"{repo_id} content", repo_id=repo_id, metadata={"team_id": "alpha"}, auto_link=False
    )


@pytest.mark.asyncio
async def test_a_jwt_without_claims_keeps_its_accounts_access(jwt_client):
    memory_id = _memory("other")

    response = await jwt_client.get(f"/memories/{memory_id}", headers=_bearer())

    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_a_jwt_without_exp_is_refused(jwt_client):
    response = await jwt_client.get("/repos", headers=_bearer(expires=False))

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_the_repo_ids_claim_restricts_the_projects_a_jwt_reaches(jwt_client):
    allowed, other = _memory("allowed"), _memory("other")
    headers = _bearer(repo_ids=["allowed"])

    assert (await jwt_client.get(f"/memories/{allowed}", headers=headers)).status_code == 200
    assert (await jwt_client.get(f"/memories/{other}", headers=headers)).status_code == 404
    assert (await jwt_client.get("/repos/other", headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_the_scopes_claim_restricts_what_a_jwt_may_do(jwt_client):
    memory_id = _memory("allowed")
    headers = _bearer(scopes=["project:read"])

    response = await jwt_client.get(f"/memories/{memory_id}", headers=headers)

    assert response.status_code == 403
    assert "memory:read" in response.json()["detail"]


@pytest.mark.asyncio
async def test_an_admin_jwt_restricted_to_a_project_is_not_a_global_admin(jwt_client):
    headers = _bearer(is_admin=True, scopes=["memory:read"], repo_ids=["allowed"])

    response = await jwt_client.get("/platform/audit-log", headers=headers)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_a_scoped_jwt_cannot_manage_accounts(jwt_client):
    response = await jwt_client.get("/auth/tokens", headers=_bearer(scopes=["memory:read"]))

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_a_malformed_scopes_claim_is_a_401_not_a_500(jwt_client):
    response = await jwt_client.get("/repos", headers=_bearer(scopes="memory:read"))

    assert response.status_code == 401
