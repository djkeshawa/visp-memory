import json
import re
from unittest.mock import Mock, patch

import pytest

from visp_memory.capture.conversation import ConversationCapture
from visp_memory.config import MemoryConfig
from visp_memory.core.memory import Memory
from visp_memory.core.trust import Provenance, WriteChannel, provenance_of

SOURCE_TEXT = (
    "user: Use Redis because Speed.\n"
    "assistant: Python 3.12 is faster.\n"
    "assistant: Race condition. Cause: No lock. Fix: Added mutex.\n"
    "assistant: Refactor auth."
)


def _mock_response(prompt):
    source_id = re.search(r"source_id:\s*(\S+)", prompt).group(1)
    speaker = re.search(r"speaker:\s*(\S+)", prompt).group(1)
    if "Use Redis because Speed." in prompt:
        return {"decisions": [{
            "what": "Use Redis", "why": "Speed", "alternatives": ["Memcached"],
            "quote": "Use Redis because Speed.", "source_id": source_id, "speaker": speaker,
        }]}
    if "Python 3.12 is faster." in prompt:
        return {"learnings": [{
            "knowledge": "Python 3.12 is faster.", "category": "pattern", "importance": 0.8,
            "quote": "Python 3.12 is faster.", "source_id": source_id, "speaker": speaker,
        }]}
    if "Race condition." in prompt:
        return {"bugs": [{
            "description": "Race condition", "cause": "No lock", "fix": "Added mutex",
            "quote": "Race condition. Cause: No lock. Fix: Added mutex.",
            "source_id": source_id, "speaker": speaker,
        }]}
    if "Refactor auth." in prompt:
        return {"tasks": [{
            "description": "Refactor auth", "status": "todo",
            "quote": "Refactor auth.", "source_id": source_id, "speaker": speaker,
        }]}
    return {}


@pytest.fixture
def mock_memory():
    mock_mem = Mock(spec=Memory)
    mock_mem.config = Mock()
    mock_mem.config.capture.llm_provider = "openai"
    mock_mem.config.capture.llm_model = "gpt-4-test"
    mock_mem.config.compression.llm_provider = None
    mock_mem.config.repo_id = "test-repo"
    mock_mem.intent = Mock()
    return mock_mem


def test_conversation_capture_parsing(mock_memory):
    mock_client = Mock()
    mock_client.completion.side_effect = lambda **kwargs: json.dumps(
        _mock_response(kwargs["prompt"])
    )

    with patch("visp_memory.capture.conversation.create_llm_client", return_value=mock_client):
        capturer = ConversationCapture(mock_memory)
        result_dry = capturer.parse_text(SOURCE_TEXT, dry_run=True)
        result = capturer.parse_text(SOURCE_TEXT, dry_run=False)

    assert result_dry["decisions"] == 1
    assert result["decisions"] == 1
    assert result["learnings"] == 1
    assert result["bugs"] == 1
    assert result["tasks"] == 1
    assert mock_memory.record.call_count == 4  # source turns precede learning
    assert mock_memory.learn.call_count == 4
    assert not mock_memory.decision.called
    assert not mock_memory.intent.set_goal.called
    assert all(
        call.kwargs["_write_channel"] is WriteChannel.CONVERSATION
        for call in mock_memory.learn.call_args_list
    )


def test_conversation_capture_skips_unchanged_text(mock_memory, tmp_path):
    mock_memory.config.storage.data_dir = tmp_path / "memory"
    mock_client = Mock()
    mock_client.completion.side_effect = lambda **kwargs: json.dumps(
        _mock_response(kwargs["prompt"])
    )

    with patch("visp_memory.capture.conversation.create_llm_client", return_value=mock_client):
        capturer = ConversationCapture(mock_memory)
        first = capturer.parse_text(
            "user: Use Redis because Speed.", source="chat.json", dry_run=False
        )
        mock_memory.record.reset_mock()
        mock_memory.learn.reset_mock()
        second = capturer.parse_text(
            "user: Use Redis because Speed.", source="chat.json", dry_run=False
        )

    assert first["capture_manifest"]["status"] == "changed"
    assert second["capture_manifest"]["status"] == "unchanged"
    mock_memory.record.assert_not_called()
    mock_memory.learn.assert_not_called()


def test_conversation_extraction_persists_assisted_provenance(tmp_path):
    config = MemoryConfig(repo_id="repo-a")
    config.storage.data_dir = tmp_path / "memory"
    config.embedding.provider = "noop"
    config.capture.llm_provider = "openai"
    config.quality.write_reconciliation = False
    config.quality.conflict_detection = False
    memory = Memory(config=config)
    client = Mock()
    client.completion.side_effect = lambda **kwargs: json.dumps(
        _mock_response(kwargs["prompt"])
    )

    with patch("visp_memory.capture.conversation.create_llm_client", return_value=client):
        result = ConversationCapture(memory).parse_text(SOURCE_TEXT, dry_run=False)

    stored = memory._storage.list_memories(repo_id="repo-a", limit=100)
    assert result["decisions"] == result["learnings"] == result["bugs"] == result["tasks"] == 1
    assert len(stored) == 8  # four original source turns plus four derived facts
    assert {provenance_of(item) for item in stored} == {Provenance.ASSISTED}
    derived = [item for item in stored if item["layer"] == "semantic"]
    assert all(item["source_ids"] for item in derived)
    assert memory.intent.get_active(repo_id="repo-a") == []


def test_conversation_capture_config_error(mock_memory):
    mock_memory.config.capture.llm_provider = None
    mock_memory.config.compression.llm_provider = None

    capturer = ConversationCapture(mock_memory)

    with pytest.raises(ValueError, match="No LLM provider configured"):
        capturer.parse_text("fail")
