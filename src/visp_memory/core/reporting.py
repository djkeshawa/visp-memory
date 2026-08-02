"""Deterministic memory intelligence reports over portable storage APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

THRESHOLDS = {
    "high_impact_importance": 0.75,
    "stale_intent_days": 30,
    "ambiguous_confidence_score": 0.5,
    "section_limit": 10,
}

SECTION_TITLES = {
    "high_impact_memories": "High-impact memories",
    "fragile_areas": "Fragile areas",
    "stale_intents": "Stale intents",
    "ambiguous_relationships": "Ambiguous relationships",
    "isolated_warnings": "Isolated warnings",
    "contradiction_candidates": "Contradiction candidates",
    "cross_repo_risks": "Cross-repo risks",
    "suggested_questions": "Suggested questions",
}


class MemoryIntelligenceReporter:
    """Generate compact, deterministic memory health reports."""

    def __init__(self, storage):
        self.storage = storage

    def generate(self, repo_id: str | None = None, limit: int = 10) -> dict[str, Any]:
        section_limit = max(1, min(int(limit or THRESHOLDS["section_limit"]), 50))
        memories = self._list_memories(repo_id)
        relationships = self._relationships(repo_id)
        intents = self._intents(repo_id)
        as_of = self._as_of(memories, intents)

        sections = {
            "high_impact_memories": self._section(
                "high_impact_memories",
                "stored_fact",
                self._high_impact_memories(memories, section_limit),
            ),
            "fragile_areas": self._section(
                "fragile_areas",
                "stored_fact",
                self._fragile_areas(memories, section_limit),
            ),
            "stale_intents": self._section(
                "stale_intents",
                "inferred_recommendation",
                self._stale_intents(intents, as_of, section_limit),
            ),
            "ambiguous_relationships": self._section(
                "ambiguous_relationships",
                "inferred_recommendation",
                self._ambiguous_relationships(relationships, section_limit),
            ),
            "isolated_warnings": self._section(
                "isolated_warnings",
                "inferred_recommendation",
                self._isolated_warnings(memories, relationships, section_limit),
            ),
            "contradiction_candidates": self._section(
                "contradiction_candidates",
                "stored_fact",
                self._contradiction_candidates(memories, section_limit),
            ),
            "cross_repo_risks": self._section(
                "cross_repo_risks",
                "stored_fact",
                self._cross_repo_risks(memories, relationships, section_limit),
            ),
        }
        sections["suggested_questions"] = self._section(
            "suggested_questions",
            "inferred_recommendation",
            self._suggested_questions(sections),
        )

        return {
            "schema_version": "1.0",
            "repo_id": repo_id,
            "as_of": as_of.isoformat() if as_of else None,
            "thresholds": dict(THRESHOLDS),
            "summary": {
                "total_memories": len(memories),
                "total_relationships": len(relationships),
                "active_intents": len(intents),
                "non_empty_sections": sum(1 for section in sections.values() if section["items"]),
            },
            "sections": sections,
        }

    @staticmethod
    def format_text(report: dict[str, Any]) -> str:
        lines = ["# Memory Intelligence Report"]
        if report.get("repo_id"):
            lines.append(f"Repo: {report['repo_id']}")
        if report.get("as_of"):
            lines.append(f"As of: {report['as_of']}")

        summary = report["summary"]
        lines.append(
            "\n"
            f"Memories: {summary['total_memories']} | "
            f"Relationships: {summary['total_relationships']} | "
            f"Active intents: {summary['active_intents']}"
        )

        lines.append("\n## Thresholds")
        for key, value in sorted(report["thresholds"].items()):
            lines.append(f"- {key}: {value}")

        for key, section in report["sections"].items():
            lines.append(f"\n## {section.get('title') or SECTION_TITLES.get(key, key)}")
            lines.append(f"Kind: {section['kind']}")
            if not section["items"]:
                lines.append("- No findings.")
                continue
            for item in section["items"]:
                label = item.get("title") or item.get("id") or "finding"
                reason = item.get("reason", "")
                lines.append(f"- {label}: {reason}")

        return "\n".join(lines)

    def _list_memories(self, repo_id: str | None) -> list[dict[str, Any]]:
        try:
            return self.storage.list_memories(repo_id=repo_id, status="active", limit=1000)
        except TypeError:
            return self.storage.list_memories(repo_id=repo_id, limit=1000)

    def _relationships(self, repo_id: str | None) -> list[dict[str, Any]]:
        try:
            return self.storage.get_all_relationships(repo_id=repo_id)
        except TypeError:
            return self.storage.get_all_relationships()

    def _intents(self, repo_id: str | None) -> list[dict[str, Any]]:
        try:
            return self.storage.get_active_intents(repo_id=repo_id, status="active")
        except TypeError:
            return self.storage.get_active_intents(repo_id=repo_id)

    def _high_impact_memories(
        self, memories: list[dict[str, Any]], limit: int
    ) -> list[dict[str, Any]]:
        items = []
        for memory in memories:
            importance = _float(memory.get("importance"), 0.5)
            if importance >= THRESHOLDS["high_impact_importance"]:
                items.append(
                    _item(
                        "memory",
                        memory["id"],
                        _snippet(memory.get("content", "")),
                        "Stored memory importance is above threshold.",
                        {
                            "importance": importance,
                            "layer": memory.get("layer"),
                            "category": memory.get("category"),
                        },
                    )
                )
        return sorted(items, key=lambda item: (-item["facts"]["importance"], item["id"]))[:limit]

    def _fragile_areas(self, memories: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        fragile_categories = {"negative", "fragile_area", "known_issue", "warning"}
        items = [
            _item(
                "memory",
                memory["id"],
                _snippet(memory.get("content", "")),
                "Stored warning or fragile-area memory.",
                {"category": memory.get("category"), "layer": memory.get("layer")},
            )
            for memory in memories
            if memory.get("category") in fragile_categories
            or "fragile" in str(memory.get("content", "")).lower()
        ]
        return sorted(items, key=lambda item: item["id"])[:limit]

    def _stale_intents(
        self, intents: list[dict[str, Any]], as_of: datetime | None, limit: int
    ) -> list[dict[str, Any]]:
        if as_of is None:
            return []
        items = []
        for intent in intents:
            created_at = _parse_datetime(intent.get("created_at"))
            if created_at is None:
                continue
            age_days = max(0, (as_of - created_at).days)
            if age_days >= THRESHOLDS["stale_intent_days"]:
                items.append(
                    _item(
                        "intent",
                        intent["id"],
                        _snippet(intent.get("description", "")),
                        "Active intent age is above stale threshold.",
                        {"age_days": age_days, "priority": intent.get("priority")},
                    )
                )
        return sorted(items, key=lambda item: (-item["facts"]["age_days"], item["id"]))[:limit]

    def _ambiguous_relationships(
        self, relationships: list[dict[str, Any]], limit: int
    ) -> list[dict[str, Any]]:
        items = []
        for relationship in relationships:
            evidence = relationship.get("evidence") or {}
            confidence = evidence.get("confidence", "ambiguous")
            score = _float(evidence.get("confidence_score"), 0.5)
            if confidence == "ambiguous" or score <= THRESHOLDS["ambiguous_confidence_score"]:
                rel_id = relationship.get("id") or (
                    f"{relationship.get('source_id')}:{relationship.get('target_id')}:"
                    f"{relationship.get('relationship')}"
                )
                items.append(
                    _item(
                        "relationship",
                        rel_id,
                        relationship.get("relationship", "related"),
                        evidence.get("reason") or "Relationship confidence needs review.",
                        {
                            "confidence": confidence,
                            "confidence_score": score,
                            "source_id": relationship.get("source_id"),
                            "target_id": relationship.get("target_id"),
                        },
                    )
                )
        return sorted(items, key=lambda item: (item["facts"]["confidence_score"], item["id"]))[
            :limit
        ]

    def _isolated_warnings(
        self,
        memories: list[dict[str, Any]],
        relationships: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        connected = {
            memory_id
            for relationship in relationships
            for memory_id in (relationship.get("source_id"), relationship.get("target_id"))
            if memory_id
        }
        items = [
            _item(
                "memory",
                memory["id"],
                _snippet(memory.get("content", "")),
                "Warning has no relationship evidence path.",
                {"category": memory.get("category")},
            )
            for memory in memories
            if memory.get("category")
            in {"negative", "fragile_area", "known_issue", "warning"}
            and memory.get("id") not in connected
        ]
        return sorted(items, key=lambda item: item["id"])[:limit]

    def _contradiction_candidates(
        self, memories: list[dict[str, Any]], limit: int
    ) -> list[dict[str, Any]]:
        items = []
        for memory in memories:
            metadata = memory.get("metadata") or {}
            flags = memory.get("quality_flags") or []
            if metadata.get("conflict") or "contradiction" in flags:
                items.append(
                    _item(
                        "memory",
                        memory["id"],
                        _snippet(memory.get("content", "")),
                        "Stored conflict metadata or contradiction flag is present.",
                        {
                            "quality_flags": flags,
                            "has_conflict_metadata": bool(metadata.get("conflict")),
                        },
                    )
                )
        return sorted(items, key=lambda item: item["id"])[:limit]

    def _cross_repo_risks(
        self,
        memories: list[dict[str, Any]],
        relationships: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        by_id = {memory["id"]: memory for memory in memories}
        items = []
        for relationship in relationships:
            source = by_id.get(relationship.get("source_id"))
            target = by_id.get(relationship.get("target_id"))
            if source and target and source.get("repo_id") != target.get("repo_id"):
                rel_id = relationship.get("id") or (
                    f"{relationship.get('source_id')}:{relationship.get('target_id')}"
                )
                items.append(
                    _item(
                        "relationship",
                        rel_id,
                        relationship.get("relationship", "cross_repo"),
                        "Relationship spans repository scopes.",
                        {
                            "source_repo": source.get("repo_id"),
                            "target_repo": target.get("repo_id"),
                        },
                    )
                )

        for memory in memories:
            content = str(memory.get("content", "")).lower()
            if (
                memory.get("category") in {"breaking_change", "cross_repo_risk"}
                or "cross-repo" in content
            ):
                items.append(
                    _item(
                        "memory",
                        memory["id"],
                        _snippet(memory.get("content", "")),
                        "Stored memory describes cross-repo risk.",
                        {"category": memory.get("category"), "repo_id": memory.get("repo_id")},
                    )
                )
        return sorted(items, key=lambda item: item["id"])[:limit]

    def _suggested_questions(self, sections: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        questions = []
        if sections["ambiguous_relationships"]["items"]:
            questions.append(
                _question(
                    "Which ambiguous relationships should be confirmed or removed?",
                    "Ambiguous relationships reduce trust in graph recall.",
                )
            )
        if sections["stale_intents"]["items"]:
            questions.append(
                _question(
                    "Which stale active intents should be completed, closed, or refreshed?",
                    "Stale intents can bias future recall toward old work.",
                )
            )
        if sections["isolated_warnings"]["items"]:
            questions.append(
                _question(
                    "Which isolated warnings need evidence links to decisions or bugs?",
                    "Warnings without relationship paths are harder to explain.",
                )
            )
        if not questions:
            questions.append(
                _question(
                    "What new evidence would make future memory recall more explainable?",
                    "No immediate report findings were detected.",
                )
            )
        return questions

    @staticmethod
    def _section(key: str, kind: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "key": key,
            "title": SECTION_TITLES[key],
            "kind": kind,
            "thresholds": dict(THRESHOLDS),
            "items": items,
        }

    @staticmethod
    def _as_of(
        memories: list[dict[str, Any]], intents: list[dict[str, Any]]
    ) -> datetime | None:
        timestamps = []
        for item in [*memories, *intents]:
            for key in ("updated_at", "accessed_at", "created_at"):
                parsed = _parse_datetime(item.get(key))
                if parsed is not None:
                    timestamps.append(parsed)
        return max(timestamps) if timestamps else None


def _item(
    item_type: str,
    item_id: str,
    title: str,
    reason: str,
    facts: dict[str, Any],
) -> dict[str, Any]:
    return {
        "type": item_type,
        "id": item_id,
        "title": title,
        "reason": reason,
        "facts": facts,
    }


def _question(question: str, reason: str) -> dict[str, Any]:
    return _item("question", question, question, reason, {})


def _snippet(value: Any, limit: int = 120) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None
