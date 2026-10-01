"""Run probes through the installed Uvicorn worker and its real heartbeat."""

import inspect

from uvicorn.supervisors.multiprocess import Process


def worker_process(config, target):
    if "target" in inspect.signature(Process).parameters:
        return Process(config, target, [])

    worker = Process(config, [])
    # Uvicorn 0.54 owns the Server and reports server.started in its heartbeat.
    # Replace only the serving probe, preserving the real spawn and heartbeat.
    worker.server.run = target
    return worker
