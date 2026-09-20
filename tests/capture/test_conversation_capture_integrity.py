"""Adversarial capture contracts: sources, retries and model output are distinct."""

import json
import re
from types import SimpleNamespace

import pytest

from visp_memory import Memory, MemoryConfig
from visp_memory.capture.conversation import ConversationCapture


def _config(tmp_path):
    config = MemoryConfig(repo_id="capture-integrity")
    config.storage.data_dir = tmp_path
    config.embedding.provider = "noop"
    config.quality.write_reconciliation = False
    config.quality.conflict_detection = False
    return config


def test_changed_conversation_never_reuses_old_evidence(tmp_path):
    with Memory(config=_config(tmp_path)) as memory:
        capture = ConversationCapture(memory)
        capture._client = SimpleNamespace(completion=lambda **kwargs: "{}")
        first = "user: Invoice total is $123."
        second = "user: Invoice total is $456."
        capture.parse_text(first, source="same-chat")
        capture.parse_text(second, source="same-chat")
        evidence = memory._storage.list_evidence(memory.config.repo_id)
        assert any(second in row["content"] for row in evidence)
        assert any(first in row["content"] for row in evidence)


@pytest.mark.parametrize("prefix", ["user: ", "Unattributed introduction: "])
def test_long_input_reaches_tail_with_bounded_prompts(tmp_path, prefix):
    prompts = []
    def complete(**kwargs):
        prompts.append(kwargs["prompt"])
        return "{}"
    with Memory(config=_config(tmp_path)) as memory:
        capture = ConversationCapture(memory)
        capture._client = SimpleNamespace(completion=complete)
        text = prefix + "Background sentence. " * 2000 + "\nassistant: Final code ZQ-719."
        capture.parse_text(text, source="long")
        assert all(len(prompt) <= 22000 for prompt in prompts)
        assert any("Final code ZQ-719" in prompt for prompt in prompts)


def test_extracted_number_cannot_replace_quoted_number(tmp_path):
    def complete(**kwargs):
        source_id = re.search(r"source_id:\s*(\S+)", kwargs["prompt"]).group(1)
        return json.dumps({"learnings": [{
            "knowledge": "Invoice total is $999.", "quote": "Invoice total is $123.",
            "source_id": source_id, "speaker": "user",
        }]})
    with Memory(config=_config(tmp_path)) as memory:
        capture = ConversationCapture(memory)
        capture._client = SimpleNamespace(completion=complete)
        result = capture.parse_text("user: Invoice total is $123.", source="invoice")
        rows = memory._storage.list_memories(repo_id=memory.config.repo_id, limit=100)
        assert all("$999" not in row["content"] for row in rows)
        assert result["failures"]
        assert result["capture_manifest"]["completed"] is False
        assert result["learnings"] == 0
        assert all(row["layer"] == "episodic" for row in rows)


def test_unstructured_conversation_cannot_bypass_source_validation(tmp_path):
    with Memory(config=_config(tmp_path)) as memory:
        capture = ConversationCapture(memory)
        capture._client = SimpleNamespace(completion=lambda **kwargs: json.dumps({
            "learnings": [{"knowledge": "The user purchased a submarine."}],
        }))
        capture.parse_text("We discussed a bicycle.", source="notes")
        rows = memory._storage.list_memories(repo_id=memory.config.repo_id, limit=100)
        assert all("submarine" not in row["content"] for row in rows)


def test_assistant_suggestion_is_not_an_active_user_goal(tmp_path):
    def complete(**kwargs):
        source_id = re.search(r"source_id:\s*(\S+)", kwargs["prompt"]).group(1)
        return json.dumps({"tasks": [{
            "description": "Delete the archive.", "quote": "Delete the archive.",
            "source_id": source_id, "speaker": "assistant",
        }]})
    with Memory(config=_config(tmp_path)) as memory:
        capture = ConversationCapture(memory)
        capture._client = SimpleNamespace(completion=complete)
        capture.parse_text("assistant: Delete the archive.", source="suggestions")
        assert memory._storage.get_active_intents(repo_id=memory.config.repo_id) == []


def _facts_response(prompt, facts):
    source_id = re.search(r"source_id:\s*(\S+)", prompt).group(1)
    return json.dumps({"learnings": [{
        "knowledge": fact, "quote": fact, "source_id": source_id, "speaker": "user",
    } for fact in facts]})


def test_capture_dry_run_writes_neither_sources_nor_manifest(tmp_path):
    with Memory(config=_config(tmp_path)) as memory:
        capture = ConversationCapture(memory)
        capture._client = SimpleNamespace(completion=lambda **kw: _facts_response(
            kw["prompt"], ["The invoice is $123."]
        ))
        result = capture.parse_text("user: The invoice is $123.", dry_run=True)
        assert result["learnings"] == 1
        assert memory._storage.list_memories(repo_id=memory.config.repo_id) == []
        assert not (tmp_path / "capture_manifest.json").exists()


def test_capture_uses_the_real_ollama_client_contract(tmp_path, monkeypatch):
    from visp_memory.core.llm import OllamaClient

    client = object.__new__(OllamaClient)
    client.model = "gemma4:e4b"
    def chat(*, model, messages):
        assert model == "gemma4:e4b"
        return {"message": {"content": _facts_response(
            messages[-1]["content"], ["The invoice is $123."]
        )}}
    client.client = SimpleNamespace(chat=chat)
    with Memory(config=_config(tmp_path)) as memory:
        capture = ConversationCapture(memory)
        capture._client = client
        result = capture.parse_text("user: The invoice is $123.")
        assert result["failures"] == []
        assert result["learnings"] == 1


def test_capture_resumes_partial_source_recording_without_duplicates(tmp_path, monkeypatch):
    with Memory(config=_config(tmp_path)) as memory:
        capture = ConversationCapture(memory)
        capture._client = SimpleNamespace(completion=lambda **kw: "{}")
        record = memory.record
        calls = []
        def interrupted(*args, **kwargs):
            calls.append(args)
            if len(calls) == 2:
                raise RuntimeError("simulated source write interruption")
            return record(*args, **kwargs)
        monkeypatch.setattr(memory, "record", interrupted)
        text = "user: First fact.\nassistant: Second fact."
        with pytest.raises(RuntimeError, match="interruption"):
            capture.parse_text(text)
        monkeypatch.setattr(memory, "record", record)
        result = capture.parse_text(text)
        rows = memory._storage.list_memories(repo_id=memory.config.repo_id)
        assert len(rows) == 2
        assert result["capture_manifest"]["completed"]
        assert set(result["capture_manifest"]["output_memory_ids"]) == {r["id"] for r in rows}


def test_capture_retry_retains_success_receipts_when_model_reorders_items(tmp_path, monkeypatch):
    with Memory(config=_config(tmp_path)) as memory:
        capture = ConversationCapture(memory)
        facts = ["First invoice is $123.", "Second invoice is $456."]
        capture._client = SimpleNamespace(completion=lambda **kw: _facts_response(
            kw["prompt"], facts
        ))
        learn = memory.learn
        calls = []
        def interrupted(*args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                raise RuntimeError("simulated learning interruption")
            return learn(*args, **kwargs)
        monkeypatch.setattr(memory, "learn", interrupted)
        text = "user: " + " ".join(facts)
        failed = capture.parse_text(text)
        assert failed["capture_manifest"]["completed"] is False
        monkeypatch.setattr(memory, "learn", learn)
        facts.reverse()
        def revised_response(**kwargs):
            payload = json.loads(_facts_response(kwargs["prompt"], facts))
            for item in payload["learnings"]:
                item["importance"] = 0.7
            return json.dumps(payload)
        capture._client = SimpleNamespace(completion=revised_response)
        result = capture.parse_text(text)
        rows = memory._storage.list_memories(repo_id=memory.config.repo_id)
        assert len(rows) == 3
        assert result["learnings"] == 1
        assert result["capture_manifest"]["completed"]
        assert set(result["capture_manifest"]["output_memory_ids"]) == {r["id"] for r in rows}
