import sys
from types import SimpleNamespace
from unittest.mock import Mock

from llm_memory.config import EmbeddingConfig
from llm_memory.core.embeddings import get_embedding_provider


def test_auto_embeddings_prefer_openai_when_key_is_available(monkeypatch):
    openai_client = Mock()
    openai_module = SimpleNamespace(OpenAI=Mock(return_value=openai_client))
    monkeypatch.setitem(sys.modules, "openai", openai_module)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)

    config = EmbeddingConfig(provider="auto")
    provider = get_embedding_provider(config)

    assert provider.provider_name == "openai"
    assert provider.model == "text-embedding-3-small"
    openai_module.OpenAI.assert_called_once()


def test_auto_embeddings_use_ollama_host_with_provider_default_model(monkeypatch):
    ollama_client = Mock()
    ollama_client.embeddings.return_value = {"embedding": [0.1, 0.2, 0.3]}
    ollama_module = SimpleNamespace(Client=Mock(return_value=ollama_client))
    monkeypatch.setitem(sys.modules, "ollama", ollama_module)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.setenv("OLLAMA_HOST", "http://ollama:11434")

    config = EmbeddingConfig(provider="auto")
    provider = get_embedding_provider(config)

    assert provider.provider_name == "ollama"
    assert provider.model == "nomic-embed-text"
    assert provider.dimension == 3
    ollama_module.Client.assert_called_once_with(host="http://ollama:11434")
    ollama_client.embeddings.assert_called_with(model="nomic-embed-text", prompt="test")


def test_explicit_openai_uses_provider_default_when_config_model_is_local_default(monkeypatch):
    openai_module = SimpleNamespace(OpenAI=Mock(return_value=Mock()))
    monkeypatch.setitem(sys.modules, "openai", openai_module)

    config = EmbeddingConfig(provider="openai")
    provider = get_embedding_provider(config)

    assert provider.provider_name == "openai"
    assert provider.model == "text-embedding-3-small"
