import pytest

from tests.core.test_remote_storage import FakeResponse, remote_storage_with


@pytest.mark.parametrize("method,kwargs,channel,suffix,payload", [
    ("get_repository", {}, "get", "", {}),
    ("update_repository", {"status": "archived"}, "post", "/archive", {}),
    ("get_repo_dependencies", {}, "get", "/dependencies", []),
    ("inspect_repository_registration", {}, "get", "/registration", {}),
    ("export_graph", {}, "get", "/export", {}),
    ("import_graph", {"data": {}}, "post", "/import", {}),
    ("purge_repository", {}, "delete", "", {"status": "purged"}),
    ("add_repo_dependency", {"target_id": "other", "dep_type": "depends_on"},
     "post", "/dependencies", {"id": "dep"}),
])
def test_repository_ids_are_quoted_in_every_client_path(method, kwargs, channel, suffix, payload):
    storage = remote_storage_with(FakeResponse(200, payload))
    repo_id = "org/project?#%"
    key = {"import_graph": "default_repo_id", "add_repo_dependency": "source_id"}.get(
        method, "repo_id"
    )
    getattr(storage, method)(**{key: repo_id}, **kwargs)
    assert getattr(storage.session, f"last_{channel}_url") == (
        f"http://memory.example/repos/org%2Fproject%3F%23%25{suffix}"
    )
