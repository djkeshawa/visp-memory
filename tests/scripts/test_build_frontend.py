"""Tests for dashboard build helper."""

import build_frontend


def test_build_frontend_fails_when_dashboard_required_and_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(build_frontend, "__file__", str(tmp_path / "build_frontend.py"))

    assert build_frontend.build_frontend(required=True) is False


def test_build_frontend_can_skip_missing_dashboard_when_optional(tmp_path, monkeypatch):
    monkeypatch.setattr(build_frontend, "__file__", str(tmp_path / "build_frontend.py"))

    assert build_frontend.build_frontend(required=False) is True


def test_build_frontend_can_skip_npm_failure_when_optional(tmp_path, monkeypatch):
    (tmp_path / "llm-memory-dashboard").mkdir()
    monkeypatch.setattr(build_frontend, "__file__", str(tmp_path / "build_frontend.py"))

    def raise_missing_npm(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(build_frontend.subprocess, "run", raise_missing_npm)

    assert build_frontend.build_frontend(required=False) is True
