"""Every project scope that holds records has a repository row (LC-86).

Repo-scoped API queries resolve a memory's ``repo_id`` against the ``repositories``
table, and nothing ever wrote it — `repos register` is frozen — so the table was
empty in every store that had never been through the multi-user server. A store
could be full and still answer as though it were empty.
"""

import sqlite3
from pathlib import Path

import pytest

from visp_memory.core.eligibility import UNSCOPED_REPO_ID
from visp_memory.core.storage import LocalStorage, is_implicitly_registered


@pytest.fixture
def store(tmp_path):
    storage = LocalStorage(tmp_path)
    yield storage
    storage.close()


def test_recording_a_memory_registers_its_project(store):
    store.store_memory("A thing that happened", repo_id="alpha")

    assert store.get_repository("alpha") is not None


def test_setting_an_intent_registers_its_project(store):
    store.set_intent("Finish the thing", repo_id="beta")

    assert store.get_repository("beta") is not None


def test_an_implicitly_registered_project_carries_no_owning_team(store):
    store.store_memory("A thing that happened", repo_id="alpha")

    repo = store.get_repository("alpha")
    assert repo["team_id"] is None
    assert is_implicitly_registered(repo)


def test_the_reserved_unscoped_bucket_never_becomes_a_repository(store):
    store.store_memory("A memory of unknown origin", repo_id=UNSCOPED_REPO_ID)

    assert store.get_repository(UNSCOPED_REPO_ID) is None


def test_registering_the_same_project_twice_writes_one_row(store):
    store.store_memory("First", repo_id="alpha")
    store.store_memory("Second", repo_id="alpha")

    assert [repo["id"] for repo in store.list_repositories()] == ["alpha"]


def test_opening_a_store_written_before_this_registers_what_it_holds(tmp_path):
    storage = LocalStorage(tmp_path)
    storage.store_memory("Recorded long ago", repo_id="legacy")
    storage.set_intent("Planned long ago", repo_id="legacy-intent")
    storage.close()
    _forget_repository_rows(tmp_path)

    reopened = LocalStorage(tmp_path)
    try:
        assert reopened.get_repository("legacy") is not None
        assert reopened.get_repository("legacy-intent") is not None
    finally:
        reopened.close()


def test_explicit_registration_takes_over_the_row_the_store_created(store):
    store.store_memory("A thing that happened", repo_id="alpha")

    store.store_repository({"id": "alpha", "name": "Alpha Service", "team_id": "team-1"})

    repo = store.get_repository("alpha")
    assert repo["name"] == "Alpha Service"
    assert repo["team_id"] == "team-1"
    assert not is_implicitly_registered(repo)


def test_explicit_registration_still_refuses_a_real_duplicate(store):
    store.store_repository({"id": "alpha", "name": "Alpha Service"})

    with pytest.raises(ValueError, match="already exists"):
        store.store_repository({"id": "alpha", "name": "Alpha Again"})


def test_inspection_reports_unregistered_scopes_without_registering_them(tmp_path):
    storage = LocalStorage(tmp_path)
    storage.store_memory("Recorded long ago", repo_id="legacy")
    storage.close()
    _forget_repository_rows(tmp_path)

    report = LocalStorage.inspect_repository_registration(tmp_path)

    assert report["exists"] is True
    assert report["unregistered_scopes"] == ["legacy"]
    # A diagnostic that repairs what it measures can never report it again.
    assert LocalStorage.inspect_repository_registration(tmp_path)["unregistered_scopes"] == [
        "legacy"
    ]


def test_inspection_of_a_directory_with_no_store_says_so(tmp_path):
    report = LocalStorage.inspect_repository_registration(tmp_path / "nothing-here")

    assert report == {"exists": False, "project_scopes": [], "unregistered_scopes": []}


def _forget_repository_rows(data_dir: Path) -> None:
    """Return a store to the state every store was in before LC-86 was fixed."""
    conn = sqlite3.connect(data_dir / "memories.db")
    try:
        conn.execute("DELETE FROM repositories")
        conn.commit()
    finally:
        conn.close()
