"""
LLM Memory - Human-inspired memory system for LLMs

A cognitive memory architecture that mimics how humans remember:
- Episodic: Compressed events and experiences
- Semantic: Extracted knowledge and patterns
- Intent: Current direction and priorities

Designed to give LLMs persistent context without re-analyzing everything.
"""

__version__ = "0.1.0"

from llm_memory.core.memory import Memory
from llm_memory.config import MemoryConfig

__all__ = ["Memory", "MemoryConfig", "__version__"]
