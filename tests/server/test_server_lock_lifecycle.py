"""The server role belongs to the process that serves, not the one that imports.

uvicorn's supervisor imports the app even with ``--reload`` and ``--workers``, then
spawns the process that serves and imports it again. Importing costs seconds per
child, so the scenarios share one serving process.
"""

import pytest

from tests.server.serving_processes import import_without_serving, serve_until_stopped, spawned
from visp_memory.core.writer_lock import WriterLockConflict, acquire_writer_lock


def test_importing_supervisor_does_not_block_the_serving_process(tmp_path):
    with spawned(import_without_serving, tmp_path) as (supervisor, imported):
        assert imported is None
        with spawned(serve_until_stopped, tmp_path) as (server, conflict):
            assert conflict is None
            assert supervisor.is_alive()
            # The role is held by the server only, and it still shuts local writers out.
            with pytest.raises(WriterLockConflict, match=str(server.pid)):
                acquire_writer_lock(tmp_path, "local")
            # A process importing the app is refused early, before touching the store.
            with spawned(import_without_serving, tmp_path) as (_, refused):
                assert str(server.pid) in refused
    with acquire_writer_lock(tmp_path, "server"):
        pass
