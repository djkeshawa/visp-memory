"""Opt-in smoke of a built image; never publishes ports or uses existing data."""

import json
import os
import subprocess
import time
import uuid

import pytest

IMAGE = os.getenv("VISP_TEST_DOCKER_IMAGE")
pytestmark = pytest.mark.skipif(not IMAGE, reason="Set VISP_TEST_DOCKER_IMAGE to a built image")


def docker(*arguments, check=True):
    result = subprocess.run(
        ["docker", *arguments],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if check:
        assert result.returncode == 0, result.stderr
    return result


def start_container(name, volume):
    docker(
        "run",
        "-d",
        "--name",
        name,
        "--init",
        "--network",
        "none",
        "--mount",
        f"type=volume,source={volume},target=/data",
        "-e",
        "VISP_MEMORY_STORAGE_BACKEND=sqlite",
        "-e",
        "VISP_MEMORY_EMBEDDING_PROVIDER=none",
        "-e",
        "VISP_MEMORY_SERVER_AUTH_ENABLED=false",
        "-e",
        "VISP_MEMORY_SERVER_ALLOW_ANONYMOUS=true",
        IMAGE,
    )
    probe = (
        "from urllib.request import urlopen; "
        "urlopen('http://127.0.0.1:8000/readyz', timeout=2).close()"
    )
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if docker("exec", name, "python", "-c", probe, check=False).returncode == 0:
            return
        time.sleep(1)
    pytest.fail(f"Container did not become ready:\n{docker('logs', name).stdout}")


def request(name, path, payload=None):
    script = """
import json, sys
from urllib.request import Request, urlopen
payload = json.loads(sys.argv[2])
data = json.dumps(payload).encode() if payload is not None else None
req = Request('http://127.0.0.1:8000' + sys.argv[1], data=data,
              headers={'Content-Type': 'application/json'})
with urlopen(req, timeout=5) as response:
    print(json.dumps({'status': response.status, 'body': response.read().decode()}))
"""
    response = docker("exec", name, "python", "-c", script, path, json.dumps(payload))
    return json.loads(response.stdout)


def test_image_serves_dashboard_as_non_root_and_preserves_memories_on_recreation():
    name = f"visp-docker-smoke-{uuid.uuid4().hex[:12]}"
    volume = f"{name}-data"
    docker("volume", "create", volume)
    try:
        start_container(name, volume)
        assert docker("exec", name, "id", "-u").stdout.strip() == "1000"
        check_runtime = """
import importlib.util, pathlib, shutil
assert shutil.which('node') is None
assert shutil.which('uv') is None
assert importlib.util.find_spec('torch') is None
assert pathlib.Path('/app/LICENSE').stat().st_size > 0
assert pathlib.Path('/app/NOTICE').stat().st_size > 0
"""
        docker("exec", name, "python", "-c", check_runtime)
        dashboard = request(name, "/dashboard/")
        assert dashboard["status"] == 200
        assert "Visp Memory" in dashboard["body"]
        created = request(
            name,
            "/memories",
            {
                "content": "Container recreation preserves project knowledge.",
                "layer": "episodic",
                "category": "note",
                "repo_id": "docker-smoke",
            },
        )
        memory_id = json.loads(created["body"])["id"]
        docker("rm", "-f", name)
        start_container(name, volume)
        records = json.loads(request(name, "/memories?repo_id=docker-smoke")["body"])
        assert any(record["id"] == memory_id for record in records)
    finally:
        docker("rm", "-f", name, check=False)
        docker("volume", "rm", volume, check=False)
