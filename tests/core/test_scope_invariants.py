"""P13 — the storage-level scope invariant, stated once and checked everywhere.

`_migrate_v2_to_v3` rewrites every NULL or empty `repo_id` to the reserved
sentinel, in `memories` and in `intents`, so the column can be queried
uniformly afterwards. That is the invariant the schema depends on:

    no row anywhere carries a NULL or empty repository scope.

`store_memory` had honoured it since the column existed. `set_intent` never
did — it inserted whatever it was handed, NULL included, re-breaking the
invariant immediately after the migration had established it. The visible
result was that `visp-memory goal` reported success for a goal that matched no
scoped query, because NULL matches nothing.

These tests check the invariant at the storage boundary rather than through any
one command, since the point of an invariant is that no caller can violate it.
"""

import pathlib
import tempfile

import pytest

from visp_memory import Memory, MemoryConfig
from visp_memory.core.eligibility import UNSCOPED_REPO_ID
from visp_memory.core.storage import LocalStorage

SCOPED_TABLES = ["memories", "intents"]


@pytest.fixture
def storage():
    return LocalStorage(pathlib.Path(tempfile.mkdtemp()))


def _null_scopes(store: LocalStorage, table: str) -> int:
    with store._get_db() as conn:
        return conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE repo_id IS NULL OR TRIM(repo_id) = ''"
        ).fetchone()[0]


@pytest.mark.parametrize("table", SCOPED_TABLES)
def test_an_unscoped_write_never_persists_a_null_scope(storage, table):
    """Both writers must default to the sentinel, not to NULL."""
    if table == "memories":
        storage.store_memory("an unscoped memory", auto_link=False)
    else:
        storage.set_intent("an unscoped intent")

    assert _null_scopes(storage, table) == 0, (
        f"A write left a NULL repo_id in {table}. The v2->v3 migration exists to remove exactly "
        "these, and a row that reappears with one matches no scoped query — it is invisible to "
        "every caller while still occupying the table."
    )


@pytest.mark.parametrize("table", SCOPED_TABLES)
def test_the_unscoped_default_is_the_reserved_sentinel(storage, table):
    """Not merely non-NULL: the sentinel specifically, so quarantine applies."""
    if table == "memories":
        storage.store_memory("an unscoped memory", auto_link=False)
        with storage._get_db() as conn:
            scopes = {row[0] for row in conn.execute("SELECT repo_id FROM memories")}
    else:
        storage.set_intent("an unscoped intent")
        with storage._get_db() as conn:
            scopes = {row[0] for row in conn.execute("SELECT repo_id FROM intents")}

    assert scopes == {UNSCOPED_REPO_ID}, (
        f"An unscoped write to {table} landed in {scopes!r} rather than the quarantine bucket. "
        "Any other value would be treated as a real project scope and served."
    )


@pytest.mark.parametrize("table", SCOPED_TABLES)
def test_a_real_scope_is_stored_unchanged(storage, table):
    """The converse: defaulting must not overwrite a scope the caller gave."""
    if table == "memories":
        storage.store_memory("a scoped memory", repo_id="repo-a", auto_link=False)
        with storage._get_db() as conn:
            scopes = {row[0] for row in conn.execute("SELECT repo_id FROM memories")}
    else:
        storage.set_intent("a scoped intent", repo_id="repo-a")
        with storage._get_db() as conn:
            scopes = {row[0] for row in conn.execute("SELECT repo_id FROM intents")}

    assert scopes == {"repo-a"}


def test_the_invariant_holds_after_a_realistic_session():
    """End to end, through the facade rather than the storage API."""
    config = MemoryConfig(repo_id="repo-a")
    config.storage.data_dir = pathlib.Path(tempfile.mkdtemp()) / "data"
    config.embedding.provider = "noop"
    memory = Memory(config=config)

    memory.record("something happened")
    memory.learn("the retry limit is five")
    memory.goal("ship the login page")
    memory.working_on("refactoring auth")

    for table in SCOPED_TABLES:
        assert _null_scopes(memory._storage, table) == 0, f"{table} holds a NULL-scoped row"
