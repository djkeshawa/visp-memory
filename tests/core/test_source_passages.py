"""Source validation preserves attribution and does not manufacture facts."""

import pytest

from visp_memory.core.source_passages import validate_source_quote


def test_quote_keeps_speaker_question_clock_and_verbatim_lineage():
    source = (
        "Session date: 2024/05/10\nuser: Where could we hold the workshop?\n"
        "assistant: I suggest Cedar Hall. It has a quiet room.\n"
        "user: I have not booked it yet."
    )
    result = validate_source_quote(source, "It has a quiet room.")
    assert result["speaker"] == "assistant"
    assert "Where could we hold the workshop?" in result["context"]
    assert "I suggest Cedar Hall." in result["context"]
    assert "Session date: 2024/05/10" in result["context"]
    assert all(source[s["start"]:s["end"]] == s["text"] for s in result["passage_spans"])


@pytest.mark.parametrize("quote", [
    "I suggest Cedar Hall.\nuser: I have not booked it yet.",
    "Cedar Hall",
    "I booked Cedar Hall.",
])
def test_quote_rejects_mixed_speakers_clipped_or_unsupported_text(quote):
    source = "assistant: I suggest Cedar Hall.\nuser: I have not booked it yet."
    with pytest.raises(ValueError):
        validate_source_quote(source, quote)


def test_planned_action_retains_negation_and_relative_date():
    source = "Session date: 2024/05/10\nuser: I plan to book tomorrow, but have not paid."
    result = validate_source_quote(source, "I plan to book tomorrow, but have not paid.")
    assert result["speaker"] == "user"
    assert result["quote"] == "I plan to book tomorrow, but have not paid."
    assert "2024/05/11" not in result["context"]


def test_decimal_point_is_not_a_complete_sentence_boundary():
    with pytest.raises(ValueError, match="clips"):
        validate_source_quote("user: I paid $5.50, not $5.", "I paid $5.")


def test_assistant_evidence_retains_a_complete_bounded_long_question():
    from visp_memory.core.source_passages import attributed_passage

    question = (
        "user: I viewed a studio on May 10 but my offer was rejected. "
        + "The application included a reference and a deposit. " * 9
        + "Which documents should I keep?\n"
    )
    source = question + "assistant: Keep the offer letter and the deposit receipt."
    passage = attributed_passage({"content": source}, len(question), len(source))
    assert question in passage["content"]
    assert all(source[s["start"]:s["end"]] == s["text"] for s in passage["passage_spans"])


def test_quote_in_continued_turn_uses_explicit_source_speaker():
    source = (
        "Session date: 2024/05/10\n"
        "Excerpt starts at character 4096; preceding speaker: assistant\n"
        "Keep the receipt for your deposit.\nuser: Thanks."
    )
    result = validate_source_quote(source, "Keep the receipt for your deposit.")
    assert result["speaker"] == "assistant"
    assert "preceding speaker: assistant" in result["context"]


def test_speaker_is_not_inferred_from_unstructured_body_text():
    source = "A note mentions preceding speaker: assistant. Keep the receipt."
    result = validate_source_quote(source, "Keep the receipt.")
    assert result["speaker"] == "as recorded in source"


def test_attributed_passage_keeps_nested_section_headings_with_exact_offsets():
    from visp_memory.core.source_passages import attributed_passage

    source = (
        "Session date: 2024/05/10\n"
        "assistant: Offices:\n"
        "2. South office:\n"
        "  - Rent:\n"
        "    * $900.\n"
        "  - Contact: south@example.test\n"
    )
    start = source.index("    * $900.")
    passage = attributed_passage({"content": source}, start, len(source))

    assert "Offices:" in passage["content"]
    assert "2. South office:" in passage["content"]
    assert "  - Rent:" in passage["content"]
    assert all(source[span["start"] : span["end"]] == span["text"]
               for span in passage["passage_spans"])


def test_attributed_passage_keeps_markdown_ancestors_without_sibling_sections():
    from visp_memory.core.source_passages import attributed_passage

    source = (
        "assistant: # Project A\n"
        "Introductory prose for the project.\n"
        "## Costs\n"
        "* Deposit: $25.\n"
        "## Schedule\n"
        "* Monday.\n"
    )
    start = source.index("* Monday.")
    passage = attributed_passage({"content": source}, start, len(source))

    assert "# Project A" in passage["content"]
    assert "## Schedule" in passage["content"]
    assert "## Costs" not in passage["content"]


def test_passage_starting_at_heading_does_not_borrow_previous_sibling():
    from visp_memory.core.source_passages import attributed_passage

    source = "assistant: # Offices\n1. North:\nRent $500.\n2. South:\nRent $900."
    start = source.index("2. South:")
    selected = attributed_passage({"content": source}, start, len(source))
    assert "# Offices" in selected["content"]
    assert "1. North:" not in selected["content"]
    assert "2. South:" in selected["content"]
