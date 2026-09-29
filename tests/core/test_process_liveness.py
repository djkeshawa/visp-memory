import os
import subprocess
import sys

from visp_memory.core import process_liveness
from visp_memory.core.process_liveness import pid_is_alive


def test_current_process_is_alive():
    assert pid_is_alive(os.getpid()) is True


def test_invalid_pids_are_inconclusive():
    assert pid_is_alive(None) is True
    assert pid_is_alive(0) is True
    assert pid_is_alive(-5) is True


def test_exited_process_is_dead():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert pid_is_alive(child.pid) is False


def test_windows_branch_never_signals_the_process(monkeypatch):
    def _forbidden(*_args):
        raise AssertionError("os.kill sends CTRL_C_EVENT on Windows")

    seen = []
    monkeypatch.setattr(process_liveness.os, "name", "nt")
    monkeypatch.setattr(process_liveness.os, "kill", _forbidden)
    monkeypatch.setattr(
        process_liveness, "_windows_pid_is_alive", lambda pid: seen.append(pid) or False
    )

    assert pid_is_alive(4242) is False
    assert seen == [4242]


def test_windows_probe_failure_is_inconclusive(monkeypatch):
    def _broken(_pid):
        raise OSError("kernel32 unavailable")

    monkeypatch.setattr(process_liveness.os, "name", "nt")
    monkeypatch.setattr(process_liveness, "_windows_pid_is_alive", _broken)

    assert pid_is_alive(4242) is True
