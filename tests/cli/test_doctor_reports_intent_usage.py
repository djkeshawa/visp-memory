"""`visp-memory doctor` says when a store has memories and no intents (LC-87).

The intent layer shipped, worked, and was never used: a full treatment-arm run
finished with `intents: 0` against 12 memories and nothing anywhere called that
a finding. `stats` printed the zero as just another number.

The report is a description of the store. It never says the work was wrong,
out of scope, or unfinished - memory does not decide those things.
"""

import json

import pytest
from typer.testing import CliRunner

from visp_memory.interfaces.cli import app

runner = CliRunner()


def _intent_usage():
    result = runner.invoke(app, ["doctor", "--format", "json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["intent_usage"]


def test_a_store_with_memories_and_no_intents_is_reported_as_never_used(cli_env):
    runner.invoke(app, ["init", "--repo", "alpha", "--no-mine"])
    runner.invoke(app, ["record", "A thing that happened"])

    report = _intent_usage()

    assert report["status"] == "never_used"
    assert report["memories"] == 1
    assert report["total_intents"] == 0
    assert "0 intents" in report["headline"]


def test_the_never_used_finding_names_the_verbs_that_would_fix_it(cli_env):
    runner.invoke(app, ["init", "--repo", "alpha", "--no-mine"])
    runner.invoke(app, ["record", "A thing that happened"])

    remediation = _intent_usage()["remediation"]

    for verb in ("goal", "focus", "working", "done"):
        assert f"visp-memory {verb}" in remediation, verb
    assert "visp-memory intent list" in remediation


def test_the_never_used_finding_does_not_claim_an_intent_would_grant_anything(cli_env):
    """Rule 9: memory is non-authoritative. The remediation must not imply otherwise."""
    runner.invoke(app, ["init", "--repo", "alpha", "--no-mine"])
    runner.invoke(app, ["record", "A thing that happened"])

    remediation = _intent_usage()["remediation"]

    assert "grants no scope or permission" in remediation


def test_a_store_that_has_set_a_goal_is_no_longer_reported_as_never_used(cli_env):
    runner.invoke(app, ["init", "--repo", "alpha", "--no-mine"])
    runner.invoke(app, ["record", "A thing that happened"])
    runner.invoke(app, ["goal", "Ship the intent lifecycle"])

    report = _intent_usage()

    assert report["status"] == "in_use"
    assert report["active_intents"] == 1
    assert report["remediation"] is None


def test_the_lifecycle_verbs_each_move_the_store_off_never_used(cli_env):
    """goal / focus / working all set an intent; any one of them is adoption."""
    runner.invoke(app, ["init", "--repo", "alpha", "--no-mine"])
    runner.invoke(app, ["record", "A thing that happened"])
    runner.invoke(app, ["focus", "the intent layer", "--avoid", "unrelated refactors"])
    runner.invoke(app, ["working", "wiring the doctor check"])

    report = _intent_usage()

    assert report["status"] == "in_use"
    assert report["active_intents"] == 2


def test_an_empty_store_is_not_reported_as_a_finding(cli_env):
    """Zero intents only matters once something has been recorded."""
    runner.invoke(app, ["init", "--repo", "alpha", "--no-mine"])

    report = _intent_usage()

    assert report["status"] == "empty"
    assert report["remediation"] is None


def test_a_directory_with_no_store_is_reported_as_having_no_store(cli_env):
    report = _intent_usage()

    assert report["status"] == "no_store"
    assert report["remediation"] is None


def test_the_text_output_shows_the_finding_and_what_to_run(cli_env):
    runner.invoke(app, ["init", "--repo", "alpha", "--no-mine"])
    runner.invoke(app, ["record", "A thing that happened"])

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0, result.output
    # Rich hard-wraps to the terminal width, so compare on collapsed whitespace.
    rendered = " ".join(result.output.split())
    assert "Intent lifecycle" in rendered
    assert "the intent lifecycle has never been used in this store" in rendered
    assert "visp-memory goal" in rendered


def test_reading_the_store_for_diagnostics_does_not_create_an_intent(cli_env):
    """A diagnostic that changed what it measured could never report it."""
    runner.invoke(app, ["init", "--repo", "alpha", "--no-mine"])
    runner.invoke(app, ["record", "A thing that happened"])

    first = _intent_usage()
    second = _intent_usage()

    assert first["status"] == "never_used"
    assert second == first


@pytest.mark.parametrize(
    "headline",
    ["unable to open [db] file", "broken [/tag] marker"],
)
def test_a_bracketed_error_message_survives_rendering(cli_env, monkeypatch, headline):
    """The unreadable-store headline is str(exc); rich would eat or reject it."""
    from visp_memory.core import intent_usage
    from visp_memory.interfaces import cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "check_intent_usage",
        lambda config: intent_usage.IntentUsageReport(
            status=intent_usage.STATUS_UNREADABLE, detail=headline
        ),
    )
    runner.invoke(app, ["init", "--repo", "alpha", "--no-mine"])

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert headline in " ".join(result.output.split())
