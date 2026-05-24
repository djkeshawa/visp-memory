"""
Pluggable embedding providers for LLM Memory.

Supports:
- sentence-transformers (local, free, default)
- OpenAI embeddings (API)
- OpenRouter embeddings (OpenAI-compatible API)
- Ollama embeddings (local)
- Custom providers
"""

import os
from abc import ABC, abstractmethod
from typing import List, Optional

import numpy as np

from llm_memory.config import EmbeddingConfig

DEFAULT_EMBEDDING_MODELS = {
    "sentence-transformers": "all-MiniLM-L6-v2",
    "openai": "text-embedding-3-small",
    "openrouter": "openai/text-embedding-3-small",
    "ollama": "nomic-embed-text",
}
OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"

_CONFIG_DEFAULT_MODEL = DEFAULT_EMBEDDING_MODELS["sentence-transformers"]


class EmbeddingProvider(ABC):
    """Abstract base class for embedding providers."""

    provider_name = "unknown"

    @abstractmethod
    def embed(self, text: str) -> List[float]:
        """Generate embedding for a single text."""
        pass

    @abstractmethod
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts."""
        pass

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Return the embedding dimension."""
        pass


class SentenceTransformerProvider(EmbeddingProvider):
    """Local embeddings using sentence-transformers."""

    provider_name = "sentence-transformers"

    def __init__(self, model_name: str = DEFAULT_EMBEDDING_MODELS["sentence-transformers"]):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError("sentence-transformers required: pip install sentence-transformers")

        self.model = SentenceTransformer(model_name)
        self._dimension = self.model.get_sentence_embedding_dimension()

    def embed(self, text: str) -> List[float]:
        embedding = self.model.encode(text, convert_to_numpy=True)
        return embedding.tolist()

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        embeddings = self.model.encode(texts, convert_to_numpy=True)
        return embeddings.tolist()

    @property
    def dimension(self) -> int:
        return self._dimension


class OpenAIProvider(EmbeddingProvider):
    """OpenAI embeddings via API."""

    provider_name = "openai"

    def __init__(
        self,
        model: str = DEFAULT_EMBEDDING_MODELS["openai"],
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        provider_name: str = "openai",
        verify: bool = False,
    ):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("openai required: pip install openai")

        kwargs = {}
        if api_key:
            kwargs["api_key"] = api_key
        if api_base:
            kwargs["base_url"] = api_base

        self.client = OpenAI(**kwargs)
        self.model = model
        self.provider_name = provider_name

        # Dimension lookup for known models
        self._dimensions = {
            "text-embedding-3-small": 1536,
            "openai/text-embedding-3-small": 1536,
            "text-embedding-3-large": 3072,
            "openai/text-embedding-3-large": 3072,
            "text-embedding-ada-002": 1536,
            "openai/text-embedding-ada-002": 1536,
        }
        self._dimension = self._dimensions.get(model)
        if verify or self._dimension is None:
            self._dimension = len(self.embed("test"))

    def embed(self, text: str) -> List[float]:
        response = self.client.embeddings.create(input=text, model=self.model)
        return response.data[0].embedding

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        response = self.client.embeddings.create(input=texts, model=self.model)
        return [item.embedding for item in response.data]

    @property
    def dimension(self) -> int:
        return self._dimension


class OpenRouterProvider(OpenAIProvider):
    """OpenRouter embeddings via the OpenAI-compatible embeddings API."""

    provider_name = "openrouter"

    def __init__(
        self,
        model: str = DEFAULT_EMBEDDING_MODELS["openrouter"],
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        verify: bool = False,
    ):
        super().__init__(
            model=model,
            api_key=api_key,
            api_base=api_base or OPENROUTER_API_BASE,
            provider_name=self.provider_name,
            verify=verify,
        )


class OllamaProvider(EmbeddingProvider):
    """Local embeddings via Ollama."""

    provider_name = "ollama"

    def __init__(self, model: str = DEFAULT_EMBEDDING_MODELS["ollama"], host: Optional[str] = None):
        try:
            import ollama
        except ImportError:
            raise ImportError("ollama required: pip install ollama")

        self._client = ollama.Client(host=host) if host and hasattr(ollama, "Client") else ollama
        self.model = model
        self.host = host

        # Get dimension by doing a test embedding
        test_embedding = self.embed("test")
        self._dimension = len(test_embedding)

    def embed(self, text: str) -> List[float]:
        response = self._client.embeddings(model=self.model, prompt=text)
        return response["embedding"]

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        # Ollama doesn't have native batch, so we loop
        return [self.embed(text) for text in texts]

    @property
    def dimension(self) -> int:
        return self._dimension


class NoOpProvider(EmbeddingProvider):
    """
    No-op provider that returns zero vectors.
    Used when embeddings are disabled or ChromaDB handles them.
    """

    provider_name = "noop"

    def __init__(self, dimension: int = 384):
        self._dimension = dimension

    def embed(self, text: str) -> List[float]:
        return [1.0] + ([0.0] * (self._dimension - 1))

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(text) for text in texts]

    @property
    def dimension(self) -> int:
        return self._dimension


def _model_for_provider(config: EmbeddingConfig, provider: str) -> str:
    """Return a usable model for the selected provider."""
    model = (config.model or "").strip()
    if not model or (model == _CONFIG_DEFAULT_MODEL and provider != "sentence-transformers"):
        return DEFAULT_EMBEDDING_MODELS[provider]
    if provider == "openrouter" and model in {
        "text-embedding-3-small",
        "text-embedding-3-large",
        "text-embedding-ada-002",
    }:
        return f"openai/{model}"
    return model


def _openai_key_available(config: EmbeddingConfig) -> bool:
    return bool(config.api_key or os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY"))


def _openrouter_api_base(config: EmbeddingConfig) -> str:
    return config.api_base or os.getenv("EMBEDDING_API_BASE") or OPENROUTER_API_BASE


def _openrouter_key(config: EmbeddingConfig) -> Optional[str]:
    return config.api_key or os.getenv("OPENROUTER_API_KEY") or os.getenv("EMBEDDING_API_KEY")


def _openrouter_key_available(config: EmbeddingConfig) -> bool:
    return bool(os.getenv("OPENROUTER_API_KEY")) or (
        bool(config.api_key or os.getenv("EMBEDDING_API_KEY"))
        and "openrouter" in _openrouter_api_base(config).lower()
    )


def _ollama_host(config: EmbeddingConfig, *, explicit: bool = False) -> Optional[str]:
    if not explicit:
        return os.getenv("OLLAMA_HOST")
    return config.api_base or os.getenv("EMBEDDING_API_BASE") or os.getenv("OLLAMA_HOST")


def _candidate_providers_for_auto(config: EmbeddingConfig) -> list[str]:
    """Return cloud-first embedding candidates for automatic provider selection."""
    providers = []
    if _openrouter_key_available(config):
        providers.append("openrouter")
    if _openai_key_available(config):
        providers.append("openai")
    providers.append("ollama")
    providers.append("noop")
    return providers


def _build_provider(
    config: EmbeddingConfig, provider: str, *, verify: bool = False
) -> EmbeddingProvider:
    if provider == "sentence-transformers":
        return SentenceTransformerProvider(model_name=_model_for_provider(config, provider))

    if provider == "openrouter":
        return OpenRouterProvider(
            model=_model_for_provider(config, provider),
            api_key=_openrouter_key(config),
            api_base=_openrouter_api_base(config),
            verify=verify,
        )

    if provider == "openai":
        return OpenAIProvider(
            model=_model_for_provider(config, provider),
            api_key=config.api_key or os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY"),
            api_base=config.api_base or os.getenv("EMBEDDING_API_BASE") or None,
            verify=verify,
        )

    if provider == "ollama":
        return OllamaProvider(
            model=_model_for_provider(config, provider),
            host=_ollama_host(config, explicit=not verify),
        )

    if provider == "none" or provider == "noop":
        return NoOpProvider()

    raise ValueError(f"Unknown embedding provider: {provider}")


def get_embedding_provider(config: EmbeddingConfig, *, verify: bool = False) -> EmbeddingProvider:
    """
    Factory function to create embedding provider from config.

    Args:
        config: EmbeddingConfig with provider settings
        verify: perform a test embedding before returning explicit API-backed providers

    Returns:
        EmbeddingProvider instance
    """
    provider = config.provider.lower()
    if provider in {"auto", "cloud"}:
        for candidate in _candidate_providers_for_auto(config):
            try:
                return _build_provider(config, candidate, verify=True)
            except Exception:
                continue
        return NoOpProvider()

    return _build_provider(config, provider, verify=verify)


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Calculate cosine similarity between two vectors."""
    a = np.array(a)
    b = np.array(b)

    dot_product = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return float(dot_product / (norm_a * norm_b))
