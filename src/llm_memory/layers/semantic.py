"""
Semantic Memory Layer

Stores extracted knowledge - patterns, rules, and facts:
- "The auth module is fragile and needs careful testing"
- "We prefer explicit errors over silent failures"
- "Connection pooling is tuned for current load"

Unlike episodic memories (events), semantic memories are:
- Timeless (not tied to specific events)
- General (apply across contexts)
- Compressed (distilled from multiple episodes)
"""

from datetime import datetime
from typing import List, Dict, Any, Optional
from enum import Enum

from llm_memory.core.storage import Storage


class KnowledgeCategory(str, Enum):
    """Categories of semantic knowledge."""

    # Facts about the codebase
    INVARIANT = "invariant"  # Things that must always be true
    BEHAVIOR = "behavior"  # How things work
    CONTRACT = "contract"  # API/interface contracts

    # Learned patterns
    PATTERN = "pattern"  # Design patterns used
    ANTIPATTERN = "antipattern"  # Things to avoid
    BEST_PRACTICE = "best_practice"  # Recommended approaches

    # Warnings
    FRAGILE_AREA = "fragile_area"  # Areas needing care
    KNOWN_ISSUE = "known_issue"  # Known problems
    GOTCHA = "gotcha"  # Non-obvious traps

    # Preferences
    CONVENTION = "convention"  # Team/project conventions
    PREFERENCE = "preference"  # Stylistic preferences

    # General
    FACT = "fact"  # General knowledge


class SemanticMemory:
    """
    Manages semantic (knowledge-based) memories.

    Semantic memories represent extracted knowledge that applies
    generally, not tied to specific events. They're often
    compressed from multiple episodic memories.
    """

    def __init__(self, storage: Storage):
        self.storage = storage

    def establish(
        self,
        knowledge: str,
        category: KnowledgeCategory = KnowledgeCategory.FACT,
        importance: float = 0.6,
        applies_to: List[str] = None,
        source_episodes: List[str] = None,
        tags: List[str] = None
    ) -> str:
        """
        Establish a piece of knowledge.

        Args:
            knowledge: The knowledge/fact/rule
            category: Type of knowledge
            importance: How important (0.0 to 1.0)
            applies_to: What this applies to (files, modules, etc.)
            source_episodes: IDs of episodes this was derived from
            tags: Tags for organization

        Returns:
            Memory ID

        Example:
            semantic.establish(
                "The auth module requires mutex locks for token refresh",
                category=KnowledgeCategory.INVARIANT,
                applies_to=["auth/token.py", "auth/refresh.py"],
                importance=0.9
            )
        """
        metadata = {
            "established_at": datetime.now().isoformat(),
            "applies_to": applies_to or []
        }

        return self.storage.store_memory(
            content=knowledge,
            layer="semantic",
            category=category.value if isinstance(category, KnowledgeCategory) else category,
            importance=importance,
            tags=tags or [],
            metadata=metadata,
            source_ids=source_episodes or []
        )

    def warn(
        self,
        area: str,
        warning: str,
        severity: float = 0.7,
        tags: List[str] = None
    ) -> str:
        """
        Establish a warning about a fragile area.

        Args:
            area: What area (file, module, feature)
            warning: What to watch out for
            severity: How serious (0.0 to 1.0)
            tags: Tags for organization

        Returns:
            Memory ID

        Example:
            semantic.warn(
                area="database/migrations",
                warning="Always backup before running. Rollbacks are unreliable.",
                severity=0.9
            )
        """
        knowledge = f"WARNING [{area}]: {warning}"

        return self.establish(
            knowledge=knowledge,
            category=KnowledgeCategory.FRAGILE_AREA,
            importance=severity,
            applies_to=[area],
            tags=["warning"] + (tags or [])
        )

    def convention(
        self,
        rule: str,
        rationale: str = None,
        importance: float = 0.5,
        tags: List[str] = None
    ) -> str:
        """
        Establish a convention or best practice.

        Args:
            rule: The convention/rule
            rationale: Why this convention exists
            importance: How strictly enforced
            tags: Tags for organization

        Returns:
            Memory ID

        Example:
            semantic.convention(
                rule="All API endpoints return explicit error objects",
                rationale="Prevents silent failures and aids debugging"
            )
        """
        knowledge = rule
        if rationale:
            knowledge += f" (Rationale: {rationale})"

        return self.establish(
            knowledge=knowledge,
            category=KnowledgeCategory.CONVENTION,
            importance=importance,
            tags=tags
        )

    def known_issue(
        self,
        issue: str,
        workaround: str = None,
        priority: float = 0.5,
        tags: List[str] = None
    ) -> str:
        """
        Document a known issue.

        Args:
            issue: Description of the issue
            workaround: How to work around it (if any)
            priority: How important to fix
            tags: Tags for organization

        Returns:
            Memory ID

        Example:
            semantic.known_issue(
                issue="Memory leak in image processing under high load",
                workaround="Restart worker every 1000 requests",
                priority=0.6
            )
        """
        knowledge = f"Known Issue: {issue}"
        if workaround:
            knowledge += f"\nWorkaround: {workaround}"

        return self.establish(
            knowledge=knowledge,
            category=KnowledgeCategory.KNOWN_ISSUE,
            importance=priority,
            tags=["known_issue"] + (tags or [])
        )

    def search(
        self,
        query: str,
        category: KnowledgeCategory = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Search semantic knowledge.

        Args:
            query: Search query (semantic search)
            category: Filter by category
            limit: Maximum results

        Returns:
            List of matching knowledge
        """
        return self.storage.search_memories(
            query=query,
            layer="semantic",
            category=category.value if category else None,
            limit=limit
        )

    def get_warnings(self, area: str = None) -> List[Dict[str, Any]]:
        """Get warnings, optionally filtered by area."""
        results = self.storage.list_memories(
            layer="semantic",
            category=KnowledgeCategory.FRAGILE_AREA.value,
            limit=100
        )

        if area:
            results = [
                r for r in results
                if area in r.get("metadata", {}).get("applies_to", [])
                or area.lower() in r["content"].lower()
            ]

        return results

    def get_conventions(self) -> List[Dict[str, Any]]:
        """Get all established conventions."""
        return self.storage.list_memories(
            layer="semantic",
            category=KnowledgeCategory.CONVENTION.value,
            limit=100,
            order_by="importance DESC"
        )

    def get_known_issues(self) -> List[Dict[str, Any]]:
        """Get all known issues."""
        return self.storage.list_memories(
            layer="semantic",
            category=KnowledgeCategory.KNOWN_ISSUE.value,
            limit=100,
            order_by="importance DESC"
        )

    def relevant_for(
        self,
        files: List[str] = None,
        query: str = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get knowledge relevant to specific files or a query.

        Args:
            files: List of file paths to get knowledge for
            query: Optional query to also consider
            limit: Maximum results

        Returns:
            Relevant knowledge sorted by relevance
        """
        results = []

        # Search by files
        if files:
            for file in files:
                file_results = self.search(file, limit=5)
                results.extend(file_results)

        # Search by query
        if query:
            query_results = self.search(query, limit=limit)
            results.extend(query_results)

        # Deduplicate and sort by similarity/importance
        seen_ids = set()
        unique_results = []
        for r in results:
            if r["id"] not in seen_ids:
                seen_ids.add(r["id"])
                # Combined score of similarity and importance
                r["relevance"] = (
                    r.get("similarity", 0.5) * 0.6 +
                    r.get("importance", 0.5) * 0.4
                )
                unique_results.append(r)

        unique_results.sort(key=lambda x: x["relevance"], reverse=True)
        return unique_results[:limit]
