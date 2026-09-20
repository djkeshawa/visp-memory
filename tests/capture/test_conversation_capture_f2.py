import json
import re
from unittest.mock import Mock, patch

from visp_memory.capture.conversation import ConversationCapture
from visp_memory.config import MemoryConfig
from visp_memory.core.memory import Memory


def _capture_memory(tmp_path):
    config = MemoryConfig(repo_id="capture-f2")
    config.storage.backend = "sqlite"
    config.storage.data_dir = tmp_path / "memory"
    config.embedding.provider = "noop"
    config.capture.llm_provider = "openai"
    config.quality.write_reconciliation = False
    config.quality.conflict_detection = False
    return Memory(config=config)


def _source_id(prompt):
    return re.search(r"source_id:\s*(\S+)", prompt).group(1)


def test_capture_learns_only_from_validated_source_quote(tmp_path):
    original = "user: The Phoenix release has NOT shipped."
    client = Mock()

    def complete(**kwargs):
        source_id = _source_id(kwargs["prompt"])
        return json.dumps({
            "learnings": [{
                "knowledge": "The Phoenix release has NOT shipped.",
                "source_id": source_id,
                "quote": "The Phoenix release has NOT shipped.",
                "speaker": "user",
            }],
        })

    client.completion.side_effect = complete
    with _capture_memory(tmp_path) as memory, patch(
        "visp_memory.capture.conversation.create_llm_client", return_value=client
    ):
        result = ConversationCapture(memory).parse_text(original, source="chat")
        rows = memory._storage.list_memories(repo_id="capture-f2", limit=100)

    assert result["learnings"] == 1
    assert len(rows) == 2  # source episode and the derived learning
    source = next(row for row in rows if row["layer"] == "episodic")
    derived = next(row for row in rows if row["layer"] == "semantic")
    assert source["content"] == original
    assert derived["evidence_ids"] == source["evidence_ids"]


def test_capture_rejects_unsupported_claim_but_keeps_original(tmp_path):
    original = "user: The Phoenix release has NOT shipped."
    client = Mock()

    def complete(**kwargs):
        source_id = _source_id(kwargs["prompt"])
        return json.dumps({
            "learnings": [{
                "knowledge": "The Phoenix release has shipped.",
                "source_id": source_id,
                "quote": "The Phoenix release has NOT shipped.",
                "speaker": "user",
            }],
        })

    client.completion.side_effect = complete
    with _capture_memory(tmp_path) as memory, patch(
        "visp_memory.capture.conversation.create_llm_client", return_value=client
    ):
        result = ConversationCapture(memory).parse_text(original, source="chat")
        rows = memory._storage.list_memories(repo_id="capture-f2", limit=100)

    assert result["learnings"] == 0
    assert result["failures"]
    assert len(rows) == 1
    assert rows[0]["content"] == original
    assert all("has shipped." not in row["content"] for row in rows)


def test_capture_processes_tail_and_resumes_completed_chunks(tmp_path):
    first = "user: " + ("background " * 3500)
    tail = "assistant: The decisive tail fact is Aurora uses SQLite."
    original = first + "\n" + tail
    client = Mock()
    prompts = []

    def complete(**kwargs):
        prompt = kwargs["prompt"]
        prompts.append(prompt)
        source_id = _source_id(prompt)
        if "decisive tail fact" in prompt:
            return json.dumps({
                "learnings": [{
                    "knowledge": "Aurora uses SQLite.",
                    "source_id": source_id,
                    "quote": "The decisive tail fact is Aurora uses SQLite.",
                    "speaker": "assistant",
                }],
            })
        return "{}"

    client.completion.side_effect = complete
    with _capture_memory(tmp_path) as memory, patch(
        "visp_memory.capture.conversation.create_llm_client", return_value=client
    ):
        capture = ConversationCapture(memory)
        first_result = capture.parse_text(original, source="long-chat")
        second_result = capture.parse_text(original, source="long-chat")

    assert first_result["learnings"] == 1
    assert any("decisive tail fact" in prompt for prompt in prompts)
    assert second_result["capture_manifest"]["status"] == "unchanged"
    assert client.completion.call_count == len(prompts)
    manifest = json.loads((tmp_path / "memory" / "capture_manifest.json").read_text())
    chunks = [
        entry for key, entry in manifest["entries"].items()
        if key.startswith("conversation_chunk:")
    ]
    assert chunks and all(entry["completed"] and entry["offsets"] for entry in chunks)
