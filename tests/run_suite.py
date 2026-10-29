"""Run the Python suite with a hang watchdog: ``python -m tests.run_suite``.

Why: on PR #349 a macOS job stopped inside the test suite and sat silent until
GitHub cancelled it after six hours, with no stack to read. This runner does
the same discovery as ``python -m unittest discover -s tests`` (same
``sys.path`` when started with ``-m`` from the repo root, same warning
filters, and the exit codes of Python 3.12+ on every version: 0, 1, and 5
when no tests ran), and adds a watchdog built on
``faulthandler.dump_traceback_later``: when the timer is not re-armed in time,
every thread's stack goes to stderr and the process exits non-zero. It works
the same on Linux, macOS and Windows.

The timer is armed before discovery, re-armed at the start of every test with
PM_TEST_HANG_LIMIT seconds (default 120), and re-armed when the run ends with
PM_TEST_SHUTDOWN_LIMIT seconds (default 120) to cover interpreter exit
(non-daemon threads, atexit handlers, coverage's save). It is never
cancelled. Class and module fixtures (setUpClass, tearDownModule, ...) run
between two ``start:`` lines, so they count toward the test before them.
120 s stays below asyncio's 300 s wait for default-executor threads on Python
3.12 and 3.13 (3.10 and 3.11 wait without limit), so a hang in an
``IsolatedAsyncioTestCase`` loop shutdown is dumped inside the test that hung.

Reading a dump: the last ``start: <test id>`` line names the test. If its
"ok" line comes after it, the hang is after the test body: its teardown or
event-loop shutdown, or a class/module fixture before the next test (the
stack shows which). A dump after the summary is a hang at interpreter exit
(a non-daemon thread or an atexit handler).

A limit is a measurement, not a dial: change it only with new measurements
written next to it (TESTING.md "Time limits and hangs").

PM_TEST_SLOWEST=N (opt-in, #487) lists the N slowest tests after the summary.
A test's time runs from its ``start:`` line to the next one, so it includes
the class and module fixtures in between, like the hang limit.
"""

import argparse
import faulthandler
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

DEFAULT_PER_TEST_LIMIT = 120
DEFAULT_SHUTDOWN_LIMIT = 120
NO_TESTS_EXIT = 5
USAGE_EXIT = 2
# LoraManager's settings-folder override (#355): every test reads an empty,
# disposable LoraManager settings folder, never the developer's real one.
LORA_MANAGER_SETTINGS_ENV = "LORA_MANAGER_SETTINGS_DIR"


class LimitError(ValueError):
    """An environment limit is not a positive integer."""


def read_limit(name, default):
    """Return the positive integer in env var ``name``, or ``default``."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    text = raw.strip()
    if not (text.isascii() and text.isdigit()) or int(text) <= 0:
        raise LimitError(f"run_suite: {name} must be a positive integer, got {raw!r}")
    return int(text)


class TestClock:
    """Seconds from each test's start to the next start (or the run's end)."""

    def __init__(self, clock=time.perf_counter):
        self._clock = clock
        self._current = None
        self._started = 0.0
        self.durations = []

    def start(self, test_id):
        self.stop()
        self._current = test_id
        self._started = self._clock()

    def stop(self):
        if self._current is not None:
            self.durations.append((self._clock() - self._started, self._current))
            self._current = None

    def report(self, count):
        """Return the lines that list the ``count`` slowest tests."""
        slowest = sorted(self.durations, key=lambda item: item[0], reverse=True)
        lines = [f"slowest {min(count, len(slowest))} of {len(slowest)} tests:"]
        lines += [f"{seconds:8.2f}s {test_id}" for seconds, test_id in slowest[:count]]
        return lines


def make_result_class(stream, per_test_limit, shutdown_limit, slowest=0):
    """Build a TextTestResult that names each test and re-arms the watchdog."""

    def arm(seconds):
        faulthandler.dump_traceback_later(seconds, exit=True, file=stream)

    clock = TestClock()

    class WatchdogResult(unittest.TextTestResult):
        def startTest(self, test):
            # At default verbosity the progress dots share a line; start ours.
            prefix = "\n" if self.dots else ""
            stream.write(f"{prefix}start: {test.id()}\n")
            stream.flush()
            clock.start(test.id())
            super().startTest(test)
            arm(per_test_limit)

        def stopTestRun(self):
            clock.stop()
            super().stopTestRun()
            if slowest:
                stream.write("\n".join(["", *clock.report(slowest), ""]))
                stream.flush()
            arm(shutdown_limit)

    return WatchdogResult


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="python -m tests.run_suite",
        description="Discover and run the tests with a hang watchdog.",
    )
    # As in unittest, -v and -q share one setting and the last one wins.
    parser.set_defaults(verbosity=1)
    parser.add_argument(
        "-v",
        "--verbose",
        dest="verbosity",
        action="store_const",
        const=2,
        help="verbose output",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        dest="verbosity",
        action="store_const",
        const=0,
        help="quiet output",
    )
    parser.add_argument(
        "-s",
        "--start-directory",
        default=str(Path(__file__).resolve().parent),
        help="directory to start discovery (default: this tests folder)",
    )
    parser.add_argument(
        "-p", "--pattern", default="test*.py", help="pattern to match test files"
    )
    return parser.parse_args(argv)


def isolate_lora_manager_settings():
    """Point LoraManager's settings lookup at a fresh temporary folder.

    Returns the folder; the caller removes it when the run ends.
    """
    folder = tempfile.mkdtemp(prefix="pm-lora-settings-")
    os.environ[LORA_MANAGER_SETTINGS_ENV] = folder
    return folder


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    # Keep the real stderr: a test may replace sys.stderr, and the watchdog
    # needs a stream with a file descriptor.
    stream = sys.stderr
    try:
        per_test_limit = read_limit("PM_TEST_HANG_LIMIT", DEFAULT_PER_TEST_LIMIT)
        shutdown_limit = read_limit("PM_TEST_SHUTDOWN_LIMIT", DEFAULT_SHUTDOWN_LIMIT)
        slowest = read_limit("PM_TEST_SLOWEST", 0)
    except LimitError as error:
        stream.write(f"{error}\n")
        return USAGE_EXIT

    faulthandler.dump_traceback_later(per_test_limit, exit=True, file=stream)
    settings_folder = isolate_lora_manager_settings()
    try:
        start = os.path.abspath(args.start_directory)
        suite = unittest.TestLoader().discover(start, args.pattern, top_level_dir=start)
        runner = unittest.TextTestRunner(
            stream=stream,
            verbosity=args.verbosity,
            # As unittest.main does: show warnings unless -W/PYTHONWARNINGS set them.
            warnings=None if sys.warnoptions else "default",
            resultclass=make_result_class(
                stream, per_test_limit, shutdown_limit, slowest
            ),
        )
        result = runner.run(suite)
    finally:
        shutil.rmtree(settings_folder, ignore_errors=True)
    if result.testsRun == 0 and not result.skipped:
        return NO_TESTS_EXIT
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
