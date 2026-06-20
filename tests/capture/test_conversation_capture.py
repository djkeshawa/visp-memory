from unittest.mock import Mock, patch

import pytest

from llm_memory.capture.conversation import ConversationCapture
from llm_memory.core.memory import Memory


@pytest.fixture
def mock_memory():
    mock_mem = Mock(spec=Memory)
    # Mock config hierarchy
    mock_mem.config = Mock()
    mock_mem.config.capture.llm_provider = "openai"
    mock_mem.config.capture.llm_model = "gpt-4-test"
    mock_mem.config.compression.llm_provider = None  # fallback test
    mock_mem.config.repo_id = "test-repo"
    mock_mem.intent = Mock()
    return mock_mem


def test_conversation_capture_parsing(mock_memory):
    # Mock LLM Client
    mock_client = Mock()
    mock_client.completion.return_value = """
    {
      "decisions": [
        {"what": "Use Redis", "why": "Speed", "alternatives": ["Memcached"]}
      ],
      "learnings": [
        {"knowledge": "Python 3.12 is faster", "category": "fact", "importance": 0.8}
      ],
      "bugs": [
        {"description": "Race condition", "cause": "No lock", "fix": "Added mutex"}
      ],
      "tasks": [
        {"description": "Refactor auth", "status": "todo"}
      ]
    }
    """

    with patch("llm_memory.capture.conversation.create_llm_client", return_value=mock_client):
        capturer = ConversationCapture(mock_memory)

        # Test dry run first
        result_dry = capturer.parse_text("log", dry_run=True)
        assert result_dry["decisions"] == 1
        assert not mock_memory.decision.called

        # Test real run
        result = capturer.parse_text("log", dry_run=False)

        assert result["decisions"] == 1
        assert result["learnings"] == 1
        assert result["tasks"] == 1

        # Check calls
        mock_memory.decision.assert_called_with(
            what="Use Redis", why="Speed", alternatives=["Memcached"], repo_id="test-repo"
        )

        mock_memory.learn.assert_called_with(
            knowledge="Python 3.12 is faster", category="fact", importance=0.8, repo_id="test-repo"
        )

        mock_memory.record.assert_called()
        call_args = mock_memory.record.call_args[1]
        assert "Bug: Race condition" == call_args["event"]
        # "bug_found" is a valid EpisodeCategory and the cause/fix detail is
        # passed via `context` (episodic.record has no `metadata` kwarg).
        assert "bug_found" == call_args["category"]
        assert call_args["context"]["cause"] == "No lock"

        mock_memory.intent.set_goal.assert_called_with(
            goal="Refactor auth",
            repo_id="test-repo",
            context={
                "captured_as": "task",
                "source": "conversation",
                "status": "todo",
            },
        )


def test_conversation_capture_skips_unchanged_text(mock_memory, tmp_path):
    mock_memory.config.storage.data_dir = tmp_path / "memory"
    mock_memory.decision.return_value = "decision-1"
    mock_memory.learn.return_value = "learning-1"
    mock_memory.record.return_value = "bug-1"
    mock_memory.intent.set_goal.return_value = "task-1"

    mock_client = Mock()
    mock_client.completion.return_value = """
    {
      "decisions": [{"what": "Use Redis", "why": "Speed"}],
      "learnings": [],
      "bugs": [],
      "tasks": []
    }
    """

    with patch("llm_memory.capture.conversation.create_llm_client", return_value=mock_client):
        capturer = ConversationCapture(mock_memory)
        first = capturer.parse_text("same log", source="chat.json", dry_run=False)
        mock_memory.decision.reset_mock()
        second = capturer.parse_text("same log", source="chat.json", dry_run=False)

    assert first["capture_manifest"]["status"] == "changed"
    assert second["capture_manifest"]["status"] == "unchanged"
    mock_memory.decision.assert_not_called()


def test_conversation_capture_config_error(mock_memory):
    # Unset provider
    mock_memory.config.capture.llm_provider = None
    mock_memory.config.compression.llm_provider = None

    capturer = ConversationCapture(mock_memory)

    with pytest.raises(ValueError, match="No LLM provider configured"):
        capturer.parse_text("fail")
