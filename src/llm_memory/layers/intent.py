"""
Intent Memory Layer

Manages current direction, goals, and priorities:
- "Currently stabilizing for v2.0 release"
- "Focus on security audit findings"
- "Avoid scope creep, minimal changes only"

Intent provides the "why" that guides all decisions.
It's the shortest-term memory but most influential.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from llm_memory.core.storage import BaseStorage
from llm_memory.layers.base import BaseMemoryLayer


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
            "set_at": datetime.now().isoformat(),
            **(context or {}),
        }

        return self.storage.set_intent(
            description=goal,
            priority=priority.value if isinstance(priority, IntentPriority) else priority,
            repo_id=repo_id,
            context=ctx,
        )

    def set_focus(
        self, focus: str, avoid: List[str] = None, priority: IntentPriority = IntentPriority.HIGH
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
        )

    def add_constraint(
        self, constraint: str, reason: str = None, priority: IntentPriority = IntentPriority.NORMAL
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

        return self.set_goal(goal=description, priority=priority)

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

    def complete(self, intent_id: str) -> bool:
        """
        Mark an intent as completed.

        Args:
            intent_id: ID of the intent to complete

        Returns:
            True if successful
        """
        return self.storage.complete_intent(intent_id)

    def clear_task(self) -> int:
        """
        Clear "WORKING ON" intents (task complete).

        Returns:
            Number of intents cleared
        """
        intents = self.get_active()
        cleared = 0

        for intent in intents:
            if intent["description"].startswith("WORKING ON:"):
                self.complete(intent["id"])
                cleared += 1

        return cleared

    def clear_all(self) -> int:
        """
        Clear all active intents/goals.

        Returns:
            Number of intents cleared
        """
        intents = self.get_active()
        for intent in intents:
            self.complete(intent["id"])
        return len(intents)

    def get_active(self, repo_id: str = None) -> List[Dict[str, Any]]:
        """Get all active intents, ordered by priority."""
        return self.storage.get_active_intents(repo_id=repo_id)

    def get_current_focus(self) -> Optional[Dict[str, Any]]:
        """Get the current primary focus."""
        intents = self.get_active()

        for intent in intents:
            if intent["description"].startswith("FOCUS:"):
                return intent

        # Return highest priority if no explicit focus
        return intents[0] if intents else None

    def get_constraints(self) -> List[str]:
        """Get all active constraints as a list of strings."""
        intents = self.get_active()
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

    def get_working_on(self) -> Optional[Dict[str, Any]]:
        """Get current task being worked on."""
        intents = self.get_active()

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
            "focus": self.get_current_focus(),
            "constraints": self.get_constraints(),
            "current_task": self.get_working_on(),
            "all_goals": intents,
            "total_active": len(intents),
        }
