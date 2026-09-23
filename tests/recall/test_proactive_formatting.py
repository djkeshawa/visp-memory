"""Bounded proactive formatting preserves complete evidence units."""

import json

from visp_memory.recall.proactive import ProactiveRecall


def test_markdown_omits_a_large_unit_instead_of_cutting_its_qualification():
    recall = object.__new__(ProactiveRecall)
    warning = (
        "The archived billing procedure may run only after a verified backup; "
        "never run it on production."
    )
    output = recall.format_injection(
        {"warnings": [{"id": "w1", "content": warning}]}, max_length=40
    )

    assert warning not in output
    assert "omitted" in output


def test_json_budget_keeps_complete_units_and_parseable_omission_status():
    recall = object.__new__(ProactiveRecall)
    warning = "Protect production data. " * 20
    output = recall.format_injection(
        {"warnings": [{"id": "w1", "content": warning}]},
        format="json",
        max_length=90,
    )

    assert len(output) <= 90
    payload = json.loads(output)
    assert payload["truncated"] is True
    assert payload["omitted"]["warnings"] == 1


def test_json_budget_too_small_for_status_returns_a_valid_empty_envelope():
    recall = object.__new__(ProactiveRecall)

    output = recall.format_injection(
        {"warnings": [{"id": "w1", "content": "warning"}]},
        format="json",
        max_length=2,
    )

    assert output == "{}"
    assert json.loads(output) == {}


def test_json_budget_below_the_minimum_envelope_is_rejected():
    recall = object.__new__(ProactiveRecall)

    try:
        recall.format_injection({"warnings": []}, format="json", max_length=1)
    except ValueError as error:
        assert "at least 2" in str(error)
    else:
        raise AssertionError("expected an explicit minimum JSON budget")


def test_markdown_budget_applies_to_the_current_direction_too():
    recall = object.__new__(ProactiveRecall)
    output = recall.format_injection(
        {"active_intent": "x" * 2000}, max_length=24
    )

    assert len(output) <= 24
    assert "omitted" in output


def test_complete_warning_retains_its_source_memory_id():
    recall = object.__new__(ProactiveRecall)
    output = recall.format_injection({"warnings": [{
        "id": "warning-42", "content": "Never publish without verified approval.",
    }]}, max_length=200)
    assert "[warning-42]" in output
    assert "Never publish without verified approval." in output
