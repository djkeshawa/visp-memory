"""Tests for informational writer attribution."""

import pytest

from visp_memory.core.attribution import (
    WriterIdentity,
    bind_writer,
    current_writer,
    identity_from_headers,
    identity_headers,
    sanitize_label,
    stamp_written_by,
    suppress_attribution,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" codex ", "codex"),
        ("claude-code/session_1", "claude-code/session_1"),
        (None, None),
        ("   ", None),
        ("bad label", None),
        ("x" * 65, None),
    ],
)
def test_sanitize_label(value, expected):
    assert sanitize_label(value) == expected


def test_writer_identity_omits_empty_fields():
    assert WriterIdentity(agent="codex", session=None, client="").to_metadata() == {
        "agent": "codex"
    }
    assert WriterIdentity(None, None, None).to_metadata() is None


def test_bind_writer_nests_and_resets(monkeypatch):
    monkeypatch.delenv("VISP_MEMORY_AGENT", raising=False)
    monkeypatch.delenv("VISP_MEMORY_SESSION", raising=False)
    outer = WriterIdentity("codex", "session-a", None)
    inner = WriterIdentity("claude-code", "session-b", None)

    assert current_writer() is None
    with bind_writer(outer):
        assert current_writer() == outer
        with bind_writer(inner):
            assert current_writer() == inner
        assert current_writer() == outer
    assert current_writer() is None


def test_current_writer_uses_sanitized_environment_default(monkeypatch):
    monkeypatch.setenv("VISP_MEMORY_AGENT", " codex ")
    monkeypatch.setenv("VISP_MEMORY_SESSION", "session_1")

    assert current_writer() == WriterIdentity("codex", "session_1", None)


def test_stamp_overwrites_current_writer_but_suppression_preserves_metadata(
    monkeypatch,
):
    monkeypatch.delenv("VISP_MEMORY_AGENT", raising=False)
    monkeypatch.delenv("VISP_MEMORY_SESSION", raising=False)
    metadata = {"written_by": {"agent": "caller"}, "keep": True}

    with bind_writer(WriterIdentity("codex", "session-a", None)):
        stamped = stamp_written_by(metadata)
        assert stamped == {
            "written_by": {"agent": "codex", "session": "session-a"},
            "keep": True,
        }
        assert metadata["written_by"] == {"agent": "caller"}
        with suppress_attribution():
            assert stamp_written_by(metadata) == metadata

    assert stamp_written_by(metadata) == metadata


def test_identity_headers_round_trip_sanitizes_input():
    identity = WriterIdentity("codex", "session-1", "Codex/1.2")

    assert identity_headers(identity) == {
        "X-Visp-Agent": "codex",
        "X-Visp-Session": "session-1",
        "X-Visp-Client": "Codex/1.2",
    }
    assert identity_from_headers(identity_headers(identity)) == identity
    assert identity_from_headers({"x-visp-agent": "bad label"}) is None
