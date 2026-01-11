import pytest
from unittest.mock import Mock, patch
from llm_memory.capture.conversation import ConversationCapture
from llm_memory.core.memory import Memory

@pytest.fixture
def mock_memory():
    mock_mem = Mock(spec=Memory)
    # Mock config hierarchy
    mock_mem.config = Mock()
    mock_mem.config.capture.llm_provider = "openai"
    mock_mem.config.capture.llm_model = "gpt-4-test"
    mock_mem.config.compression.llm_provider = None # fallback test
    mock_mem.config.repo_id = "test-repo"
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
        assert result_dry['decisions'] == 1
        assert not mock_memory.decision.called
        
        # Test real run
        result = capturer.parse_text("log", dry_run=False)
        
        assert result['decisions'] == 1
        assert result['learnings'] == 1
        
        # Check calls
        mock_memory.decision.assert_called_with(
            what="Use Redis",
            why="Speed",
            alternatives=["Memcached"],
            repo_id="test-repo"
        )
        
        mock_memory.learn.assert_called_with(
            knowledge="Python 3.12 is faster",
            category="fact",
            importance=0.8,
            repo_id="test-repo"
        )
        
        mock_memory.record.assert_called()
        call_args = mock_memory.record.call_args[1]
        assert "Bug: Race condition" == call_args['event']
        assert "bug" == call_args['category']
        assert call_args['metadata']['cause'] == "No lock"

def test_conversation_capture_config_error(mock_memory):
    # Unset provider
    mock_memory.config.capture.llm_provider = None
    mock_memory.config.compression.llm_provider = None
    
    capturer = ConversationCapture(mock_memory)
    
    with pytest.raises(ValueError, match="No LLM provider configured"):
        capturer.parse_text("fail")
