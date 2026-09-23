"""
Episodic Memory Layer

Stores event-based memories - things that happened:
- Decisions made and why
- Problems encountered
- Changes implemented
- Bugs found and fixed

Like human episodic memory, these are specific events that can later
be compressed into semantic knowledge (patterns/rules).
"""

from enum import Enum
from typing import Any, Dict, List

from visp_memory.core.clock import utc_now
from visp_memory.core.storage import BaseStorage
from visp_memory.core.trust import (
    WriteChannel,
    channel_policy,
    parse_write_channel,
    with_channel_provenance,
)
from visp_memory.layers.base import BaseMemoryLayer


class EpisodeCategory(str, Enum):
    """Categories of episodic memories for code projects."""

    # Decisions
    ARCHITECTURE_DECISION = "architecture_decision"
    TRADE_OFF = "trade_off"
    DESIGN_CHOICE = "design_choice"

    # Events
    BUG_FIXED = "bug_fixed"
    BUG_FOUND = "bug_found"
    FEATURE_ADDED = "feature_added"
    REFACTOR = "refactor"
    INCIDENT = "incident"

    # Learning
    DISCOVERY = "discovery"
    INVESTIGATION = "investigation"
    EXPERIMENT = "experiment"

    # General
    NOTE = "note"
    SESSION = "session"


class EpisodicMemory(BaseMemoryLayer):
    """
    Manages episodic (event-based) memories.

    Episodic memories capture specific events with context.
    Over time, they can be compressed into semantic knowledge.
    """

    def __init__(self, storage: BaseStorage):
        super().__init__(storage)

    def record(
        self,
        content: str,
        category: EpisodeCategory = EpisodeCategory.NOTE,
        importance: float = 0.5,
        repo_id: str = None,
        context: Dict[str, Any] = None,
        tags: List[str] = None,
        *,
        _write_channel: WriteChannel | str = WriteChannel.LIBRARY,
    ) -> str:
        """
        Record an episodic memory (something that happened).

        Args:
            content: Description of what happened
            category: Type of episode
            importance: How important (0.0 to 1.0)
            context: Additional context (files involved, etc.)
            tags: Tags for organization

        Returns:
            Memory ID

        Example:
            episodic.record(
                "Fixed race condition in token refresh by adding mutex",
                category=EpisodeCategory.BUG_FIXED,
                importance=0.8,
                context={"files": ["auth/token.py"], "issue": "#142"}
            )
        """
        write_channel = parse_write_channel(_write_channel)
        policy = channel_policy(write_channel)
        metadata = {
            "recorded_at": utc_now().isoformat(),
            **(context or {}),
            "write_channel": write_channel.value,
        }

        return self.storage.store_memory(
            content=content,
            layer="episodic",
            repo_id=repo_id,
            category=category.value if isinstance(category, EpisodeCategory) else category,
            importance=importance,
            tags=with_channel_provenance(tags, write_channel),
            metadata=metadata,
            source=policy.source,
        )

    def decision(
        self,
        what: str,
        why: str,
        alternatives: List[str] = None,
        importance: float = 0.7,
        repo_id: str = None,
        tags: List[str] = None,
        *,
        _write_channel: WriteChannel | str = WriteChannel.LIBRARY,
    ) -> str:
        """
        Record an architecture/design decision.

        Args:
            what: What was decided
            why: Why this choice was made
            alternatives: What alternatives were considered
            importance: How important (default 0.7 for decisions)
            tags: Tags for organization

        Returns:
            Memory ID

        Example:
            episodic.decision(
                what="Use PostgreSQL for primary database",
                why="Need ACID compliance and complex queries",
                alternatives=["MongoDB", "MySQL", "SQLite"]
            )
        """
        content = f"Decision: {what}\nReasoning: {why}"
        if alternatives:
            content += f"\nAlternatives considered: {', '.join(alternatives)}"

        return self.record(
            content=content,
            category=EpisodeCategory.ARCHITECTURE_DECISION,
            importance=importance,
            repo_id=repo_id,
            context={"alternatives": alternatives or []},
            tags=tags,
            _write_channel=_write_channel,
        )

    def bug(
        self,
        description: str,
        cause: str = None,
        fix: str = None,
        files: List[str] = None,
        importance: float = 0.6,
        repo_id: str = None,
        tags: List[str] = None,
        *,
        _write_channel: WriteChannel | str = WriteChannel.LIBRARY,
    ) -> str:
        """
        Record a bug discovery or fix.

        Args:
            description: What the bug was
            cause: Root cause (if known)
            fix: How it was fixed (if fixed)
            files: Files involved
            importance: How important

        Returns:
            Memory ID
        """
        content = f"Bug: {description}"
        if cause:
            content += f"\nCause: {cause}"
        if fix:
            content += f"\nFix: {fix}"

        category = EpisodeCategory.BUG_FIXED if fix else EpisodeCategory.BUG_FOUND

        return self.record(
            content=content,
            category=category,
            importance=importance,
            repo_id=repo_id,
            context={"files": files or []},
            tags=["bug", *(tags or [])],
            _write_channel=_write_channel,
        )

    def discovery(
        self,
        insight: str,
        context: str = None,
        importance: float = 0.5,
        repo_id: str = None,
        *,
        _write_channel: WriteChannel | str = WriteChannel.LIBRARY,
    ) -> str:
        """
        Record a discovery or learning.

        Args:
            insight: What was discovered/learned
            context: How it was discovered
            importance: How important

        Returns:
            Memory ID
        """
        content = f"Discovery: {insight}"
        if context:
            content += f"\nContext: {context}"

        return self.record(
            content=content,
            category=EpisodeCategory.DISCOVERY,
            importance=importance,
            repo_id=repo_id,
            _write_channel=_write_channel,
        )

    def search(
        self,
        query: str,
        category: EpisodeCategory = None,
        limit: int = 10,
        repo_id: str = None,
        environment: Any = None,
        task_type: Any = None,
        as_of: Any = None,
    ) -> List[Dict[str, Any]]:
        """
        Search episodic memories.

        Args:
            query: Search query (semantic search)
            category: Filter by category (EpisodeCategory enum or string)
            limit: Maximum results
            repo_id: Optional repository filter

        Returns:
            List of matching memories
        """
        # Handle both enum and string category inputs
        if isinstance(category, EpisodeCategory):
            category_value = category.value
        elif isinstance(category, str):
            category_value = category
        else:
            category_value = None

        return super().search(
            query=query,
            layer="episodic",
            category=category_value,
            limit=limit,
            repo_id=repo_id,
            environment=environment,
            task_type=task_type,
            as_of=as_of,
        )

    def recent(
        self, limit: int = 20, category: EpisodeCategory = None, repo_id: str = None
    ) -> List[Dict[str, Any]]:
        """Get recent episodic memories."""
        # Accept both an EpisodeCategory enum and a plain string, matching ``search``.
        category_value = category.value if isinstance(category, EpisodeCategory) else category
        return self.list_items(
            layer="episodic",
            category=category_value,
            limit=limit,
            order_by="created_at DESC",
            repo_id=repo_id,
        )

    def get_uncompressed(self, limit: int = 50, repo_id: str = None) -> List[Dict[str, Any]]:
        """Get episodic memories that haven't been compressed yet.

        Excludes episodes already compressed into semantic knowledge (marked via
        ``metadata.compressed_to``) and honors repository scoping. Over-fetches
        before filtering so up to ``limit`` genuinely-uncompressed episodes are
        returned.
        """
        if limit <= 0:
            return []

        page_limit = min(200, max(50, limit * 4))
        offset = 0
        uncompressed = []
        while True:
            episodes = self.list_items(
                layer="episodic",
                limit=page_limit,
                offset=offset,
                order_by="created_at ASC",
                repo_id=repo_id,
            )
            uncompressed.extend(
                ep
                for ep in episodes
                if not (ep.get("metadata") or {}).get("compressed_to")
            )
            if len(uncompressed) >= limit or len(episodes) < page_limit:
                return uncompressed[:limit]
            offset += page_limit
