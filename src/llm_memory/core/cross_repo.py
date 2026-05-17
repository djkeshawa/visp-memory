"""
Cross-repository query and context aggregation.

Enables surfacing relevant context from dependent repositories:
- Warnings from libraries you depend on
- Breaking changes in upstream repos
- Related knowledge across projects
"""

from typing import Any, Dict, List, Optional

from llm_memory.core.repository import RepositoryManager
from llm_memory.core.storage import BaseStorage


class CrossRepoContext:
    """Aggregates context across multiple repositories."""

    def __init__(self, storage: BaseStorage, repo_mgr: RepositoryManager = None):
        self.storage = storage
        self.repo_mgr = repo_mgr or RepositoryManager(storage)

    def get_context_for_repo(
        self, repo_id: str, include_dependencies: bool = True, max_depth: int = 2
    ) -> Dict[str, Any]:
        """
        Get aggregated context for a repository.
        """
        # Get target repo info
        repo = self.repo_mgr.get(repo_id)
        if not repo:
            return {"error": "Repository not found"}

        relevant_repos = self._resolve_relevant_repos(
            repo_id=repo_id,
            include_dependencies=include_dependencies,
            max_depth=max_depth,
        )

        # Aggregate memories from these repos
        # Look for warnings, breaking changes, etc.
        all_memories = []
        for rid in relevant_repos:
            mems = self.storage.list_memories(repo_id=rid, limit=50)
            all_memories.extend(mems)

        # Filter by category
        warnings = [m for m in all_memories if m.get("category") in ["warning", "error"]]
        breaking = [m for m in all_memories if m.get("category") == "breaking_change"]
        knowledge = [m for m in all_memories if m.get("layer") == "semantic"]

        return {
            "repo": repo.__dict__ if hasattr(repo, "__dict__") else repo,
            "warnings": warnings,
            "breaking_changes": breaking,
            "knowledge": knowledge[:20],  # Limit knowledge
            "monitored_repos": relevant_repos,
        }

    def _resolve_relevant_repos(
        self, repo_id: str, include_dependencies: bool, max_depth: int
    ) -> List[str]:
        """Return repo_id plus dependency repos up to max_depth, preserving first-seen order."""
        relevant_repos = [repo_id]
        seen = {repo_id}

        if not include_dependencies or max_depth <= 0:
            return relevant_repos

        queue = [(repo_id, 0)]
        while queue:
            current_repo_id, depth = queue.pop(0)
            if depth >= max_depth:
                continue

            for dep in self.repo_mgr.get_dependencies(current_repo_id):
                target_repo_id = dep.target_repo_id
                if target_repo_id in seen:
                    continue
                seen.add(target_repo_id)
                relevant_repos.append(target_repo_id)
                queue.append((target_repo_id, depth + 1))

        return relevant_repos

    def search_across_repos(
        self,
        query: str,
        repo_ids: List[str],
        layer: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict]:
        """Search memories across multiple repos."""
        results = []
        for rid in repo_ids:
            results.extend(
                self.storage.search_memories(
                    query=query,
                    repo_id=rid,
                    layer=layer,
                    limit=limit,
                )
            )
        results.sort(key=lambda x: x.get("similarity", 0), reverse=True)
        return results[:limit]
