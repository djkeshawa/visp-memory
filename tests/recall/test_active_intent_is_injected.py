"""The injected context block carries the current direction (LC-87).

The block an assistant reads every turn carried warnings, bugs, decisions and
knowledge, and nothing about what the work was *for*. An agent that is told its
own current goal does not spend the turn re-deriving it.

One line, hard-capped: the block is paid for on every turn, so this is the whole
budget the direction gets. It is direction, not permission — nothing here grants
scope, certifies evidence, or says a task is finished.
"""

import tempfile
from pathlib import Path

import pytest

from visp_memory import Memory, MemoryConfig
from visp_memory.recall.proactive import ACTIVE_INTENT_MAX_CHARS, ProactiveRecall


@pytest.fixture
def memory():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        config = MemoryConfig(repo_id="repo-a")
        config.storage.data_dir = Path(tmpdir)
        config.embedding.provider = "noop"
        yield Memory(config=config)


def _injected(memory, **kwargs) -> str:
    recall = ProactiveRecall(memory)
    if "task" in kwargs:
        context = recall.find_relevant_for_task(kwargs["task"], files=kwargs.get("files"))
    else:
        context = recall.on_file_open(kwargs["file"])
    return recall.format_injection(context)


def test_a_task_set_with_working_appears_in_the_injected_block(memory):
    memory.working_on("Fixing the token refresh race", files=["auth/token.py"])

    injected = _injected(memory, task="refresh tokens")

    assert "Current direction" in injected
    assert "Fixing the token refresh race" in injected


def test_a_goal_appears_in_the_block_injected_when_a_file_is_opened(memory):
    memory.goal("Ship the auth rewrite")

    injected = _injected(memory, file="auth/token.py")

    assert "Ship the auth rewrite" in injected


def test_the_stored_verb_prefix_is_not_shown_to_the_reader(memory):
    """`working` stores "WORKING ON: ..."; the prefix is storage detail."""
    memory.working_on("Fixing the token refresh race")

    injected = _injected(memory, task="refresh tokens")

    assert "WORKING ON:" not in injected


def test_the_live_task_wins_over_a_standing_focus(memory):
    """Told both, an assistant needs the narrower one."""
    memory.intent.set_focus("Bug fixes only", avoid=["Refactoring"], repo_id="repo-a")
    memory.working_on("Fixing the token refresh race")

    injected = _injected(memory, task="refresh tokens")

    assert "Fixing the token refresh race" in injected
    assert "Bug fixes only" not in injected


def test_the_focus_is_used_when_no_task_is_live(memory):
    memory.intent.set_focus("Bug fixes only", avoid=["Refactoring"], repo_id="repo-a")

    injected = _injected(memory, task="anything")

    assert "Bug fixes only" in injected


def test_a_store_with_no_intent_adds_no_direction_line(memory):
    """Nothing set means nothing spent."""
    memory.warn("auth/token.py", "fragile refresh path")

    injected = _injected(memory, file="auth/token.py")

    assert "Current direction" not in injected


def test_the_direction_costs_at_most_one_short_line(memory):
    """The token budget is the reason this is a line and not a section."""
    memory.working_on("x" * 500)

    injected = _injected(memory, task="anything")

    direction = [line for line in injected.splitlines() if "Current direction" in line]
    assert len(direction) == 1
    assert len(direction[0]) <= ACTIVE_INTENT_MAX_CHARS + 40


def test_the_direction_line_never_says_the_intent_grants_or_completes_anything(memory):
    """Rule 9: memory is non-authoritative. The line states direction, nothing more."""
    memory.working_on("Fixing the token refresh race")

    injected = _injected(memory, task="refresh tokens")

    lowered = injected.lower()
    for claim in ("approved", "authorized", "authorised", "permitted", "in scope", "ready to"):
        assert claim not in lowered


def test_an_unreadable_intent_layer_yields_no_direction_rather_than_an_error(memory):
    """The direction is a nicety; it must never be what breaks an injection."""
    recall = ProactiveRecall(memory)

    def _boom(*args, **kwargs):
        raise RuntimeError("intent layer unavailable")

    recall.memory.intent.get_active = _boom

    assert recall.active_intent() is None


def test_a_task_reported_done_stops_leading_the_injected_block(memory):
    """`done` records an outcome and deliberately leaves status alone.

    Without reading that history the block would keep naming a task the user has
    already reported finished, on every turn, with no way to clear it.
    """
    memory.working_on("Fixing the token refresh race")
    memory.done()

    injected = _injected(memory, task="refresh tokens")

    assert "Fixing the token refresh race" not in injected


def test_a_goal_still_shows_after_an_unrelated_task_is_reported_done(memory):
    memory.goal("Ship the auth rewrite")
    memory.working_on("Fixing the token refresh race")
    memory.done()

    injected = _injected(memory, task="refresh tokens")

    assert "Ship the auth rewrite" in injected


def test_a_constraint_is_never_rendered_as_the_current_direction(memory):
    """A CONSTRAINT says what NOT to do; under "direction" it reads inverted."""
    memory.intent.add_constraint("Do not modify production config files", repo_id="repo-a")

    injected = _injected(memory, task="anything")

    assert "Current direction" not in injected


def test_a_long_goal_is_cut_on_a_word_boundary_and_marked(memory):
    """A mid-word cut of an instruction an LLM reads can invert its meaning."""
    memory.goal(
        "Do not delete the legacy aider adapter until the served pair check "
        "passes on the published Kit build and the owner has confirmed it"
    )

    injected = _injected(memory, task="anything")

    direction = [line for line in injected.splitlines() if "Current direction" in line][0]
    assert direction.endswith("...")
    # Cut between words, not through one.
    assert "  " not in direction
    assert direction.replace("...", "").rstrip()[-1] != " "


def test_the_intent_layer_is_read_once_however_many_files_are_injected(memory):
    """One line must not cost one intent query per file."""
    memory.working_on("Fixing the token refresh race")
    recall = ProactiveRecall(memory)
    calls = []
    original = recall._read_active_intent

    def counted():
        calls.append(1)
        return original()

    recall._read_active_intent = counted

    recall.for_files(["auth/token.py", "auth/refresh.py", "auth/session.py"])

    assert len(calls) == 1


def test_injecting_for_several_files_carries_the_direction_too(memory):
    memory.working_on("Fixing the token refresh race")
    recall = ProactiveRecall(memory)

    context = recall.for_files(["auth/token.py", "auth/refresh.py"])

    assert context["active_intent"] == "Fixing the token refresh race"
