import logging
from unittest.mock import Mock

from visp_memory.quality.conflict import ConflictDetector


def test_conflict_detection():
    storage = Mock()
    client = Mock()

    detector = ConflictDetector(storage, llm_client=client)

    # Mock relevant memories
    relevant = [{"id": "1", "content": "Sky is blue"}, {"id": "2", "content": "Water is wet"}]

    # Mock LLM response for conflict
    client.completion.return_value = """
    {
        "conflict": true,
        "reason": "Direct contradiction",
        "conflicting_ids": ["1"]
    }
    """

    result = detector.detect_conflicts("Sky is green", relevant)

    assert result.has_conflict
    assert result.conflict["conflict"] is True
    assert result.conflict["conflicting_ids"] == ["1"]

    # Verify prompt contains relevant info
    call_args = client.completion.call_args
    prompt = call_args[0][0]
    assert "Sky is blue" in prompt
    assert "Sky is green" in prompt


def test_no_conflict():
    storage = Mock()
    client = Mock()
    detector = ConflictDetector(storage, llm_client=client)

    client.completion.return_value = '{"conflict": false}'

    result = detector.detect_conflicts("Grass is green", [{"id": "1", "content": "Sky is blue"}])
    # Detection ran and found nothing. That is a real answer, and distinct from
    # the cases below where detection could not reach one.
    assert result.determined
    assert not result.has_conflict


def test_conflict_json_error():
    """Test handling of invalid JSON from LLM."""
    storage = Mock()
    client = Mock()
    detector = ConflictDetector(storage, llm_client=client)

    client.completion.return_value = "Not JSON"

    result = detector.detect_conflicts("Sky is green", [{"id": "1", "content": "Sky is blue"}])
    # Unparseable output is not a clean bill of health. This used to return the
    # same None as "no conflict", so malformed responses let contradictory
    # knowledge through (MG-026).
    assert not result.determined
    assert not result.has_conflict


def test_conflict_exception(caplog):
    """Test handling of client exceptions.

    A detector that raised did not clear the content: the verdict is
    undetermined, not clear, and the failure stays observable in the log.
    """
    storage = Mock()
    client = Mock()
    detector = ConflictDetector(storage, llm_client=client)

    client.completion.side_effect = Exception("API Error")

    with caplog.at_level(logging.WARNING, logger="visp_memory.quality.conflict"):
        result = detector.detect_conflicts("Sky is green", [{"id": "1", "content": "Sky is blue"}])
    assert not result.determined
    assert "API Error" in (result.reason or "")

    messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("Conflict detection failed" in m and "API Error" in m for m in messages)


def test_no_relevant_memories():
    """Test short-circuit when no memories provided."""
    detector = ConflictDetector(Mock())
    result = detector.detect_conflicts("Test", [])
    # Nothing to contradict is a determined answer, even with no detector.
    assert result.determined
    assert not result.has_conflict
