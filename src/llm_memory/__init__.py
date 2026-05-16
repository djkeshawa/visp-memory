"""
LLM Memory - Human-inspired memory system for LLMs

A cognitive memory architecture that mimics how humans remember:
- Episodic: Compressed events and experiences
- Semantic: Extracted knowledge and patterns
- Intent: Current direction and priorities

Designed to give LLMs persistent context without re-analyzing everything.
"""

try:
    from importlib.metadata import PackageNotFoundError, version
except ImportError:  # pragma: no cover - Python 3.10+ includes importlib.metadata
    PackageNotFoundError = Exception
    version = None

try:
    __version__ = version("llm-memory") if version else "0.2.0"
except PackageNotFoundError:
    __version__ = "0.2.0"

from llm_memory.config import MemoryConfig
from llm_memory.core.memory import Memory

__all__ = ["Memory", "MemoryConfig", "__version__"]
