from typing import Optional

from visp_memory.config import MemoryConfig


def request_repo_id(requested: Optional[str], config: MemoryConfig) -> Optional[str]:
    """Resolve a request scope without leaking a server scope in shared mode."""
    if requested:
        return requested
    if not config.server.shared:
        return config.repo_id
    return None

