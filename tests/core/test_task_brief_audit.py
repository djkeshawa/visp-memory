"""Task brief regressions: budget truncation keeps Unknowns; prose is not a file path."""

import pytest

from visp_memory.core.storage import LocalStorage
from visp_memory.core.task_brief import TaskMemoryBriefCompiler
from visp_memory.core.tokens import estimate_tokens

LONG_TASK = (
    "Refactor the payment retry scheduler so that idempotency keys survive restarts " * 3
)


@pytest.mark.parametrize("budget", [64, 90, 120])
def test_over_budget_header_keeps_the_whole_unknowns_section(tmp_path, budget):
    brief = TaskMemoryBriefCompiler(LocalStorage(tmp_path)).prepare(
        LONG_TASK, repo_id="r", token_budget=budget
    )

    assert brief["truncated"] is True
    assert estimate_tokens(brief["context"]) <= budget
    assert brief["unknowns"]
    unknowns_block = "## Unknowns\n" + "\n".join(f"- {u}" for u in brief["unknowns"])
    assert brief["context"].endswith(unknowns_block)
    assert brief["context"].startswith("# Task Memory Brief")


def test_truncation_drops_whole_lines_and_only_cuts_the_task_line():
    text = "\n".join([
        "# Task Memory Brief", "", "Task: " + "word " * 200, "Project: r", "Action: refactor",
        "", "## Unknowns", "- Nothing matched.",
    ])

    result, truncated = TaskMemoryBriefCompiler._truncate_to_budget(text, 80)

    assert truncated
    assert estimate_tokens(result) <= 80
    assert result.endswith("\n\n## Unknowns\n- Nothing matched.")
    for line in result.split("\n"):
        assert line in text.split("\n") or line == "..." or (
            line.startswith("Task: ") and line.endswith("...")
        )


@pytest.mark.parametrize(
    "task",
    [
        "Fix the read/write race in the cache layer",
        "Support and/or filters",
        "Harden the TCP/IP stack",
        "About 1/2 of runs fail",
        "See https://example.com/docs/guide.py for details",
    ],
)
def test_prose_with_slashes_is_not_inferred_as_a_file(task):
    profile = TaskMemoryBriefCompiler(None)._task_profile(task, files=[], symbols=[])

    assert profile["files"] == []


def test_real_paths_are_still_inferred():
    profile = TaskMemoryBriefCompiler(None)._task_profile(
        "Edit src/auth.py, docs/guides/ and src/auth/handlers; see .github/workflows/ci.yml.",
        files=[],
        symbols=[],
    )

    assert profile["files"] == [
        "src/auth.py", "docs/guides/", "src/auth/handlers", ".github/workflows/ci.yml",
    ]
