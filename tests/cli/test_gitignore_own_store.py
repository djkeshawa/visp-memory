"""E1 — visp-memory keeps its own store out of the user's repository.

`init` writes a SQLite database and binary vector indexes (data_level0.bin,
link_lists.bin, chroma.sqlite3) into the working tree — roughly a megabyte for
a handful of memories — and never ignored them.

That is not a tidiness concern. During a dogfooding session on a real project
the file volume stopped the owner's IDE from loading the repository, and they
added the entries to .gitignore by hand mid-run to get working again. A tool
that writes a data directory into someone's project is responsible for keeping
it out of their history.
"""

import subprocess
from pathlib import Path

import pytest

from visp_memory.interfaces.cli import ensure_gitignored


def _git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def test_the_store_is_added_to_gitignore(tmp_path):
    root = _git_repo(tmp_path)

    added = ensure_gitignored(root, Path(".visp-memory") / "data")

    assert added == [".visp-memory/"]
    assert ".visp-memory/" in (root / ".gitignore").read_text(encoding="utf-8")


def test_git_no_longer_reports_the_store(tmp_path):
    """The assertion that matters: git must not see it."""
    root = _git_repo(tmp_path)
    ensure_gitignored(root, Path(".visp-memory") / "data")

    store = root / ".visp-memory" / "data" / "vectors"
    store.mkdir(parents=True)
    (store / "data_level0.bin").write_bytes(b"\0" * 1024)

    reported = subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=True
    ).stdout

    assert ".visp-memory" not in reported, (
        f"git still reports the store, so it will be committed or clutter every diff:\n{reported}"
    )


def test_existing_entries_are_preserved(tmp_path):
    """Append only. A project's own conventions must survive."""
    root = _git_repo(tmp_path)
    (root / ".gitignore").write_text("target/\nnode_modules/\n", encoding="utf-8")

    ensure_gitignored(root, Path(".visp-memory") / "data")

    contents = (root / ".gitignore").read_text(encoding="utf-8")
    assert "target/" in contents
    assert "node_modules/" in contents
    assert ".visp-memory/" in contents


def test_running_twice_adds_nothing_further(tmp_path):
    root = _git_repo(tmp_path)
    ensure_gitignored(root, Path(".visp-memory") / "data")
    first = (root / ".gitignore").read_text(encoding="utf-8")

    added = ensure_gitignored(root, Path(".visp-memory") / "data")

    assert added == []
    assert (root / ".gitignore").read_text(encoding="utf-8") == first


def test_an_entry_the_user_already_wrote_is_respected(tmp_path):
    # Written without the trailing slash, as a person would.
    root = _git_repo(tmp_path)
    (root / ".gitignore").write_text(".visp-memory\n", encoding="utf-8")

    assert ensure_gitignored(root, Path(".visp-memory") / "data") == []


def test_nothing_is_written_outside_a_git_repository(tmp_path):
    """No .git means no .gitignore to own — creating one would be presumptuous."""
    assert ensure_gitignored(tmp_path, Path(".visp-memory") / "data") == []
    assert not (tmp_path / ".gitignore").exists()


def test_the_config_file_is_not_ignored(tmp_path):
    """visp-memory.yaml is meant to be committed and shared, unlike the store."""
    root = _git_repo(tmp_path)
    ensure_gitignored(root, Path(".visp-memory") / "data")

    assert "visp-memory.yaml" not in (root / ".gitignore").read_text(encoding="utf-8")
