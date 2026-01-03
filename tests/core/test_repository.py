from visp_memory.core.repository import Repository, RepositoryManager
from visp_memory.core.storage import StorageCapabilities


class FakeStorage:
    """Storage stub that returns raw node dicts with extra normalized fields."""

    def __init__(self, repos):
        self.repos = repos

    def get_capabilities(self):
        return StorageCapabilities(repositories=True)

    def list_repositories(self, team_id=None):
        return list(self.repos)

    def get_repository(self, repo_id):
        for repo in self.repos:
            if repo["id"] == repo_id:
                return repo
        return None


def test_list_all_ignores_unknown_storage_fields():
    # Neo4j _node_to_dict injects normalized fields like ``tags`` on every
    # node; Repository must tolerate them (regression: /repos/scopes 500).
    storage = FakeStorage(
        [
            {
                "id": "app",
                "name": "App",
                "tags": [],
                "metadata": {},
                "tech_stack": ["python"],
            }
        ]
    )

    repos = RepositoryManager(storage).list_all()

    assert len(repos) == 1
    assert repos[0].id == "app"
    assert repos[0].tech_stack == ["python"]


def test_get_ignores_unknown_storage_fields():
    storage = FakeStorage([{"id": "app", "name": "App", "tags": ["x"], "extra": 1}])

    repo = RepositoryManager(storage).get("app")

    assert isinstance(repo, Repository)
    assert repo.id == "app"
    assert not hasattr(repo, "tags")
