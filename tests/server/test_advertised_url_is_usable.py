"""The URL `visp-memory serve` prints leads somewhere (LC-85).

The banner prints the server root, which is what a user copies into a browser.
It answered `400 repo_id is required`; the dashboard was at /dashboard, a name
that appeared only in a log line.
"""

import pytest

from visp_memory.server import app as server_app

BROWSER = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


@pytest.fixture
def with_dashboard(monkeypatch):
    monkeypatch.setattr(server_app, "_dashboard_available", lambda: True)


@pytest.fixture
def without_dashboard(monkeypatch):
    monkeypatch.setattr(server_app, "_dashboard_available", lambda: False)


@pytest.mark.asyncio
async def test_a_browser_at_the_root_is_sent_to_the_dashboard(client, with_dashboard):
    response = await client.get("/", headers=BROWSER)

    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/dashboard"


@pytest.mark.asyncio
async def test_the_redirect_needs_no_credentials(client, with_dashboard):
    # /dashboard is static and unauthenticated; demanding a token to be told where
    # the page lives would hand the same user a 401 instead of the 400 they had.
    response = await client.get("/", headers=BROWSER)

    assert response.status_code in (302, 307)


@pytest.mark.asyncio
async def test_an_api_client_at_the_root_still_gets_the_status_json(client, with_dashboard):
    response = await client.get("/?repo_id=repo-a", headers={"X-API-KEY": "test_key"})

    assert response.status_code == 200
    assert response.json()["status"] == "online"


@pytest.mark.asyncio
async def test_a_headless_install_still_serves_status_at_the_root(client, without_dashboard):
    response = await client.get(
        "/?repo_id=repo-a", headers={**BROWSER, "X-API-KEY": "test_key"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "online"


@pytest.mark.asyncio
async def test_status_is_the_name_that_always_means_the_json(client, with_dashboard):
    response = await client.get(
        "/status?repo_id=repo-a", headers={**BROWSER, "X-API-KEY": "test_key"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "online"
