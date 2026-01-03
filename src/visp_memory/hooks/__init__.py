"""
LLM Tool Integration Hooks

Universal adapters for integrating with different LLM tools:
- Claude Code (hooks + CLAUDE.md)
- Codex (AGENTS.md + MCP config)
- Cursor (.cursorrules)
- Aider (.aider)
- Generic (any context file)
"""

from visp_memory.hooks.aider import AiderAdapter
from visp_memory.hooks.base import LLMToolAdapter
from visp_memory.hooks.claude_code import ClaudeCodeAdapter
from visp_memory.hooks.codex import CodexAdapter
from visp_memory.hooks.cursor import CursorAdapter
from visp_memory.hooks.generic import GenericAdapter

__all__ = [
    "LLMToolAdapter",
    "GenericAdapter",
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "CursorAdapter",
    "AiderAdapter",
]


def get_adapter(tool_name: str, **kwargs) -> LLMToolAdapter:
    """
    Factory function to get the appropriate adapter.

    Args:
        tool_name: Name of tool (claude-code, codex, cursor, aider, generic)
        **kwargs: Additional arguments for adapter

    Returns:
        Tool adapter instance
    """
    adapters = {
        "claude-code": ClaudeCodeAdapter,
        "codex": CodexAdapter,
        "cursor": CursorAdapter,
        "aider": AiderAdapter,
        "generic": GenericAdapter,
    }

    adapter_class = adapters.get(tool_name.lower())
    if not adapter_class:
        raise ValueError(f"Unknown tool: {tool_name}. Available: {', '.join(adapters.keys())}")

    return adapter_class(**kwargs)
