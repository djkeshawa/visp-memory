"""The local owner proves OS-user access before using maintenance endpoints."""

import json
import os
import stat
import tempfile
import unittest.mock as mock
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from visp_memory.config import MemoryConfig, ServerConfig
from visp_memory.core.intent_evaluator import IntentEvaluator
from visp_memory.core.lifecycle import MemoryLifecycleManager
from visp_memory.core.model_router import ModelRouter
from visp_memory.core.owner_token import OWNER_TOKEN_HEADER
from visp_memory.core.storage import LocalStorage
from visp_memory.core.trust import LOCAL_WORKFLOW_ACTOR
from visp_memory.server import app as server_app
from visp_memory.server.auth import SESSION_COOKIE_NAME
from visp_memory.server.auth_store import AuthStore
from visp_memory.server.owner_token import (
    OWNER_TOKEN_FILE_ENV,
    cleanup_owner_token_files,
    create_owner_token_files,
)

LOOPBACK_PEER = ("127.0.0.1", 51515)
REMOTE_PEER = ("203.0.113.5", 51515)
OWNER_TOKEN = "local-owner-maintenance-secret"
REPO_ID = "owner-test-repo"


def route_requests():
    """Maintenance routes that must accept a token-bearing local owner."""
    return [
        ("POST", f"/repos/{REPO_ID}/archive", None),
        ("POST", f"/repos/{REPO_ID}/restore", None),
        ("GET", f"/repos/{REPO_ID}/purge-preview", None),
        ("DELETE", f"/repos/{REPO_ID}?confirmation=wrong", None),
        ("POST", "/memories/purge/preview", {"memory_ids": ["missing-memory"]}),
        ("DELETE", "/memories/missing-memory/purge?confirmation=wrong", None),
        ("GET", f"/maintenance/retention-preview?repo_id={REPO_ID}", None),
        ("POST", f"/maintenance/retention?repo_id={REPO_ID}&confirmation=wrong", None),
        ("GET", f"/maintenance/verify?repo_id={REPO_ID}", None),
        ("GET", "/diagnostics/embedding-index", None),
        ("POST", "/diagnostics/embedding-index/reindex", {"dry_run": True}),
        ("GET", "/platform/audit-log", None),
        ("GET", f"/dreaming/{REPO_ID}", None),
        ("PUT", f"/dreaming/{REPO_ID}/schedule", {"enabled": False}),
        ("GET", f"/dreaming/{REPO_ID}/preview", None),
        ("POST", f"/dreaming/{REPO_ID}/run", None),
        (
            "POST",
            f"/dreaming/{REPO_ID}/runs/run/proposals/proposal",
            {"decision": "dismiss"},
        ),
        ("POST", f"/dreaming/{REPO_ID}/actions/action/undo", None),
    ]


@pytest_asyncio.fixture
async def owner_client(monkeypatch, request):
    peer, local_owner_mode = getattr(request, "param", (LOOPBACK_PEER, True))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        data_dir = Path(tmpdir)
        storage = LocalStorage(data_dir)
        storage.store_memory("A local owner maintenance test memory", repo_id=REPO_ID)
        app = server_app.app
        state_names = (
            "storage",
            "auth_store",
            "memory_lifecycle",
            "model_router",
            "intent_evaluator",
            "owner_maintenance_token",
            "owner_maintenance_token_file",
        )
        previous_state = {name: getattr(app.state, name, None) for name in state_names}
        app.state.storage = storage
        app.state.auth_store = AuthStore(data_dir / "auth.db")
        app.state.memory_lifecycle = MemoryLifecycleManager(storage, data_dir / "lifecycle.db")
        config = MemoryConfig()
        config.embedding.provider = "noop"
        config.server = ServerConfig(
            auth_enabled=True,
            allow_anonymous=True,
            local_owner_mode=local_owner_mode,
            port=8765,
        )
        app.state.model_router = ModelRouter(config.llm)
        app.state.intent_evaluator = IntentEvaluator(storage, app.state.model_router, config.llm)
        token_path = data_dir / "owner-8765.token"
        token_path.write_text(OWNER_TOKEN, encoding="utf-8")
        app.state.owner_maintenance_token = OWNER_TOKEN
        app.state.owner_maintenance_token_file = str(token_path)
        monkeypatch.setenv(OWNER_TOKEN_FILE_ENV, str(token_path))

        transport = httpx.ASGITransport(app=app, client=peer)
        with (
            mock.patch("visp_memory.server.auth.load_config", return_value=config),
            mock.patch("visp_memory.server.app.config", config),
            mock.patch("visp_memory.server.routers.diagnostics.load_config", return_value=config),
        ):
            try:
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://127.0.0.1:8765"
                ) as client:
                    yield client
            finally:
                storage.close()
                for name, value in previous_state.items():
                    setattr(app.state, name, value)


@pytest.mark.asyncio
async def test_owner_token_allows_every_maintenance_route(owner_client, monkeypatch):
    class UnsupportedDreaming:
        def __init__(self, storage):
            raise NotImplementedError("Dreaming is not under test")

    monkeypatch.setattr(
        "visp_memory.server.routers.dreaming.Dreaming", UnsupportedDreaming
    )
    for method, path, payload in route_requests():
        response = await owner_client.request(
            method,
            path,
            headers={OWNER_TOKEN_HEADER: OWNER_TOKEN},
            json=payload,
        )
        assert response.status_code != 403, f"{method} {path}: {response.text}"


@pytest.mark.parametrize("owner_token", [None, "wrong-token"])
@pytest.mark.asyncio
async def test_maintenance_route_requires_the_owner_token(owner_client, owner_token):
    headers = {} if owner_token is None else {OWNER_TOKEN_HEADER: owner_token}
    response = await owner_client.get("/platform/audit-log", headers=headers)
    assert response.status_code == 403


@pytest.mark.parametrize(
    "owner_client",
    [(REMOTE_PEER, True), (LOOPBACK_PEER, False)],
    indirect=True,
)
@pytest.mark.asyncio
async def test_peer_and_local_owner_mode_are_still_required(owner_client):
    response = await owner_client.get(
        "/platform/audit-log", headers={OWNER_TOKEN_HEADER: OWNER_TOKEN}
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_owner_token_does_not_authorize_accounts_teams_providers_or_routing(owner_client):
    headers = {OWNER_TOKEN_HEADER: OWNER_TOKEN}
    requests = [
        ("GET", "/auth/users", None),
        (
            "POST",
            "/auth/users",
            {
                "username": "blocked-owner-user",
                "password": "a-long-test-password",
                "role": "user",
            },
        ),
        ("POST", "/teams", {"name": "blocked team"}),
        ("POST", "/diagnostics/providers/noop/test", None),
        ("GET", "/ai/routing", None),
    ]
    for method, path, payload in requests:
        response = await owner_client.request(method, path, headers=headers, json=payload)
        assert response.status_code == 403, f"{method} {path}: {response.text}"


@pytest.mark.asyncio
async def test_pat_and_session_cannot_gain_owner_maintenance_from_the_header(owner_client):
    app = server_app.app
    account = app.state.auth_store.create_account(
        username="owner-maint-user", password="a-long-test-password", role="user"
    )
    _, pat = app.state.auth_store.create_token(
        user_id=account["id"],
        name="maintenance test",
        scopes=["memory:write"],
        repo_ids=[REPO_ID],
    )
    owner_header = {OWNER_TOKEN_HEADER: OWNER_TOKEN}
    pat_response = await owner_client.post(
        "/memories/purge/preview",
        json={"memory_ids": ["missing-memory"]},
        headers={**owner_header, "Authorization": f"Bearer {pat}"},
    )
    assert pat_response.status_code == 403

    session, _ = app.state.auth_store.create_session(account["id"])
    session_response = await owner_client.get(
        f"/maintenance/verify?repo_id={REPO_ID}",
        headers={**owner_header, "Cookie": f"{SESSION_COOKIE_NAME}={session}"},
    )
    assert session_response.status_code == 403


WORKFLOW_REPORT = {
    "source": "assistant",
    "task_id": "task-1",
    "event_id": "event-1",
    "revision": 1,
    "status": "completed",
    "summary": "Work completed",
    "evidence": [{"description": "Acceptance check passed"}],
}


def _intent(name):
    return server_app.app.state.storage.set_intent(
        name, repo_id=REPO_ID, context={"author_id": "agent-user"}
    )


@pytest.mark.asyncio
async def test_local_owner_with_the_token_can_report_workflow_status(owner_client):
    app = server_app.app
    intent_id = _intent("Finish local owner workflow")
    response = await owner_client.post(
        f"/intents/{intent_id}/workflow-status",
        json=WORKFLOW_REPORT,
        headers={OWNER_TOKEN_HEADER: OWNER_TOKEN},
    )
    assert response.status_code == 200, response.text
    report = app.state.storage.get_active_intents(
        repo_id=REPO_ID, status="completed"
    )[0]["context"]["external_workflow"]
    assert report["reported_by"] == LOCAL_WORKFLOW_ACTOR
    assert report["channel"] == "local_owner"


@pytest.mark.parametrize("owner_token", [None, "wrong-token"])
@pytest.mark.asyncio
async def test_loopback_caller_without_the_token_cannot_close_an_intent(
    owner_client, owner_token
):
    # A loopback peer is not proof of the owner: any local process, other OS
    # user or DNS-rebinding page has one. Intent status belongs to the external
    # workflow authority, so the token is required, not just the peer address.
    intent_id = _intent("Anonymous loopback caller is refused")
    headers = {} if owner_token is None else {OWNER_TOKEN_HEADER: owner_token}
    response = await owner_client.post(
        f"/intents/{intent_id}/workflow-status", json=WORKFLOW_REPORT, headers=headers
    )
    assert response.status_code == 403
    active = server_app.app.state.storage.get_active_intents(repo_id=REPO_ID)
    assert [row["id"] for row in active] == [intent_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_client", [(REMOTE_PEER, True)], indirect=True)
async def test_the_token_does_not_help_a_remote_peer_report(owner_client):
    intent_id = _intent("Remote peer with the token is refused")
    response = await owner_client.post(
        f"/intents/{intent_id}/workflow-status",
        json=WORKFLOW_REPORT,
        headers={OWNER_TOKEN_HEADER: OWNER_TOKEN},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_local_workflow_binding_accepts_a_rest_report(owner_client):
    app = server_app.app
    intent_id = _intent("Finish existing local workflow")
    first = {**WORKFLOW_REPORT, "status": "active", "summary": "Started", "evidence": []}
    app.state.storage.report_intent_workflow(
        intent_id, first, actor_id=LOCAL_WORKFLOW_ACTOR, channel="cli"
    )
    response = await owner_client.post(
        f"/intents/{intent_id}/workflow-status",
        json={**first, "event_id": "event-2", "revision": 2, "summary": "Updated"},
        headers={OWNER_TOKEN_HEADER: OWNER_TOKEN},
    )
    assert response.status_code == 200, response.text
    assert response.json()["applied"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_client", [(LOOPBACK_PEER, False)], indirect=True)
async def test_anonymous_without_local_owner_mode_cannot_report(owner_client):
    intent_id = server_app.app.state.storage.set_intent(
        "Anonymous reporter is refused", repo_id=REPO_ID
    )
    response = await owner_client.post(
        f"/intents/{intent_id}/workflow-status",
        json={
            "source": "assistant",
            "task_id": "task-1",
            "event_id": "event-1",
            "revision": 1,
            "status": "active",
            "summary": "Started",
            "evidence": [],
        },
    )
    assert response.status_code == 403


def test_owner_token_files_are_atomic_private_and_discoverable(tmp_path, monkeypatch):
    import visp_memory.server.owner_token as owner_token

    replacements = []
    real_replace = os.replace

    def record_replace(source, target):
        replacements.append((Path(source), Path(target)))
        real_replace(source, target)

    monkeypatch.setattr(owner_token.os, "replace", record_replace)
    files = create_owner_token_files(
        port=8765,
        url="http://127.0.0.1:8765",
        data_dir=tmp_path / "data",
        directory=tmp_path / "run",
    )
    assert files.token_path.read_text(encoding="utf-8") == files.token
    assert files.token and len(files.token) >= 40
    metadata = json.loads(files.server_path.read_text(encoding="utf-8"))
    assert set(metadata) == {"pid", "url", "data_dir", "started_at"}
    assert metadata["url"] == "http://127.0.0.1:8765"
    assert metadata["data_dir"] == str((tmp_path / "data").resolve())
    assert replacements
    assert all(source.parent == target.parent for source, target in replacements)
    if os.name != "nt":
        assert stat.S_IMODE(files.token_path.stat().st_mode) == 0o600
    cleanup_owner_token_files(files)
    assert not files.token_path.exists()
    assert not files.server_path.exists()


@pytest.mark.asyncio
async def test_app_lifespan_creates_and_removes_owner_discovery_files(tmp_path, monkeypatch):
    from visp_memory.server import owner_token

    app = server_app.app
    config = MemoryConfig()
    config.storage.data_dir = tmp_path / "data"
    config.server = ServerConfig(local_owner_mode=True, port=8765)
    monkeypatch.setattr(server_app, "config", config)
    monkeypatch.setattr(owner_token, "run_dir", lambda: tmp_path / "run")
    monkeypatch.delenv(OWNER_TOKEN_FILE_ENV, raising=False)

    class Storage:
        def close(self):
            pass

    previous_storage = getattr(app.state, "storage", None)
    app.state.storage = Storage()
    try:
        async with server_app.lifespan(app):
            token_path = Path(os.environ[OWNER_TOKEN_FILE_ENV])
            server_path = token_path.with_name("server-8765.json")
            assert token_path.is_file()
            assert os.environ[OWNER_TOKEN_FILE_ENV] == str(token_path)
            assert os.environ[OWNER_TOKEN_FILE_ENV] != token_path.read_text(encoding="utf-8")
            assert app.state.owner_maintenance_token == token_path.read_text(encoding="utf-8")
            assert server_path.is_file()

        assert not token_path.exists()
        assert not server_path.exists()
    finally:
        app.state.storage = previous_storage
