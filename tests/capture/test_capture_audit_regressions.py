"""Regression tests for capture defects found in the module audit.

Each test drives the real code path (real git repositories, real hook scripts,
real files on disk) rather than asserting on implementation details.
"""

import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

from visp_memory import Memory, MemoryConfig
from visp_memory.capture.conversation import ConversationCapture
from visp_memory.capture.git import CaptureManifest
from visp_memory.capture.instructions import (
    discover_instruction_files,
    ingest_instructions,
    split_sections,
)


@pytest.fixture
def memory(tmp_path):
    config = MemoryConfig(repo_id="audit")
    config.storage.data_dir = tmp_path / "store"
    config.embedding.provider = "noop"
    return Memory(config=config)


def _git_repo(path: Path):
    git = pytest.importorskip("git", reason="git capture requires GitPython")
    repository = git.Repo.init(path)
    with repository.config_writer() as writer:
        writer.set_value("user", "name", "Audit")
        writer.set_value("user", "email", "audit@example.com")
    return repository


def _commit(repository, path: str, content: str, message: str):
    target = Path(repository.working_tree_dir) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    repository.index.add([path])
    return repository.index.commit(message)


# --- conversation capture ---------------------------------------------------------


class _FakeClient:
    def __init__(self):
        self.calls = 0

    def completion(self, prompt, system_prompt=None):
        self.calls += 1
        return '{"decisions":[],"learnings":[],"bugs":[],"tasks":[]}'


def test_conversation_capture_never_sends_the_embedding_key_to_the_llm(
    memory, monkeypatch
):
    seen = {}

    def fake_create(provider, model=None, api_key=None, base_url=None):
        seen.update(provider=provider, api_key=api_key)
        return _FakeClient()

    monkeypatch.setattr("visp_memory.capture.conversation.create_llm_client", fake_create)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-own-key")
    memory.config.embedding.api_key = "openai-embedding-key"
    memory.config.capture.llm_provider = "anthropic"

    ConversationCapture(memory)._get_client()
    assert seen == {"provider": "anthropic", "api_key": "anthropic-own-key"}

    # The shared LLM key is used when it belongs to the same provider.
    memory.config.llm.provider = "anthropic"
    memory.config.llm.api_key = "shared-anthropic-key"
    ConversationCapture(memory)._get_client()
    assert seen["api_key"] == "shared-anthropic-key"

    # ...but never when it is configured for a different vendor.
    memory.config.llm.provider = "openai"
    memory.config.llm.api_key = "openai-llm-key"
    ConversationCapture(memory)._get_client()
    assert seen["api_key"] == "anthropic-own-key"


def test_same_named_conversation_files_in_different_directories_do_not_collide(
    memory, tmp_path
):
    first = tmp_path / "s1" / "chat.md"
    second = tmp_path / "s2" / "chat.md"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text(
        "user: we chose postgres over mysql for json support\nassistant: noted\n",
        encoding="utf-8",
    )
    second.write_text(
        "user: the deploy script needs a retry on 502s\nassistant: ok\n",
        encoding="utf-8",
    )
    capture = ConversationCapture(memory)
    client = capture._client = _FakeClient()

    capture.parse_file(str(first))
    after_first = client.calls
    capture.parse_file(str(second))
    after_second = client.calls
    assert after_second > after_first

    # Capturing the other chat.md must not have evicted the first one's state.
    result = capture.parse_file(str(first))
    assert result["capture_manifest"]["status"] == "unchanged"
    assert client.calls == after_second

    sources = {
        row["metadata"].get("capture_source")
        for row in memory._storage.list_memories(
            layer="episodic", repo_id="audit", status="all", limit=100
        )
    }
    assert sources == {"chat.md"}  # display name is unchanged; only identity moved


# --- git capture ------------------------------------------------------------------


@pytest.mark.skipif(os.name != "posix", reason="hook scripts are POSIX shell")
def test_post_commit_hook_captures_every_commit_of_a_rapid_sequence(
    memory, tmp_path, monkeypatch
):
    if shutil.which("git") is None:
        pytest.skip("git executable required")
    from visp_memory.capture.git import GitCapture

    repository = _git_repo(tmp_path / "repo")
    _commit(repository, "a.txt", "0", "chore: seed")

    # A stand-in CLI that records the ref it was asked to capture, slowly, the way
    # a real capture lags behind the commit that triggered it.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "captured.log"
    fake = bin_dir / "visp-memory"
    fake.write_text(
        "#!/bin/sh\n"
        "sleep 0.3\n"
        "echo noisy-output\n"
        f"echo \"$5\" >> '{log}'\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    assert GitCapture(memory, repo_path=repository.working_tree_dir).install_hooks()[
        "post-commit"
    ]
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    shas = []
    outputs = []
    for index in range(3):
        Path(repository.working_tree_dir, "a.txt").write_text(str(index + 1))
        run = subprocess.run(
            ["git", "commit", "-qam", f"fix: change {index}"],
            cwd=repository.working_tree_dir, env=env, capture_output=True, text=True,
        )
        assert run.returncode == 0, run.stderr
        outputs.append(run.stdout + run.stderr)
        shas.append(repository.head.commit.hexsha)

    for _ in range(50):
        if log.exists() and len(log.read_text().split()) == 3:
            break
        time.sleep(0.1)
    assert sorted(log.read_text().split()) == sorted(shas)
    assert not any("noisy-output" in output for output in outputs)


def test_hooks_install_where_git_runs_them_for_linked_worktrees(memory, tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git executable required")
    from visp_memory.capture.git import GitCapture

    repository = _git_repo(tmp_path / "main")
    _commit(repository, "a.txt", "1", "chore: seed")
    worktree = tmp_path / "linked"
    repository.git.worktree("add", str(worktree), "-b", "side")

    GitCapture(memory, repo_path=worktree).install_hooks()

    common_hooks = Path(repository.git_dir) / "hooks"
    assert "visp-memory capture" in (common_hooks / "post-commit").read_text()
    assert not list((Path(repository.git_dir) / "worktrees").rglob("post-commit"))


def test_hooks_install_into_core_hooks_path(memory, tmp_path):
    from visp_memory.capture.git import GitCapture

    repository = _git_repo(tmp_path / "repo")
    with repository.config_writer() as writer:
        writer.set_value("core", "hooksPath", ".githooks")

    capture = GitCapture(memory, repo_path=repository.working_tree_dir)
    capture.install_hooks()

    custom = Path(repository.working_tree_dir) / ".githooks"
    assert "visp-memory capture" in (custom / "post-commit").read_text()
    assert not (Path(repository.git_dir) / "hooks" / "post-commit").exists()
    assert capture.uninstall_hooks() == {"post-commit": True, "post-merge": True}
    assert not (custom / "post-commit").exists()


@pytest.mark.parametrize(
    ("message", "category", "clean"),
    [
        ("feat!: drop python 3.9", "feature_added", "drop python 3.9"),
        ("fix(api)!: reject empty ids", "bug_fixed", "reject empty ids"),
        ("WIP: fix the flaky login test", "bug_fixed", "WIP: fix the flaky login test"),
        ("Note: nothing to see here", "note", "Note: nothing to see here"),
        ("docs: explain setup", "note", "explain setup"),
    ],
)
def test_conventional_commit_parsing_only_trusts_known_types(message, category, clean):
    from visp_memory.capture.git import GitCapture

    capture = GitCapture.__new__(GitCapture)
    assert capture._parse_commit_message(message) == (category, clean)


def test_capture_manifest_write_is_atomic(memory, monkeypatch):
    manifest = CaptureManifest(memory)
    manifest.record("git_commit", "abc", "hash-1", ["m1"])
    before = manifest.path.read_text(encoding="utf-8")

    def broken_dump(*args, **kwargs):
        raise OSError("disk full")

    manifest.data["entries"]["x"] = {"content_hash": "y"}
    monkeypatch.setattr("visp_memory.capture.git.os.replace", broken_dump)
    with pytest.raises(OSError):
        manifest._write()

    # The previous manifest is intact and no temp file is left behind.
    assert manifest.path.read_text(encoding="utf-8") == before
    assert json.loads(before)["entries"]
    assert [p.name for p in manifest.path.parent.iterdir() if p.suffix == ".tmp"] == []


# --- bootstrap --------------------------------------------------------------------


def test_bootstrap_rerun_does_not_duplicate_warnings_or_decisions(memory, tmp_path):
    from visp_memory.capture.bootstrap import bootstrap_project

    repository = _git_repo(tmp_path / "repo")
    _commit(repository, "src/a.py", "1", "feat: switch cache to LRU because the old one leaked")
    _commit(repository, "src/b.py", "2", 'Revert "feat: add the experimental scheduler"')
    for index in range(3):
        _commit(repository, "src/c.py", str(index), f"fix: handle race in worker pool {index}")

    first = bootstrap_project(memory, repository.working_tree_dir, include_instructions=False)
    second = bootstrap_project(memory, repository.working_tree_dir, include_instructions=False)

    assert (first.warnings, first.decisions) == (2, 1)
    assert (second.warnings, second.decisions, second.memory_ids) == (0, 0, [])
    assert len(memory.semantic.get_warnings()) == 2

    # A further fix changes the fragile-file count, which is new information.
    _commit(repository, "src/c.py", "x", "fix: handle race in worker pool again")
    third = bootstrap_project(memory, repository.working_tree_dir, include_instructions=False)
    assert (third.warnings, third.decisions) == (1, 0)


# --- instruction ingestion --------------------------------------------------------


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unsupported")
def test_instruction_ingest_ignores_symlinks_that_leave_the_project(memory, tmp_path):
    secret = tmp_path / "outside" / "credentials"
    secret.parent.mkdir()
    secret.write_text(
        "[default]\naws_secret_access_key = AKIAEXAMPLESECRETVALUE1234567890\n",
        encoding="utf-8",
    )
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    try:
        os.symlink(secret, repo / "AGENTS.md")
    except OSError:
        pytest.skip("cannot create symlinks here")
    inside = repo / "docs" / "claude.md"
    inside.write_text("# Rules\n\nAlways run the full test suite before pushing.\n")
    os.symlink(inside, repo / "CLAUDE.md")  # in-repo symlink stays allowed

    assert discover_instruction_files(repo) == [repo / "CLAUDE.md"]
    report = ingest_instructions(memory, root=repo)
    assert report.files == ["CLAUDE.md"]
    stored = memory._storage.list_memories(
        layer="semantic", repo_id="audit", status="all", limit=10
    )
    assert not any("aws_secret" in row["content"] for row in stored)


def test_split_sections_ignores_comments_in_code_fences_and_bare_hashes():
    text = (
        "# Setup\n\n"
        "```bash\n"
        "# install the dependencies first\n"
        "pip install -e .\n"
        "```\n\n"
        "~~~\n## not a heading either\n~~~\n\n"
        "#\nnext line is not a title\n\n"
        "## Testing\n\nRun pytest.\n"
    )

    titles = [title for title, _body in split_sections(text)]

    assert titles == ["Setup", "Testing"]
    setup_body = split_sections(text)[0][1]
    assert "# install the dependencies first" in setup_body
    assert "next line is not a title" in setup_body
