"""A real Uvicorn worker must remain responsive instead of being respawned."""

import functools
import multiprocessing

from uvicorn import Config
from uvicorn.supervisors.multiprocess import Process

from tests.core.test_writer_lock_processes import memory_config
from visp_memory.core.writer_lock import acquire_writer_lock


def _refused_worker(data_dir, announced, sockets=None):
    from visp_memory.server.writer_role import preflight_server_role

    announced.set()
    preflight_server_role(memory_config(data_dir))
    raise AssertionError("a refused worker must never start serving")


def test_refused_uvicorn_worker_stays_idle_and_answers_supervisor(tmp_path):
    context = multiprocessing.get_context("spawn")
    announced = context.Event()
    config = Config("visp_memory.server.app:app", workers=2, log_config=None)
    worker = Process(config, functools.partial(_refused_worker, tmp_path, announced), [])
    with acquire_writer_lock(tmp_path, "server"):
        worker.start()
        try:
            assert announced.wait(15)
            # Wait for the writer gate's bounded probe, then ping twice: a blocked
            # import still has Uvicorn's heartbeat thread and must not crash-loop.
            worker.process.join(1)
            assert worker.is_alive(timeout=3)
            assert worker.is_alive(timeout=3)
        finally:
            worker.terminate()
            worker.process.join(5)
            try:
                assert worker.process.exitcode == 0, "idle worker must stop normally"
            finally:
                if worker.process.is_alive():
                    worker.kill()
                    worker.process.join(3)
                worker.process.close()


def _import_then_serve(data_dir, imported, begin, serving, stop, sockets=None):
    import asyncio

    from tests.server.serving_processes import _import_app

    application = _import_app(data_dir)
    imported.set()
    assert begin.wait(30)

    async def serve():
        async with application.router.lifespan_context(application):
            serving.set()
            while not stop.is_set():
                await asyncio.sleep(0.05)

    asyncio.run(serve())


def test_uvicorn_serving_child_keeps_import_role_until_lifespan(tmp_path):
    from tests.core.test_writer_lock_processes import spawned_role

    context = multiprocessing.get_context("spawn")
    imported, begin, serving, stop = (context.Event() for _ in range(4))
    config = Config("visp_memory.server.app:app", reload=True, log_config=None)
    worker = Process(config, functools.partial(
        _import_then_serve, tmp_path, imported, begin, serving, stop,
    ), [])
    worker.start()
    try:
        assert imported.wait(30)
        with spawned_role(tmp_path, "local") as (_, conflict):
            assert conflict, "import must not leave a gap for another writer"
        begin.set()
        assert serving.wait(30)
        with spawned_role(tmp_path, "server") as (_, conflict):
            assert conflict, "lifespan must adopt the import role"
        stop.set()
        worker.process.join(10)
        assert worker.process.exitcode == 0
        with acquire_writer_lock(tmp_path, "server"):
            pass
    finally:
        begin.set()
        stop.set()
        if worker.process.is_alive():
            worker.terminate()
            worker.process.join(5)
        if worker.process.is_alive():
            worker.kill()
            worker.process.join(3)
        worker.process.close()
