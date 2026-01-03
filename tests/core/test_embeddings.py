import logging
import sys
from types import SimpleNamespace
from unittest.mock import Mock

from visp_memory.config import EmbeddingConfig
from visp_memory.core.embeddings import get_embedding_provider


def test_auto_embeddings_prefer_openai_when_key_is_available(monkeypatch):
    openai_client = Mock()
    openai_client.embeddings.create.return_value = SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3])]
    )
    openai_module = SimpleNamespace(OpenAI=Mock(return_value=openai_client))
    monkeypatch.setitem(sys.modules, "openai", openai_module)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)

    config = EmbeddingConfig(provider="auto")
    provider = get_embedding_provider(config)

    assert provider.provider_name == "openai"
    assert provider.model == "text-embedding-3-small"
    assert provider.dimension == 3
    openai_module.OpenAI.assert_called_once()
    openai_client.embeddings.create.assert_called_once_with(
        input="test", model="text-embedding-3-small"
    )


def test_auto_embeddings_prefer_openrouter_as_cloud_provider(monkeypatch):
    openai_client = Mock()
    openai_client.embeddings.create.return_value = SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3])]
    )
    openai_module = SimpleNamespace(OpenAI=Mock(return_value=openai_client))
    monkeypatch.setitem(sys.modules, "openai", openai_module)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)

    config = EmbeddingConfig(provider="auto")
    provider = get_embedding_provider(config)

    assert provider.provider_name == "openrouter"
    assert provider.model == "openai/text-embedding-3-small"
    assert provider.dimension == 3
    openai_module.OpenAI.assert_called_once_with(
        api_key="sk-or-test", base_url="https://openrouter.ai/api/v1"
    )
    openai_client.embeddings.create.assert_called_once_with(
        input="test", model="openai/text-embedding-3-small"
    )


def test_cloud_embeddings_fall_back_to_ollama_when_cloud_fails(monkeypatch):
    openai_module = SimpleNamespace(OpenAI=Mock(side_effect=RuntimeError("cloud down")))
    monkeypatch.setitem(sys.modules, "openai", openai_module)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    ollama_client = Mock()
    ollama_client.embeddings.return_value = {"embedding": [0.1, 0.2, 0.3]}
    ollama_module = SimpleNamespace(Client=Mock(return_value=ollama_client))
    monkeypatch.setitem(sys.modules, "ollama", ollama_module)
    monkeypatch.setenv("OLLAMA_HOST", "http://ollama:11434")

    config = EmbeddingConfig(provider="cloud")
    provider = get_embedding_provider(config)

    assert provider.provider_name == "ollama"
    assert provider.model == "nomic-embed-text"
    ollama_module.Client.assert_called_once_with(host="http://ollama:11434")


def test_auto_embeddings_fall_back_to_noop_when_cloud_and_ollama_fail(monkeypatch):
    openai_module = SimpleNamespace(OpenAI=Mock(side_effect=RuntimeError("cloud down")))
    monkeypatch.setitem(sys.modules, "openai", openai_module)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    ollama_module = SimpleNamespace(Client=Mock(side_effect=RuntimeError("ollama down")))
    monkeypatch.setitem(sys.modules, "ollama", ollama_module)
    monkeypatch.setenv("OLLAMA_HOST", "http://ollama:11434")

    config = EmbeddingConfig(provider="auto")
    provider = get_embedding_provider(config)

    assert provider.provider_name == "noop"


def test_noop_fallback_is_informational_and_actionable(monkeypatch, caplog):
    """Landing on NoOpProvider is a supported configuration, not a fault.

    Every backend routes recall through keyword search when embeddings are noop, so this
    must not emit a WARNING: doing so fired on the documented lean-install path on every
    CLI invocation. It must still name how to enable semantic recall.
    """
    import visp_memory.core.embeddings as embeddings_module

    monkeypatch.setattr(embeddings_module, "_noop_fallback_warned", False)

    openai_module = SimpleNamespace(OpenAI=Mock(side_effect=RuntimeError("cloud down")))
    monkeypatch.setitem(sys.modules, "openai", openai_module)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    ollama_module = SimpleNamespace(Client=Mock(side_effect=RuntimeError("ollama down")))
    monkeypatch.setitem(sys.modules, "ollama", ollama_module)
    monkeypatch.setenv("OLLAMA_HOST", "http://ollama:11434")

    config = EmbeddingConfig(provider="auto")

    with caplog.at_level(logging.DEBUG, logger="visp_memory.core.embeddings"):
        provider = get_embedding_provider(config)

    assert provider.provider_name == "noop"

    # No WARNING+ noise for a working, supported configuration.
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    # The operator still gets one actionable INFO message naming every enable path.
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert len(infos) == 1, f"expected exactly one INFO summary, got {infos}"
    assert "keyword search" in infos[0]
    assert "OPENROUTER_API_KEY" in infos[0]
    assert "OPENAI_API_KEY" in infos[0]
    assert "local-embeddings" in infos[0]

    # Per-candidate failures stay diagnosable at DEBUG.
    debugs = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("unavailable during auto-selection" in m for m in debugs)


def test_noop_fallback_message_is_not_repeated(monkeypatch, caplog):
    """The summary is deduplicated per process; Memory() is built many times per run."""
    import visp_memory.core.embeddings as embeddings_module

    monkeypatch.setattr(embeddings_module, "_noop_fallback_warned", False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setitem(
        sys.modules, "ollama", SimpleNamespace(Client=Mock(side_effect=RuntimeError("down")))
    )

    config = EmbeddingConfig(provider="auto")
    with caplog.at_level(logging.INFO, logger="visp_memory.core.embeddings"):
        get_embedding_provider(config)
        get_embedding_provider(config)
        get_embedding_provider(config)

    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(infos) == 1, "fallback summary must be emitted once per process"


def test_auto_selection_prefers_local_sentence_transformers_when_installed(monkeypatch):
    """A lean install has no sentence-transformers; an explicit [local-embeddings]
    install must be picked up automatically rather than silently landing on noop."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)

    import visp_memory.core.embeddings as embeddings_module

    monkeypatch.setattr(embeddings_module, "_sentence_transformers_available", lambda: True)

    built: list[str] = []
    real_build = embeddings_module._build_provider

    def fake_build(config, provider, *, verify=False):
        built.append(provider)
        if provider == "sentence-transformers":
            return SimpleNamespace(provider_name="sentence-transformers", embed=lambda t: [0.0])
        return real_build(config, provider, verify=verify)

    monkeypatch.setattr(embeddings_module, "_build_provider", fake_build)

    provider = embeddings_module.get_embedding_provider(EmbeddingConfig(provider="auto"))

    assert provider.provider_name == "sentence-transformers"
    # ...and it wins outright: Ollama is never probed, so no daemon timeout on startup.
    assert built == ["sentence-transformers"]


def test_auto_embeddings_use_ollama_host_with_provider_default_model(monkeypatch):
    ollama_client = Mock()
    ollama_client.embeddings.return_value = {"embedding": [0.1, 0.2, 0.3]}
    ollama_module = SimpleNamespace(Client=Mock(return_value=ollama_client))
    monkeypatch.setitem(sys.modules, "ollama", ollama_module)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
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


def test_explicit_openrouter_can_verify_connection(monkeypatch):
    openai_client = Mock()
    openai_client.embeddings.create.return_value = SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3])]
    )
    openai_module = SimpleNamespace(OpenAI=Mock(return_value=openai_client))
    monkeypatch.setitem(sys.modules, "openai", openai_module)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    config = EmbeddingConfig(provider="openrouter")
    provider = get_embedding_provider(config, verify=True)

    assert provider.provider_name == "openrouter"
    assert provider.dimension == 3
    openai_client.embeddings.create.assert_called_once_with(
        input="test", model="openai/text-embedding-3-small"
    )
