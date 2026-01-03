"""Evidence-gated automatic intent completion."""

from __future__ import annotations

import re
from typing import Any, Optional

from visp_memory.config import LLMConfig
from visp_memory.core.clock import utc_now_iso
from visp_memory.core.model_router import ModelRouter, ModelUnavailableError

EVALUATOR_VERSION = "intent-evidence-v1"
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
    """Evaluate completion signals without allowing model-only closure."""

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
        objective_evidence = bool(evidence_memories) and completion_language

        deterministic_confidence = 0.0
        if objective_evidence:
            deterministic_confidence += 0.45
        deterministic_confidence += min(0.25, overlap * 0.5)
        if completion_language:
            deterministic_confidence += 0.2
        if test_passed:
            deterministic_confidence += 0.1
        deterministic_confidence = round(min(1.0, deterministic_confidence), 4)

        model_confidence: Optional[float] = None
        model_reason: Optional[str] = None
        provider: Optional[str] = None
        model: Optional[str] = None
        if self.model_router.configured and evidence_text:
            try:
                model_payload, route = self.model_router.complete_json(
                    "intent_verification",
                    (
                        "Return JSON with keys achieved (boolean), confidence (0 to 1), "
                        "and reason.\n\nIntent:\n"
                        f"{intent.get('description', '')}\n\nEvidence:\n{evidence_text[:6000]}"
                    ),
                    system_prompt=(
                        "You verify whether objective evidence proves a software task intent. "
                        "Be conservative and do not infer completion from plans or promises."
                    ),
                )
                model_confidence = max(0.0, min(float(model_payload.get("confidence", 0)), 1.0))
                if not model_payload.get("achieved", False):
                    model_confidence = min(model_confidence, 0.49)
                model_reason = str(model_payload.get("reason", ""))[:1000]
                provider = route["provider"]
                model = route["model"]
            except (ModelUnavailableError, ValueError, TypeError, KeyError):
                model_reason = "Model verification was unavailable or malformed"

        confidence = deterministic_confidence
        if model_confidence is not None:
            confidence = round(max(confidence, 0.65 * model_confidence + 0.35 * confidence), 4)

        threshold = self.config.intent_completion_threshold
        suggestion_threshold = self.config.intent_suggestion_threshold
        auto_completed = bool(
            allow_auto_complete
            and self.config.intent_auto_complete
            and objective_evidence
            and confidence >= threshold
        )
        if auto_completed:
            decision = "completed"
        elif confidence >= suggestion_threshold:
            decision = "suggested"
        else:
            decision = "incomplete"

        evaluation = {
            "decision": decision,
            "confidence": confidence,
            "deterministic_confidence": deterministic_confidence,
            "model_confidence": model_confidence,
            "objective_evidence": objective_evidence,
            "evidence_memory_ids": [memory["id"] for memory in evidence_memories],
            "summary": summary[:2000],
            "reason": model_reason,
            "provider": provider,
            "model": model,
            "evaluator_version": EVALUATOR_VERSION,
            "evaluated_at": utc_now_iso(),
            "evaluated_by": actor_id,
        }
        context = dict(intent.get("context") or {})
        history = list(context.get("completion_history") or [])[-19:]
        history.append(evaluation)
        context["completion_evaluation"] = evaluation
        context["completion_history"] = history
        updates: dict[str, Any] = {"context": context}
        if auto_completed:
            updates["status"] = "completed"
            context["completed_automatically"] = True
            context["completed_at"] = evaluation["evaluated_at"]
        self.storage.update_intent(intent["id"], **updates)
        return {"intent_id": intent["id"], **evaluation}

    def evaluate_repository(
        self,
        repo_id: str,
        *,
        summary: str,
        memory_ids: list[str],
        actor_id: str,
        allow_auto_complete: bool = True,
    ) -> list[dict[str, Any]]:
        return [
            self.evaluate(
                intent,
                summary=summary,
                memory_ids=memory_ids,
                actor_id=actor_id,
                allow_auto_complete=allow_auto_complete,
            )
            for intent in self.storage.get_active_intents(repo_id=repo_id, status="active")
        ]
