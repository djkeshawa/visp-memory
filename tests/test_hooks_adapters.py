"""
Tests for hook adapter file-safety guarantees.

These cover the marker-order guard, backup-on-install behavior, and the
round-trip that must preserve user content outside the managed markers.

The adapters' ``get_memory_context`` is stubbed so the tests exercise the
file-rewriting logic without constructing a full Memory/storage stack.
"""

from types import SimpleNamespace

import pytest

from llm_memory.hooks.base import _replace_between_markers
from llm_memory.hooks.claude_code import ClaudeCodeAdapter
from llm_memory.hooks.generic import GenericAdapter


STUB_CONTEXT = "STUBBED-MEMORY-CONTEXT-BODY"


def _fake_memory():
    """Minimal stand-in for a Memory instance (config only used by codex)."""
    return SimpleNamespace(config=SimpleNamespace(repo_id="test-repo"))


def _stub_context(adapter):
    """Force a deterministic memory context so file output is predictable."""
    adapter.get_memory_context = lambda files=None, task=None, format="markdown": STUB_CONTEXT
    return adapter


# ---------------------------------------------------------------------------
# _replace_between_markers helper
# ---------------------------------------------------------------------------


def test_helper_replaces_well_formed_region():
    content = "head\nSTART\nold\nEND\ntail\n"
    result = _replace_between_markers(content, "START", "END", "\nnew\n")
    assert result == "head\nSTART\nnew\nEND\ntail\n"


def test_helper_refuses_reordered_markers():
    content = "head\nEND\nmiddle\nSTART\ntail\n"
    assert _replace_between_markers(content, "START", "END", "X") is None


def test_helper_refuses_duplicate_start():
    content = "START\na\nSTART\nb\nEND\n"
    assert _replace_between_markers(content, "START", "END", "X") is None


def test_helper_refuses_duplicate_end():
    content = "START\na\nEND\nb\nEND\n"
    assert _replace_between_markers(content, "START", "END", "X") is None


def test_helper_refuses_missing_marker():
    assert _replace_between_markers("no markers here", "START", "END", "X") is None


# ---------------------------------------------------------------------------
# Generic adapter (injection mode) round-trip
# ---------------------------------------------------------------------------


def test_generic_round_trip_preserves_surrounding_content(tmp_path):
    marker = "<!-- MEM -->"
    adapter = _stub_context(
        GenericAdapter(
            _fake_memory(),
            project_root=tmp_path,
            context_file="CONTEXT.md",
            injection_marker=marker,
            append_mode=True,
        )
    )

    target = tmp_path / "CONTEXT.md"
    user_before = "# My Project\n\nImportant user notes above.\n\n"
    user_after = "\n\n## Footer\n\nUser notes below.\n"
    target.write_text(
        f"{user_before}{marker} START\n\nplaceholder\n{marker} END{user_after}",
        encoding="utf-8",
    )

    assert adapter.update_context() is True

    updated = target.read_text(encoding="utf-8")
    # User content outside the markers is preserved verbatim.
    assert updated.startswith(user_before)
    assert updated.endswith(user_after)
    # The stubbed memory context landed between the markers.
    assert STUB_CONTEXT in updated

    # A second update is idempotent w.r.t. the surrounding content.
    assert adapter.update_context() is True
    updated2 = target.read_text(encoding="utf-8")
    assert updated2.startswith(user_before)
    assert updated2.endswith(user_after)


def test_generic_reordered_markers_refuses_and_leaves_file_untouched(tmp_path):
    marker = "<!-- MEM -->"
    adapter = _stub_context(
        GenericAdapter(
            _fake_memory(),
            project_root=tmp_path,
            context_file="CONTEXT.md",
            injection_marker=marker,
            append_mode=True,
        )
    )

    target = tmp_path / "CONTEXT.md"
    # END appears before START -> malformed / reordered.
    original = f"prefix\n{marker} END\nmiddle\n{marker} START\nsuffix\n"
    target.write_text(original, encoding="utf-8")

    assert adapter.update_context() is False
    assert target.read_text(encoding="utf-8") == original


def test_generic_duplicate_start_marker_refuses(tmp_path):
    marker = "<!-- MEM -->"
    adapter = _stub_context(
        GenericAdapter(
            _fake_memory(),
            project_root=tmp_path,
            context_file="CONTEXT.md",
            injection_marker=marker,
            append_mode=True,
        )
    )

    target = tmp_path / "CONTEXT.md"
    original = (
        f"{marker} START\nfirst\n{marker} START\nsecond\n{marker} END\n"
    )
    target.write_text(original, encoding="utf-8")

    assert adapter.update_context() is False
    assert target.read_text(encoding="utf-8") == original


def test_generic_install_backs_up_existing_file(tmp_path):
    marker = "<!-- MEM -->"
    target = tmp_path / "CONTEXT.md"
    original = "# Existing user file without markers\n"
    target.write_text(original, encoding="utf-8")

    adapter = GenericAdapter(
        _fake_memory(),
        project_root=tmp_path,
        context_file="CONTEXT.md",
        injection_marker=marker,
        append_mode=True,
    )

    adapter.install()

    backup = tmp_path / "CONTEXT.md.backup"
    assert backup.exists()
    # Backup captured the pre-modification content.
    assert backup.read_text(encoding="utf-8") == original
    # Markers were added to the live file.
    assert f"{marker} START" in target.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Claude Code adapter round-trip
# ---------------------------------------------------------------------------


def test_claude_code_round_trip_preserves_surrounding_content(tmp_path):
    adapter = _stub_context(ClaudeCodeAdapter(_fake_memory(), project_root=tmp_path))

    target = tmp_path / "CLAUDE.md"
    start = "<!-- LLM-MEMORY --> START"
    end = "<!-- LLM-MEMORY --> END"
    user_before = "# CLAUDE.md\n\nHand-written guidance that must survive.\n\n"
    user_after = "\n\n## Manual section\n\nMore hand-written text.\n"
    target.write_text(
        f"{user_before}{start}\n\nplaceholder\n{end}{user_after}",
        encoding="utf-8",
    )

    assert adapter.update_context() is True

    updated = target.read_text(encoding="utf-8")
    assert updated.startswith(user_before)
    assert updated.endswith(user_after)
    assert STUB_CONTEXT in updated


def test_claude_code_reordered_markers_refuses_and_leaves_file_untouched(tmp_path):
    adapter = _stub_context(ClaudeCodeAdapter(_fake_memory(), project_root=tmp_path))

    target = tmp_path / "CLAUDE.md"
    start = "<!-- LLM-MEMORY --> START"
    end = "<!-- LLM-MEMORY --> END"
    original = f"prefix\n{end}\nmiddle\n{start}\nsuffix\n"
    target.write_text(original, encoding="utf-8")

    assert adapter.update_context() is False
    assert target.read_text(encoding="utf-8") == original


def test_claude_code_duplicate_start_marker_refuses(tmp_path):
    adapter = _stub_context(ClaudeCodeAdapter(_fake_memory(), project_root=tmp_path))

    target = tmp_path / "CLAUDE.md"
    start = "<!-- LLM-MEMORY --> START"
    end = "<!-- LLM-MEMORY --> END"
    original = f"{start}\nfirst\n{start}\nsecond\n{end}\n"
    target.write_text(original, encoding="utf-8")

    assert adapter.update_context() is False
    assert target.read_text(encoding="utf-8") == original


def test_claude_code_install_creates_file_and_update_round_trips(tmp_path):
    """Fresh install then update should produce a valid, marker-guarded file."""
    adapter = _stub_context(ClaudeCodeAdapter(_fake_memory(), project_root=tmp_path))

    adapter.install()
    target = tmp_path / "CLAUDE.md"
    assert target.exists()

    assert adapter.update_context() is True
    updated = target.read_text(encoding="utf-8")
    assert STUB_CONTEXT in updated
    # Markers remain intact and unique after update.
    assert updated.count("<!-- LLM-MEMORY --> START") == 1
    assert updated.count("<!-- LLM-MEMORY --> END") == 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
