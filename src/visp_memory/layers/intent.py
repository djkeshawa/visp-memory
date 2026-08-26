"""
Intent Memory Layer

Manages current direction, goals, and priorities:
- "Currently stabilizing for v2.0 release"
- "Focus on security audit findings"
- "Avoid scope creep, minimal changes only"

Intent provides the "why" that guides all decisions.
It's the shortest-term memory but most influential.
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from visp_memory.core.clock import utc_now
from visp_memory.core.storage import BaseStorage
from visp_memory.core.trust import WriteChannel, channel_policy, parse_write_channel
from visp_memory.layers.base import BaseMemoryLayer


class IntentPriority(int, Enum):
    """Priority levels for intents."""

    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


class IntentMemory(BaseMemoryLayer):
    """
    Manages intent (goal/direction) tracking.

    Intent answers:
    - What are we trying to achieve?
    - What are the current priorities?
    - What constraints should guide decisions?
    """

    def __init__(self, storage: BaseStorage):
        super().__init__(storage)

    def set_goal(
        self,
        goal: str,
        priority: IntentPriority = IntentPriority.NORMAL,
        constraints: List[str] = None,
        repo_id: str = None,
        context: Dict[str, Any] = None,
    ) -> str:
        """
        Set a current goal/intent.

        Args:
            goal: Description of the goal
            priority: How important
            constraints: Constraints to respect
            context: Additional context

        Returns:
            Intent ID

        Example:
            intent.set_goal(
                "Implement OAuth2 authentication",
                priority=IntentPriority.HIGH,
                constraints=[
                    "Must support existing session tokens",
                    "No breaking changes to public API"
                ]
            )
        """
        ctx = {
            "constraints": constraints or [],
            "set_at": utc_now().isoformat(),
            **(context or {}),
        }

        return self.storage.set_intent(
            description=goal,
            priority=priority.value if isinstance(priority, IntentPriority) else priority,
            repo_id=repo_id,
            context=ctx,
        )

    def set_focus(
        self,
        focus: str,
        avoid: List[str] = None,
        priority: IntentPriority = IntentPriority.HIGH,
        repo_id: str = None,
    ) -> str:
        """
        Set current focus area with things to avoid.

        Args:
            focus: What to focus on
            avoid: What to avoid/not do
            priority: Priority level

        Returns:
            Intent ID

        Example:
            intent.set_focus(
                focus="Bug fixes only",
                avoid=["New features", "Refactoring", "Dependency updates"]
            )
        """
        return self.set_goal(
            goal=f"FOCUS: {focus}",
            priority=priority,
            constraints=[f"AVOID: {item}" for item in (avoid or [])],
            repo_id=repo_id,
        )

    def add_constraint(
        self,
        constraint: str,
        reason: str = None,
        priority: IntentPriority = IntentPriority.NORMAL,
        repo_id: str = None,
    ) -> str:
        """
        Add a constraint/rule that should be respected.

        Args:
            constraint: The constraint
            reason: Why this constraint exists
            priority: How strictly to enforce

        Returns:
            Intent ID

        Example:
            intent.add_constraint(
                constraint="Do not modify production config files",
                reason="Requires separate review process"
            )
        """
        description = f"CONSTRAINT: {constraint}"
        if reason:
            description += f" (Reason: {reason})"

        return self.set_goal(goal=description, priority=priority, repo_id=repo_id)

    def working_on(
        self, task: str, files: List[str] = None, notes: str = None, repo_id: str = None
    ) -> str:
        """
        Record what is currently being worked on.

        Args:
            task: Description of current task
            files: Files being modified
            notes: Additional notes

        Returns:
            Intent ID

        Example:
            intent.working_on(
                task="Fixing token refresh race condition",
                files=["auth/token.py", "auth/refresh.py"],
                notes="Need to add mutex locks"
            )
        """
        return self.set_goal(
            goal=f"WORKING ON: {task}",
            priority=IntentPriority.HIGH,
            repo_id=repo_id,
            context={"files": files or [], "notes": notes},
        )

    def record_outcome(
        self,
        intent_id: str,
        outcome: str,
        *,
        actor_id: str = "library-caller",
        channel: WriteChannel | str = WriteChannel.LIBRARY,
    ) -> bool:
        """
        Append a provenance-bearing external outcome without changing status.

        Intent status belongs to the external workflow authority. This history is
        informational and deliberately cannot make the recorded outcome current.
        """
        parsed_channel = parse_write_channel(channel)
        policy = channel_policy(parsed_channel)
        entry = {
            "outcome": outcome,
            "recorded_at": utc_now().isoformat(),
            "actor_id": actor_id,
            "provenance": {
                "source": policy.source,
                "channel": parsed_channel.value,
                "tier": policy.provenance.value,
            },
            "authoritative": False,
            "status_changed": False,
        }
        return self.storage.append_intent_outcome(intent_id, entry)

    def complete(
        self,
        intent_id: str,
        *,
        actor_id: str = "library-caller",
        channel: WriteChannel | str = WriteChannel.LIBRARY,
    ) -> bool:
        """Record a completion outcome without changing intent status.

        Returns:
            True if the non-authoritative history entry was stored.
        """
        return self.record_outcome(
            intent_id,
            "completed",
            actor_id=actor_id,
            channel=channel,
        )

    def update(
        self,
        intent_id: str,
        description: str = None,
        priority: IntentPriority | int = None,
        status: str = None,
        context: Dict[str, Any] = None,
        *,
        actor_id: str = "library-caller",
        channel: WriteChannel | str = WriteChannel.LIBRARY,
    ) -> bool:
        """Update content fields and record any requested status as history."""
        if isinstance(priority, IntentPriority):
            priority = priority.value
        updated = False
        if description is not None or priority is not None or context is not None:
            updated = self.storage.update_intent(
                intent_id,
                description=description,
                priority=priority,
                context=context,
            )
        outcome_recorded = False
        if status is not None:
            outcome_recorded = self.record_outcome(
                intent_id,
                status,
                actor_id=actor_id,
                channel=channel,
            )
        return updated or outcome_recorded

    def close(
        self,
        intent_id: str,
        *,
        actor_id: str = "library-caller",
        channel: WriteChannel | str = WriteChannel.LIBRARY,
    ) -> bool:
        """Record a close outcome without changing intent status."""
        return self.record_outcome(
            intent_id,
            "closed",
            actor_id=actor_id,
            channel=channel,
        )

    def clear_task(
        self,
        repo_id: str = None,
        *,
        actor_id: str = "library-caller",
        channel: WriteChannel | str = WriteChannel.LIBRARY,
    ) -> int:
        """
        Record completion outcomes for matching ``WORKING ON`` intents.

        Args:
            repo_id: Optional repository filter.

        Returns:
            Number of intents cleared
        """
        intents = self.get_active(repo_id=repo_id)
        cleared = 0

        for intent in intents:
            if intent["description"].startswith("WORKING ON:"):
                if self.complete(
                    intent["id"],
                    actor_id=actor_id,
                    channel=channel,
                ):
                    cleared += 1

        return cleared

    def clear_all(
        self,
        *,
        actor_id: str = "library-caller",
        channel: WriteChannel | str = WriteChannel.LIBRARY,
    ) -> int:
        """
        Record completion outcomes for all active intents/goals.

        Returns:
            Number of intents cleared
        """
        intents = self.get_active()
        recorded = 0
        for intent in intents:
            if self.complete(
                intent["id"],
                actor_id=actor_id,
                channel=channel,
            ):
                recorded += 1
        return recorded

    def get_active(self, repo_id: str = None) -> List[Dict[str, Any]]:
        """Get all active intents, ordered by priority."""
        return self.storage.get_active_intents(repo_id=repo_id)

    def get_current_focus(self, repo_id: str = None) -> Optional[Dict[str, Any]]:
        """Get the current primary focus."""
        intents = self.get_active(repo_id=repo_id)

        for intent in intents:
            if intent["description"].startswith("FOCUS:"):
                return intent

        # Return highest priority if no explicit focus
        return intents[0] if intents else None

    def get_constraints(self, repo_id: str = None) -> List[str]:
        """Get all active constraints as a list of strings."""
        intents = self.get_active(repo_id=repo_id)
        constraints = []

        for intent in intents:
            desc = intent["description"]

            # Extract CONSTRAINT intents
            if desc.startswith("CONSTRAINT:"):
                constraints.append(desc.replace("CONSTRAINT: ", ""))

            # Extract constraints from goal contexts
            ctx = intent.get("context", {})
            if isinstance(ctx, dict):
                for c in ctx.get("constraints", []):
                    constraints.append(c)

        return constraints

    def get_working_on(self, repo_id: str = None) -> Optional[Dict[str, Any]]:
        """Get current task being worked on."""
        intents = self.get_active(repo_id=repo_id)

        for intent in intents:
            if intent["description"].startswith("WORKING ON:"):
                return intent

        return None

    def summarize(self, repo_id: str = None) -> Dict[str, Any]:
        """
        Get a summary of current intent state.

        Returns:
            Dict with focus, constraints, current_task, and all goals
        """
        intents = self.get_active(repo_id=repo_id)

        return {
            "focus": self.get_current_focus(repo_id=repo_id),
            "constraints": self.get_constraints(repo_id=repo_id),
            "current_task": self.get_working_on(repo_id=repo_id),
            "all_goals": intents,
            "total_active": len(intents),
        }
