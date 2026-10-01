"""Inspect scopes in a local migration source, including unregistered Evidence."""

from visp_memory.core.arcadedb_storage import ArcadeDbStorage
from visp_memory.core.storage import LocalStorage
from visp_memory.interfaces.connect_models import ConnectError


def source_repository_ids(storage) -> set[str]:
    # Registration rows are insufficient: standalone Evidence may predate
    # registration, and archived records still need to remain reachable.
    if isinstance(storage, LocalStorage):
        with storage._get_db() as connection:
            rows = connection.execute(
                "SELECT repo_id FROM memories UNION SELECT repo_id FROM evidence "
                "UNION SELECT repo_id FROM intents"
            ).fetchall()
        return {row["repo_id"] for row in rows}
    if isinstance(storage, ArcadeDbStorage):
        repo_ids = set()
        with storage._database() as database:
            for kind in (storage.MEMORY_TYPE, "Evidence", "Intent"):
                for row in storage._rows(database.query("sql", f"SELECT repo_id FROM {kind}")):
                    repo_ids.add(storage._record_get(row, "repo_id"))
        return repo_ids
    raise ConnectError("Cannot inspect repository scopes in this local storage backend")
