from unittest.mock import Mock

from llm_memory.quality.conflict import ConflictDetector


def test_conflict_detection():
    storage = Mock()
    client = Mock()

    detector = ConflictDetector(storage, llm_client=client)

    # Mock relevant memories
    relevant = [
        {"id": "1", "content": "Sky is blue"},
        {"id": "2", "content": "Water is wet"}
    ]

    # Mock LLM response for conflict
    client.completion.return_value = """
    {
        "conflict": true,
        "reason": "Direct contradiction",
        "conflicting_ids": ["1"]
    }
    """

    result = detector.detect_conflicts("Sky is green", relevant)

    assert result is not None
    assert result["conflict"] is True
    assert result["conflicting_ids"] == ["1"]

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
    assert result is None

def test_conflict_json_error():
    """Test handling of invalid JSON from LLM."""
    storage = Mock()
    client = Mock()
    detector = ConflictDetector(storage, llm_client=client)

    client.completion.return_value = "Not JSON"

    result = detector.detect_conflicts("Sky is green", [{"id": "1", "content": "Sky is blue"}])
    assert result is None

def test_conflict_exception():
    """Test handling of client exceptions."""
    storage = Mock()
    client = Mock()
    detector = ConflictDetector(storage, llm_client=client)

    client.completion.side_effect = Exception("API Error")

    result = detector.detect_conflicts("Sky is green", [{"id": "1", "content": "Sky is blue"}])
    assert result is None

def test_no_relevant_memories():
    """Test short-circuit when no memories provided."""
    detector = ConflictDetector(Mock())
    result = detector.detect_conflicts("Test", [])
    assert result is None
