"""
Team and user management for collaborative memory.

Supports:
- User identity and attribution
- Team membership
- Repository ownership
- Shared memory access
"""

from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Any, Dict, List, Optional

from visp_memory.core.storage import BaseStorage


@dataclass
class User:
    """User entity."""

    id: str
    username: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    created_at: Optional[datetime] = None
    last_active: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Team:
    """Team entity."""

    id: str
    name: str
    description: Optional[str] = None
    members: List[str] = field(default_factory=list)  # user_ids
    repositories: List[str] = field(default_factory=list)  # repo_ids
    created_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class TeamManager:
    """Manages team and user operations."""

    def __init__(self, storage: BaseStorage):
        self.storage = storage

    @staticmethod
    def _entity_from_storage(entity_type, data: Dict[str, Any]):
        allowed = {item.name for item in fields(entity_type)}
        return entity_type(**{key: value for key, value in data.items() if key in allowed})

    # User operations
    def create_user(self, user: User) -> str:
        """Create a new user. Raises NotImplementedError if backend lacks support."""
        if not self.storage.get_capabilities().teams:
            raise NotImplementedError("Storage backend does not support user management")
        user_dict = {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "display_name": user.display_name,
            "metadata": user.metadata,
        }
        return self.storage.store_user(user_dict)

    def get_user(self, user_id: str) -> Optional[User]:
        """Get user by ID."""
        if not self.storage.get_capabilities().teams:
            return None
        data = self.storage.get_user(user_id)
        if data:
            return self._entity_from_storage(User, data)
        return None

    # Team operations
    def create_team(self, team: Team) -> str:
        """Create a new team. Raises NotImplementedError if backend lacks support."""
        if not self.storage.get_capabilities().teams:
            raise NotImplementedError("Storage backend does not support team management")
        team_dict = {
            "id": team.id,
            "name": team.name,
            "description": team.description,
            "metadata": team.metadata,
        }
        return self.storage.store_team(team_dict)

    def get_team(self, team_id: str) -> Optional[Team]:
        """Get team by ID."""
        if not self.storage.get_capabilities().teams:
            return None
        data = self.storage.get_team(team_id)
        if data:
            return self._entity_from_storage(Team, data)
        return None

    def add_member(self, team_id: str, user_id: str) -> bool:
        """Add user to team."""
        if not self.storage.get_capabilities().teams:
            return False
        if self.get_team(team_id) is None:
            raise ValueError(f"Team not found: {team_id}")
        if self.get_user(user_id) is None:
            raise ValueError(f"User not found: {user_id}")
        return self.storage.add_team_member(team_id, user_id)

    def get_user_teams(self, user_id: str) -> List[Team]:
        """Get all teams a user belongs to."""
        if not self.storage.get_capabilities().teams:
            return []
        teams_data = self.storage.get_user_teams(user_id)
        return [self._entity_from_storage(Team, item) for item in teams_data]
