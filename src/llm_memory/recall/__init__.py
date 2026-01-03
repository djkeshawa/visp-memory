"""
Proactive Recall System

Automatically surfaces relevant memories without being asked:
- File-triggered recall (warnings, bugs, patterns for specific files)
- Error matching (find similar past errors)
- Directory aggregation (knowledge for entire directories)
- Context formatting (prepare for LLM injection)
"""

from llm_memory.recall.proactive import ProactiveRecall

__all__ = ["ProactiveRecall"]
