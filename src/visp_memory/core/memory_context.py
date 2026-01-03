"""Context generation helpers for the Memory facade."""

from __future__ import annotations

from typing import Any, Dict

from visp_memory.core.clock import utc_now


def build_context(
    memory: Any,
    include_history: bool,
    include_knowledge: bool,
    include_intent: bool,
    repo_id: str = None,
) -> Dict[str, Any]:
    """Build structured context from a Memory instance."""
    context: Dict[str, Any] = {}
    repo_id = repo_id if repo_id is not None else memory.config.repo_id

    if include_intent:
        intent_summary = memory.intent.summarize(repo_id=repo_id)
        context["intent"] = {
            "current_focus": (
                intent_summary["focus"]["description"] if intent_summary["focus"] else None
            ),
            "current_task": (
                intent_summary["current_task"]["description"]
                if intent_summary["current_task"]
                else None
            ),
            "constraints": intent_summary["constraints"],
            "goals": [goal["description"] for goal in intent_summary["all_goals"][:5]],
        }

    if include_knowledge:
        warnings = memory.semantic.get_warnings(repo_id=repo_id)[:10]
        conventions = memory.semantic.get_conventions(repo_id=repo_id)[:10]
        known_issues = memory.semantic.get_known_issues(repo_id=repo_id)[:5]

        context["knowledge"] = {
            "warnings": [warning["content"] for warning in warnings],
            "conventions": [convention["content"] for convention in conventions],
            "known_issues": [issue["content"] for issue in known_issues],
        }

    if include_history:
        recent = memory.episodic.recent(limit=10, repo_id=repo_id)
        context["history"] = {
            "recent_events": [
                {
                    "event": event["content"],
                    "category": event["category"],
                    "when": event["created_at"],
                }
                for event in recent
            ]
        }

    context["meta"] = {
        "generated_at": utc_now().isoformat(),
        "stats": memory._storage.get_stats(repo_id=repo_id),
    }
    return context


def format_context_text(context: Dict[str, Any]) -> str:
    """Format structured context as human/LLM readable text."""
    lines = ["# Project Memory Context", ""]

    if "intent" in context:
        intent = context["intent"]
        lines.append("## Current Direction")

        if intent["current_focus"]:
            lines.append(f"**Focus:** {intent['current_focus']}")

        if intent["current_task"]:
            lines.append(f"**Working on:** {intent['current_task']}")

        if intent["constraints"]:
            lines.append("\n**Constraints:**")
            for constraint in intent["constraints"]:
                lines.append(f"- {constraint}")

        if intent["goals"]:
            lines.append("\n**Active Goals:**")
            for goal in intent["goals"]:
                if not goal.startswith("WORKING ON:") and not goal.startswith("CONSTRAINT:"):
                    lines.append(f"- {goal}")

        lines.append("")

    if "knowledge" in context:
        knowledge = context["knowledge"]

        if knowledge["warnings"]:
            lines.append("## Warnings")
            for warning in knowledge["warnings"]:
                lines.append(f"- {warning}")
            lines.append("")

        if knowledge["conventions"]:
            lines.append("## Conventions")
            for convention in knowledge["conventions"]:
                lines.append(f"- {convention}")
            lines.append("")

        if knowledge["known_issues"]:
            lines.append("## Known Issues")
            for issue in knowledge["known_issues"]:
                lines.append(f"- {issue}")
            lines.append("")

    if "history" in context and context["history"]["recent_events"]:
        lines.append("## Recent Activity")
        for event in context["history"]["recent_events"][:5]:
            lines.append(f"- [{event['category']}] {event['event']}")
        lines.append("")

    return "\n".join(lines)
