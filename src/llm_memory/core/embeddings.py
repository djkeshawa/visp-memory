"""
Pluggable embedding providers for LLM Memory.

Supports:
- sentence-transformers (local, free, default)
- OpenAI embeddings (API)
- Ollama embeddings (local)
- Custom providers
"""

from abc import ABC, abstractmethod
from typing import List, Optional
import numpy as np

from llm_memory.config import EmbeddingConfig


class EmbeddingProvider(ABC):
    """Abstract base class for embedding providers."""

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

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "sentence-transformers required: pip install sentence-transformers"
            )

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

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: Optional[str] = None,
        api_base: Optional[str] = None
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

        # Dimension lookup for known models
        self._dimensions = {
            "text-embedding-3-small": 1536,
            "text-embedding-3-large": 3072,
            "text-embedding-ada-002": 1536,
        }
        self._dimension = self._dimensions.get(model, 1536)

    def embed(self, text: str) -> List[float]:
        response = self.client.embeddings.create(
            input=text,
            model=self.model
        )
        return response.data[0].embedding

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        response = self.client.embeddings.create(
            input=texts,
            model=self.model
        )
        return [item.embedding for item in response.data]

    @property
    def dimension(self) -> int:
        return self._dimension


class OllamaProvider(EmbeddingProvider):
    """Local embeddings via Ollama."""

    def __init__(
        self,
        model: str = "nomic-embed-text",
        host: Optional[str] = None
    ):
        try:
            import ollama
        except ImportError:
            raise ImportError("ollama required: pip install ollama")

        self._ollama = ollama
        self.model = model
        self.host = host

        # Get dimension by doing a test embedding
        test_embedding = self.embed("test")
        self._dimension = len(test_embedding)

    def embed(self, text: str) -> List[float]:
        kwargs = {"model": self.model, "prompt": text}
        if self.host:
            kwargs["host"] = self.host

        response = self._ollama.embeddings(**kwargs)
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

    def __init__(self, dimension: int = 384):
        self._dimension = dimension

    def embed(self, text: str) -> List[float]:
        return [1.0] + ([0.0] * (self._dimension - 1))

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(text) for text in texts]

    @property
    def dimension(self) -> int:
        return self._dimension


def get_embedding_provider(config: EmbeddingConfig) -> EmbeddingProvider:
    """
    Factory function to create embedding provider from config.

    Args:
        config: EmbeddingConfig with provider settings

    Returns:
        EmbeddingProvider instance
    """
    provider = config.provider.lower()

    if provider == "sentence-transformers":
        return SentenceTransformerProvider(model_name=config.model)

    elif provider == "openai":
        return OpenAIProvider(
            model=config.model,
            api_key=config.api_key,
            api_base=config.api_base
        )

    elif provider == "ollama":
        return OllamaProvider(
            model=config.model,
            host=config.api_base
        )

    elif provider == "none" or provider == "noop":
        return NoOpProvider()

    else:
        raise ValueError(f"Unknown embedding provider: {provider}")


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
