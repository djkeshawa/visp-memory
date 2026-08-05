"""P13 — no MCP tool may report a save the engine has quarantined.

The CLI defect had a twin here, and this is the surface that matters more.
`memory_record` in a project that never ran `visp-memory init` answered
"Recorded event (ID: ...)" for a row written to the reserved quarantine bucket,
and the matching `memory_recall` then raised.

A human at a terminal has some chance of noticing that a later search comes up
empty. An agent has none: it receives an id, treats the write as durable, and
carries on with a memory that nothing will ever return to it. The whole product
claim is recall-before-work — a write the engine has agreed to forget is worse
than a refused one, because the refusal is actionable and the false success is
not.

Parameterised over every write tool on purpose. The CLI fix originally guarded
only the command named in the bug report and left nine siblings broken; a test
that named only `memory_record` would repeat that exactly.
"""

import pathlib
import tempfile

import pytest

from visp_memory import Memory, MemoryConfig
from visp_memory.interfaces.mcp import _WRITE_TOOLS, _refuse_unscoped_write

# Minimal valid arguments per write tool — enough to reach the guard.
WRITE_TOOL_ARGS = {
    "memory_record": {"event": "something happened"},
    "memory_decision": {"what": "rotate the keys", "why": "the old one leaked"},
    "memory_learn": {"knowledge": "the retry limit is five"},
    "memory_warn": {"file_path": "src/auth.py", "warning": "fragile"},
    "memory_issue": {"issue": "login is broken"},
    "memory_goal": {"description": "ship the login page"},
    "memory_working_on": {"task": "refactoring auth"},
    "memory_done": {"intent_id": "intent-1"},
    "memory_update_intent": {"intent_id": "intent-1"},
    "memory_close_intent": {"intent_id": "intent-1"},
}


def _memory(repo_id):
    directory = tempfile.mkdtemp()
    config = MemoryConfig(repo_id=repo_id)
    config.storage.data_dir = pathlib.Path(directory) / "data"
    config.embedding.provider = "noop"
    return Memory(config=config)


def test_every_write_tool_has_arguments_here():
    """Guards the guard: a new write tool must not slip past unparameterised."""
    assert set(WRITE_TOOL_ARGS) == set(_WRITE_TOOLS), (
        "The write-tool list and this test's argument table disagree. Whichever is stale, "
        "the effect is that some write tool is no longer being checked."
    )


@pytest.mark.parametrize("tool", sorted(_WRITE_TOOLS))
def test_write_tool_is_refused_without_a_scope(tool):
    refusal = _refuse_unscoped_write(tool, dict(WRITE_TOOL_ARGS[tool]), _memory(None))

    assert refusal is not None, (
        f"{tool} was allowed to write with no repository scope. Whatever it stores is "
        "quarantined, and the agent is told it succeeded."
    )
    assert "repo_id" in refusal or "init" in refusal, (
        f"{tool} refused without naming the repair, so the model cannot act on it: {refusal!r}"
    )


@pytest.mark.parametrize("tool", sorted(_WRITE_TOOLS))
def test_write_tool_proceeds_with_a_configured_scope(tool):
    """The converse. Refusing everything would satisfy the test above."""
    assert _refuse_unscoped_write(tool, dict(WRITE_TOOL_ARGS[tool]), _memory("demo")) is None


@pytest.mark.parametrize("tool", sorted(_WRITE_TOOLS))
def test_an_explicit_repo_id_argument_is_enough(tool):
    """A caller may supply the scope per call rather than via config."""
    args = dict(WRITE_TOOL_ARGS[tool], repo_id="demo")
    assert _refuse_unscoped_write(tool, args, _memory(None)) is None


def test_the_reserved_bucket_is_not_an_acceptable_scope():
    """Naming the quarantine explicitly must not be a way into it."""
    args = dict(WRITE_TOOL_ARGS["memory_record"], repo_id="__visp_unscoped__")
    assert _refuse_unscoped_write("memory_record", args, _memory(None)) is not None


def test_read_tools_are_untouched():
    """The guard must not block reads; they refuse on their own terms."""
    for tool in ["memory_recall", "memory_remember", "memory_stats", "memory_context"]:
        assert _refuse_unscoped_write(tool, {}, _memory(None)) is None, (
            f"{tool} is a read and must not be caught by the write guard"
        )
