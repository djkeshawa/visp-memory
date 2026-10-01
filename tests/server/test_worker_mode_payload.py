"""Only Uvicorn's known spawn payload may change writer-role handling."""

from types import SimpleNamespace

import pytest
from uvicorn import Config
from uvicorn._subprocess import get_subprocess, subprocess_started

from visp_memory.server import worker_mode


def _foreign_target(sockets=None):
    pass


def _lookalike_bootstrap():
    pass


_lookalike_bootstrap.__module__ = subprocess_started.__module__
_lookalike_bootstrap.__name__ = subprocess_started.__name__


@pytest.fixture
def spawn_payload(monkeypatch):
    monkeypatch.delenv(worker_mode.SERVER_IMPORT_OWNER_PID_ENV, raising=False)

    def install(process):
        monkeypatch.setattr(worker_mode.multiprocessing, "current_process", lambda: process)

    return install


@pytest.mark.parametrize("workers,reload,multiworker", [(2, False, True), (1, True, False)])
def test_installed_uvicorn_spawn_payload_is_recognized(spawn_payload, workers, reload, multiworker):
    config = Config("visp_memory.server.app:app", workers=workers, reload=reload, log_config=None)
    spawn_payload(get_subprocess(config, _foreign_target, []))

    assert worker_mode.is_uvicorn_multiworker() is multiworker
    assert worker_mode.retain_serving_import_role()


@pytest.mark.parametrize("target,kwargs", [
    (None, {"config": Config("unused", workers=2)}),
    (_foreign_target, {"config": Config("unused", workers=2)}),
    (_lookalike_bootstrap, {"config": Config("unused", workers=2)}),
    (subprocess_started, {}),
    (subprocess_started, {"config": object()}),
    (subprocess_started, None),
    (subprocess_started, []),
])
def test_unrecognized_payload_keeps_ordinary_refusal_and_release(spawn_payload, target, kwargs):
    spawn_payload(SimpleNamespace(_target=target, _kwargs=kwargs))

    assert not worker_mode.is_uvicorn_multiworker()
    assert not worker_mode.retain_serving_import_role()


def test_idle_worker_exits_when_its_supervisor_is_gone(monkeypatch):
    """A parked worker must never be orphaned when its supervisor dies."""
    import pytest

    from visp_memory.server import worker_mode

    monkeypatch.setattr(worker_mode, "pid_is_alive", lambda pid: False)
    with pytest.raises(SystemExit) as stopped:
        worker_mode.idle_refused_worker()
    assert stopped.value.code == 0
