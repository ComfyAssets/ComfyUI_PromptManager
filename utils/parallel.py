"""Bounded thread-pool map for the long jobs (output scan, thumbnails).

Threads, not processes: this code runs inside ComfyUI's server, where forking
a CUDA-initialised, multi-threaded process is unsafe and the spawn start
method would re-import ComfyUI's main module in every child. Pillow releases
the GIL while decoding, resizing and encoding, so threads still scale close
to linearly for both jobs.
"""

from concurrent.futures import ThreadPoolExecutor


def map_parallel(func, items, workers):
    """Apply ``func`` to every item on up to ``workers`` threads, keeping order.

    ``workers`` of 1 (or fewer) runs on the calling thread with no pool. An
    exception in ``func`` propagates to the caller, so callers that must
    survive bad input wrap ``func`` themselves.
    """
    items = list(items)
    workers = max(1, int(workers or 1))
    if workers == 1 or len(items) <= 1:
        return [func(item) for item in items]
    max_workers = min(workers, len(items))
    with ThreadPoolExecutor(max_workers, thread_name_prefix="pm-worker") as pool:
        return list(pool.map(func, items))
