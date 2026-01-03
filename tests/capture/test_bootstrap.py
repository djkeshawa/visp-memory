"""Tests for first-run project bootstrap.

The value of the bootstrap is *selectivity*, so the assertions focus on what it refuses
to import as much as on what it keeps.
"""

import pytest

from visp_memory import Memory, MemoryConfig
from visp_memory.capture.bootstrap import (
    BootstrapReport,
    bootstrap_project,
    is_low_signal,
)

git = pytest.importorskip("git", reason="git capture requires GitPython")


@pytest.fixture
def memory(tmp_path):
    config = MemoryConfig()
    config.storage.data_dir = tmp_path / "memory"
    config.embedding.provider = "noop"
    return Memory(config=config)


class TestLowSignalDetection:
    @pytest.mark.parametrize(
        "message",
        [
            "chore: bump version to 1.2.3",
            "chore(deps): update lockfile",
            "style: satisfy ruff",
            "ci: retry flaky job",
            "build: pin wheel",
            "v1.2.3",
            "Merge branch 'develop'",
            "wip",
            "fixup! earlier commit",
            "Initial commit",
            "typo",
        ],
    )
    def test_rejects_mechanical_commits(self, message):
        assert is_low_signal(message)

    @pytest.mark.parametrize(
        "message",
        [
            "fix: race condition in the session cache",
            "feat: add cross-repo dependency resolution",
            "Revert \"use optimistic locking\"",
            "refactor storage layer to drop the global lock",
        ],
    )
    def test_keeps_meaningful_commits(self, message):
        assert not is_low_signal(message)

    def test_empty_message_is_low_signal(self):
        assert is_low_signal("")
        assert is_low_signal("   ")


class TestReport:
    def test_headline_is_honest_when_nothing_found(self):
        assert "No memories imported" in BootstrapReport().headline()

    def test_headline_reports_span_in_months(self):
        report = BootstrapReport(commits_scanned=50, events=10, span_days=180)
        headline = report.headline()
        assert "10 memories" in headline
        assert "50 commits" in headline
        assert "6 months" in headline

    def test_detail_lines_are_singular_when_count_is_one(self):
        report = BootstrapReport(instruction_sections=1, warnings=1, events=1, decisions=1)
        lines = " | ".join(report.detail_lines())
        assert "1 section from" in lines
        assert "1 warning " in lines
        assert "1 event " in lines
        assert "1 decision " in lines
        assert "sections" not in lines

    def test_detail_lines_report_what_was_skipped(self):
        """Silent truncation reads as "covered everything" when it did not."""
        report = BootstrapReport(events=3, skipped_low_signal=40)
        assert any("40 low-signal commits skipped" in line for line in report.detail_lines())


def _commit(repo, path, content, message):
    file_path = repo.working_dir + "/" + path
    import os

    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w") as handle:
        handle.write(content)
    repo.index.add([path])
    return repo.index.commit(message)


@pytest.fixture
def seeded_repo(tmp_path):
    repo = git.Repo.init(tmp_path)
    repo.config_writer().set_value("user", "name", "Test").release()
    repo.config_writer().set_value("user", "email", "test@example.com").release()
    return repo


class TestBootstrapProject:
    def test_reports_missing_git_repository_without_raising(self, tmp_path, memory):
        report = bootstrap_project(memory, tmp_path / "not-a-repo")
        assert report.total == 0
        assert report.errors

    def test_skips_low_signal_commits(self, seeded_repo, memory):
        _commit(seeded_repo, "a.py", "x = 1", "feat: add the scheduler")
        _commit(seeded_repo, "a.py", "x = 2", "chore: bump version to 0.2.0")
        _commit(seeded_repo, "a.py", "x = 3", "style: reformat")

        report = bootstrap_project(memory, seeded_repo.working_dir, include_instructions=False)

        assert report.skipped_low_signal == 2
        assert report.events == 1

    def test_reverts_become_warnings(self, seeded_repo, memory):
        _commit(seeded_repo, "a.py", "x = 1", "feat: use optimistic locking")
        _commit(seeded_repo, "a.py", "x = 2", 'Revert "use optimistic locking"')

        report = bootstrap_project(memory, seeded_repo.working_dir, include_instructions=False)

        assert report.warnings >= 1
        warnings = memory.semantic.get_warnings()
        assert any("reverted" in w["content"].lower() for w in warnings)

    def test_commits_that_explain_themselves_become_decisions(self, seeded_repo, memory):
        _commit(
            seeded_repo,
            "a.py",
            "x = 1",
            "Use a queue for uploads\n\nWe chose this because direct writes "
            "saturated the connection pool under load.",
        )

        report = bootstrap_project(memory, seeded_repo.working_dir, include_instructions=False)

        assert report.decisions == 1

    def test_repeatedly_fixed_code_files_become_warnings(self, seeded_repo, memory):
        for i in range(3):
            _commit(seeded_repo, "src/flaky.py", f"x = {i}", f"fix: correct the off-by-one {i}")

        report = bootstrap_project(memory, seeded_repo.working_dir, include_instructions=False)

        warnings = memory.semantic.get_warnings()
        assert any("src/flaky.py" in w["content"] for w in warnings), report.as_dict()

    def test_documentation_is_not_called_fragile(self, seeded_repo, memory):
        """Doc churn is not fragility; mining this repo wrongly flagged README.md."""
        for i in range(4):
            _commit(seeded_repo, "README.md", f"docs {i}", f"fix: correct a typo {i}")

        bootstrap_project(memory, seeded_repo.working_dir, include_instructions=False)

        warnings = memory.semantic.get_warnings()
        assert not any("README.md" in w["content"] for w in warnings)

    def test_rerunning_does_not_duplicate(self, seeded_repo, memory):
        _commit(seeded_repo, "a.py", "x = 1", "feat: add the scheduler")

        first = bootstrap_project(memory, seeded_repo.working_dir, include_instructions=False)
        second = bootstrap_project(memory, seeded_repo.working_dir, include_instructions=False)

        assert first.events == 1
        assert second.events == 0  # content-hashed by the capture manifest
