"""Contract tests for the git capture boundary.

The regular capture tests cover the manifest's happy path.  These tests exercise the
integration boundary itself: real commits and merges, idempotent history sync, and
hook installation must preserve user hooks and report what happened.
"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

git = pytest.importorskip("git", reason="git capture requires GitPython")

from visp_memory import Memory, MemoryConfig  # noqa: E402
from visp_memory.capture.git import CaptureManifest, GitCapture  # noqa: E402
from visp_memory.core.trust import WriteChannel  # noqa: E402


@pytest.fixture
def memory(tmp_path):
    config = MemoryConfig(repo_id="git-test")
    config.storage.data_dir = tmp_path / "store"
    config.embedding.provider = "noop"
    return Memory(config=config)


@pytest.fixture
def repo(tmp_path):
    repository = git.Repo.init(tmp_path / "repo")
    with repository.config_writer() as writer:
        writer.set_value("user", "name", "Git Test")
        writer.set_value("user", "email", "git-test@example.com")
    return repository


def _commit(repository, path: str, content: str, message: str):
    target = Path(repository.working_tree_dir) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    repository.index.add([path])
    return repository.index.commit(message)


def _commit_files(repository, files: dict[str, str], message: str):
    for path, content in files.items():
        target = Path(repository.working_tree_dir) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    repository.index.add(list(files))
    return repository.index.commit(message)


def test_manifest_handles_malformed_json_and_replacement(memory, tmp_path):
    manifest_path = tmp_path / "store" / "capture_manifest.json"
    manifest_path.parent.mkdir(exist_ok=True)
    manifest_path.write_text("{not json", encoding="utf-8")

    manifest = CaptureManifest(memory)
    assert manifest.summary() == {"changed": 0, "unchanged": 0, "stale": 0, "entries": 0}

    manifest.replace(
        {
            "capture_version": "legacy",
            "entries": {"git_commit:abc": {"status": "stale", "output_memory_ids": ["m1"]}},
        }
    )
    assert manifest.to_export()["capture_version"] == "legacy"
    assert manifest.to_export()["entries"]["git_commit:abc"]["status"] == "stale"
    assert CaptureManifest.status_report(["changed", "stale", "ignored"]) == {
        "changed": 1,
        "unchanged": 0,
        "stale": 1,
    }


def test_manifest_without_storage_config_is_read_only():
    memory = SimpleNamespace(
        config=SimpleNamespace(storage=SimpleNamespace(data_dir=object()))
    )
    manifest = CaptureManifest(memory)
    assert manifest.path is None
    manifest.record("source", "id", "hash", ["memory"])
    assert manifest.summary()["entries"] == 1


def test_on_commit_records_context_and_skips_same_content(memory, repo):
    commit = _commit(repo, "src/auth.py", "raise RuntimeError('fixed')\n", "fix: repair auth race")
    capture = GitCapture(memory, repo_path=repo.working_tree_dir)

    memory_id = capture.on_commit(commit.hexsha)
    assert memory_id
    stored = memory._storage.get_memory(memory_id)
    assert stored["category"] == "bug_fixed"
    assert stored["metadata"]["write_channel"] == WriteChannel.GIT.value
    assert stored["metadata"]["commit_hash"] == commit.hexsha
    assert stored["metadata"]["files_changed"] == 1
    assert "Modified: src/auth.py" in stored["content"]

    assert capture.on_commit(commit.hexsha) is None
    assert CaptureManifest(memory).summary() == {
        "changed": 0,
        "unchanged": 1,
        "stale": 0,
        "entries": 1,
    }


def test_on_commit_formats_large_code_change_and_tracks_changed_files(memory, repo):
    capture = GitCapture(memory, repo_path=repo.working_tree_dir)
    files = {
        "src/a.py": "a\n",
        "src/b.py": "b\n",
        "src/c.py": "c\n",
        "src/d.py": "d\n",
        "src/e.py": "e\n",
        "src/f.py": "f\n",
        "tests/test_api.py": "def test_api(): pass\n",
        "README.md": "capture\n",
        "config.toml": "[capture]\nenabled = true\n",
    }
    commit = _commit_files(repo, files, "feat: add modules")

    memory_id = capture.on_commit(commit.hexsha)
    stored = memory._storage.get_memory(memory_id)
    assert stored["content"].startswith("Commit: add modules | Modified: ")
    assert "... and 1 more files" in stored["content"]
    assert stored["metadata"]["files_changed"] == len(files)
    assert "tests/test_api.py" not in stored["content"]
    assert "README.md" not in stored["content"]
    assert "config.toml" not in stored["content"]


def test_on_commit_exposes_message_categories_and_size_importance(memory, repo):
    capture = GitCapture(memory, repo_path=repo.working_tree_dir)
    cases = (
        ("fix api timeout", "bug_fixed", "fix api timeout"),
        ("reorganize the cache\nextra details", "refactor", "reorganize the cache"),
        ("implement cache", "feature_added", "implement cache"),
        ("misc note", "note", "misc note"),
    )

    for index, (message, category, subject) in enumerate(cases):
        commit = _commit(repo, f"src/case_{index}.py", f"case = {index}\n", message)
        memory_id = capture.on_commit(commit.hexsha)
        stored = memory._storage.get_memory(memory_id)
        assert stored["category"] == category
        assert stored["content"].startswith(f"Commit: {subject}")

    large_commit = _commit(
        repo,
        "src/large_fix.py",
        "\n".join(f"line_{index}" for index in range(600)) + "\n",
        "fix: large repair",
    )
    large_id = capture.on_commit(large_commit.hexsha)
    large_stored = memory._storage.get_memory(large_id)
    assert large_stored["category"] == "bug_fixed"
    assert large_stored["importance"] == pytest.approx(0.9)


def test_on_merge_records_branch_summary_and_is_idempotent(memory, repo):
    _commit(repo, "base.py", "base = True\n", "feat: add base")
    base_branch = repo.active_branch.name
    repo.git.checkout("-b", "feature/auth")
    _commit(repo, "auth.py", "auth = True\n", "feat: add auth")
    _commit(repo, "bug.py", "bug = False\n", "fix: repair auth")
    repo.git.checkout(base_branch)
    merge = repo.git.merge("--no-ff", "feature/auth", "-m", "Merge branch 'feature/auth'")
    assert merge

    capture = GitCapture(memory, repo_path=repo.working_tree_dir)
    memory_id = capture.on_merge()
    assert memory_id
    stored = memory._storage.get_memory(memory_id)
    assert "feature/auth" in stored["content"]
    assert "1 feature(s)" in stored["content"]
    assert "1 fix(es)" in stored["content"]
    assert capture.on_merge() is None


def test_sync_history_forwards_dates_skips_merges_and_continues_after_error(memory):
    capture = GitCapture.__new__(GitCapture)
    calls = {}

    class Commit:
        def __init__(self, name, parents=()):
            self.hexsha = name
            self.parents = list(parents)

    commits = [Commit("good"), Commit("merge", parents=[1, 2]), Commit("same"), Commit("bad")]

    def iter_commits(**kwargs):
        calls.update(kwargs)
        return commits

    capture.repo = SimpleNamespace(iter_commits=iter_commits)

    def on_commit(ref):
        if ref == "bad":
            raise RuntimeError("broken commit")
        return {"good": "memory-1", "same": None}[ref]

    capture.on_commit = on_commit
    ids = capture.sync_history(since="2024-01-01", until="2024-02-01", limit=8)

    assert ids == ["memory-1"]
    assert calls == {"max_count": 8, "since": "2024-01-01", "until": "2024-02-01"}
    assert capture.last_manifest_report == {"changed": 1, "unchanged": 1, "stale": 0}


def test_install_and_uninstall_hooks_backup_foreign_files(memory, repo):
    hooks = Path(repo.git_dir) / "hooks"
    hooks.mkdir(exist_ok=True)
    foreign = hooks / "post-commit"
    foreign.write_text("#!/bin/sh\necho user-hook\n", encoding="utf-8")

    capture = GitCapture(memory, repo_path=repo.working_tree_dir)
    assert capture.install_hooks() == {"post-commit": True, "post-merge": True}
    assert (hooks / "post-commit.backup").read_text(encoding="utf-8") == (
        "#!/bin/sh\necho user-hook\n"
    )
    installed = foreign.read_text(encoding="utf-8")
    assert installed.startswith("#!/bin/sh\n")
    assert "visp-memory capture git commit --ref HEAD" in installed
    assert installed.rstrip().endswith("exit 0")
    merge_hook = hooks / "post-merge"
    merge_installed = merge_hook.read_text(encoding="utf-8")
    assert merge_installed.startswith("#!/bin/sh\n")
    assert "visp-memory capture git merge" in merge_installed
    assert merge_installed.rstrip().endswith("exit 0")
    if os.name == "posix":
        assert foreign.stat().st_mode & 0o111
        assert merge_hook.stat().st_mode & 0o111
    assert capture.install_hooks() == {"post-commit": True, "post-merge": True}

    assert capture.uninstall_hooks() == {"post-commit": True, "post-merge": True}
    assert not (hooks / "post-commit").exists()
    assert (hooks / "post-commit.backup").exists()


def test_uninstall_hooks_preserves_foreign_and_missing_hooks(memory, repo):
    hooks = Path(repo.git_dir) / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "post-commit").write_text("#!/bin/sh\necho keep\n", encoding="utf-8")
    capture = GitCapture(memory, repo_path=repo.working_tree_dir)

    result = capture.uninstall_hooks()
    assert result == {"post-commit": False, "post-merge": True}
    assert (hooks / "post-commit").exists()


def test_invalid_repository_is_reported(memory, tmp_path):
    with pytest.raises(ValueError, match="Not a git repository"):
        GitCapture(memory, repo_path=tmp_path)
