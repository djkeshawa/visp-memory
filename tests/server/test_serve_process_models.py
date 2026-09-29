"""`serve --reload` and uvicorn workers run the app in a spawned child of the importer."""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="terminates uvicorn supervisors with POSIX signals"
)

STARTUP_SECONDS = 90


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def launch(tmp_path, *args):
    log = (tmp_path / "server.log").open("w+")
    env = {
        **os.environ,
        "VISP_MEMORY_STORAGE_DATA_DIR": str(tmp_path / "store"),
        "VISP_MEMORY_EMBEDDING_PROVIDER": "noop",
    }
    process = subprocess.Popen(
        [sys.executable, *args], env=env, stdout=log, stderr=subprocess.STDOUT
    )
    return process, log


def stop(process):
    process.terminate()
    try:
        process.wait(20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(5)


def readyz(port):
    try:
        response = urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz", timeout=2)
    except urllib.error.HTTPError as error:
        response = error  # 503 still proves the app answered; storage is what we check
    except OSError:
        return None
    return json.loads(response.read())


def wait_for(condition, process):
    deadline = time.monotonic() + STARTUP_SECONDS
    while time.monotonic() < deadline:
        found = condition()
        if found:
            return found
        assert process.poll() is None, "server exited before it answered"
        time.sleep(0.5)
    pytest.fail("server did not answer in time")


def log_text(log):
    log.seek(0)
    return log.read()


def test_serve_with_reload_answers_from_the_spawned_child(tmp_path):
    port = free_port()
    process, log = launch(
        tmp_path, "-m", "visp_memory.interfaces.cli", "serve", "--port", str(port), "--reload"
    )
    try:
        ready = wait_for(lambda: readyz(port), process)
        assert ready["checks"]["storage"] == "ok", log_text(log)
        assert "WriterLockConflict" not in log_text(log)
    finally:
        stop(process)
        log.close()
    assert not (tmp_path / "store/.locks/server.json").exists()


def test_multiple_workers_are_refused_with_a_clear_message(tmp_path):
    port = free_port()
    process, log = launch(
        tmp_path, "-m", "uvicorn", "visp_memory.server.app:app",
        "--port", str(port), "--workers", "2",
    )
    try:
        wait_for(lambda: "sibling worker" in log_text(log), process)
        assert "`--workers` greater than 1 is not supported" in log_text(log)
    finally:
        stop(process)
        log.close()
