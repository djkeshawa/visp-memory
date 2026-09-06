"""
Visp Memory - Human-inspired memory system for LLMs

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

from visp_memory.config import MemoryConfig
from visp_memory.core.memory import Memory

# Single source of truth is pyproject.toml; this is only reached when the package is
# not installed (e.g. running straight from a source checkout).
_FALLBACK_VERSION = "0.6.0"

try:
    # Must match the distribution name in pyproject.toml. Querying a name that is not
    # installed raises PackageNotFoundError and silently falls back, so a stale name
    # here reports a stale version rather than failing loudly -- which is exactly what
    # happened after the rename: the lookup asked for "visp-memory-mcp" and every
    # install reported the hardcoded fallback instead of its real version.
    __version__ = version("visp-memory") if version else _FALLBACK_VERSION
except PackageNotFoundError:
    __version__ = _FALLBACK_VERSION

__all__ = ["Memory", "MemoryConfig", "__version__"]
