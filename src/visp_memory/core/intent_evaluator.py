"""Advisory intent completion-signal evaluation."""

from __future__ import annotations

import re
from typing import Any, Callable

from visp_memory.config import LLMConfig
from visp_memory.core.clock import utc_now_iso
from visp_memory.core.model_router import ModelRouter

EVALUATOR_VERSION = "intent-advisory-v2"
COMPLETION_TERMS = {
    "achieved",
    "complete",
    "completed",
    "done",
    "fixed",
    "implemented",
    "landed",
    "merged",
    "resolved",
}
STOP_WORDS = {
    "a",
    "an",
    "and",
    "for",
    "in",
    "of",
    "on",
    "the",
    "to",
    "with",
    "working",
}


class IntentEvaluator:
    """Evaluate completion signals without changing workflow state."""

    def __init__(self, storage, model_router: ModelRouter, config: LLMConfig):
        self.storage = storage
        self.model_router = model_router
        self.config = config

    @staticmethod
    def _terms(value: str) -> set[str]:
        return {
            term
            for term in re.findall(r"[a-z0-9_]+", value.casefold())
            if len(term) > 1 and term not in STOP_WORDS
        }

    def evaluate(
        self,
        intent: dict[str, Any],
        *,
        summary: str,
        memory_ids: list[str],
        actor_id: str,
        allow_auto_complete: bool = True,
    ) -> dict[str, Any]:
        repo_id = intent.get("repo_id")
        evidence_memories = []
        for memory_id in dict.fromkeys(memory_ids):
            memory = self.storage.get_memory(memory_id)
            if memory and memory.get("repo_id") == repo_id and memory.get("status") == "active":
                evidence_memories.append(memory)

        evidence_text = " ".join(
            [summary, *(memory.get("content", "") for memory in evidence_memories)]
        ).strip()
        intent_terms = self._terms(intent.get("description", ""))
        evidence_terms = self._terms(evidence_text)
        overlap = (
            len(intent_terms & evidence_terms) / len(intent_terms) if intent_terms else 0.0
        )
        completion_language = bool(COMPLETION_TERMS & evidence_terms)
        test_passed = "test" in evidence_terms and bool(
            {"pass", "passed", "passing", "green"} & evidence_terms
        )
        completion_signal = bool(evidence_memories) and completion_language

        deterministic_confidence = 0.0
        if completion_signal:
            deterministic_confidence += 0.45
        deterministic_confidence += min(0.25, overlap * 0.5)
        if completion_language:
            deterministic_confidence += 0.2
        if test_passed:
            deterministic_confidence += 0.1
        deterministic_confidence = round(min(1.0, deterministic_confidence), 4)

        confidence = deterministic_confidence

        suggestion_threshold = self.config.intent_suggestion_threshold
        if confidence >= suggestion_threshold:
            decision = "suggested"
        else:
            decision = "incomplete"

        evaluation = {
            "decision": decision,
            "confidence": confidence,
            "deterministic_confidence": deterministic_confidence,
            "model_confidence": None,
            # Kept for response compatibility. A Memory record is never objective
            # completion evidence for an external workflow authority.
            "objective_evidence": False,
            "completion_signal": completion_signal,
            "evidence_memory_ids": [memory["id"] for memory in evidence_memories],
            "summary": summary[:2000],
            "reason": None,
            "provider": None,
            "model": None,
            "evaluator_version": EVALUATOR_VERSION,
            "evaluated_at": utc_now_iso(),
            "evaluated_by": actor_id,
            "authoritative": False,
            "status_changed": False,
            "deprecated_inputs": {
                "allow_auto_complete": "accepted_but_ineffective",
                "intent_auto_complete": "accepted_but_ineffective",
            },
        }
        return {"intent_id": intent["id"], **evaluation}

    def evaluate_repository(
        self,
        repo_id: str,
        *,
        summary: str,
        memory_ids: list[str],
        actor_id: str,
        allow_auto_complete: bool = True,
        intent_filter: Callable[[dict[str, Any]], bool] | None = None,
    ) -> list[dict[str, Any]]:
        intents = self.storage.get_active_intents(repo_id=repo_id, status="active")
        if intent_filter is not None:
            intents = [intent for intent in intents if intent_filter(intent)]
        return [
            self.evaluate(
                intent,
                summary=summary,
                memory_ids=memory_ids,
                actor_id=actor_id,
                allow_auto_complete=allow_auto_complete,
            )
            for intent in intents
        ]
