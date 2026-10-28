"""A thread or pool refused at interpreter shutdown is quiet (#375).

When the interpreter exits, ``threading._shutdown`` first shuts every
``ThreadPoolExecutor`` (``submit`` then raises "cannot schedule new futures
after interpreter shutdown") and joins the non-daemon threads; only then do
the ``atexit`` callbacks (the package teardown) run. Our daemon threads keep
working meanwhile. ``Thread.start`` is refused ("can't create new thread at
interpreter shutdown") only later, once the interpreter is finalizing; the
unit tests cover that case, the child process the pool case. Such a refusal
is logged once at DEBUG, with no traceback, and leaves nothing pending.

``threading.Thread`` is replaced only in the module under test (a copy of
the ``threading`` namespace), never process-wide. The child-process test runs
the real exit: a daemon thread queues previews while the main thread returns.
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from py.api import thumbnail_auto
from utils import image_monitor, parallel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHILD_CEILING_SECONDS = 60  # the hang watchdog allows 120 s per test
REPORT_PREFIX = "PM_EXIT_REPORT "
THREAD_REFUSAL = "can't create new thread at interpreter shutdown"
POOL_REFUSAL = "cannot schedule new futures after interpreter shutdown"


def _refusing_threading():
    """A ``threading`` namespace whose ``Thread.start`` is refused."""

    class RefusedThread(threading.Thread):
        def start(self):
            raise RuntimeError(THREAD_REFUSAL)

    namespace = types.SimpleNamespace(**vars(threading))
    namespace.Thread = RefusedThread
    return namespace


class _ShutPool(ThreadPoolExecutor):
    def submit(self, *args, **kwargs):
        raise RuntimeError(POOL_REFUSAL)


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _capture(testcase, name):
    parallel._noted.clear()  # "once" is per process: start each test afresh
    logger = logging.getLogger(name)
    handler = _Capture()
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

    def restore():
        logger.removeHandler(handler)
        logger.setLevel(previous)

    testcase.addCleanup(restore)
    return handler.records


def _assert_one_debug_line(testcase, records):
    """Exactly one refusal line, at DEBUG and without a traceback."""
    refusals = [r for r in records if "interpreter is exiting" in r.getMessage()]
    testcase.assertEqual(len(refusals), 1, [r.getMessage() for r in records])
    testcase.assertEqual(refusals[0].levelno, logging.DEBUG)
    testcase.assertEqual([r for r in records if r.levelno > logging.DEBUG], [])
    testcase.assertTrue(all(r.exc_info is None for r in records))


class TestScheduleRefusedAtShutdown(unittest.TestCase):
    def setUp(self):
        self.handler = image_monitor.ImageGenerationHandler(
            mock.MagicMock(), mock.MagicMock()
        )
        self.addCleanup(self.handler.close)

    def test_schedule_returns_false_and_leaves_nothing_pending(self):
        records = _capture(self, "prompt_manager.image_monitor")
        with mock.patch.object(image_monitor, "threading", _refusing_threading()):
            self.assertFalse(self.handler.schedule("/out/a.png"))
            self.assertFalse(self.handler.schedule("/out/b.png"))
        self.assertEqual(self.handler.pending_count(), 0)
        _assert_one_debug_line(self, records)

    def test_another_start_failure_still_raises_and_leaves_nothing_pending(self):
        class Failing(threading.Thread):
            def start(self):
                raise RuntimeError("can't start new thread")

        namespace = types.SimpleNamespace(**vars(threading))
        namespace.Thread = Failing
        with mock.patch.object(image_monitor, "threading", namespace):
            with self.assertRaises(RuntimeError):
                self.handler.schedule("/out/a.png")
        self.assertEqual(self.handler.pending_count(), 0)

    def test_schedule_before_shutdown_is_unchanged(self):
        self.handler.processing_delay = 3600
        self.assertTrue(self.handler.schedule("/out/a.png"))
        self.assertEqual(self.handler.pending_count(), 1)


class TestPreviewQueueRefusedAtShutdown(unittest.TestCase):
    def thumbnailer(self, logger):
        made = thumbnail_auto.AutoThumbnailer(
            lambda *a, **k: {"action": "generated"}, lambda: True, logger
        )
        self.addCleanup(made.close)
        return made

    def test_enqueue_is_refused_quietly_and_drops_the_file(self):
        logger = logging.getLogger("prompt_manager.test_exit_window")
        records = _capture(self, logger.name)
        thumbnailer = self.thumbnailer(logger)
        src = Path("/out/a.png")
        with mock.patch.object(thumbnail_auto, "threading", _refusing_threading()):
            self.assertFalse(thumbnailer.enqueue(src, Path("/out/t/a.webp"), False))
            self.assertFalse(thumbnailer.enqueue(src, Path("/out/t/b.webp"), False))
        self.assertFalse(thumbnailer._worker_running)
        self.assertEqual(len(thumbnailer._queue), 0)
        self.assertEqual(thumbnailer._queued, set())
        self.assertTrue(thumbnailer.wait_idle(0))
        _assert_one_debug_line(self, records)


class TestMapParallelRefusedAtShutdown(unittest.TestCase):
    def test_a_shut_pool_runs_the_items_on_the_calling_thread(self):
        records = _capture(self, "prompt_manager.parallel")
        with mock.patch.object(parallel, "ThreadPoolExecutor", _ShutPool):
            self.assertEqual(
                parallel.map_parallel(lambda n: n * n, [1, 2, 3], 4), [1, 4, 9]
            )
        _assert_one_debug_line(self, records)

    def test_another_pool_error_still_raises(self):
        class Broken(ThreadPoolExecutor):
            def submit(self, *args, **kwargs):
                raise RuntimeError("something else")

        with mock.patch.object(parallel, "ThreadPoolExecutor", Broken):
            with self.assertRaises(RuntimeError):
                parallel.map_parallel(lambda n: n, [1, 2], 2)


CHILD = textwrap.dedent("""
    import threading

    # Registered before concurrent.futures is imported, so it runs after the
    # pool's own exit hook: from here on, a pool refuses new work.
    exiting = threading.Event()
    threading._register_atexit(exiting.set)

    import json
    import logging
    import os
    import sys
    from pathlib import Path

    root, tmp = sys.argv[1], sys.argv[2]
    sys.path.insert(0, root)

    from py.api.thumbnail_auto import AutoThumbnailer

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    logger = logging.getLogger("prompt_manager.exit_child")

    def generate(src, dst, is_video, *, stop, guard, track):
        Path(dst).write_bytes(b"preview")
        return {"action": "generated"}

    thumbnailer = AutoThumbnailer(
        generate, lambda: True, logger, workers=lambda: 4
    )
    queued = threading.Event()

    def late_monitor():
        exiting.wait()
        # One batch of two: the worker maps it on a pool.
        with thumbnailer._changed:
            for name in ("a", "b"):
                src = Path(tmp, name + ".png")
                src.write_bytes(b"x")
                thumbnailer.enqueue(src, Path(tmp, name + "_thumb.webp"), False)
        queued.set()

    def holder():
        # Non-daemon: the interpreter waits for it, inside the exit window.
        exiting.wait()
        queued.wait(30)
        idle = thumbnailer.wait_idle(30)
        written = sorted(
            n for n in os.listdir(tmp) if n.endswith("_thumb.webp")
        )
        print("PM_EXIT_REPORT " + json.dumps({"idle": idle, "written": written}))
        sys.stdout.flush()

    threading.Thread(target=late_monitor, daemon=True).start()
    threading.Thread(target=holder).start()
    # Return normally, as ComfyUI does after Ctrl+C.
    """)


class TestExitWindowChild(unittest.TestCase):
    def test_previews_queued_in_the_exit_window_log_no_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = os.path.join(tmp, "child.py")
            with open(script, "w", encoding="utf-8") as handle:
                handle.write(CHILD)
            env = dict(os.environ)
            env.pop("PROMPT_MANAGER_CONFIG_PATH", None)
            env["PROMPT_MANAGER_DATA_DIR"] = tmp
            env["PYTHONIOENCODING"] = "utf-8"
            try:
                completed = subprocess.run(
                    [sys.executable, script, ROOT, tmp],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    timeout=CHILD_CEILING_SECONDS,
                    encoding="utf-8",
                    errors="replace",
                )
            except subprocess.TimeoutExpired as hung:
                self.fail(
                    f"the child did not exit within {CHILD_CEILING_SECONDS} s\n"
                    f"stdout:\n{hung.stdout}\nstderr:\n{hung.stderr}"
                )
        detail = f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        self.assertEqual(completed.returncode, 0, detail)
        reports = [
            line[len(REPORT_PREFIX) :]
            for line in completed.stdout.splitlines()
            if line.startswith(REPORT_PREFIX)
        ]
        self.assertEqual(len(reports), 1, detail)
        report = json.loads(reports[0])
        self.assertTrue(report["idle"], detail)
        # The batch still ran: on the worker's own thread.
        self.assertEqual(report["written"], ["a_thumb.webp", "b_thumb.webp"], detail)
        self.assertNotIn("Traceback", completed.stderr, detail)


if __name__ == "__main__":
    unittest.main()
