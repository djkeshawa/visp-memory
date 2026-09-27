import os
import sys
import types

import pytest
from typer.testing import CliRunner

from visp_memory.interfaces.cli import app

runner = CliRunner()
SHARED_ENV_NAMES = (
    "VISP_MEMORY_CONFIG",
    "VISP_MEMORY_REPO_ID",
    "VISP_MEMORY_STORAGE_DATA_DIR",
    "VISP_MEMORY_SERVER_SHARED",
    "VISP_MEMORY_SERVER_ALLOW_ANONYMOUS",
    "VISP_MEMORY_SERVER_LOCAL_OWNER_MODE",
)


@pytest.fixture(autouse=True)
def _restore_shared_env():
    original = {name: os.environ.get(name) for name in SHARED_ENV_NAMES}
    yield
    for name, value in original.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def _mock_uvicorn(monkeypatch):
    calls = []
    monkeypatch.setitem(
        sys.modules,
        "uvicorn",
        types.SimpleNamespace(run=lambda *args, **kwargs: calls.append((args, kwargs))),
    )
    return calls


def _clear_shared_env(monkeypatch):
    for name in SHARED_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_serve_shared_sets_inherited_env_and_uses_default_data_dir(
    cli_env, monkeypatch
):
    import visp_memory.interfaces.shared_server as shared_server

    _clear_shared_env(monkeypatch)
    root = cli_env / "shared-root"
    monkeypatch.setattr(shared_server, "shared_root", lambda: root)
    monkeypatch.setenv("VISP_MEMORY_REPO_ID", "project-repo")
    calls = _mock_uvicorn(monkeypatch)

    result = runner.invoke(app, ["serve", "--shared"])

    assert result.exit_code == 0
    assert "VISP_MEMORY_REPO_ID" not in os.environ
    assert os.environ["VISP_MEMORY_STORAGE_DATA_DIR"] == str((root / "data").resolve())
    assert os.environ["VISP_MEMORY_SERVER_SHARED"] == "true"
    assert os.environ["VISP_MEMORY_CONFIG"] == str((root / "config.yaml").resolve())
    assert (root / "config.yaml").is_file()
    assert (root / "data").is_dir()
    assert len(calls) == 1


def test_serve_shared_ignores_a_project_config_in_the_working_directory(
    cli_env, monkeypatch
):
    """The shared server's config comes from its root, never from its cwd."""
    import visp_memory.interfaces.shared_server as shared_server
    from visp_memory.config import load_config

    _clear_shared_env(monkeypatch)
    root = cli_env / "shared-root"
    project = cli_env / "some-project"
    project.mkdir()
    (project / "visp-memory.yaml").write_text("repo_id: leak\n", encoding="utf-8")
    monkeypatch.chdir(project)
    monkeypatch.setattr(shared_server, "shared_root", lambda: root)
    _mock_uvicorn(monkeypatch)

    result = runner.invoke(app, ["serve", "--shared"])

    assert result.exit_code == 0
    assert load_config().repo_id is None


def test_serve_data_dir_without_shared_is_refused(cli_env, monkeypatch):
    _clear_shared_env(monkeypatch)
    calls = _mock_uvicorn(monkeypatch)

    result = runner.invoke(app, ["serve", "--data-dir", str(cli_env / "x")])

    assert result.exit_code != 0
    assert "--shared" in result.output
    assert calls == []


def test_serve_shared_honours_data_dir_option(cli_env, monkeypatch):
    import visp_memory.interfaces.shared_server as shared_server

    _clear_shared_env(monkeypatch)
    root = cli_env / "shared-root"
    data_dir = cli_env / "elsewhere" / "memory-data"
    monkeypatch.setattr(shared_server, "shared_root", lambda: root)
    calls = _mock_uvicorn(monkeypatch)

    result = runner.invoke(app, ["serve", "--shared", "--data-dir", str(data_dir)])

    assert result.exit_code == 0
    assert os.environ["VISP_MEMORY_STORAGE_DATA_DIR"] == str(data_dir.resolve())
    assert data_dir.is_dir()
    assert len(calls) == 1


def test_serve_shared_refuses_a_configured_repo_before_uvicorn(cli_env, monkeypatch):
    import visp_memory.interfaces.shared_server as shared_server

    _clear_shared_env(monkeypatch)
    root = cli_env / "shared-root"
    root.mkdir()
    (root / "config.yaml").write_text(
        "repo_id: leak\nserver:\n  auth_enabled: false\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(shared_server, "shared_root", lambda: root)
    calls = _mock_uvicorn(monkeypatch)

    result = runner.invoke(app, ["serve", "--shared"])

    assert result.exit_code == 1
    assert "shared" in result.output.lower()
    assert "repo_id" in result.output
    assert os.environ["VISP_MEMORY_CONFIG"] == str((root / "config.yaml").resolve())
    assert calls == []
