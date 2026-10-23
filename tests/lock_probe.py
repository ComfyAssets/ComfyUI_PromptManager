"""A lock stand-in that reports when a second caller has to wait for it.

Concurrency tests patch it over a module's lock so they can wait until the
second caller is really blocked on the lock, instead of hoping a thread
start or one event-loop tick got it there.
"""

import threading


class ProbeLock:
    """A re-entrant lock whose ``contended`` event is set when a caller blocks."""

    def __init__(self):
        self._lock = threading.RLock()
        self.contended = threading.Event()

    def acquire(self, blocking=True, timeout=-1):
        if self._lock.acquire(blocking=False):
            return True
        self.contended.set()
        if not blocking:
            return False
        return self._lock.acquire(timeout=timeout)

    def release(self):
        self._lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False
