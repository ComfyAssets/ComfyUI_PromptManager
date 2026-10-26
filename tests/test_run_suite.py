"""Tests for tests/run_suite.py, the CI test runner and its hang watchdog (#350).

Each test writes a temporary folder of tiny test modules and runs
``python -m tests.run_suite -s <folder> -v`` in a subprocess from the repo
root. Every subprocess call has a timeout, so a broken watchdog fails this
file instead of hanging the suite. Probes that should hang wait a bounded
time, so a watchdog that never fires shows up as a plain assertion failure.
"""

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SUBPROCESS_TIMEOUT = 60
WATCHDOG_ENV = ("PM_TEST_HANG_LIMIT", "PM_TEST_SHUTDOWN_LIMIT")


def run_suite(folder, extra_env=None, *args, verbosity=("-v",)):
    """Run the runner on ``folder`` and return the completed process."""
    env = {k: v for k, v in os.environ.items() if k not in WATCHDOG_ENV}
    # Windows writes a piped stderr in the ANSI codepage; ask for the UTF-8
    # this helper decodes, so non-ASCII values and paths survive.
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra_env or {})
    command = [sys.executable, "-m", "tests.run_suite", "-s", str(folder)]
    return subprocess.run(
        [*command, *verbosity, *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=SUBPROCESS_TIMEOUT,
    )


class RunSuiteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pm-run-suite-")
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name)

    def write(self, name, source):
        (self.folder / name).write_text(textwrap.dedent(source), encoding="utf-8")

    def assertDumped(self, result, test_id):
        detail = result.stderr[-4000:]
        self.assertNotEqual(result.returncode, 0, detail)
        self.assertIn(f"start: {test_id}", result.stderr, detail)
        self.assertIn("Timeout (", result.stderr, detail)
        self.assertIn("Thread 0x", result.stderr, detail)

    def test_passing_test_exits_zero_and_names_each_start(self):
        self.write(
            "test_probe_pass.py",
            """
            import unittest

            class Probe(unittest.TestCase):
                def test_ok(self):
                    pass
            """,
        )
        result = run_suite(self.folder)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("start: test_probe_pass.Probe.test_ok", result.stderr)

    def test_start_line_is_written_at_quiet_verbosity_too(self):
        self.write(
            "test_probe_quiet.py",
            """
            import unittest

            class Probe(unittest.TestCase):
                def test_ok(self):
                    pass
            """,
        )
        result = run_suite(self.folder, None, "-q")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("start: test_probe_quiet.Probe.test_ok", result.stderr)

    def test_start_line_begins_its_own_line_at_default_verbosity(self):
        self.write(
            "test_probe_dots.py",
            """
            import unittest

            class Probe(unittest.TestCase):
                def test_a(self):
                    pass

                def test_b(self):
                    pass
            """,
        )
        result = run_suite(self.folder, verbosity=())
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stderr.splitlines()
        for name in ("test_a", "test_b"):
            self.assertIn(f"start: test_probe_dots.Probe.{name}", lines)

    def test_warnings_are_shown_as_with_unittest_discover(self):
        self.write(
            "test_probe_warn.py",
            """
            import unittest
            import warnings

            class Probe(unittest.TestCase):
                def test_warns(self):
                    warnings.warn("probe-deprecated", DeprecationWarning)
            """,
        )
        env = {"PYTHONWARNINGS": ""}
        result = run_suite(self.folder, env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("probe-deprecated", result.stderr)

    def test_failing_test_exits_one(self):
        self.write(
            "test_probe_fail.py",
            """
            import unittest

            class Probe(unittest.TestCase):
                def test_fails(self):
                    self.fail("expected")
            """,
        )
        result = run_suite(self.folder)
        self.assertEqual(result.returncode, 1, result.stderr)

    def test_pattern_option_selects_modules(self):
        self.write(
            "check_probe.py",
            """
            import unittest

            class Probe(unittest.TestCase):
                def test_fails(self):
                    self.fail("selected by -p")
            """,
        )
        self.assertEqual(run_suite(self.folder).returncode, 5)
        result = run_suite(self.folder, None, "-p", "check_*.py")
        self.assertEqual(result.returncode, 1, result.stderr)

    def test_empty_folder_exits_five(self):
        result = run_suite(self.folder)
        self.assertEqual(result.returncode, 5, result.stderr)

    def test_repo_root_modules_import_as_with_unittest_discover(self):
        self.write(
            "test_probe_import.py",
            """
            import unittest

            from utils.hashing import generate_prompt_hash

            class Probe(unittest.TestCase):
                def test_import(self):
                    same = generate_prompt_hash(" A ") == generate_prompt_hash("a")
                    self.assertTrue(same)
            """,
        )
        result = run_suite(self.folder)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_hung_test_dumps_every_thread_and_exits(self):
        self.write(
            "test_probe_hang.py",
            """
            import time
            import unittest

            class Probe(unittest.TestCase):
                def test_hangs(self):
                    time.sleep(30)
            """,
        )
        result = run_suite(self.folder, {"PM_TEST_HANG_LIMIT": "2"})
        self.assertDumped(result, "test_probe_hang.Probe.test_hangs")

    def test_hang_in_async_teardown_dumps_and_exits(self):
        self.write(
            "test_probe_teardown.py",
            """
            import threading
            import unittest

            class Probe(unittest.IsolatedAsyncioTestCase):
                async def asyncTearDown(self):
                    threading.Event().wait(30)

                async def test_body_passes(self):
                    pass
            """,
        )
        result = run_suite(self.folder, {"PM_TEST_HANG_LIMIT": "2"})
        self.assertDumped(result, "test_probe_teardown.Probe.test_body_passes")

    def test_hang_in_event_loop_shutdown_after_ok_dumps_and_exits(self):
        # The #349 shape: the test passes ("ok" is printed), then the loop's
        # shutdown waits for a default-executor thread that never finishes.
        self.write(
            "test_probe_loop.py",
            """
            import asyncio
            import threading
            import unittest

            class Probe(unittest.IsolatedAsyncioTestCase):
                async def test_leaves_executor_busy(self):
                    loop = asyncio.get_running_loop()
                    loop.run_in_executor(None, threading.Event().wait, 30)
            """,
        )
        result = run_suite(self.folder, {"PM_TEST_HANG_LIMIT": "2"})
        self.assertDumped(result, "test_probe_loop.Probe.test_leaves_executor_busy")
        start = result.stderr.index("start: test_probe_loop")
        self.assertIn("ok", result.stderr[start:], result.stderr[-4000:])

    def test_hang_at_interpreter_exit_dumps_and_exits(self):
        self.write(
            "test_probe_exit.py",
            """
            import threading
            import unittest

            class Probe(unittest.TestCase):
                def test_leaves_thread(self):
                    threading.Thread(target=threading.Event().wait, args=(30,)).start()
            """,
        )
        result = run_suite(self.folder, {"PM_TEST_SHUTDOWN_LIMIT": "2"})
        detail = result.stderr[-4000:]
        self.assertNotEqual(result.returncode, 0, detail)
        self.assertIn("ok", result.stderr, detail)
        self.assertIn("Timeout (", result.stderr, detail)

    def test_invalid_limit_is_a_usage_error(self):
        for name in WATCHDOG_ENV:
            for value in ("abc", "0", "-3", "1.5", "\u00b2"):
                with self.subTest(name=name, value=value):
                    result = run_suite(self.folder, {name: value})
                    self.assertEqual(result.returncode, 2, result.stderr)
                    lines = result.stderr.strip().splitlines()
                    self.assertEqual(len(lines), 1, result.stderr)
                    self.assertIn(name, lines[0])
                    self.assertIn(repr(value), lines[0])


if __name__ == "__main__":
    unittest.main()
