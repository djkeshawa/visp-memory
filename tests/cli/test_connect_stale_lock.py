"""Migration trusts the OS lock, never a crashed server's metadata alone."""

import json

from tests.core.test_writer_lock_processes import SERVER_URL, spawned_role
from visp_memory.core.writer_lock import acquire_writer_lock
from visp_memory.interfaces.connect_migration import _data_dir_claims_server


def test_crashed_server_metadata_does_not_claim_store(tmp_path):
    with spawned_role(tmp_path, "server") as (child, conflict):
        assert conflict is None
        assert _data_dir_claims_server(tmp_path, SERVER_URL)
        child.kill()
        child.join(3)
        assert (tmp_path / ".locks/server.json").exists()
        assert not _data_dir_claims_server(tmp_path, SERVER_URL)
        with acquire_writer_lock(tmp_path, "server", url="http://127.0.0.1:9999"):
            metadata = json.loads((tmp_path / ".locks/server.json").read_text())
            assert metadata["url"] == "http://127.0.0.1:9999"
            assert metadata["pid"] != child.pid


def test_stale_discovery_record_does_not_claim_store(tmp_path):
    from visp_memory.interfaces.connect_migration import refuse_served_source
    from visp_memory.interfaces.connect_models import ServerRecord

    record = ServerRecord(tmp_path / "server.json", None, SERVER_URL, tmp_path)
    refuse_served_source(tmp_path, SERVER_URL, record)
