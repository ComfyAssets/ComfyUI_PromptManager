"""The shared, non-blocking model gate (#249 B2).

One owner at a time may load, swap, run, download or unload a tagging model.
A release requested while gated work still runs in a worker thread takes
effect only when that work really exits, so a client disconnect never lets a
second owner swap the model under a running inference.
"""

import asyncio
import os
import sys
import threading
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:  # guarded so a missing module fails assertions, not test loading
    from py import model_gate
except ImportError:
    model_gate = None

WAIT = 30.0  # ceiling for cross-thread handoffs, never a short clock


class GateTestCase(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(model_gate, "py.model_gate is not implemented")
        self.gate = model_gate.ModelGate()


class TestGate(GateTestCase):
    def test_gate_is_nonblocking_and_single_owner(self):
        self.assertIsNone(self.gate.holder())
        self.assertTrue(self.gate.try_acquire("legacy-batch"))
        self.assertEqual(self.gate.holder(), "legacy-batch")

        # A second owner is refused at once, never queued.
        self.assertFalse(self.gate.try_acquire("image-annotations"))
        self.assertFalse(self.gate.try_acquire("legacy-batch"))
        self.assertEqual(self.gate.holder(), "legacy-batch")

        # Only the holder can release.
        self.gate.release("image-annotations")
        self.assertEqual(self.gate.holder(), "legacy-batch")
        self.gate.release("legacy-batch")
        self.assertIsNone(self.gate.holder())
        self.gate.release("legacy-batch")  # idempotent

        self.assertTrue(self.gate.try_acquire("image-annotations"))
        self.assertEqual(self.gate.holder(), "image-annotations")

    def test_release_waits_for_running_work(self):
        self.assertTrue(self.gate.try_acquire("owner"))
        self.gate.begin("owner")
        self.gate.release("owner")
        self.assertEqual(self.gate.holder(), "owner")
        self.assertFalse(self.gate.try_acquire("other"))
        # No new work is admitted once the owner let go.
        with self.assertRaises(model_gate.GateNotHeld):
            self.gate.begin("owner")
        self.gate.end("owner")
        self.assertIsNone(self.gate.holder())

    def test_begin_requires_the_holder(self):
        with self.assertRaises(model_gate.GateNotHeld):
            self.gate.begin("nobody")
        self.assertTrue(self.gate.try_acquire("owner"))
        with self.assertRaises(model_gate.GateNotHeld):
            self.gate.begin("other")
        self.gate.end("other")  # a stray end never disturbs the holder
        self.assertEqual(self.gate.holder(), "owner")

    def test_module_gate_is_one_process_wide_instance(self):
        self.assertIsNone(model_gate.holder())
        self.assertTrue(model_gate.try_acquire("a"))
        try:
            self.assertFalse(model_gate.try_acquire("b"))
            self.assertEqual(model_gate.holder(), "a")
        finally:
            model_gate.release("a")
        self.assertIsNone(model_gate.holder())

    def test_new_owner_names_are_unique(self):
        first = model_gate.new_owner("legacy-single")
        second = model_gate.new_owner("legacy-single")
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("legacy-single:"))


class TestGatedExecutor(GateTestCase):
    def test_cancelled_waiter_keeps_the_gate_until_the_thread_exits(self):
        started = threading.Event()
        proceed = threading.Event()
        finished = threading.Event()

        def blocking_work():
            started.set()
            proceed.wait(WAIT)
            finished.set()
            return "done"

        async def scenario():
            self.assertTrue(self.gate.try_acquire("native"))
            task = asyncio.ensure_future(self.gate.run("native", blocking_work))
            try:
                await asyncio.get_running_loop().run_in_executor(
                    None, started.wait, WAIT
                )
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            finally:
                self.gate.release("native")
            # The waiter is gone but the inference still runs: still held.
            self.assertEqual(self.gate.holder(), "native")
            self.assertFalse(self.gate.try_acquire("legacy-unload"))
            proceed.set()
            await asyncio.get_running_loop().run_in_executor(None, finished.wait, WAIT)
            for _ in range(1000):
                if self.gate.holder() is None:
                    break
                await asyncio.sleep(0.01)
            self.assertIsNone(self.gate.holder())

        asyncio.run(scenario())

    def test_run_returns_the_result_and_propagates_errors(self):
        async def scenario():
            self.assertTrue(self.gate.try_acquire("owner"))
            try:
                self.assertEqual(await self.gate.run("owner", lambda x: x * 2, 21), 42)

                def boom():
                    raise ValueError("bad")

                with self.assertRaises(ValueError):
                    await self.gate.run("owner", boom)
                self.assertEqual(self.gate.holder(), "owner")
            finally:
                self.gate.release("owner")
            self.assertIsNone(self.gate.holder())
            with self.assertRaises(model_gate.GateNotHeld):
                await self.gate.run("owner", lambda: None)

        asyncio.run(scenario())

    def test_work_that_never_starts_gives_its_admission_back(self):
        async def scenario():
            loop = asyncio.get_running_loop()
            self.assertTrue(self.gate.try_acquire("owner"))

            def refused(executor, func):
                raise RuntimeError("executor shut down")

            with unittest.mock.patch.object(loop, "run_in_executor", refused):
                with self.assertRaises(RuntimeError):
                    await self.gate.run("owner", lambda: None)

            def cancelled(executor, func):
                future = loop.create_future()
                future.cancel()  # a loop shutdown drops the queued call
                return future

            with unittest.mock.patch.object(loop, "run_in_executor", cancelled):
                with self.assertRaises(asyncio.CancelledError):
                    await self.gate.run("owner", lambda: None)
            await asyncio.sleep(0)  # let the done callback run
            self.gate.release("owner")
            self.assertIsNone(self.gate.holder())

        asyncio.run(scenario())

    def test_skipped_work_runs_its_cleanup_exactly_once(self):
        async def scenario():
            loop = asyncio.get_running_loop()
            skipped = []

            def cleanup():
                skipped.append(True)

            self.assertTrue(self.gate.try_acquire("owner"))
            self.assertEqual(
                await self.gate.run("owner", lambda: 7, on_skipped=cleanup), 7
            )
            self.assertEqual(skipped, [])

            def refused(executor, func):
                raise RuntimeError("executor shut down")

            with unittest.mock.patch.object(loop, "run_in_executor", refused):
                with self.assertRaises(RuntimeError):
                    await self.gate.run("owner", lambda: None, on_skipped=cleanup)
            self.assertEqual(skipped, [True])

            def cancelled(executor, func):
                future = loop.create_future()
                future.cancel()
                return future

            with unittest.mock.patch.object(loop, "run_in_executor", cancelled):
                with self.assertRaises(asyncio.CancelledError):
                    await self.gate.run("owner", lambda: None, on_skipped=cleanup)
            await asyncio.sleep(0)  # let the done callback run
            self.assertEqual(skipped, [True, True])

            self.gate.release("owner")
            with self.assertRaises(model_gate.GateNotHeld):
                await self.gate.run("owner", lambda: None, on_skipped=cleanup)
            self.assertEqual(skipped, [True, True, True])
            self.assertIsNone(self.gate.holder())

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
