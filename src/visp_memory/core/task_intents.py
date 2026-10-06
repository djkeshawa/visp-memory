"""Intent selection and text shaping for task memory briefs."""

import re
from typing import Any, Optional


def unique_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))


def as_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def terms(value: str) -> list[str]:
    return re.findall(r"[a-z0-9_./-]+", value.casefold())


def select_intent(
    intents: list[dict[str, Any]], task: str, *, intent_id: Optional[str]
) -> Optional[dict[str, Any]]:
    if intent_id:
        return next((intent for intent in intents if intent.get("id") == intent_id), None)
    if not intents:
        return None
    task_terms = set(terms(task))

    def score(intent: dict[str, Any]) -> tuple[float, int, str]:
        context = intent.get("context") or {}
        searchable = " ".join(
            [
                str(intent.get("description") or ""),
                *as_strings(context.get("constraints")),
                *as_strings(context.get("acceptance_criteria")),
            ]
        )
        intent_terms = set(terms(searchable))
        overlap = len(task_terms.intersection(intent_terms)) / max(len(task_terms), 1)
        return overlap, int(intent.get("priority") or 0), str(intent.get("created_at") or "")

    return max(intents, key=score)


def intent_payload(intent: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not intent:
        return None
    context = intent.get("context") or {}
    return {
        "id": intent.get("id"),
        "description": intent.get("description"),
        "priority": intent.get("priority", 0),
        "status": intent.get("status", "active"),
        "acceptance_criteria": as_strings(context.get("acceptance_criteria")),
    }


def constraints(
    explicit: list[str], intent: Optional[dict[str, Any]]
) -> list[str]:
    if not intent:
        return unique_strings(explicit)
    context = intent.get("context") or {}
    avoid = [f"Avoid: {item}" for item in as_strings(context.get("avoid"))]
    return unique_strings(
        [*explicit, *as_strings(context.get("constraints")), *avoid]
    )
