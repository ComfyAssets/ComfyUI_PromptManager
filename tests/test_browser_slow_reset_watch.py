"""A slow page-workflow reset reports where the server waited.

CI run 39904288262 had one ``/__test__/reset`` wait 16 s with nothing in the
server's stderr. tests/browser/slow_reset_watch.py reports any reset past
SLOW_RESET_SECONDS with every thread's stack, taken while it still waits.
These tests hold a real SQLite write lock behind a real test server's reset.
"""

import glob
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

from tests.browser import slow_reset_watch
from tests.browser.slow_reset_watch import REPORT_PREFIX, ResetWatch, is_reset_path

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_TIMEOUT_S = 60
HOLD_SECONDS = slow_reset_watch.SLOW_RESET_SECONDS + 4
# The reset may take a little longer than the hold, never the 30 s busy timeout.
RESET_MARGIN_SECONDS = 10
# While the reset waits, the server still answers other requests this fast.
# Reading the waiting connection from the report's thread froze it (the GIL is
# held while SQLite's connection mutex is awaited) until the lock was released.
RESPONSIVE_SECONDS = 2.0
PROBE_INTERVAL_SECONDS = 0.1


def blocked_here(gate: threading.Event) -> None:
    gate.wait()


class TestResetWatch(unittest.TestCase):
    def test_reset_paths_are_the_test_reset_routes(self):
        self.assertTrue(is_reset_path("/__test__/reset"))
        self.assertTrue(is_reset_path("/__test__/tagging/reset"))
        self.assertFalse(is_reset_path("/prompt_manager/reset"))
        self.assertFalse(is_reset_path("/__test__/reset/extra"))

    def test_a_stalled_reset_reports_state_and_stacks_while_it_waits(self):
        stream = io.StringIO()
        reported = threading.Event()

        class Stream(io.StringIO):
            def flush(self):
                reported.set()

        stream = Stream()
        gate = threading.Event()
        worker = threading.Thread(target=blocked_here, args=(gate,), name="held-up")
        worker.start()
        self.addCleanup(worker.join)
        self.addCleanup(gate.set)
        watch = ResetWatch(
            "POST /__test__/reset",
            lambda: ["database: 1 connections"],
            threshold=0.05,
            stream=stream,
        ).start()
        self.assertTrue(reported.wait(SERVER_TIMEOUT_S))
        report = stream.getvalue().splitlines()
        self.assertTrue(all(line.startswith(REPORT_PREFIX) for line in report))
        text = "\n".join(report)
        self.assertIn("POST /__test__/reset still running after", text)
        self.assertIn("database: 1 connections", text)
        self.assertIn(f"thread 0x{worker.ident:016x} is held-up", text)
        self.assertIn("in blocked_here", text)

        watch.record_call("seed", 0.25, 1.5)
        watch.finish("answered 200")
        self.assertIn("POST /__test__/reset answered 200 after", stream.getvalue())
        self.assertIn(
            "executor call seed: queued 0.250s, ran 1.500s", stream.getvalue()
        )

    def test_a_fast_reset_writes_nothing_and_stops_its_timer(self):
        stream = io.StringIO()
        watch = ResetWatch("reset", lambda: [], threshold=60, stream=stream).start()
        watch.finish("answered 200")
        self.assertTrue(watch._timer.finished.is_set())
        self.assertEqual(stream.getvalue(), "")

    def test_a_failing_state_probe_still_reports_the_stacks(self):
        def broken():
            raise RuntimeError("no state")

        reported = threading.Event()

        class Stream(io.StringIO):
            def flush(self):
                reported.set()

        stream = Stream()
        ResetWatch("reset", broken, threshold=0.01, stream=stream).start()
        self.assertTrue(reported.wait(SERVER_TIMEOUT_S))
        self.assertIn("state unavailable: RuntimeError('no state')", stream.getvalue())
        self.assertIn("most recent call first", stream.getvalue())


class TestSlowResetOnTheServer(unittest.TestCase):
    """A reset behind another process's write lock, on tests/browser/server.py."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = {**os.environ, "TMPDIR": self.tmp.name}
        env.update(TEMP=self.tmp.name, TMP=self.tmp.name)
        self.stderr = tempfile.TemporaryFile()
        self.addCleanup(self.stderr.close)
        process = subprocess.Popen(
            [sys.executable, "-m", "tests.browser.server", "--port", "0"],
            cwd=REPO_ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.stderr,
        )

        def stop():
            if process.poll() is None:
                process.kill()
            process.wait(timeout=SERVER_TIMEOUT_S)
            for stream in (process.stdin, process.stdout):
                stream.close()

        self.addCleanup(stop)
        line = process.stdout.readline().decode()
        self.assertTrue(line.startswith("PM_TEST_SERVER_PORT="), line)
        self.base = f"http://127.0.0.1:{int(line.split('=', 1)[1])}"
        deadline = time.monotonic() + SERVER_TIMEOUT_S
        while True:
            try:
                with urllib.request.urlopen(  # nosec B310
                    self.base + "/prompt_manager/test", timeout=1
                ):
                    break
            except OSError:
                self.assertIsNone(process.poll(), "the server exited early")
                self.assertLess(time.monotonic(), deadline, "never ready")
                time.sleep(0.1)
        [self.db_path] = glob.glob(
            os.path.join(self.tmp.name, "pm-browser-*", "browser.db")
        )

    def reset(self) -> tuple[int, float]:
        request = urllib.request.Request(
            self.base + "/__test__/reset",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        start = time.monotonic()
        with urllib.request.urlopen(  # nosec B310
            request, timeout=SERVER_TIMEOUT_S
        ) as response:
            return response.status, time.monotonic() - start

    def server_stderr(self) -> str:
        self.stderr.seek(0)
        return self.stderr.read().decode("utf-8", "replace")

    def test_a_reset_held_behind_a_write_lock_names_the_wait_and_ends_with_it(self):
        self.assertEqual(self.reset()[0], 200, self.server_stderr())
        self.assertNotIn(REPORT_PREFIX, self.server_stderr())

        held = threading.Event()

        def hold_write_lock():
            holder = sqlite3.connect(self.db_path, timeout=SERVER_TIMEOUT_S)
            try:
                holder.execute("BEGIN IMMEDIATE")
                held.set()
                time.sleep(HOLD_SECONDS)
                holder.rollback()
            finally:
                holder.close()

        lock = threading.Thread(target=hold_write_lock)
        lock.start()
        self.addCleanup(lock.join)
        self.assertTrue(held.wait(SERVER_TIMEOUT_S))
        outcome = []
        resetting = threading.Thread(target=lambda: outcome.append(self.reset()))
        resetting.start()
        self.addCleanup(resetting.join)
        slowest = 0.0
        while resetting.is_alive():
            start = time.monotonic()
            with urllib.request.urlopen(  # nosec B310
                self.base + "/prompt_manager/test", timeout=SERVER_TIMEOUT_S
            ):
                slowest = max(slowest, time.monotonic() - start)
            time.sleep(PROBE_INTERVAL_SECONDS)
        resetting.join()
        lock.join()
        self.assertEqual(len(outcome), 1, self.server_stderr())
        [(status, elapsed)] = outcome

        self.assertLess(slowest, RESPONSIVE_SECONDS)
        self.assertEqual(status, 200)
        self.assertGreaterEqual(elapsed, HOLD_SECONDS - 0.5)
        self.assertLess(elapsed, HOLD_SECONDS + RESET_MARGIN_SECONDS)
        report = [
            line[len(REPORT_PREFIX) :]
            for line in self.server_stderr().splitlines()
            if line.startswith(REPORT_PREFIX)
        ]
        text = "\n".join(report)
        self.assertIn("POST /__test__/reset still running after", text)
        self.assertIn("default executor:", text)
        if sys.platform.startswith("linux"):
            # SQLite's busy handler sleeps between its retries.
            self.assertRegex(
                text, r"kernel: asyncio_\d+ \(0x[0-9a-f]{16}\) state \w, waiting in "
            )
        # The plain reset seeds a scratch copy (#384), then waits to publish it.
        self.assertRegex(text, r"line \d+ in _publish\n")
        self.assertIn("POST /__test__/reset answered 200 after", text)
        self.assertRegex(
            text,
            r"executor call ScratchSeeder.run: queued \d+\.\d+s, ran \d+\.\d+s",
        )


if __name__ == "__main__":
    unittest.main()
