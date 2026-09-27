"""Storage construction owns the guard even when a backend fails to start."""

from unittest.mock import Mock

import pytest

from tests.core.test_writer_lock_processes import memory_config, spawned_role
from visp_memory import Memory
from visp_memory.core import storage_factory


@pytest.mark.parametrize(
    "backend, constructor", [("sqlite", "LocalStorage"), ("arcadedb", "ArcadeDbStorage")]
)
def test_local_backend_is_guarded_before_construction(tmp_path, monkeypatch, backend, constructor):
    config = memory_config(tmp_path)
    config.storage.backend = backend

    def fail(*args, **kwargs):
        assert len(list((tmp_path / ".locks").glob("local-*.lock"))) == 1
        raise RuntimeError("backend unavailable")

    monkeypatch.setattr(storage_factory, constructor, fail)
    with pytest.raises(RuntimeError, match="backend unavailable"):
        Memory(config=config)
    assert not list((tmp_path / ".locks").glob("local-*.lock"))


def test_neo4j_has_no_data_dir_guard(tmp_path, monkeypatch):
    config = memory_config(tmp_path)
    config.storage.backend = "neo4j"
    config.embedding.turn_keys = True
    constructor = Mock()
    monkeypatch.setattr(storage_factory, "Neo4jStorage", constructor)
    with Memory(config=config):
        assert not (tmp_path / ".locks").exists()
    assert constructor.call_args.kwargs["turn_keys"] is True
    constructor.return_value.close.assert_called_once()


def test_memory_close_releases_guard_even_when_storage_close_fails(tmp_path, monkeypatch):
    memory = Memory(config=memory_config(tmp_path))
    monkeypatch.setattr(memory._storage, "close", Mock(side_effect=RuntimeError("close failed")))
    with pytest.raises(RuntimeError, match="close failed"):
        memory.close()
    memory.close()
    with spawned_role(tmp_path, "server") as (_, conflict):
        assert conflict is None
