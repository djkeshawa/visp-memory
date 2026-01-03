"""Deterministic, evidence-backed task preparation for LLM clients."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from typing import Any, Optional

from visp_memory.core.context_compiler import ContextCompiler
from visp_memory.core.tokens import estimate_tokens

SECTION_ORDER = ("warnings", "decisions", "knowledge", "history")
WARNING_CATEGORIES = {
    "fragile_area",
    "gotcha",
    "known_issue",
    "warning",
    "bug_found",
    "error",
}
DECISION_CATEGORIES = {
    "architecture_decision",
    "decision",
    "convention",
    "invariant",
    "principle",
}
ACTION_TERMS = {
    "debug": {"debug", "diagnose", "failure", "fix", "bug", "error"},
    "implement": {"add", "build", "create", "implement", "introduce", "ship"},
    "refactor": {"refactor", "restructure", "simplify", "migrate", "replace"},
    "review": {"audit", "inspect", "review", "verify", "check"},
    "test": {"test", "benchmark", "evaluate", "validate"},
    "deploy": {"deploy", "release", "publish", "docker", "rollout"},
    "document": {"document", "explain", "readme", "guide"},
}
STOP_WORDS = {
    "about",
    "after",
    "again",
    "also",
    "and",
    "are",
    "before",
    "build",
    "can",
    "for",
    "from",
    "have",
    "into",
    "make",
    "need",
    "our",
    "that",
    "the",
    "their",
    "this",
    "use",
    "using",
    "want",
    "with",
}


class TaskMemoryBriefCompiler:
    """Prepare a compact task brief without requiring an LLM call."""

    def __init__(self, storage):
        self.storage = storage

    @staticmethod
    def _unique(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))

    @staticmethod
    def _as_strings(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple, set)):
            return [str(item) for item in value if item is not None]
        return [str(value)]

    @staticmethod
    def _terms(value: str) -> list[str]:
        return re.findall(r"[a-z0-9_./-]+", value.casefold())

    def _task_profile(
        self, task: str, *, files: list[str], symbols: list[str]
    ) -> dict[str, Any]:
        terms = self._terms(task)
        term_set = set(terms)
        action = "general"
        action_score = 0
        for candidate, signals in ACTION_TERMS.items():
            score = len(term_set.intersection(signals))
            if score > action_score:
                action = candidate
                action_score = score

        code_references = re.findall(r"`([^`\r\n]{1,200})`", task)
        path_references = re.findall(
            r"(?:[A-Za-z0-9_.-]+[/\\])+[A-Za-z0-9_.\\/-]+", task
        )
        inferred_files = [
            reference
            for reference in [*code_references, *path_references]
            if "/" in reference or "\\" in reference
        ]
        inferred_symbols = [
            reference
            for reference in code_references
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:()-]*", reference)
            and reference not in inferred_files
        ]
        keywords = self._unique(
            [
                term
                for term in terms
                if len(term) > 2
                and term not in STOP_WORDS
                and "/" not in term
                and "\\" not in term
            ]
        )[:20]
        return {
            "action": action,
            "keywords": keywords,
            "files": self._unique([*files, *inferred_files]),
            "symbols": self._unique([*symbols, *inferred_symbols]),
        }

    def _match_intent(
        self, task: str, *, repo_id: Optional[str], intent_id: Optional[str]
    ) -> Optional[dict[str, Any]]:
        intents = self.storage.get_active_intents(repo_id=repo_id, status="active")
        if intent_id:
            return next((intent for intent in intents if intent.get("id") == intent_id), None)
        if not intents:
            return None
        task_terms = set(self._terms(task))

        def score(intent: dict[str, Any]) -> tuple[float, int, str]:
            context = intent.get("context") or {}
            searchable = " ".join(
                [
                    str(intent.get("description") or ""),
                    *self._as_strings(context.get("constraints")),
                    *self._as_strings(context.get("acceptance_criteria")),
                ]
            )
            intent_terms = set(self._terms(searchable))
            overlap = len(task_terms.intersection(intent_terms)) / max(len(task_terms), 1)
            return overlap, int(intent.get("priority") or 0), str(intent.get("created_at") or "")

        return max(intents, key=score)

    def _intent_payload(self, intent: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if not intent:
            return None
        context = intent.get("context") or {}
        return {
            "id": intent.get("id"),
            "description": intent.get("description"),
            "priority": intent.get("priority", 0),
            "status": intent.get("status", "active"),
            "acceptance_criteria": self._as_strings(context.get("acceptance_criteria")),
        }

    def _constraints(
        self, explicit: list[str], intent: Optional[dict[str, Any]]
    ) -> list[str]:
        if not intent:
            return self._unique(explicit)
        context = intent.get("context") or {}
        avoid = [f"Avoid: {item}" for item in self._as_strings(context.get("avoid"))]
        return self._unique(
            [*explicit, *self._as_strings(context.get("constraints")), *avoid]
        )

    @staticmethod
    def _section(item: dict[str, Any]) -> str:
        category = str(item.get("category") or "").casefold()
        tags = {str(tag).casefold() for tag in item.get("tags") or []}
        if category in WARNING_CATEGORIES or "warning" in tags:
            return "warnings"
        if category in DECISION_CATEGORIES:
            return "decisions"
        if item.get("layer") == "episodic":
            return "history"
        return "knowledge"

    @staticmethod
    def _score(item: dict[str, Any]) -> float:
        return float(item.get("relevance_score") or 0.0)

    def _candidate_order(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped = {
            section: sorted(
                [item for item in candidates if self._section(item) == section],
                key=lambda item: (-self._score(item), str(item.get("id"))),
            )
            for section in SECTION_ORDER
        }
        ordered: list[dict[str, Any]] = []
        for section in SECTION_ORDER:
            if grouped[section]:
                ordered.append(grouped[section][0])
        seeded = {item["id"] for item in ordered}
        ordered.extend(
            sorted(
                [item for item in candidates if item["id"] not in seeded],
                key=lambda item: (
                    -self._score(item),
                    SECTION_ORDER.index(self._section(item)),
                    str(item.get("id")),
                ),
            )
        )
        return ordered

    @staticmethod
    def _brief_item(item: dict[str, Any], citation: str) -> dict[str, Any]:
        return {
            **item,
            "citation": citation,
        }

    def _shape_sections(
        self, selected: list[dict[str, Any]]
    ) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
        sections: dict[str, list[dict[str, Any]]] = {
            section: [] for section in SECTION_ORDER
        }
        citations = []
        for index, item in enumerate(selected, start=1):
            citation = f"M{index}"
            brief_item = self._brief_item(item, citation)
            sections[self._section(item)].append(brief_item)
            citations.append(
                {
                    "citation": citation,
                    "memory_id": item["id"],
                    "layer": item.get("layer"),
                    "category": item.get("category"),
                    "repo_id": item.get("repo_id"),
                    "confidence": item.get("confidence"),
                    "source_revision": item.get("source_revision"),
                    "source_hash": item.get("source_hash"),
                    "observed_at": item.get("observed_at"),
                    "valid_from": item.get("valid_from"),
                    "valid_to": item.get("valid_to"),
                    "files": item.get("files") or [],
                    "symbols": item.get("symbols") or [],
                }
            )
        return sections, citations

    def _contradictions(
        self,
        selected: list[dict[str, Any]],
        *,
        repo_id: Optional[str],
        memory_filter: Optional[Callable[[dict[str, Any]], bool]],
    ) -> list[dict[str, Any]]:
        selected_by_id = {item["id"]: item for item in selected}
        if not selected_by_id:
            return []
        citations = {
            item["id"]: f"M{index}" for index, item in enumerate(selected, start=1)
        }
        results = []
        for relationship in self.storage.get_all_relationships(repo_id=repo_id):
            relationship_type = str(relationship.get("relationship") or "").casefold()
            if relationship_type not in {"contradicts", "conflicts_with"}:
                continue
            source_id = relationship.get("source_id")
            target_id = relationship.get("target_id")
            if source_id not in selected_by_id and target_id not in selected_by_id:
                continue
            selected_id = source_id if source_id in selected_by_id else target_id
            other_id = target_id if selected_id == source_id else source_id
            other = selected_by_id.get(other_id) or self.storage.get_memory(other_id)
            if not other or (memory_filter and not memory_filter(other)):
                continue
            results.append(
                {
                    "relationship_id": relationship.get("id"),
                    "relationship": relationship_type,
                    "citation": citations[selected_id],
                    "memory_id": selected_id,
                    "other_memory_id": other_id,
                    "other_status": other.get("status", "active"),
                    "other_snippet": str(other.get("content") or "")[:240],
                    "evidence": relationship.get("evidence") or {},
                }
            )
        return sorted(
            results,
            key=lambda item: (
                item["citation"],
                str(item.get("relationship_id") or ""),
            ),
        )

    @staticmethod
    def _render(
        *,
        task: str,
        repo_id: Optional[str],
        profile: dict[str, Any],
        intent: Optional[dict[str, Any]],
        constraints: list[str],
        sections: dict[str, list[dict[str, Any]]],
        contradictions: list[dict[str, Any]],
        unknowns: list[str],
    ) -> str:
        lines = [
            "# Task Memory Brief",
            "",
            f"Task: {task.strip()}",
            f"Project: {repo_id or 'unscoped'}",
            f"Action: {profile['action']}",
        ]
        if profile["files"]:
            lines.append(f"Files: {', '.join(profile['files'])}")
        if profile["symbols"]:
            lines.append(f"Symbols: {', '.join(profile['symbols'])}")
        if intent:
            lines.extend(["", "## Active Intent", f"- {intent['description']}"])
            for criterion in intent.get("acceptance_criteria") or []:
                lines.append(f"- Acceptance: {criterion}")
        if constraints:
            lines.extend(["", "## Constraints"])
            lines.extend(f"- {constraint}" for constraint in constraints)
        titles = {
            "warnings": "Warnings",
            "decisions": "Decisions and Conventions",
            "knowledge": "Relevant Knowledge",
            "history": "Relevant History",
        }
        for section in SECTION_ORDER:
            if not sections[section]:
                continue
            lines.extend(["", f"## {titles[section]}"])
            lines.extend(
                f"- [{item['citation']}] {item['content']}" for item in sections[section]
            )
        if contradictions:
            lines.extend(["", "## Contradictions"])
            for item in contradictions:
                reason = (item.get("evidence") or {}).get("reason")
                suffix = f" Evidence: {reason}" if reason else ""
                lines.append(
                    f"- [{item['citation']}] conflicts with memory "
                    f"`{item['other_memory_id']}` ({item['other_status']}): "
                    f"{item['other_snippet']}.{suffix}"
                )
        if unknowns:
            lines.extend(["", "## Unknowns"])
            lines.extend(f"- {unknown}" for unknown in unknowns)
        return "\n".join(lines).strip()

    @staticmethod
    def _truncate_to_budget(text: str, token_budget: int) -> tuple[str, bool]:
        if estimate_tokens(text) <= token_budget:
            return text, False
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if estimate_tokens(text[:middle]) <= max(token_budget - 1, 1):
                low = middle
            else:
                high = middle - 1
        return text[:low].rstrip() + "...", True

    def prepare(
        self,
        task: str,
        *,
        repo_id: Optional[str],
        token_budget: int = 2000,
        as_of=None,
        files: Optional[list[str]] = None,
        symbols: Optional[list[str]] = None,
        intent_id: Optional[str] = None,
        constraints: Optional[list[str]] = None,
        previous_fingerprint: Optional[str] = None,
        min_confidence: float = 0.0,
        memory_filter: Optional[Callable[[dict[str, Any]], bool]] = None,
    ) -> dict[str, Any]:
        files = files or []
        symbols = symbols or []
        token_budget = max(64, int(token_budget))
        profile = self._task_profile(task, files=files, symbols=symbols)
        matched_intent = self._match_intent(
            task, repo_id=repo_id, intent_id=intent_id
        )
        intent = self._intent_payload(matched_intent)
        resolved_constraints = self._constraints(constraints or [], matched_intent)
        query = " ".join(
            [
                task,
                str(intent.get("description") if intent else ""),
                *resolved_constraints,
            ]
        )[:20000]
        candidate_context = ContextCompiler(self.storage).compile(
            query,
            repo_id=repo_id,
            token_budget=min(100000, max(4000, token_budget * 4)),
            as_of=as_of,
            files=profile["files"],
            symbols=profile["symbols"],
            min_confidence=min_confidence,
            memory_filter=memory_filter,
        )
        candidates = candidate_context["items"]
        unknowns = []
        if not intent:
            unknowns.append(
                "No active intent matched this task. Confirm the desired outcome before "
                "making broad or irreversible changes."
            )
        if not candidates:
            unknowns.append(
                "No sufficiently relevant current memory evidence was found. Inspect the "
                "source of truth instead of inferring project behavior."
            )
        for file_path in profile["files"]:
            if candidates and not any(
                file_path in (item.get("files") or []) for item in candidates
            ):
                unknowns.append(f"No cited memory evidence covers `{file_path}`.")

        selected: list[dict[str, Any]] = []
        for candidate in self._candidate_order(candidates):
            trial = [*selected, candidate]
            sections, _ = self._shape_sections(trial)
            contradictions = self._contradictions(
                trial, repo_id=repo_id, memory_filter=memory_filter
            )
            rendered = self._render(
                task=task,
                repo_id=repo_id,
                profile=profile,
                intent=intent,
                constraints=resolved_constraints,
                sections=sections,
                contradictions=contradictions,
                unknowns=unknowns,
            )
            if estimate_tokens(rendered) <= token_budget:
                selected = trial

        if candidates and not selected:
            unknowns.append("Relevant evidence did not fit within the requested token budget.")
        sections, citations = self._shape_sections(selected)
        contradictions = self._contradictions(
            selected, repo_id=repo_id, memory_filter=memory_filter
        )
        context = self._render(
            task=task,
            repo_id=repo_id,
            profile=profile,
            intent=intent,
            constraints=resolved_constraints,
            sections=sections,
            contradictions=contradictions,
            unknowns=unknowns,
        )
        context, truncated = self._truncate_to_budget(context, token_budget)
        fingerprint_payload = {
            "task": task,
            "repo_id": repo_id,
            "profile": profile,
            "intent": intent,
            "constraints": resolved_constraints,
            "items": [
                {
                    "id": item["id"],
                    "content": item.get("content"),
                    "confidence": item.get("confidence"),
                    "valid_to": item.get("valid_to"),
                    "source_revision": item.get("source_revision"),
                }
                for item in selected
            ],
            "contradictions": contradictions,
            "unknowns": unknowns,
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:24]
        unchanged = bool(previous_fingerprint and previous_fingerprint == fingerprint)
        abstained = not selected
        section_counts = {name: len(items) for name, items in sections.items()}
        return {
            "schema_version": "1.0",
            "task": task,
            "repo_id": repo_id,
            "as_of": candidate_context["as_of"],
            "task_profile": profile,
            "intent": intent,
            "constraints": resolved_constraints,
            "unknowns": [] if unchanged else unknowns,
            "contradictions": [] if unchanged else contradictions,
            "sections": {name: [] for name in SECTION_ORDER} if unchanged else sections,
            "citations": [] if unchanged else citations,
            "token_budget": token_budget,
            "token_count": 0 if unchanged else estimate_tokens(context),
            "fingerprint": fingerprint,
            "unchanged": unchanged,
            "abstained": abstained,
            "abstention_reason": (
                "Insufficient relevant evidence" if abstained else None
            ),
            "truncated": truncated,
            "context": "" if unchanged else context,
            "retrieval": candidate_context.get("retrieval") or {},
            "metrics": {
                "candidate_count": int(
                    (candidate_context.get("retrieval") or {}).get(
                        "candidate_count", len(candidates)
                    )
                ),
                "selected_count": len(selected),
                "omitted_count": max(
                    0,
                    int(
                        (candidate_context.get("retrieval") or {}).get(
                            "candidate_count", len(candidates)
                        )
                    )
                    - len(selected),
                ),
                "section_counts": section_counts,
            },
        }
