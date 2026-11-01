"""Tests for utils/parallel.py: bounded thread-pool map used by long jobs.

The jobs share one long-lived pool of Worker threads (#523): its threads (and
their malloc arenas) are reused from job to job instead of a new set per call.
"""

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import parallel  # noqa: E402
from utils.parallel import JOB_THREAD_PREFIX, map_parallel  # noqa: E402

WAIT = 30


def _job_threads():
    return {
        t for t in threading.enumerate() if t.name.startswith(JOB_THREAD_PREFIX + "_")
    }


class TestMapParallel(unittest.TestCase):
    def setUp(self):
        self.assertTrue(parallel.shutdown_job_pool(WAIT))
        self.addCleanup(parallel.shutdown_job_pool, WAIT)

    def test_results_keep_input_order(self):
        def slow_square(n):
            time.sleep(0.01 * (5 - n))  # later items finish first
            return n * n

        self.assertEqual(
            map_parallel(slow_square, [1, 2, 3, 4], workers=4), [1, 4, 9, 16]
        )

    def test_one_worker_runs_on_the_one_job_thread(self):
        names = []
        map_parallel(
            lambda _: names.append(threading.current_thread().name), [1, 2, 3], 1
        )
        self.assertEqual(len(set(names)), 1)
        self.assertTrue(names[0].startswith(JOB_THREAD_PREFIX + "_"), names)

    def test_jobs_reuse_the_same_long_lived_threads(self):
        """Arenas are reused instead of a new set of threads per job."""
        first, second = set(), set()
        map_parallel(lambda _: first.add(threading.current_thread()), range(8), 2)
        map_parallel(lambda _: second.add(threading.current_thread()), range(8), 2)
        self.assertTrue(second <= first, (first, second))
        self.assertLessEqual(len(_job_threads()), 2)
        self.assertTrue(all(t.is_alive() for t in first | second))

    def test_a_new_worker_count_replaces_the_pool(self):
        map_parallel(lambda n: n, range(4), 2)
        old = _job_threads()
        map_parallel(lambda n: n, range(4), 3)
        for thread in old:
            thread.join(WAIT)
            self.assertFalse(thread.is_alive(), thread.name)
        self.assertLessEqual(len(_job_threads()), 3)

    def test_shutdown_also_joins_a_pool_a_resize_retired(self):
        entered, release = threading.Event(), threading.Event()

        def hold(_):
            entered.set()
            release.wait(WAIT)

        caller = threading.Thread(target=map_parallel, args=(hold, [1], 1))
        caller.start()
        self.addCleanup(caller.join, WAIT)
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(WAIT))
        map_parallel(lambda n: n, [1, 2], 2)  # a new size retires the busy pool
        self.assertFalse(parallel.shutdown_job_pool(0.05))
        release.set()
        caller.join(WAIT)
        self.assertTrue(parallel.shutdown_job_pool(WAIT))
        self.assertFalse(parallel.job_pool_started())

    def test_an_item_run_from_a_job_thread_runs_inline(self):
        """A nested map never waits for the threads it is running on."""

        def outer(n):
            return map_parallel(lambda m: (m, threading.current_thread()), [n], 1)

        results = map_parallel(outer, [1, 2], 1)
        self.assertEqual([r[0][0] for r in results], [1, 2])

    def test_shutdown_stops_the_threads_and_a_later_job_starts_fresh(self):
        map_parallel(lambda n: n, range(4), 2)
        threads = _job_threads()
        self.assertTrue(threads)
        self.assertTrue(parallel.job_pool_started())
        self.assertTrue(parallel.shutdown_job_pool(WAIT))
        self.assertFalse(parallel.job_pool_started())
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(map_parallel(lambda n: n + 1, [1], 2), [2])

    def test_shutdown_reports_a_thread_still_running(self):
        entered, release = threading.Event(), threading.Event()

        def hold(_):
            entered.set()
            release.wait(WAIT)

        caller = threading.Thread(target=map_parallel, args=(hold, [1], 1))
        caller.start()
        self.assertTrue(entered.wait(WAIT))
        self.assertFalse(parallel.shutdown_job_pool(0.05))
        release.set()
        caller.join(WAIT)
        self.assertFalse(caller.is_alive())

    def test_a_failing_item_waits_for_the_running_ones(self):
        """The caller sees the error only after its other items stopped."""
        running = threading.Event()
        finished = []

        def item(n):
            if n == 0:
                self.assertTrue(running.wait(WAIT))
                raise ValueError("zero")
            running.set()
            time.sleep(0.05)
            finished.append(n)
            return n

        with self.assertRaises(ValueError):
            map_parallel(item, [0, 1], 2)
        self.assertEqual(finished, [1])

    def test_several_workers_use_several_threads(self):
        names = set()
        lock = threading.Lock()

        def record(_):
            with lock:
                names.add(threading.current_thread().name)
            time.sleep(0.05)

        map_parallel(record, range(8), workers=4)
        self.assertGreaterEqual(len(names), 2)
        self.assertNotIn(threading.current_thread().name, names)

    def test_worker_count_is_capped_by_item_count_and_floor_one(self):
        self.assertEqual(map_parallel(lambda n: n + 1, [1], workers=16), [2])
        self.assertEqual(map_parallel(lambda n: n + 1, [1, 2], workers=0), [2, 3])
        self.assertEqual(map_parallel(lambda n: n, [], workers=4), [])

    def test_exception_propagates_to_the_caller(self):
        def boom(n):
            if n == 2:
                raise ValueError("two")
            return n

        with self.assertRaises(ValueError):
            map_parallel(boom, [1, 2, 3], workers=3)


if __name__ == "__main__":
    unittest.main()
