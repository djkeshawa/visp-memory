"""Bound password guessing against ``POST /auth/login``.

Failures are counted per client address and per (address, username) pair inside
a sliding window. The pair limit stops a focused guess at one account; the
address limit stops spraying one password across many. Neither is keyed on the
username alone, so a remote caller cannot lock a known user out from elsewhere.

In-process and in-memory by design: it protects one server from one attacker
cheaply, and a restart forgetting the counts costs only that.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable

#: Failed attempts one address may make for one username per window.
MAX_FAILURES_PER_ACCOUNT = 5
#: Failed attempts one address may make across all usernames per window.
MAX_FAILURES_PER_ADDRESS = 20
WINDOW_SECONDS = 300.0
#: Upper bound on tracked keys, so a spray of distinct usernames cannot grow
#: the table without limit. The oldest key is dropped first.
MAX_TRACKED_KEYS = 10_000


class LoginThrottle:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._failures: dict[tuple[str, ...], deque[float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _keys(address: str, username: str) -> tuple[tuple[tuple[str, ...], int], ...]:
        normalized = username.strip().casefold()
        return (
            (("account", address, normalized), MAX_FAILURES_PER_ACCOUNT),
            (("address", address), MAX_FAILURES_PER_ADDRESS),
        )

    def _recent(self, key: tuple[str, ...], now: float) -> deque[float]:
        attempts = self._failures.get(key, deque())
        while attempts and attempts[0] <= now - WINDOW_SECONDS:
            attempts.popleft()
        if not attempts:
            self._failures.pop(key, None)
        return attempts

    def retry_after(self, address: str, username: str) -> int:
        """Seconds until this caller may try again; 0 when it may try now."""
        now = self._clock()
        wait = 0.0
        with self._lock:
            for key, limit in self._keys(address, username):
                attempts = self._recent(key, now)
                if len(attempts) >= limit:
                    wait = max(wait, attempts[0] + WINDOW_SECONDS - now)
        return math.ceil(wait)

    def record_failure(self, address: str, username: str) -> None:
        now = self._clock()
        with self._lock:
            for key, _ in self._keys(address, username):
                attempts = self._recent(key, now)
                if key not in self._failures:
                    if len(self._failures) >= MAX_TRACKED_KEYS:
                        self._failures.pop(next(iter(self._failures)))
                    self._failures[key] = attempts
                attempts.append(now)

    def record_success(self, address: str, username: str) -> None:
        """Clear the account's count; the address count still bounds spraying."""
        with self._lock:
            self._failures.pop(self._keys(address, username)[0][0], None)
