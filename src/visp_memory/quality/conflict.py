"""
Conflict Resolution Module

Identifies and resolves conflicting information in memories.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from visp_memory.core.llm import LLMClient, create_llm_client
from visp_memory.core.storage import BaseStorage
from visp_memory.quality.secrets import redact_for_storage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConflictVerdict:
    """What detection concluded, kept distinct from why it concluded nothing.

    ``None`` used to mean three different things — no detector configured, the
    detector raised, and the detector ran and found nothing. A caller could not
    tell "all clear" from "I could not check", so an unavailable detector read as
    a clean bill of health and the write proceeded. That is MG-026.

    ``determined`` says whether detection actually completed. ``conflict`` is the
    finding when it did. A caller that needs to fail closed checks ``determined``.
    """

    determined: bool
    conflict: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None

    @property
    def has_conflict(self) -> bool:
        return self.determined and self.conflict is not None

    @classmethod
    def clear(cls) -> "ConflictVerdict":
        """Detection ran and found no contradiction."""
        return cls(determined=True)

    @classmethod
    def found(cls, conflict: Dict[str, Any]) -> "ConflictVerdict":
        return cls(determined=True, conflict=conflict)

    @classmethod
    def undetermined(cls, reason: str) -> "ConflictVerdict":
        """Detection could not run or could not complete. Never a clear result."""
        return cls(determined=False, reason=reason)


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
    ) -> ConflictVerdict:
        """
        Check if new content conflicts with existing memories.

        Args:
            new_content: The new fact/memory
            relevant_memories: List of potentially conflicting existing memories

        Returns:
            A ConflictVerdict. Check ``determined`` before trusting the absence
            of a conflict — an undetermined verdict is not a clear one.
        """
        new_content, _ = redact_for_storage(new_content, None)
        # Nothing to contradict is a real answer; no detector is not.
        if not relevant_memories:
            return ConflictVerdict.clear()
        if not self.client:
            return ConflictVerdict.undetermined("no conflict detector is configured")

        safe_memories = []
        for memory in relevant_memories:
            safe_content, _ = redact_for_storage(str(memory.get("content", "")), None)
            safe_memories.append(f"- [{memory['id']}] {safe_content}")
        mem_text = "\n".join(safe_memories)

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
                return ConflictVerdict.found(result)
            return ConflictVerdict.clear()

        except Exception as e:
            # A detector that raised did not clear the content. Reporting this as
            # "no conflict" is how contradictory knowledge used to get written
            # whenever the model was slow, rate-limited, or returned malformed
            # JSON — the failure was logged, and the write proceeded anyway.
            logger.warning("Conflict detection failed: %s", e)
            return ConflictVerdict.undetermined(f"conflict detection failed: {e}")
