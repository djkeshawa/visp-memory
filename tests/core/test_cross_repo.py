from visp_memory.core.cross_repo import CrossRepoContext
from visp_memory.core.repository import DependencyType, Repository, RepositoryDependency


class FakeStorage:
    def __init__(self, memories_by_repo=None):
        self.memories_by_repo = memories_by_repo or {}
        self.listed_repo_ids = []

    def list_memories(self, repo_id=None, limit=50):
        self.listed_repo_ids.append(repo_id)
        return list(self.memories_by_repo.get(repo_id, []))


class FakeRepoManager:
    def __init__(self, repos, dependencies_by_repo=None):
        self.repos = repos
        self.dependencies_by_repo = dependencies_by_repo or {}

    def get(self, repo_id):
        return self.repos.get(repo_id)

    def get_dependencies(self, repo_id):
        return self.dependencies_by_repo.get(repo_id, [])


def dependency(source_repo_id, target_repo_id):
    return RepositoryDependency(
        source_repo_id=source_repo_id,
        target_repo_id=target_repo_id,
        dependency_type=DependencyType.DEPENDS_ON,
    )


def test_cross_repo_context_includes_transitive_dependencies_up_to_max_depth():
    repos = {
        "app": Repository(id="app", name="App"),
        "lib": Repository(id="lib", name="Lib"),
        "core": Repository(id="core", name="Core"),
    }
    repo_mgr = FakeRepoManager(
        repos,
        {
            "app": [dependency("app", "lib")],
            "lib": [dependency("lib", "core")],
        },
    )
    storage = FakeStorage(
        {
            "core": [
                {
                    "id": "core-warning",
                    "content": "Core API is changing",
                    "repo_id": "core",
                    "category": "warning",
                    "tags": ["warning"],
                }
            ]
        }
    )

    context = CrossRepoContext(storage, repo_mgr).get_context_for_repo("app", max_depth=2)

    assert context["monitored_repos"] == ["app", "lib", "core"]
    assert [warning["content"] for warning in context["warnings"]] == ["Core API is changing"]
    assert storage.listed_repo_ids == ["app", "lib", "core"]


def test_cross_repo_context_deduplicates_dependencies_and_stops_cycles():
    repos = {
        "app": Repository(id="app", name="App"),
        "lib": Repository(id="lib", name="Lib"),
    }
    repo_mgr = FakeRepoManager(
        repos,
        {
            "app": [dependency("app", "lib"), dependency("app", "lib")],
            "lib": [dependency("lib", "app")],
        },
    )
    storage = FakeStorage(
        {
            "lib": [
                {
                    "id": "lib-warning",
                    "content": "Lib warning",
                    "repo_id": "lib",
                    "category": "warning",
                    "tags": ["warning"],
                }
            ]
        }
    )

    context = CrossRepoContext(storage, repo_mgr).get_context_for_repo("app", max_depth=3)

    assert context["monitored_repos"] == ["app", "lib"]
    assert [warning["id"] for warning in context["warnings"]] == ["lib-warning"]
    assert storage.listed_repo_ids == ["app", "lib"]
