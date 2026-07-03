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
    # Distribution name on PyPI is "llm-memory-mcp" ("llm-memory" is owned by an
    # unrelated project); the import package and CLI stay "llm-memory".
    __version__ = version("llm-memory-mcp") if version else "0.2.3"
except PackageNotFoundError:
    __version__ = "0.2.3"

from llm_memory.config import MemoryConfig
from llm_memory.core.memory import Memory

__all__ = ["Memory", "MemoryConfig", "__version__"]
