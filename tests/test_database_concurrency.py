"""Concurrent access to PromptDatabase from several threads.

ComfyUI touches the database from the server loop, executor threads, the
watchdog image monitor and the queue hook at the same time. Every thread must
get its own connection so one thread's commit or rollback never lands on
another thread's half-done write.
"""

import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.operations import PromptDatabase
from utils.hashing import generate_prompt_hash

THREADS = 8
CALLS_PER_THREAD = 50


class ConcurrencyTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = PromptDatabase(self.path)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self.db.close()
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(self.path + suffix):
                os.unlink(self.path + suffix)

    def _run_threads(self, target):
        errors = []

        def wrapped(index):
            try:
                target(index)
            except Exception as exc:  # noqa: BLE001 - collected for the assertion
                errors.append(exc)
            finally:
                self.db.close()

        threads = [threading.Thread(target=wrapped, args=(i,)) for i in range(THREADS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return errors


class TestPerThreadConnections(ConcurrencyTestCase):
    def test_each_thread_gets_its_own_connection(self):
        main_conn = self.db.model.get_connection()
        self.assertIs(self.db.model.get_connection(), main_conn)

        seen = []

        def grab(_index):
            first = self.db.model.get_connection()
            seen.append((first, self.db.model.get_connection()))

        self.assertEqual(self._run_threads(grab), [])
        self.assertEqual(len(seen), THREADS)
        for first, second in seen:
            self.assertIs(first, second)
            self.assertIsNot(first, main_conn)
        self.assertEqual(len({id(first) for first, _ in seen}), THREADS)

    def test_close_releases_only_the_calling_threads_connection(self):
        main_conn = self.db.model.get_connection()
        other = []

        def grab(_index):
            other.append(self.db.model.get_connection())
            self.db.close()

        self.assertEqual(self._run_threads(grab), [])
        # The main thread's connection survives closes made by other threads
        self.assertIs(self.db.model.get_connection(), main_conn)
        main_conn.execute("SELECT 1").fetchone()
        self.db.close()
        self.assertIsNot(self.db.model.get_connection(), main_conn)


class TestConcurrentWrites(ConcurrencyTestCase):
    def test_distinct_prompts_saved_and_counted_from_many_threads(self):
        def work(index):
            for n in range(CALLS_PER_THREAD):
                text = f"thread {index} prompt {n}"
                pid = self.db.save_prompt(
                    text=text, prompt_hash=generate_prompt_hash(text)
                )
                self.assertTrue(self.db.record_prompt_use(pid))

        self.assertEqual(self._run_threads(work), [])

        info = self.db.model.get_database_info()
        self.assertEqual(info["total_prompts"], THREADS * CALLS_PER_THREAD)
        prompts = self.db.search_prompts(limit=THREADS * CALLS_PER_THREAD + 1)
        self.assertEqual(len(prompts), THREADS * CALLS_PER_THREAD)
        self.assertTrue(all(p["run_count"] == 1 for p in prompts))

    def test_saving_the_same_prompt_from_many_threads_keeps_one_row(self):
        text = "the same prompt from every thread"
        prompt_hash = generate_prompt_hash(text)
        ids = []

        def work(_index):
            for _ in range(CALLS_PER_THREAD):
                pid = self.db.save_prompt(text=text, prompt_hash=prompt_hash)
                ids.append(pid)
                self.assertTrue(self.db.record_prompt_use(pid))

        self.assertEqual(self._run_threads(work), [])

        self.assertEqual(len(ids), THREADS * CALLS_PER_THREAD)
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(self.db.model.get_database_info()["total_prompts"], 1)
        prompt = self.db.get_prompt_by_hash(prompt_hash)
        self.assertEqual(prompt["id"], ids[0])
        self.assertEqual(prompt["run_count"], THREADS * CALLS_PER_THREAD)


if __name__ == "__main__":
    unittest.main()
