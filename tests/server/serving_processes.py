"""Child processes that import the real server app the way uvicorn's processes do."""

import asyncio
import multiprocessing
import os
from contextlib import contextmanager

from visp_memory.core.writer_lock import WriterLockConflict

# Importing the app costs seconds, and spawned children each pay it.
TIMEOUT = 90


def _import_app(data_dir):
    os.environ["VISP_MEMORY_STORAGE_DATA_DIR"] = str(data_dir)
    os.environ["VISP_MEMORY_EMBEDDING_PROVIDER"] = "noop"
    from visp_memory.server import app as server_app

    return server_app.app


def import_without_serving(data_dir, ready, stop, result):
    """The supervisor: imports the app, never runs the lifespan."""
    try:
        _import_app(data_dir)
        result.send(None)
    except WriterLockConflict as error:
        result.send(str(error))
    finally:
        ready.set()
        result.close()
    stop.wait(TIMEOUT)


def serve_until_stopped(data_dir, ready, stop, result):
    """The serving process: imports the app and runs its lifespan."""
    try:
        application = _import_app(data_dir)
    except WriterLockConflict as error:  # refused before serving, at import
        result.send(str(error))
        ready.set()
        result.close()
        return

    async def serve():
        try:
            async with application.router.lifespan_context(application):
                result.send(None)
                ready.set()
                await asyncio.to_thread(stop.wait, TIMEOUT)
        except WriterLockConflict as error:
            result.send(str(error))
            ready.set()

    try:
        asyncio.run(serve())
    finally:
        result.close()


@contextmanager
def spawned(target, data_dir):
    context = multiprocessing.get_context("spawn")
    ready, stop = context.Event(), context.Event()
    receive, send = context.Pipe(duplex=False)
    child = context.Process(target=target, args=(data_dir, ready, stop, send))
    child.start()
    send.close()
    try:
        assert ready.wait(TIMEOUT), "child did not report"
        assert receive.poll(TIMEOUT)
        yield child, receive.recv()
    finally:
        stop.set()
        child.join(10)
        if child.is_alive():
            child.kill()
            child.join(3)
        receive.close()
        child.close()
