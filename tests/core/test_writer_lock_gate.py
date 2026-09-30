"""A local writer holds server.lock only as a momentary gate; racing one is no conflict."""

import multiprocessing
import threading
import time

import pytest

from tests.core.test_writer_lock import raw_lock
from tests.core.test_writer_lock_processes import TIMEOUT, spawned_role
from visp_memory.core.writer_lock import WriterLockConflict, acquire_writer_lock

PROCESSES = 6
ROUNDS = 100


def race_locals(data_dir, barrier, rounds, result):
    conflicts = 0
    try:
        barrier.wait(TIMEOUT)
        for _ in range(rounds):
            try:
                acquire_writer_lock(data_dir, "local").release()
            except WriterLockConflict:
                conflicts += 1
        result.send(conflicts)
    finally:
        result.close()


def test_simultaneous_local_writers_never_conflict(tmp_path):
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(PROCESSES)
    pipes = [context.Pipe(duplex=False) for _ in range(PROCESSES)]
    children = [
        context.Process(target=race_locals, args=(tmp_path, barrier, ROUNDS, send))
        for _, send in pipes
    ]
    for child in children:
        child.start()
    for _, send in pipes:
        send.close()
    try:
        conflicts = []
        for receive, _ in pipes:
            assert receive.poll(TIMEOUT * 4), "a racing writer never reported"
            conflicts.append(receive.recv())
        # No server exists, so every one of PROCESSES * ROUNDS attempts must succeed.
        assert conflicts == [0] * PROCESSES
    finally:
        for child in children:
            child.join(TIMEOUT)
            if child.is_alive():
                child.kill()
                child.join(3)


def test_held_server_lock_is_still_reported_promptly(tmp_path):
    with spawned_role(tmp_path, "server") as (child, conflict):
        assert conflict is None
        started = time.monotonic()
        with pytest.raises(WriterLockConflict, match=str(child.pid)):
            acquire_writer_lock(tmp_path, "local")
        # The retry is a short bounded pause, not a wait for the server to finish.
        assert time.monotonic() - started < 1
        assert not list((tmp_path / ".locks").glob("local-*.lock"))


def test_gate_held_by_a_descheduled_local_writer_is_waited_out(tmp_path):
    """A loaded machine can pause a local writer mid-gate for well over 50 ms.

    Windows CI once did, and the next local writer reported a server that did not
    exist. The gate budget must outlast such a pause.
    """
    locks = tmp_path / ".locks"
    locks.mkdir()
    gated, done = threading.Event(), threading.Event()

    def descheduled_writer():
        with raw_lock(locks / "server.lock"):
            gated.set()
            done.wait(0.15)

    holder = threading.Thread(target=descheduled_writer)
    holder.start()
    try:
        assert gated.wait(TIMEOUT)
        acquire_writer_lock(tmp_path, "local").release()
    finally:
        done.set()
        holder.join(TIMEOUT)
