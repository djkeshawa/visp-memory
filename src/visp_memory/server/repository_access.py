"""Repository discovery and traversal under the shared record access policy."""

from typing import Any

from fastapi import HTTPException

from visp_memory.core.eligibility import UNSCOPED_REPO_ID
from visp_memory.core.repository import Repository, RepositoryDependency, RepositoryManager
from visp_memory.core.storage import (
    IMPLICIT_REGISTRATION_KEY,
    IMPLICIT_REGISTRATION_VALUE,
    is_implicitly_registered,
    iter_repository_memories,
)
from visp_memory.server.auth import UserContext
from visp_memory.server.authorization import (
    can_access_scoped_record,
    has_admin_privileges,
)


class AuthorizedRepositoryManager(RepositoryManager):
    def __init__(self, storage, user: UserContext):
        super().__init__(storage)
        self.user = user

    def _can_access(self, repo: Repository | None) -> bool:
        if repo is None or repo.id.strip() == UNSCOPED_REPO_ID:
            return False
        return can_access_scoped_record(
            self.storage,
            {"repo_id": repo.id, "metadata": {"team_id": repo.team_id}},
            self.user,
            scope_field="metadata",
        )

    def get(self, repo_id: str) -> Repository | None:
        repo = super().get(repo_id)
        return repo if self._can_access(repo) else None

    def require(self, repo_id: str) -> Repository:
        repo = self.get(repo_id)
        if repo is None:
            raise HTTPException(status_code=404, detail="Repository not found")
        return repo

    def list_all(self, team_id: str | None = None, *, include_archived=False) -> list[Repository]:
        if (
            team_id
            and team_id != self.user.team_id
            and not (has_admin_privileges(self.user) or self.user.is_local_owner)
        ):
            raise HTTPException(status_code=403, detail="Cannot list repositories for another team")
        return [
            repo
            for repo in super().list_all(team_id=team_id, include_archived=include_archived)
            if self._can_access(repo)
        ]

    def require_claimable(self, repo_id: str) -> None:
        """Refuse to let this principal register a scope that already holds others' records.

        A row the store created for itself is a placeholder that registration takes
        over, and registration stamps the caller's team on it. Without this check
        any authenticated user could register another team's unregistered project:
        that team's writes would then 404, and every untagged record in the scope
        would become visible to the claimant through the repository fallback.

        Administrators and the local owner already see every record, so the claim
        moves nothing for them. Anyone else may claim only a scope whose every
        memory and intent is already tagged with their own team.
        """
        if has_admin_privileges(self.user) or self.user.is_local_owner:
            return
        existing = super().get(repo_id)
        if existing is None or _is_declared(existing):
            return
        if not self._owns_every_record(repo_id):
            raise HTTPException(status_code=404, detail="Repository not found")

    def _owns_every_record(self, repo_id: str) -> bool:
        def visible(record: dict[str, Any], scope_field: str) -> bool:
            return can_access_scoped_record(
                self.storage, record, self.user, scope_field=scope_field
            )

        memories = iter_repository_memories(self.storage, repo_id)
        if not all(visible(memory, "metadata") for memory in memories):
            return False
        intents = self.storage.get_active_intents(repo_id=repo_id, status="all")
        return all(visible(intent, "context") for intent in intents)

    def get_dependencies(self, repo_id: str) -> list[RepositoryDependency]:
        if self.get(repo_id) is None:
            return []
        return [
            dependency
            for dependency in super().get_dependencies(repo_id)
            if self.get(dependency.target_repo_id) is not None
        ]

    def project_scopes(self) -> list[dict[str, Any]]:
        repositories = self.list_all(include_archived=True)
        archived_ids = {repo.id for repo in repositories if repo.status == "archived"}
        by_id = {repo.id: repo for repo in repositories if repo.status != "archived"}
        if (has_admin_privileges(self.user) or self.user.is_local_owner) and hasattr(
            self.storage, "list_project_ids"
        ):
            for repo_id in self.storage.list_project_ids():
                repo = Repository(
                    id=repo_id,
                    name=repo_id,
                    metadata={IMPLICIT_REGISTRATION_KEY: IMPLICIT_REGISTRATION_VALUE},
                )
                if repo_id not in archived_ids and self._can_access(repo):
                    by_id.setdefault(repo_id, repo)
        return [
            {
                "id": repo.id,
                "name": repo.name if _is_declared(repo) else repo.id,
                "registered": _is_declared(repo),
                "status": repo.status,
            }
            for repo in sorted(by_id.values(), key=lambda repo: repo.id)
        ]


def _is_declared(repo: Repository) -> bool:
    return not is_implicitly_registered({"metadata": repo.metadata})
