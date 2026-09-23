"""Tests for dashboard build helper."""

import subprocess

import pytest

import build_frontend


def test_build_frontend_fails_when_dashboard_required_and_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(build_frontend, "__file__", str(tmp_path / "build_frontend.py"))

    assert build_frontend.build_frontend(required=True) is False


def test_build_frontend_can_skip_missing_dashboard_when_optional(tmp_path, monkeypatch):
    monkeypatch.setattr(build_frontend, "__file__", str(tmp_path / "build_frontend.py"))

    assert build_frontend.build_frontend(required=False) is True


def test_build_frontend_can_skip_npm_failure_when_optional(tmp_path, monkeypatch):
    (tmp_path / "visp-memory-dashboard").mkdir()
    monkeypatch.setattr(build_frontend, "__file__", str(tmp_path / "build_frontend.py"))

    def raise_missing_npm(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(build_frontend.subprocess, "run", raise_missing_npm)

    assert build_frontend.build_frontend(required=False) is True


def test_build_frontend_skips_when_npm_not_on_path(tmp_path, monkeypatch):
    # npm is resolved via shutil.which (which honours PATHEXT so npm.cmd is found
    # on Windows). When npm is genuinely absent, which returns None and the build
    # skips gracefully when optional / fails when required.
    (tmp_path / "visp-memory-dashboard").mkdir()
    monkeypatch.setattr(build_frontend, "__file__", str(tmp_path / "build_frontend.py"))
    monkeypatch.setattr(build_frontend.shutil, "which", lambda name: None)

    assert build_frontend.build_frontend(required=False) is True
    assert build_frontend.build_frontend(required=True) is False


@pytest.mark.parametrize("existing_modules", [False, True])
def test_build_synchronizes_locked_dependencies_before_export(
    tmp_path, monkeypatch, existing_modules
):
    dashboard = tmp_path / "visp-memory-dashboard"
    dashboard.mkdir()
    if existing_modules:
        (dashboard / "node_modules").mkdir()
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command[1:] == ["run", "export"]:
            (dashboard / "out").mkdir()
            (dashboard / "out" / "index.html").write_text("fresh dashboard")

    monkeypatch.setattr(build_frontend, "__file__", str(tmp_path / "build_frontend.py"))
    monkeypatch.setattr(build_frontend.shutil, "which", lambda _: "npm")
    monkeypatch.setattr(build_frontend.subprocess, "run", run)

    assert build_frontend.build_frontend() is True
    assert commands == [["npm", "ci"], ["npm", "run", "export"]]
    bundled = tmp_path / "src/visp_memory/server/static/index.html"
    assert bundled.read_text() == "fresh dashboard"


def test_failed_dependency_sync_stops_build_even_with_existing_modules(tmp_path, monkeypatch):
    dashboard = tmp_path / "visp-memory-dashboard"
    (dashboard / "node_modules").mkdir(parents=True)
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command[1] == "ci":
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(build_frontend, "__file__", str(tmp_path / "build_frontend.py"))
    monkeypatch.setattr(build_frontend.shutil, "which", lambda _: "npm")
    monkeypatch.setattr(build_frontend.subprocess, "run", run)

    assert build_frontend.build_frontend() is False
    assert commands == [["npm", "ci"]]
