"""
Repository management for multi-repo memory.

Repositories are first-class entities that:
- Group memories by project context
- Track dependencies between projects
- Enable cross-repo context sharing
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from llm_memory.core.storage import BaseStorage


class DependencyType(str, Enum):
    """Types of repository relationships."""

    DEPENDS_ON = "depends_on"  # Uses code from
    OWNED_BY = "owned_by"  # Team ownership
    RELATED_TO = "related_to"  # General relation
    FORKED_FROM = "forked_from"  # Fork relationship


@dataclass
class Repository:
    """Repository entity."""

    id: str
    name: str
    url: Optional[str] = None
    description: Optional[str] = None
    tech_stack: List[str] = field(default_factory=list)
    team_id: Optional[str] = None
    created_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RepositoryDependency:
    """Dependency relationship between repositories."""

    source_repo_id: str
    target_repo_id: str
    dependency_type: DependencyType
    version: Optional[str] = None
    notes: Optional[str] = None


class RepositoryManager:
    """Manages repository operations."""

    def __init__(self, storage: BaseStorage):
        self.storage = storage

    def register(self, repo: Repository) -> str:
        """Register a new repository. Raises NotImplementedError if backend lacks support."""
        if not hasattr(self.storage, "store_repository"):
            raise NotImplementedError("Storage backend does not support repository management")
        repo_dict = {
            "id": repo.id,
            "name": repo.name,
            "url": repo.url,
            "description": repo.description,
            "tech_stack": repo.tech_stack,
            "team_id": repo.team_id,
            "metadata": repo.metadata,
        }
        return self.storage.store_repository(repo_dict)

    def get(self, repo_id: str) -> Optional[Repository]:
        """Get repository by ID."""
        if not hasattr(self.storage, "get_repository"):
            return None
        data = self.storage.get_repository(repo_id)
        if data:
            return Repository(**data)
        return None

    def list_all(self, team_id: Optional[str] = None) -> List[Repository]:
        """List all repositories, optionally filtered by team."""
        if not hasattr(self.storage, "list_repositories"):
            return []
        repos_data = self.storage.list_repositories(team_id=team_id)
        return [Repository(**repo) for repo in repos_data]

    def add_dependency(self, dep: RepositoryDependency) -> str:
        """Add a dependency relationship. Raises NotImplementedError if backend lacks support."""
        if not hasattr(self.storage, "add_repo_dependency"):
            raise NotImplementedError("Storage backend does not support repository dependencies")
        return self.storage.add_repo_dependency(
            source_id=dep.source_repo_id,
            target_id=dep.target_repo_id,
            dep_type=dep.dependency_type.value,
            version=dep.version,
            notes=dep.notes,
        )

    def get_dependencies(self, repo_id: str) -> List[RepositoryDependency]:
        """Get direct dependencies of a repository."""
        if not hasattr(self.storage, "get_repo_dependencies"):
            return []
        deps_data = self.storage.get_repo_dependencies(repo_id)
        return [
            RepositoryDependency(
                source_repo_id=repo_id,
                target_repo_id=d["target_id"],
                dependency_type=DependencyType(d["type"]),
                version=d.get("version"),
                notes=d.get("notes"),
            )
            for d in deps_data
        ]
