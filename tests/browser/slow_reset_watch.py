"""Report a slow test reset with what the server was doing while it waited.

A reset normally answers within about a second. Once one runs past
SLOW_RESET_SECONDS, this writes to stderr, while the reset is still waiting:

- every thread's stack, so a reset waiting on a lock, a full executor or a
  slow disk shows where it waits and who holds it;
- the default executor's threads and queued work;
- how many database connections are open, and the WAL size;
- on Linux, each thread's scheduler state and the kernel function it sleeps
  in (``D`` with a journal or writeback wait is a slow disk flush, a nanosleep
  is SQLite's busy handler, a futex is a Python lock or an idle worker).

When the reset ends it writes how long it took and, for each executor call it
made, how long the call queued and how long it ran.

Every report line starts with REPORT_PREFIX. The worker fixture
(tools/vendor/start-browser-server.mjs) copies those lines to the job log,
so a slow reset is visible even in a test that still passes.
"""

import asyncio
import contextvars
import faulthandler
import os
import sys
import tempfile
import threading
import time
from typing import Callable, TextIO

from aiohttp import web

SLOW_RESET_SECONDS = 2.0
REPORT_PREFIX = "pm-slow-reset| "

_current: contextvars.ContextVar["ResetWatch | None"] = contextvars.ContextVar(
    "pm_reset_watch", default=None
)


def is_reset_path(path: str) -> bool:
    return path.startswith("/__test__/") and path.endswith("/reset")


class ResetWatch:
    """Time one reset; dump the server's state if it passes the threshold."""

    def __init__(
        self,
        label: str,
        describe: Callable[[], list[str]],
        *,
        threshold: float = SLOW_RESET_SECONDS,
        stream: TextIO | None = None,
    ) -> None:
        self.label = label
        self.threshold = threshold
        self._describe = describe
        self._stream = stream
        self._start = time.monotonic()
        self._calls: list[str] = []
        self._calls_lock = threading.Lock()
        self._timer = threading.Timer(threshold, self._report_stall)
        self._timer.daemon = True
        self._timer.name = "pm-slow-reset-watch"

    def start(self) -> "ResetWatch":
        self._timer.start()
        return self

    def elapsed(self) -> float:
        return time.monotonic() - self._start

    def record_call(self, name: str, queued: float, ran: float) -> None:
        with self._calls_lock:
            self._calls.append(
                f"executor call {name}: queued {queued:.3f}s, ran {ran:.3f}s"
            )

    def finish(self, outcome: str) -> None:
        self._timer.cancel()
        elapsed = self.elapsed()
        if elapsed < self.threshold:
            return
        with self._calls_lock:
            calls = list(self._calls)
        self._write([f"{self.label} {outcome} after {elapsed:.3f}s", *calls])

    def _report_stall(self) -> None:
        lines = [f"{self.label} still running after {self.elapsed():.3f}s"]
        try:
            lines.extend(self._describe())
        except Exception as error:  # a diagnostic never breaks the reset
            lines.append(f"state unavailable: {error!r}")
        lines.extend(thread_stacks())
        self._write(lines)

    def _write(self, lines: list[str]) -> None:
        stream = self._stream or sys.stderr
        text = "".join(
            f"{REPORT_PREFIX}{part}\n" for line in lines for part in line.splitlines()
        )
        try:
            stream.write(text)
            stream.flush()
        except (OSError, ValueError):
            pass


def thread_stacks() -> list[str]:
    """Every thread's stack, from faulthandler.

    Formatting sys._current_frames() showed a thread blocked in SQLite as idle
    (Python 3.14); faulthandler reports where each thread really is.
    """
    with tempfile.TemporaryFile() as dump:
        faulthandler.dump_traceback(file=dump, all_threads=True)
        dump.seek(0)
        return dump.read().decode("utf-8", "replace").splitlines()


def executor_state(loop: asyncio.AbstractEventLoop) -> list[str]:
    executor = getattr(loop, "_default_executor", None)
    if executor is None:
        return ["default executor: not started"]
    threads = getattr(executor, "_threads", ())
    queue = getattr(executor, "_work_queue", None)
    queued = queue.qsize() if queue is not None else "?"
    return [
        f"default executor: {len(threads)} of "
        f"{getattr(executor, '_max_workers', '?')} threads, {queued} queued"
    ]


def database_state(model) -> list[str]:
    """How many connections are open, and the WAL size.

    Never touches another thread's connection: a connection busy in SQLite
    holds its mutex, and reading even ``in_transaction`` from here would block
    on it while holding the GIL, freezing every thread until that query ends.
    """
    with model._conn_lock:
        count = len(model._connections)
    wal = model.db_path + "-wal"
    size = os.path.getsize(wal) if os.path.exists(wal) else 0
    return [f"database: {count} connections, WAL {size} bytes"]


def kernel_wait_state() -> list[str]:
    """Each thread's state and kernel wait channel, where /proc has them."""
    lines = []
    for thread in threading.enumerate():
        task = f"/proc/self/task/{thread.native_id}"
        try:
            with open(f"{task}/stat", encoding="ascii") as stat:
                state = stat.read().rsplit(")", 1)[1].split()[0]
            with open(f"{task}/wchan", encoding="ascii") as wchan:
                channel = wchan.read().strip() or "0"
        except (OSError, IndexError):
            continue
        # The id matches the "Thread 0x..." headers of the stacks below.
        lines.append(
            f"kernel: {thread.name} (0x{thread.ident:016x}) state {state}, "
            f"waiting in {channel}"
        )
    return lines


def install(app: web.Application, api) -> None:
    """Watch every reset route, and time the executor calls each one makes."""
    run_in_executor = api._run_in_executor

    async def timed_run_in_executor(func, *args, **kwargs):
        watch = _current.get()
        if watch is None:
            return await run_in_executor(func, *args, **kwargs)
        queued_at = time.monotonic()
        started: list[float] = []

        def timed(*call_args, **call_kwargs):
            started.append(time.monotonic())
            return func(*call_args, **call_kwargs)

        try:
            return await run_in_executor(timed, *args, **kwargs)
        finally:
            end = time.monotonic()
            begin = started[0] if started else end
            watch.record_call(
                getattr(func, "__qualname__", repr(func)),
                begin - queued_at,
                end - begin,
            )

    api._run_in_executor = timed_run_in_executor

    @web.middleware
    async def watch_resets(request: web.Request, handler):
        if not is_reset_path(request.path):
            return await handler(request)
        loop = asyncio.get_running_loop()

        def describe() -> list[str]:
            return [
                *executor_state(loop),
                *database_state(api.db.model),
                *kernel_wait_state(),
            ]

        watch = ResetWatch(
            f"{request.method} {request.path}", describe, threshold=SLOW_RESET_SECONDS
        ).start()
        token = _current.set(watch)
        outcome = "failed"
        try:
            response = await handler(request)
            outcome = f"answered {response.status}"
            return response
        except web.HTTPException as error:
            outcome = f"answered {error.status}"
            raise
        finally:
            _current.reset(token)
            watch.finish(outcome)

    app.middlewares.append(watch_resets)
