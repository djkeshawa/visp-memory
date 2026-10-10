"""Ollama calls must be bounded: a stalled local server must not hang the caller."""

import sys
from types import SimpleNamespace
from unittest.mock import Mock

from visp_memory.core.embeddings import OLLAMA_TIMEOUT_SECONDS, OllamaProvider
from visp_memory.core.llm import OllamaClient


def _fake_ollama(monkeypatch):
    timed = Mock()
    timed.chat.return_value = {"message": {"content": " ok "}}
    timed.embeddings.return_value = {"embedding": [0.1, 0.2]}
    module = SimpleNamespace(
        Client=Mock(return_value=timed),
        chat=Mock(side_effect=AssertionError("the unbounded module client was used")),
        embeddings=Mock(side_effect=AssertionError("the unbounded module client was used")),
    )
    monkeypatch.setitem(sys.modules, "ollama", module)
    return module, timed


def test_completion_timeout_reaches_a_bounded_ollama_client(monkeypatch):
    module, timed = _fake_ollama(monkeypatch)
    client = OllamaClient()

    assert client.completion("hi", timeout=5.0, max_output_tokens=64) == "ok"
    assert client.completion("again", timeout=5.0) == "ok"

    module.Client.assert_called_once_with(timeout=5.0)
    assert "timeout" not in timed.chat.call_args.kwargs
    assert timed.chat.call_args_list[0].kwargs["options"] == {"num_predict": 64}


def test_embedding_provider_uses_a_bounded_client_without_a_host(monkeypatch):
    module, timed = _fake_ollama(monkeypatch)

    provider = OllamaProvider()

    assert provider.embed("text") == [0.1, 0.2]
    module.Client.assert_called_once_with(host=None, timeout=OLLAMA_TIMEOUT_SECONDS)
    module.embeddings.assert_not_called()
