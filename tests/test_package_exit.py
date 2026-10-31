"""The package teardown runs at a normal interpreter exit (#301, #302).

ComfyUI catches Ctrl+C in ``main.py`` and returns, so its exit is a normal
return: ``atexit`` callbacks then run, newest first. A child process imports
the package with ComfyUI stubbed, leaves the image worker alive in its
processing delay and returns. Its own ``atexit`` reporter, registered before
the package, runs after the teardown and prints the result as one JSON line.
"""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The parent never waits longer than this for the child (the hang watchdog
# allows 120 s per test).
CHILD_CEILING_SECONDS = 60
REPORT_PREFIX = "PM_EXIT_REPORT "

CHILD = textwrap.dedent("""
    import atexit
    import contextlib
    import io
    import json
    import os
    import sys
    import threading

    root, tmp = sys.argv[1], sys.argv[2]
    stuck = sys.argv[3:] == ["stuck-observer"]
    sys.path.insert(0, root)
    state = {}

    # watchdog 7's Windows observer at its worst (#561): start() starts a
    # plain reader thread, and stop() waits for it, which never ends.
    class StuckObserver:

        def __init__(self, *args, **kwargs):
            self._never = threading.Event()
            self._reader = None

        def schedule(self, *args, **kwargs):
            pass

        def start(self):
            self._reader = threading.Thread(
                target=self._never.wait, name="StuckReader"
            )
            self._reader.start()

        def stop(self):
            self._reader.join()

        def join(self, timeout=None):
            self._reader.join(timeout)

        def is_alive(self):
            return self._reader.is_alive()

    if stuck:
        import watchdog.observers

        watchdog.observers.Observer = StuckObserver

    def report():
        from watchdog.utils import BaseThread

        package = state.get("package")
        result = getattr(package, "_background_work_result", None)
        # The image worker, the tracker, and watchdog's observer and emitters.
        # watchdog 7's InotifyFD is one daemon reader per process that no
        # observer stop ends, by design; it is not the package's.
        alive = sorted(
            t.name
            for t in threading.enumerate()
            if t.name in ("PromptManagerImageWorker", "PromptManagerPromptCleanup")
            or (isinstance(t, BaseThread) and type(t).__name__ != "InotifyFD")
        )
        # Threads that would hold the exit before any atexit callback (#561).
        holding = sorted(
            t.name
            for t in threading.enumerate()
            if not t.daemon and t is not threading.main_thread()
        )
        print(
            "PM_EXIT_REPORT "
            + json.dumps({"result": result, "alive": alive, "holding": holding})
        )
        sys.stdout.flush()

    # First registered, so it runs last: after the package's teardown and
    # after the history dispose.
    atexit.register(report)

    from tests.test_init_package import _import_package, fake_server_module

    output = os.path.join(tmp, "output")
    with contextlib.redirect_stdout(io.StringIO()):
        package, _ = _import_package(
            "pm_exit_child",
            os.path.join(tmp, "prompts.db"),
            output,
            fake_server_module(queue_task_done=True),
        )
    state["package"] = package
    if stuck:
        # The stop gives up after this long instead of the full 5 s.
        monitor_module = sys.modules[package.__name__ + ".utils.image_monitor"]
        monitor_module.OBSERVER_JOIN_SECONDS = 0.5
    monitor = package._global_image_monitor
    handler = monitor.handler if monitor is not None else None
    if handler is None:
        sys.exit(3)
    handler.processing_delay = 3600
    path = os.path.join(tmp, "busy.png")  # outside the watched folder
    with open(path, "wb") as image:
        image.write(b"x" * 16)
    if not handler.schedule(path) or not handler._worker.is_alive():
        sys.exit(4)
    # Return normally with the worker alive, as ComfyUI does after Ctrl+C.
    """)


def run_child(testcase, *args):
    """Run CHILD with ``args``; returns (completed, report, detail)."""
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "output"))
        script = os.path.join(tmp, "child.py")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(CHILD)
        env = dict(os.environ)
        env.pop("PROMPT_MANAGER_CONFIG_PATH", None)
        # The child's library and log file live in its own folder, never
        # in the checkout's logs/ (which this runner may hold open) or a
        # data folder the caller set.
        env["PROMPT_MANAGER_DATA_DIR"] = tmp
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            completed = subprocess.run(
                [sys.executable, script, ROOT, tmp, *args],
                cwd=ROOT,
                env=env,
                capture_output=True,
                timeout=CHILD_CEILING_SECONDS,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired as hung:
            testcase.fail(
                f"the child did not exit within {CHILD_CEILING_SECONDS} s\n"
                f"stdout:\n{hung.stdout}\nstderr:\n{hung.stderr}"
            )
        # Leaving the block removes the folder: on Windows that fails
        # if the child left a handle open, which it cannot once it exited,
        # so the check here is that the child exited at all.
    detail = f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    testcase.assertEqual(completed.returncode, 0, detail)
    reports = [
        line[len(REPORT_PREFIX) :]
        for line in completed.stdout.splitlines()
        if line.startswith(REPORT_PREFIX)
    ]
    testcase.assertEqual(len(reports), 1, detail)
    return completed, json.loads(reports[0]), detail


class TestPackageExit(unittest.TestCase):
    def test_teardown_runs_at_a_normal_exit_with_the_worker_alive(self):
        completed, report, detail = run_child(self)
        # "thumbnails" is True here because no preview worker started; its
        # close result is tested in test_thumbnail_auto and test_init_package.
        self.assertEqual(
            report["result"],
            {"monitor": True, "thumbnails": True, "tracker": True},
            detail,
        )
        self.assertEqual(report["alive"], [], detail)
        self.assertEqual(report["holding"], [], detail)
        # The teardown's own warnings. Import-time warnings (the API's
        # output-folder search finds no ComfyUI here) are not about exit.
        for text in (
            "Traceback",
            "Background work not stopped in time",
            "still running after",
            "Error while stopping",
        ):
            self.assertNotIn(text, completed.stderr, detail)

    def test_an_observer_that_never_stops_does_not_hold_the_exit(self):
        # Before #561 the reader thread kept the interpreter waiting before
        # any atexit callback ran, so the teardown never started.
        completed, report, detail = run_child(self, "stuck-observer")
        self.assertEqual(
            report["result"],
            {"monitor": False, "thumbnails": True, "tracker": True},
            detail,
        )
        # The stuck reader is still alive, but as a daemon it holds nothing.
        self.assertEqual(report["holding"], [], detail)
        self.assertIn("watchdog observer still running", completed.stderr, detail)
        self.assertIn("Background work not stopped in time", completed.stderr, detail)
