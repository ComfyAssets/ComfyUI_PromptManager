"""Bounded thread-pool map for the long jobs (output scan, thumbnails).

Threads, not processes: this code runs inside ComfyUI's server, where forking
a CUDA-initialised, multi-threaded process is unsafe and the spawn start
method would re-import ComfyUI's main module in every child. Pillow releases
the GIL while decoding, resizing and encoding, so threads still scale close
to linearly for both jobs.

Interpreter exit (#375): ``threading._shutdown`` shuts every thread pool
(``submit`` then raises "cannot schedule new futures after interpreter
shutdown") before it joins the non-daemon threads and long before the package
teardown runs, while our daemon threads keep working. Measured on 3.12 and
3.14, ``Thread.start`` still succeeds in that window; it is refused ("can't
create new thread at interpreter shutdown") only once the interpreter is
marked finalizing, after the ``atexit`` callbacks. ``refused_at_shutdown``
recognises both; ``note_refusal`` logs one DEBUG line per place, with no
traceback.

One long-lived pool (#523): every ``map_parallel`` call shares one pool of
``workers`` threads (``JOB_THREAD_PREFIX``), kept between jobs, instead of a
new pool per call. Image work then reuses the same few threads, and their
malloc arenas, rather than leaving each call's image-sized buffers in a new
set of arenas. A different ``workers`` count (the Worker threads setting
changed) replaces the pool; the old one finishes what it holds and exits.
``shutdown_job_pool`` stops it (the package teardown); the next call starts
a fresh one.
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait

from .exit_signal import checked

SHUTDOWN_REFUSAL = "interpreter shutdown"
# ``submit`` on a pool already shut down (this module's teardown or a resize).
SHUT_POOL_REFUSAL = "after shutdown"
JOB_THREAD_PREFIX = "pm-worker"
# The package teardown's bound for the job threads to finish their item.
JOB_POOL_JOIN_SECONDS = 5
_logger = logging.getLogger("prompt_manager.parallel")
_noted = set()
_noted_lock = threading.Lock()
_pool_lock = threading.Lock()
_pool = None  # (workers, ThreadPoolExecutor)


def refused_at_shutdown(error):
    """Whether ``error`` is a thread or pool start refused at interpreter exit."""
    return isinstance(error, RuntimeError) and SHUTDOWN_REFUSAL in str(error)


def note_refusal(logger, what):
    """Log once per ``what``, at DEBUG and without a traceback, that ``what``
    was refused because the interpreter is exiting."""
    key = (logger.name, what)
    with _noted_lock:
        if key in _noted:
            return
        _noted.add(key)
    logger.debug(f"{what}: refused, the interpreter is exiting")


def job_pool_started():
    """Whether the long-lived job pool exists (the teardown stops it)."""
    return _pool is not None


def _job_pool(workers):
    """The shared pool of ``workers`` threads, replacing one of another size."""
    global _pool
    with _pool_lock:
        if _pool is not None and _pool[0] == workers:
            return _pool[1]
        retired = _pool
        _pool = (
            workers,
            ThreadPoolExecutor(workers, thread_name_prefix=JOB_THREAD_PREFIX),
        )
        current = _pool[1]
    if retired is not None:
        retired[1].shutdown(wait=False)  # it still runs what it holds
    return current


def shutdown_job_pool(timeout=None):
    """Stop the job pool: queued items are cancelled (their callers run
    them on their own thread), running ones are joined up to ``timeout``
    seconds. True when no job thread is left running."""
    global _pool
    with _pool_lock:
        retired, _pool = _pool, None
    if retired is None:
        return True
    executor = retired[1]
    # After shutdown: it takes the lock ``submit`` holds while it starts and
    # records a thread, so every thread is listed and no new one starts.
    executor.shutdown(wait=False, cancel_futures=True)
    threads = list(getattr(executor, "_threads", ()))
    deadline = None if timeout is None else time.monotonic() + timeout
    for thread in threads:
        thread.join(None if deadline is None else max(0.0, deadline - time.monotonic()))
    return not any(thread.is_alive() for thread in threads)


def _forget(executor):
    """Drop *executor* as the shared pool (a later call starts a new one)."""
    global _pool
    with _pool_lock:
        if _pool is not None and _pool[1] is executor:
            _pool = None


def _on_job_thread():
    return threading.current_thread().name.startswith(JOB_THREAD_PREFIX + "_")


def map_parallel(func, items, workers):
    """Apply ``func`` to every item on the shared pool of ``workers``
    threads, keeping order.

    ``workers`` below 1 counts as 1. A call made from a job thread runs on
    that thread, so nested work never waits for its own pool. An exception
    in ``func`` propagates to the caller once the call's other items have
    stopped (those not started are dropped), so callers that must survive
    bad input wrap ``func`` themselves. A pool refused at interpreter exit
    runs the items it did not start on the calling thread (#375). An item
    that has not started when the interpreter starts exiting raises
    ExitRequested instead (#376).
    """
    func = checked(func)
    items = list(items)
    if not items or _on_job_thread():
        return [func(item) for item in items]
    pool = _job_pool(max(1, int(workers or 1)))
    futures = []
    try:
        for item in items:
            futures.append(pool.submit(func, item))
    except RuntimeError as error:
        if refused_at_shutdown(error):
            note_refusal(_logger, "A worker pool")
            # Drop what was queued but not started; it runs here instead.
            # (A refused Thread.start leaves its item queued without a future
            # here: it is cancelled too, unless a busy pool thread took it in
            # between, the one case where an item can run twice.)
            _forget(pool)
            pool.shutdown(wait=False, cancel_futures=True)
        elif SHUT_POOL_REFUSAL not in str(error):
            raise
    return _collect(func, items, futures)


def _collect(func, items, futures):
    """``_run_rest``; on an error, first drop the call's items not started
    and wait for its running ones, as the old per-call pool's exit did."""
    try:
        return _run_rest(func, items, futures)
    except BaseException:
        for future in futures:
            future.cancel()
        wait(futures)
        raise


def _run_rest(func, items, futures):
    """The pool's finished results, and every item it did not run, run here.

    After ``shutdown(cancel_futures=True)`` each submitted future either ran
    (its result or exception is kept) or was cancelled before it started.
    """
    results = []
    for index, item in enumerate(items):
        future = futures[index] if index < len(futures) else None
        if future is not None and not future.cancelled():
            results.append(future.result())
        else:
            results.append(func(item))
    return results
