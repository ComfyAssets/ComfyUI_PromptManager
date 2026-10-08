"""Tests for utils/parallel.py: bounded thread-pool map used by long jobs."""

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.parallel import map_parallel  # noqa: E402


class TestMapParallel(unittest.TestCase):

    def test_results_keep_input_order(self):
        def slow_square(n):
            time.sleep(0.01 * (5 - n))  # later items finish first
            return n * n

        self.assertEqual(
            map_parallel(slow_square, [1, 2, 3, 4], workers=4), [1, 4, 9, 16]
        )

    def test_one_worker_runs_on_the_calling_thread(self):
        names = []
        map_parallel(
            lambda _: names.append(threading.current_thread().name), [1, 2, 3], 1
        )
        self.assertEqual(set(names), {threading.current_thread().name})

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
