"""Bounded thread-pool map for the long jobs (output scan, thumbnails).

Threads, not processes: this code runs inside ComfyUI's server, where forking
a CUDA-initialised, multi-threaded process is unsafe and the spawn start
method would re-import ComfyUI's main module in every child. Pillow releases
the GIL while decoding, resizing and encoding, so threads still scale close
to linearly for both jobs.

Interpreter exit (#375): ``threading._shutdown`` shuts every thread pool
before it joins the non-daemon threads and long before the package teardown
runs, while our daemon threads keep working. A pool then refuses new work
("cannot schedule new futures after interpreter shutdown") and a late
``Thread.start`` can be refused ("can't create new thread at interpreter
shutdown"). ``refused_at_shutdown`` recognises both; ``note_refusal`` logs
one DEBUG line per place, with no traceback.
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

SHUTDOWN_REFUSAL = "interpreter shutdown"
_logger = logging.getLogger("prompt_manager.parallel")
_noted = set()
_noted_lock = threading.Lock()


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


def map_parallel(func, items, workers):
    """Apply ``func`` to every item on up to ``workers`` threads, keeping order.

    ``workers`` of 1 (or fewer) runs on the calling thread with no pool. An
    exception in ``func`` propagates to the caller, so callers that must
    survive bad input wrap ``func`` themselves. A pool refused at interpreter
    exit runs the items it did not start on the calling thread (#375).
    """
    items = list(items)
    workers = max(1, int(workers or 1))
    if workers == 1 or len(items) <= 1:
        return [func(item) for item in items]
    max_workers = min(workers, len(items))
    with ThreadPoolExecutor(max_workers, thread_name_prefix="pm-worker") as pool:
        futures = []
        try:
            for item in items:
                futures.append(pool.submit(func, item))
        except RuntimeError as error:
            if not refused_at_shutdown(error):
                raise
            note_refusal(_logger, "A worker pool")
            # Drop what was queued but not started; it runs here instead.
            # (A refused Thread.start leaves its item queued without a future
            # here: it is cancelled too, unless a busy pool thread took it in
            # between, the one case where an item can run twice.)
            pool.shutdown(wait=True, cancel_futures=True)
        else:
            return [future.result() for future in futures]
    return _run_rest(func, items, futures)


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
