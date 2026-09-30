#!/usr/bin/env python3
"""Smoke-test the default quick start: a real ``visp-memory serve --shared`` process.

The other runtime smokes start uvicorn directly with authentication switched off, so
they never reach what a user gets from the quick start: local-owner mode, the
Host/Origin guard, the owner maintenance token, the store's writer lock, and
``connect`` followed by client-mode CLI writes. This script starts the real console
script with HOME (and USERPROFILE) pointing at a temporary directory and checks each
of those against the running process. Stdlib plus ``requests`` only; cross-platform.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import requests
import yaml

IS_WINDOWS = os.name == "nt"
SMOKE_REPO = "smoke"
SMOKE_AGENT = "smoke-agent"
OWNER_TOKEN_HEADER = "X-Visp-Owner-Token"
START_TIMEOUT = 60.0
STOP_TIMEOUT = 20.0
COMMAND_TIMEOUT = 60.0


class SmokeCheckError(AssertionError):
    """One check did not hold; the message says what was seen."""


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeCheckError(message)


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def console_script() -> str:
    """The ``visp-memory`` script installed beside this interpreter, else on PATH."""
    scripts_dir = Path(sys.executable).parent
    found = shutil.which("visp-memory", path=str(scripts_dir)) or shutil.which("visp-memory")
    if not found:
        raise SystemExit("visp-memory console script not found; install the package first.")
    return found


def isolated_env(home: Path) -> dict[str, str]:
    """The caller's environment minus any Visp Memory setting, with HOME redirected.

    ``Path.home()`` reads HOME on POSIX and USERPROFILE on Windows, and the shared
    root, run directory and owner token all hang off it.
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("VISP_")}
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "VISP_MEMORY_EMBEDDING_PROVIDER": "noop",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "NO_COLOR": "1",
            "TERM": "dumb",
            "COLUMNS": "200",
        }
    )
    return env


def poll(predicate: Callable[[], bool], timeout: float, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


@dataclass
class Server:
    """One ``visp-memory serve`` subprocess and what it wrote while running."""

    args: list[str]
    env: dict[str, str]
    cwd: Path
    port: int
    log_path: Path
    process: Optional[subprocess.Popen] = None
    _log: Optional[object] = field(default=None, repr=False)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self._log = open(self.log_path, "wb")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0
        self.process = subprocess.Popen(
            self.args,
            cwd=self.cwd,
            env=self.env,
            stdin=subprocess.DEVNULL,
            stdout=self._log,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )

    def log_text(self) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def wait_ready(self, run_dir: Path) -> requests.Response:
        """Poll /readyz until it answers and the owner token exists (lifespan ran)."""
        last: list = [None]
        token_path = run_dir / f"owner-{self.port}.token"

        def answered() -> bool:
            if self.process.poll() is not None:
                raise SmokeCheckError(
                    f"server exited early with code {self.process.returncode}:\n{self.log_text()}"
                )
            try:
                last[0] = requests.get(f"{self.base}/readyz", timeout=2)
            except requests.RequestException as error:
                last[0] = error
                return False
            return token_path.exists()

        if not poll(answered, START_TIMEOUT, 0.2):
            raise SmokeCheckError(f"server not ready after {START_TIMEOUT}s: {last[0]!r}")
        return last[0]

    def stop(self) -> Optional[int]:
        """Ask for a graceful shutdown; returns the exit code, or None if it was killed."""
        process = self.process
        if process is None or process.poll() is not None:
            return None if process is None else process.returncode
        try:
            if IS_WINDOWS:
                # Reaches the console-script launcher and the Python child in the new
                # process group; uvicorn treats SIGBREAK as a shutdown request.
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.send_signal(signal.SIGTERM)
            return process.wait(timeout=STOP_TIMEOUT)
        except (subprocess.TimeoutExpired, OSError):
            return None

    def kill(self) -> None:
        process = self.process
        if process is not None and process.poll() is None:
            if IS_WINDOWS:
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                process.kill()
            try:
                process.wait(timeout=STOP_TIMEOUT)
            except subprocess.TimeoutExpired:
                pass
        if self._log is not None:
            self._log.close()
            self._log = None


def run_cli(
    script: str, args: list[str], env: dict[str, str], cwd: Path
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [script, *args],
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=COMMAND_TIMEOUT,
    )


def describe(result: subprocess.CompletedProcess) -> str:
    return f"exit={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


class Smoke:
    def __init__(self, server: Server, script: str, env: dict[str, str], home: Path, tmp: Path):
        self.server = server
        self.script = script
        self.env = env
        self.home = home
        self.tmp = tmp
        self.run_dir = home / ".visp-memory" / "run"
        self.data_dir = home / ".visp-memory" / "data"
        self.token_path = self.run_dir / f"owner-{server.port}.token"
        self.discovery_path = self.run_dir / f"server-{server.port}.json"

    @property
    def base(self) -> str:
        return self.server.base

    def memory(self, content: str, **headers: str) -> requests.Response:
        return requests.post(
            f"{self.base}/memories",
            json={"content": content, "repo_id": SMOKE_REPO, "layer": "episodic"},
            headers=headers,
            timeout=10,
        )

    def readiness(self, response: requests.Response) -> None:
        # /readyz also requires the built dashboard. A source checkout without
        # `python build_frontend.py` has none, so accept exactly that one failure.
        body = response.json()
        checks = body.get("checks", {})
        check(checks.get("storage") == "ok", f"/readyz storage check failed: {body}")
        if checks.get("dashboard") == "ok":
            check(response.status_code == 200, f"/readyz {response.status_code}: {body}")
        else:
            check(response.status_code == 503, f"/readyz {response.status_code}: {body}")
            print("      note: dashboard not built, /readyz is 503 on that check alone")

    def host_guard(self) -> None:
        port = self.server.port
        own = requests.get(f"{self.base}/readyz", headers={"Host": f"127.0.0.1:{port}"}, timeout=5)
        check(own.status_code in (200, 503), f"own Host got {own.status_code}")
        self.readiness(own)
        rebound = requests.get(
            f"{self.base}/readyz", headers={"Host": f"evil.example:{port}"}, timeout=5
        )
        check(rebound.status_code == 421, f"foreign Host got {rebound.status_code}, want 421")

    def origin_guard(self) -> None:
        hostile = self.memory("smoke hostile origin", Origin="https://evil.example")
        check(hostile.status_code == 403, f"hostile Origin got {hostile.status_code}, want 403")
        own = self.memory("smoke own origin", Origin=self.base)
        check(own.status_code == 200, f"own Origin got {own.status_code}: {own.text}")
        bare = self.memory("smoke no origin")
        check(bare.status_code == 200, f"no Origin got {bare.status_code}: {bare.text}")
        listed = requests.get(f"{self.base}/memories", params={"repo_id": SMOKE_REPO}, timeout=10)
        contents = {row["content"] for row in listed.json()}
        check(
            {"smoke own origin", "smoke no origin"} <= contents
            and "smoke hostile origin" not in contents,
            f"stored contents unexpected: {sorted(contents)}",
        )

    def unscoped_refused(self) -> None:
        response = requests.get(f"{self.base}/memories", timeout=10)
        check(response.status_code == 400, f"unscoped GET got {response.status_code}")

    def owner_token(self) -> None:
        check(self.token_path.is_file(), f"missing owner token {self.token_path}")
        check(self.discovery_path.is_file(), f"missing discovery {self.discovery_path}")
        if not IS_WINDOWS:
            mode = stat.S_IMODE(self.token_path.stat().st_mode)
            check(mode == 0o600, f"token mode {oct(mode)}, want 0o600")
        token = self.token_path.read_text(encoding="utf-8")
        url = f"{self.base}/platform/audit-log"
        denied = requests.get(url, timeout=10)
        check(denied.status_code == 403, f"audit log without token got {denied.status_code}")
        wrong = requests.get(url, headers={OWNER_TOKEN_HEADER: token + "x"}, timeout=10)
        check(wrong.status_code == 403, f"audit log with wrong token got {wrong.status_code}")
        allowed = requests.get(url, headers={OWNER_TOKEN_HEADER: token}, timeout=10)
        check(
            allowed.status_code == 200,
            f"audit log with token got {allowed.status_code}: {allowed.text}",
        )

    def writer_lock(self) -> None:
        local = self.tmp / "local-writer"
        local.mkdir()
        env = {
            **self.env,
            "VISP_MEMORY_STORAGE_DATA_DIR": str(self.data_dir),
            "VISP_MEMORY_STORAGE_MODE": "local",
        }
        result = run_cli(
            self.script, ["record", "must be refused", "--repo", SMOKE_REPO], env, local
        )
        check(result.returncode != 0, f"local writer was not refused:\n{describe(result)}")
        output = (result.stderr + result.stdout).strip()
        check("Traceback" not in output, f"conflict printed a traceback:\n{describe(result)}")
        lines = [line for line in result.stderr.splitlines() if line.strip()]
        check(len(lines) == 1, f"want a one-line conflict on stderr:\n{describe(result)}")
        print(f"      refusal: {lines[0][:160]}")

    def connect_and_record(self) -> None:
        project = self.tmp / "project"
        project.mkdir()
        connected = run_cli(
            self.script,
            ["connect", "--repo", SMOKE_REPO, "--server-url", self.base],
            self.env,
            project,
        )
        check(connected.returncode == 0, f"connect failed:\n{describe(connected)}")
        config = yaml.safe_load((project / "visp-memory.yaml").read_text(encoding="utf-8"))
        storage = config.get("storage", {})
        check(storage.get("mode") == "client", f"storage.mode is {storage.get('mode')!r}")
        check(storage.get("server_url") == self.base, f"server_url is {storage.get('server_url')}")
        check(config.get("repo_id") == SMOKE_REPO, f"repo_id is {config.get('repo_id')!r}")

        marker = f"smoke client write {uuid.uuid4().hex[:8]}"
        env = {**self.env, "VISP_MEMORY_AGENT": SMOKE_AGENT}
        recorded = run_cli(self.script, ["record", marker], env, project)
        check(recorded.returncode == 0, f"client-mode record failed:\n{describe(recorded)}")

        listed = requests.get(
            f"{self.base}/memories", params={"repo_id": SMOKE_REPO, "limit": 200}, timeout=10
        )
        check(listed.status_code == 200, f"list got {listed.status_code}: {listed.text}")
        rows = [row for row in listed.json() if row.get("content") == marker]
        check(len(rows) == 1, f"client write not found on the server: {marker!r}")
        written_by = (rows[0].get("metadata") or {}).get("written_by") or {}
        check(
            written_by.get("agent") == SMOKE_AGENT,
            f"written_by is {written_by!r}, want agent={SMOKE_AGENT!r}",
        )

    def graceful_stop(self) -> None:
        code = self.server.stop()
        check(code is not None, "server did not exit after the stop signal")
        print(f"      exit code {code}")
        if IS_WINDOWS:
            # A console break ends the process without guaranteeing the lifespan's
            # cleanup ran; only report what was left behind.
            leftover = [p.name for p in (self.token_path, self.discovery_path) if p.exists()]
            print(f"      left behind on Windows (not asserted): {leftover or 'nothing'}")
            return
        check(not self.token_path.exists(), f"owner token not removed: {self.token_path}")
        check(not self.discovery_path.exists(), f"discovery not removed: {self.discovery_path}")


def run_steps(steps: list[tuple[str, Callable[[], None]]]) -> list[str]:
    """Run each step, printing PASS/FAIL; stop at the first failure (later ones depend)."""
    failed = []
    for name, step in steps:
        try:
            step()
        except (SmokeCheckError, requests.RequestException, ValueError, OSError) as error:
            print(f"FAIL  {name}\n      {error}")
            failed.append(name)
            break
        except subprocess.TimeoutExpired as error:
            print(f"FAIL  {name}\n      command timed out: {error}")
            failed.append(name)
            break
        print(f"PASS  {name}")
    return failed


def serve_args(script: str, port: int, reload: bool) -> list[str]:
    args = [script, "serve", "--shared", "--port", str(port)]
    return [*args, "--reload"] if reload else args


def main_smoke(script: str, tmp: Path) -> list[str]:
    home = tmp / "home"
    home.mkdir()
    env = isolated_env(home)
    port = find_free_port()
    server = Server(serve_args(script, port, False), env, tmp, port, tmp / "serve.log")
    smoke = Smoke(server, script, env, home, tmp)
    try:
        server.start()
        steps = [
            (
                "server starts and /readyz answers",
                lambda: smoke.readiness(server.wait_ready(smoke.run_dir)),
            ),
            ("Host guard: own Host served, foreign Host 421", smoke.host_guard),
            ("Origin guard: foreign 403, own and absent Origin write", smoke.origin_guard),
            ("shared server refuses unscoped GET /memories (400)", smoke.unscoped_refused),
            ("owner token file and maintenance route gating", smoke.owner_token),
            ("writer lock refuses a local-mode CLI on the served store", smoke.writer_lock),
            (
                "connect writes client config; client record lands with written_by",
                smoke.connect_and_record,
            ),
            ("graceful stop and runtime-file cleanup", smoke.graceful_stop),
        ]
        failed = run_steps(steps)
    finally:
        server.kill()
    if failed:
        print("---- server log ----")
        print(server.log_text()[-6000:])
    return failed


def reload_smoke(script: str, tmp: Path) -> list[str]:
    home = tmp / "home-reload"
    home.mkdir()
    env = isolated_env(home)
    port = find_free_port()
    server = Server(serve_args(script, port, True), env, tmp, port, tmp / "serve-reload.log")
    smoke = Smoke(server, script, env, home, tmp)
    try:
        server.start()
        failed = run_steps(
            [
                (
                    "--reload server answers /readyz",
                    lambda: smoke.readiness(server.wait_ready(smoke.run_dir)),
                ),
                (
                    "--reload server stops",
                    lambda: check(server.stop() is not None, "reload server did not exit"),
                ),
            ]
        )
    finally:
        server.kill()
    if failed:
        print("---- server log ----")
        print(server.log_text()[-6000:])
    return failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--reload", action="store_true", help="Also check that `serve --shared --reload` starts."
    )
    args = parser.parse_args()
    script = console_script()
    tmp = Path(tempfile.mkdtemp(prefix="visp-serve-smoke-"))
    started = time.monotonic()
    try:
        failed = main_smoke(script, tmp)
        if args.reload and not failed:
            failed = reload_smoke(script, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    elapsed = time.monotonic() - started
    if failed:
        print(f"Local serve smoke FAILED ({', '.join(failed)}) in {elapsed:.1f}s")
        return 1
    print(f"Local serve smoke passed in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
