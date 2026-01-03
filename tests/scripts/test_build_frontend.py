"""Tests for dashboard build helper."""

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
