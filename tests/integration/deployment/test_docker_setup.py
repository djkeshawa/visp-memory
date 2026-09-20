"""Exercise Compose's resolved configuration and Docker's actual context filtering."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def compose_config(tmp_path, *overlays, profile="lite", values=None):
    if not shutil.which("docker"):
        pytest.skip("Docker CLI is required for Compose configuration checks")
    env_file = tmp_path / "compose.env"
    env_file.write_text("\n".join(f"{key}={value}" for key, value in (values or {}).items()))
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("VISP_MEMORY_", "COMPOSE_", "EMBEDDING_", "OLLAMA_"))
    }
    command = [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "-f",
        str(ROOT / "docker-compose.yml"),
    ]
    for overlay in overlays:
        command.extend(["-f", str(ROOT / overlay)])
    command.extend(["--profile", profile, "config", "--format", "json"])
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("profile", ["lite", "arcadedb"])
def test_app_waits_for_the_selected_ollama_model(tmp_path, profile):
    config = compose_config(
        tmp_path,
        "docker-compose.ollama.yml",
        profile=profile,
        values={"VISP_MEMORY_OLLAMA_EMBEDDING_MODEL": "test-embedding-model"},
    )
    app = config["services"][f"visp-memory-{profile}"]
    assert app["depends_on"]["ollama-pull"]["condition"] == "service_completed_successfully"
    assert app["environment"]["OLLAMA_HOST"] == "http://ollama:11434"
    assert app["environment"]["VISP_MEMORY_EMBEDDING_PROVIDER"] == "ollama"
    model = app["environment"]["VISP_MEMORY_EMBEDDING_MODEL"]
    assert model == "test-embedding-model"
    assert config["services"]["ollama-pull"]["command"] == ["pull", model]


@pytest.mark.parametrize("extras", [None, "api,mcp"])
def test_ollama_overlay_builds_both_provider_and_vector_index(tmp_path, extras):
    config = compose_config(
        tmp_path, "docker-compose.ollama.yml",
        values={"VISP_MEMORY_EXTRAS": extras} if extras else {},
    )
    app = config["services"]["visp-memory-lite"]
    selected = set(app["build"]["args"]["VISP_MEMORY_EXTRAS"].split(","))
    assert {"api", "mcp", "ollama", "chroma"} <= selected


@pytest.mark.parametrize("profile", ["lite", "arcadedb"])
def test_base_profile_keeps_persistent_storage_and_bounded_health_checks(tmp_path, profile):
    config = compose_config(tmp_path, profile=profile)
    app = config["services"][f"visp-memory-{profile}"]
    assert app["ports"][0]["host_ip"] == "127.0.0.1"
    assert app["volumes"][0]["source"] == f"visp-memory-{profile}-data"
    assert app["environment"]["VISP_MEMORY_SERVER_AUTH_ENABLED"] == "true"
    assert app["init"] is True
    assert app["stop_grace_period"] == "30s"
    assert "timeout=3" in " ".join(app["healthcheck"]["test"])
    assert app["healthcheck"]["start_period"] == "30s"
    assert "ollama" not in config["services"]


@pytest.mark.skipif(os.getenv("VISP_TEST_DOCKER") != "1", reason="Opt-in Docker build test")
def test_build_context_excludes_local_secrets_and_memory_exports(tmp_path):
    context = tmp_path / "context"
    context.mkdir()
    shutil.copyfile(ROOT / ".dockerignore", context / ".dockerignore")
    excluded = [
        ".env",
        ".env.bak-llm-memory",
        "migration-export/project.json",
        ".llm-memory/data/store.db-wal",
        "visp-memory.yaml",
        ".codex/config.toml",
        "visp-memory-dashboard/.env.production",
        "visp-memory-dashboard/test-results/trace.zip",
        "benchmarks/run.json",
    ]
    included = [
        "pyproject.toml",
        "uv.lock",
        "README.md",
        "LICENSE",
        "NOTICE",
        "src/visp_memory/config.py",
        "visp-memory-dashboard/app/page.tsx",
    ]
    for name in excluded + included:
        path = context / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic build-context fixture\n")
    output = tmp_path / "export"
    result = subprocess.run(
        ["docker", "build", "--file", "-", "--output", f"type=local,dest={output}", str(context)],
        input="FROM scratch\nCOPY . /\n",
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert all((output / name).is_file() for name in included)
    assert not [name for name in excluded if (output / name).exists()]


def test_compose_forwards_the_configured_jwt_secret(tmp_path):
    config = compose_config(tmp_path, values={"VISP_MEMORY_JWT_SECRET": "synthetic-test-secret"})
    assert config["services"]["visp-memory-lite"]["environment"]["VISP_MEMORY_JWT_SECRET"] == (
        "synthetic-test-secret"
    )
