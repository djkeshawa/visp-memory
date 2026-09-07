"""Text preview exposes the same policy decisions on success and abstention."""

from typer.testing import CliRunner

from visp_memory.core.injection import InjectionResult, select_for_injection
from visp_memory.interfaces.cli import app


def preview_result(monkeypatch, result):
    monkeypatch.setattr("visp_memory.core.injection.inject_for_task", lambda *a, **k: result)
    return CliRunner().invoke(app, ["preview", "refactor session token validation"])


def test_abstention_shows_trust_and_expiry_rejections(cli_env, monkeypatch):
    candidates = [
        {"id": "external", "repo_id": "test", "content": "token validation",
         "source": "external", "relevance_score": 0.99},
        {"id": "expired", "repo_id": "test", "content": "old token validation",
         "source": "authored", "metadata": {"valid_to": "2000-01-01T00:00:00Z"}},
    ]
    result = select_for_injection(candidates, repo_id="test", task="token validation")
    shown = preview_result(monkeypatch, result)
    assert shown.exit_code == 0, shown.output
    assert "quarantined" in shown.output
    assert "temporal validity or scope" in shown.output
    assert "expired" in shown.output
    assert "2 candidates considered" in shown.output


def test_stale_code_reason_survives_abstention(cli_env, monkeypatch):
    shown = preview_result(monkeypatch, InjectionResult(
        considered=1, dropped_stale_anchor=1, reason="every candidate described deleted code"
    ))
    assert shown.exit_code == 0, shown.output
    assert "1 anchored to deleted code" in shown.output


def test_selected_records_show_identity_provenance_and_score(cli_env, monkeypatch):
    row = {"id": "selected-123", "repo_id": "test", "content": "preserve token expiry",
           "source": "authored", "relevance_score": 0.95}
    result = select_for_injection([row], repo_id="test", task="refactor session token validation")
    shown = preview_result(monkeypatch, result)
    assert shown.exit_code == 0, shown.output
    assert "selected-123" in shown.output
    assert "authored" in shown.output
    assert "0.950" in shown.output
    assert "not the entire store" in shown.output
