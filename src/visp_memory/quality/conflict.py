"""
Conflict Resolution Module

Identifies and resolves conflicting information in memories.
"""

import logging
from typing import Any, Dict, List, Optional

from visp_memory.core.llm import LLMClient, create_llm_client
from visp_memory.core.storage import BaseStorage

logger = logging.getLogger(__name__)


class ConflictDetector:
    """Detects contradictions between memories."""

    def __init__(
        self,
        storage: BaseStorage,
        llm_client: Optional[LLMClient] = None,
        provider: str = None,
        model: str = None,
        api_key: str = None,
    ):
        self.storage = storage
        if llm_client:
            self.client = llm_client
        elif provider:
            self.client = create_llm_client(provider, model, api_key)
        else:
            self.client = None  # Must be injected or configured

    def detect_conflicts(
        self, new_content: str, relevant_memories: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        Check if new content conflicts with existing memories.

        Args:
            new_content: The new fact/memory
            relevant_memories: List of potentially conflicting existing memories

        Returns:
            Dict describing the conflict if found, else None
        """
        if not self.client or not relevant_memories:
            return None

        mem_text = "\n".join([f"- [{m['id']}] {m['content']}" for m in relevant_memories])

        prompt = f"""Analyze if the NEW FACT conflicts with existing KNOWLEDGE.

EXISTING KNOWLEDGE:
{mem_text}

NEW FACT:
{new_content}

Does the new fact directly contradict any existing knowledge?
If yes, return JSON: {{"conflict": true, "reason": "Explanation", "conflicting_ids": ["id1"]}}
If no, return JSON: {{"conflict": false}}
"""

        try:
            response = self.client.completion(
                prompt,
                response_format={"type": "json_object"}
                if hasattr(self.client, "client") and hasattr(self.client.client, "chat")
                else None,
            )

            import json

            clean = response.replace("```json", "").replace("```", "").strip()
            result = json.loads(clean)

            if result.get("conflict"):
                return result
            return None

        except Exception as e:
            # Don't let LLM/parse failures pass silently; keep returning None
            # (behavior unchanged) but make the failure observable.
            logger.warning("Conflict detection failed: %s", e)
            return None

    def scan_all(self, layer: str = "semantic", sample_size: int = 50) -> List[Dict[str, Any]]:
        """
        Scan a sample of memories for internal consistency.
        (Expensive operation, best for periodic maintenance)
        """
        if not self.client:
            return []

        conflicts = []

        # Naive N^2 check is too slow.
        # Better: Group by topic/category, then check.
        # MVP: Just check pairs that are semantically close?
        # Leave this as a placeholder for a bounded full-scan implementation.

        return conflicts
