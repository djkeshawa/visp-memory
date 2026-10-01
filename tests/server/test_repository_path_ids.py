"""Repository IDs are opaque scopes, including slash-separated project names."""
from urllib.parse import quote

import pytest

from tests.server.test_owner_maintenance import owner_client as owner_client
from tests.server.test_portability import OWNER_HEADERS, graph_document
from visp_memory.server.app import app


@pytest.mark.asyncio
@pytest.mark.parametrize("repo_id", ["org/project", "org/export/project", "org/a?#%"])
async def test_slash_repository_registration_portability_and_suffix_routes(owner_client, repo_id):
    path = f"/repos/{quote(repo_id, safe='')}"
    headers = OWNER_HEADERS
    response = await owner_client.post("/repos", json={"id": repo_id, "name": "Project"})
    assert response.status_code == 200, response.text
    response = await owner_client.get(path)
    assert response.status_code == 200, response.text
    assert response.json()["id"] == repo_id
    response = await owner_client.post(f"{path}/import", json=graph_document(), headers=headers)
    assert response.status_code == 200, response.text
    response = await owner_client.get(f"{path}/export")
    assert response.status_code == 200, response.text
    assert response.json()["memories"]["episodic"][0]["repo_id"] == repo_id
    response = await owner_client.get(f"{path}/registration")
    assert response.status_code == 200, response.text
    assert response.json()["project_scopes"] == [repo_id]
    for suffix in ("dependencies", "context", "purge-preview"):
        response = await owner_client.get(f"{path}/{suffix}", headers=headers)
        assert response.status_code == 200, response.text
    for action in ("archive", "restore"):
        response = await owner_client.post(f"{path}/{action}", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["id"] == repo_id
    assert app.state.storage.get_repository(repo_id)["status"] == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize("repo_id", ["org/project", "../outside", r"org\project"])
async def test_slash_repository_purge_backup_stays_in_backup_directory(owner_client, repo_id):
    storage = app.state.storage
    storage.store_memory("Purge observation", repo_id=repo_id, auto_link=False)
    path = f"/repos/{quote(repo_id, safe='')}"
    response = await owner_client.delete(
        path, params={"confirmation": repo_id}, headers=OWNER_HEADERS,
    )
    assert response.status_code == 200, response.text
    backup_dir = app.state.auth_store.path.parent / "backups"
    backup_name = response.json()["backup"]
    assert (backup_dir / backup_name).parent == backup_dir
    assert (backup_dir / backup_name).is_file()
    assert storage.get_repository(repo_id) is None
