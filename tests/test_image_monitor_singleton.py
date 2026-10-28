"""
Tests for image monitor singleton lifecycle.

Verifies that:
- The singleton image monitor survives node garbage collection
- Node __del__ / cleanup_gallery_system does NOT stop the shared monitor
- Multiple node instances share the same monitor
- The monitor observer stays alive across node lifecycles
"""

import os
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock ComfyUI server before importing anything that touches config. Only the
# first test module to run installs the stub; nothing else in sys.modules is
# replaced, so later modules (test_comfyui_integration) see the real package.
_mock_server = MagicMock()
_mock_server.PromptServer.instance.routes = MagicMock()
sys.modules.setdefault("server", _mock_server)

from utils.image_monitor import get_image_monitor
import utils.image_monitor as im_mod


class TestMonitorSingletonLifecycle(unittest.TestCase):
    """The singleton monitor must survive node instance garbage collection."""

    def setUp(self):
        # Reset singleton for each test
        im_mod._monitor_instance = None

    def tearDown(self):
        im_mod._monitor_instance = None

    def test_get_image_monitor_returns_singleton(self):
        """Multiple calls should return the exact same instance."""
        db = MagicMock()
        tracker = MagicMock()

        m1 = get_image_monitor(db, tracker)
        m2 = get_image_monitor(db, tracker)

        self.assertIs(m1, m2)

    def test_singleton_survives_across_different_callers(self):
        """Different db/tracker args on subsequent calls still return the same
        instance."""
        db1, tracker1 = MagicMock(), MagicMock()
        db2, tracker2 = MagicMock(), MagicMock()

        m1 = get_image_monitor(db1, tracker1)
        m2 = get_image_monitor(db2, tracker2)

        self.assertIs(m1, m2)

    def test_cleanup_does_not_stop_monitor(self):
        """PromptManagerBase.cleanup_gallery_system must NOT stop the monitor."""
        from prompt_manager_base import PromptManagerBase

        with patch.object(PromptManagerBase, "__init__", lambda self, **kw: None):
            node = PromptManagerBase()
            node.logger = MagicMock()

            mock_monitor = MagicMock()
            node.image_monitor = mock_monitor

            node.cleanup_gallery_system()

            mock_monitor.stop_monitoring.assert_not_called()

    def test_del_does_not_stop_monitor(self):
        """Node __del__ must NOT stop the singleton monitor."""
        from prompt_manager_base import PromptManagerBase

        with patch.object(PromptManagerBase, "__init__", lambda self, **kw: None):
            node = PromptManagerBase()
            node.logger = MagicMock()

            mock_monitor = MagicMock()
            node.image_monitor = mock_monitor

            # Simulate garbage collection
            del node

            mock_monitor.stop_monitoring.assert_not_called()

    def test_observer_stays_alive_after_node_cleanup(self):
        """A running observer must remain alive after node cleanup."""
        db = MagicMock()
        tracker = MagicMock()
        monitor = get_image_monitor(db, tracker)

        # Simulate a running observer
        mock_observer = MagicMock()
        mock_observer.is_alive.return_value = True
        monitor.observer = mock_observer
        monitor.handler = MagicMock()
        monitor.monitored_directories = ["/fake/output"]

        from prompt_manager_base import PromptManagerBase

        with patch.object(PromptManagerBase, "__init__", lambda self, **kw: None):
            node = PromptManagerBase()
            node.logger = MagicMock()
            node.image_monitor = monitor

            node.cleanup_gallery_system()

        # Observer must still be alive
        self.assertIsNotNone(monitor.observer)
        self.assertTrue(monitor.observer.is_alive())
        self.assertEqual(monitor.monitored_directories, ["/fake/output"])

    def test_monitor_start_not_called_when_already_running(self):
        """start_monitoring should be a no-op if observer is already active."""
        db = MagicMock()
        tracker = MagicMock()
        monitor = get_image_monitor(db, tracker)

        # Set up as if already running
        mock_observer = MagicMock()
        monitor.observer = mock_observer

        monitor.start_monitoring()

        # Should not create a new observer
        self.assertIs(monitor.observer, mock_observer)


class FakeObserver:
    """Records what the monitor does with the watchdog observer.

    ``calls`` lists ``stop`` and ``join`` in order (shared with a test's own
    recorders when given); ``stay_alive`` keeps it alive after ``join``;
    ``stop_error`` makes ``stop`` raise.
    """

    def __init__(self, calls=None, stay_alive=False, stop_error=None):
        self.scheduled = []
        self.started = False
        self.stopped = False
        self.joined = False
        self.calls = calls if calls is not None else []
        self.stay_alive = stay_alive
        self.stop_error = stop_error

    def schedule(self, handler, path, recursive=False):
        self.scheduled.append((handler, path, recursive))

    def start(self):
        self.started = True

    def stop(self):
        self.calls.append(("stop",))
        if self.stop_error is not None:
            raise self.stop_error
        self.stopped = True

    def join(self, timeout=None):
        self.calls.append(("join", timeout))
        self.joined = True

    def is_alive(self):
        if self.stay_alive:
            return self.started
        return self.started and not self.stopped


class TestStartStop(unittest.TestCase):
    """start_monitoring validates directories before it creates an observer."""

    def setUp(self):
        im_mod._monitor_instance = None
        self.monitor = im_mod.ImageMonitor(MagicMock(), MagicMock())
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.missing = os.path.join(self.tmp.name, "does-not-exist")

    def tearDown(self):
        # Before the folder is removed (addCleanup runs after tearDown).
        self.assertTrue(self.monitor.stop_monitoring())
        im_mod._monitor_instance = None

    def test_only_nonexistent_directories_leaves_monitoring_off(self):
        with patch.object(im_mod, "Observer") as observer_cls:
            self.monitor.start_monitoring([self.missing])
        observer_cls.assert_not_called()
        self.assertFalse(self.monitor.is_monitoring)
        self.assertIsNone(self.monitor.observer)
        self.assertEqual(self.monitor.monitored_directories, [])
        self.monitor.stop_monitoring()  # must not raise

    def test_stop_without_start_does_not_raise(self):
        self.monitor.stop_monitoring()
        self.assertFalse(self.monitor.is_monitoring)

    def test_start_twice_is_a_noop(self):
        fake = FakeObserver()
        with patch.object(im_mod, "Observer", return_value=fake) as observer_cls:
            self.monitor.start_monitoring([self.tmp.name])
            self.monitor.start_monitoring([self.tmp.name])
        self.assertEqual(observer_cls.call_count, 1)
        self.assertEqual(len(fake.scheduled), 1)
        self.assertTrue(self.monitor.is_monitoring)

    def test_stop_joins_the_observer_and_clears_state(self):
        fake = FakeObserver()
        with patch.object(im_mod, "Observer", return_value=fake):
            self.monitor.start_monitoring([self.tmp.name, self.missing])
        self.assertTrue(fake.started)
        self.assertEqual([p for _, p, _ in fake.scheduled], [self.tmp.name])
        self.assertEqual(self.monitor.monitored_directories, [self.tmp.name])
        self.assertTrue(self.monitor.get_status()["observer_alive"])

        self.monitor.stop_monitoring()
        self.assertTrue(fake.stopped)
        self.assertTrue(fake.joined)
        self.assertFalse(self.monitor.is_monitoring)
        self.assertIsNone(self.monitor.handler)
        self.assertEqual(self.monitor.monitored_directories, [])
        self.assertFalse(self.monitor.get_status()["running"])

    def test_stop_shuts_the_handler_worker_down(self):
        fake = FakeObserver()
        with patch.object(im_mod, "Observer", return_value=fake):
            self.monitor.start_monitoring([self.tmp.name])
        handler = self.monitor.handler
        handler.processing_delay = 10
        image = os.path.join(self.tmp.name, "w.png")
        with open(image, "wb") as f:
            f.write(b"x" * 16)
        handler.schedule(image)
        worker = handler._worker
        self.assertTrue(worker.is_alive())

        self.assertTrue(self.monitor.stop_monitoring())

        self.assertFalse(worker.is_alive())
        self.assertEqual(handler.pending_count(), 0)
        # A restart gets a fresh handler and observer
        fake2 = FakeObserver()
        with patch.object(im_mod, "Observer", return_value=fake2):
            self.monitor.start_monitoring([self.tmp.name])
        self.assertTrue(self.monitor.is_monitoring)
        self.assertIsNot(self.monitor.handler, handler)
        self.monitor.stop_monitoring()

    def test_observer_failing_to_start_leaves_monitoring_off(self):
        fake = FakeObserver()
        fake.start = MagicMock(side_effect=OSError("inotify limit reached"))
        with patch.object(im_mod, "Observer", return_value=fake):
            self.monitor.start_monitoring([self.tmp.name])
        self.assertFalse(self.monitor.is_monitoring)
        self.monitor.stop_monitoring()

    def test_disabled_in_config_leaves_monitoring_off(self):
        config = types.SimpleNamespace(
            MONITORING_ENABLED=False, MONITORING_DIRECTORIES=[]
        )
        with patch.object(im_mod, "Observer") as observer_cls:
            with patch.object(
                im_mod.ImageMonitor, "_gallery_config", return_value=config
            ):
                self.monitor.start_monitoring([self.tmp.name])
        observer_cls.assert_not_called()
        self.assertFalse(self.monitor.is_monitoring)

    def test_directories_failing_validation_are_not_watched(self):
        allowed = os.path.join(self.tmp.name, "allowed")
        os.mkdir(allowed)
        config = types.SimpleNamespace(
            MONITORING_ENABLED=True,
            MONITORING_DIRECTORIES=[self.tmp.name, allowed],
            validate_gallery_root=lambda p: (p == allowed, "outside ComfyUI"),
        )
        fake = FakeObserver()
        with patch.object(im_mod, "Observer", return_value=fake):
            with patch.object(
                im_mod.ImageMonitor, "_gallery_config", return_value=config
            ):
                self.monitor.start_monitoring()
        self.assertEqual([p for _, p, _ in fake.scheduled], [allowed])
        self.assertEqual(self.monitor.monitored_directories, [allowed])
        self.monitor.stop_monitoring()

    def test_explicit_directories_are_validated_too(self):
        config = types.SimpleNamespace(
            MONITORING_ENABLED=True,
            MONITORING_DIRECTORIES=[],
            validate_gallery_root=lambda p: (False, "outside ComfyUI"),
        )
        with patch.object(im_mod, "Observer") as observer_cls:
            with patch.object(
                im_mod.ImageMonitor, "_gallery_config", return_value=config
            ):
                self.monitor.start_monitoring([self.tmp.name])
        observer_cls.assert_not_called()
        self.assertFalse(self.monitor.is_monitoring)

    def test_configured_directories_are_used_when_none_are_given(self):
        config = types.SimpleNamespace(
            MONITORING_ENABLED=True, MONITORING_DIRECTORIES=[self.tmp.name]
        )
        fake = FakeObserver()
        with patch.object(im_mod, "Observer", return_value=fake):
            with patch.object(
                im_mod.ImageMonitor, "_gallery_config", return_value=config
            ):
                self.monitor.start_monitoring()
        self.assertEqual(self.monitor.monitored_directories, [self.tmp.name])
        self.monitor.stop_monitoring()

    def _start_with(self, fake):
        with patch.object(im_mod, "Observer", return_value=fake):
            self.monitor.start_monitoring([self.tmp.name])
        handler = self.monitor.handler
        real_close = handler.close

        def close(timeout=im_mod.WORKER_JOIN_SECONDS):
            fake.calls.append(("close",))
            return real_close(timeout)

        handler.close = close
        return handler

    def test_stop_monitoring_joins_observer_and_worker(self):
        fake = FakeObserver()
        handler = self._start_with(fake)

        self.assertTrue(self.monitor.stop_monitoring(timeout=3.0))

        self.assertEqual([c[0] for c in fake.calls], ["close", "stop", "join"])
        join_timeout = fake.calls[2][1]
        self.assertIsNotNone(join_timeout)
        self.assertLessEqual(join_timeout, 3.0)
        self.assertTrue(handler._closed)

    def test_stop_monitoring_reports_an_observer_still_running(self):
        fake = FakeObserver(stay_alive=True)
        self._start_with(fake)
        with self.assertLogs("prompt_manager.image_monitor", "WARNING") as logs:
            self.assertFalse(self.monitor.stop_monitoring(timeout=0.05))
        self.assertIn("watchdog observer still running", "\n".join(logs.output))
        self.assertFalse(self.monitor.is_monitoring)
        fake.stay_alive = False  # it ends; tearDown's stop waits for it

    def test_a_second_stop_monitoring_waits_for_the_observer(self):
        fake = FakeObserver(stay_alive=True)
        self._start_with(fake)
        with self.assertLogs("prompt_manager.image_monitor", "WARNING"):
            self.assertFalse(self.monitor.stop_monitoring(timeout=0.05))
            self.assertFalse(self.monitor.stop_monitoring(timeout=0.05))
        fake.stay_alive = False
        self.assertTrue(self.monitor.stop_monitoring())
        self.assertFalse(self.monitor._unfinished)

    def test_stop_monitoring_reports_an_observer_stop_that_raised(self):
        fake = FakeObserver(stop_error=OSError("descriptor gone"))
        self._start_with(fake)
        fake.stopped = True  # its thread has ended; stop() still raises
        with self.assertLogs("prompt_manager.image_monitor", "WARNING") as logs:
            self.assertFalse(self.monitor.stop_monitoring())
        text = "\n".join(logs.output)
        self.assertIn("Error while stopping image monitoring", text)
        self.assertNotIn("watchdog observer still running", text)
        fake.stop_error = None  # tearDown's stop calls stop() again

    def test_the_still_running_warning_names_the_time_the_join_got(self):
        fake = FakeObserver(stay_alive=True)
        handler = self._start_with(fake)
        now = [100.0]
        recorded_close = handler.close

        def close_that_uses_the_deadline(timeout=im_mod.WORKER_JOIN_SECONDS):
            ok = recorded_close(timeout)
            now[0] += 5.0  # a busy worker took the whole deadline
            return ok

        handler.close = close_that_uses_the_deadline
        with (
            patch.object(
                im_mod, "time", types.SimpleNamespace(monotonic=lambda: now[0])
            ),
            self.assertLogs("prompt_manager.image_monitor", "WARNING") as logs,
        ):
            self.assertFalse(self.monitor.stop_monitoring(timeout=5.0))
        self.assertEqual(fake.calls[-1], ("join", 0.0))
        self.assertIn(
            "watchdog observer still running after 0.0 s", "\n".join(logs.output)
        )
        fake.stay_alive = False  # it ends; tearDown's stop waits for it

    def test_stop_monitoring_without_an_observer_returns_true(self):
        self.assertTrue(self.monitor.stop_monitoring())


class FakeEvent:
    def __init__(self, path, is_directory=False):
        self.src_path = path
        self.is_directory = is_directory


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class TestHandlerScheduling(unittest.TestCase):
    """New images are processed once each, in order, after the file settles."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tracker = MagicMock()
        self.tracker.keyed_snapshot.return_value = {"key": ({"id": 7}, "run", 1)}
        self.handler = im_mod.ImageGenerationHandler(MagicMock(), self.tracker)
        self.handler.processing_delay = 0
        self.handler.settle_interval = 0.01
        self.processed = []
        self.handler.process_new_image = lambda path, prompt_snapshot=None: (
            self.processed.append((path, prompt_snapshot))
        )

    def tearDown(self):
        # Before the folder is removed (addCleanup runs after tearDown).
        self.assertTrue(self.handler.close())

    def _image(self, name, content=b"x" * 16):
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as f:
            f.write(content)
        return path

    def test_same_path_is_scheduled_once(self):
        path = self._image("a.png")
        self.assertTrue(self.handler.schedule(path, {"id": 1}))
        self.assertFalse(self.handler.schedule(path, {"id": 2}))
        self.assertTrue(wait_for(lambda: self.handler.pending_count() == 0))
        self.assertEqual(self.processed, [(path, {"id": 1})])

    def test_on_created_snapshots_the_prompt_and_skips_non_images(self):
        path = self._image("b.png")
        self.handler.on_created(FakeEvent(path))
        self.handler.on_created(FakeEvent(os.path.join(self.tmp.name, "c.txt")))
        self.handler.on_created(FakeEvent(self.tmp.name, is_directory=True))
        self.assertTrue(wait_for(lambda: self.handler.pending_count() == 0))
        self.assertEqual(self.processed, [(path, {"key": ({"id": 7}, "run", 1)})])

    def test_a_failed_snapshot_still_schedules_the_image(self):
        self.tracker.keyed_snapshot.side_effect = RuntimeError("tracker gone")
        path = self._image("d.png")
        self.handler.on_created(FakeEvent(path))
        self.assertTrue(wait_for(lambda: self.handler.pending_count() == 0))
        self.assertEqual(self.processed, [(path, None)])

    def test_images_are_processed_in_creation_order(self):
        paths = [self._image(f"{i}.png") for i in range(5)]
        for path in paths:
            self.handler.schedule(path)
        self.assertTrue(wait_for(lambda: self.handler.pending_count() == 0))
        self.assertEqual([p for p, _ in self.processed], paths)

    def test_pending_set_is_bounded(self):
        self.handler.processing_delay = 10  # keep the worker busy
        first = self._image("first.png")
        self.handler.schedule(first)
        with patch.object(im_mod, "MAX_PENDING_IMAGES", 2):
            self.assertTrue(self.handler.schedule(self._image("second.png")))
            self.assertFalse(self.handler.schedule(self._image("third.png")))
        self.assertEqual(self.handler.pending_count(), 2)

    def test_file_that_never_settles_is_skipped_and_cleared(self):
        path = self._image("d.png")
        sizes = iter(range(1, 100))
        with patch.object(im_mod.os.path, "getsize", side_effect=lambda p: next(sizes)):
            self.handler._process_pending(path)
        self.assertEqual(self.processed, [])
        self.assertEqual(self.handler.pending_count(), 0)

    def test_vanished_file_is_skipped(self):
        path = os.path.join(self.tmp.name, "gone.png")
        self.assertFalse(self.handler.wait_until_settled(path))
        self.handler._process_pending(path)
        self.assertEqual(self.processed, [])

    def test_settled_file_is_detected_after_two_stable_reads(self):
        path = self._image("e.png")
        self.assertTrue(self.handler.wait_until_settled(path))
        empty = self._image("empty.png", content=b"")
        self.assertFalse(self.handler.wait_until_settled(empty))

    def test_shutdown_joins_the_worker_within_the_bound(self):
        self.handler.processing_delay = 10  # keep the first image waiting
        self.handler.schedule(self._image("s1.png"))
        self.handler.schedule(self._image("s2.png"))
        worker = self.handler._worker
        self.assertTrue(worker.is_alive())

        self.assertTrue(self.handler.shutdown())

        self.assertFalse(worker.is_alive())
        self.assertEqual(self.handler.pending_count(), 0)

    def test_shutdown_reports_a_worker_still_busy(self):
        entered, release = threading.Event(), threading.Event()

        def busy(path, prompt_snapshot=None):
            entered.set()
            release.wait(30)

        self.handler.process_new_image = busy
        self.handler.schedule(self._image("busy.png"))
        worker = self.handler._worker
        self.addCleanup(worker.join, 30)
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(30), "the worker never took the image")

        with self.assertLogs("prompt_manager.image_monitor", "WARNING") as logs:
            self.assertFalse(self.handler.shutdown(timeout=0.05))
        self.assertIn("image worker still running", "\n".join(logs.output))

        release.set()
        worker.join(30)
        self.assertFalse(worker.is_alive())

    def test_a_second_stop_waits_for_a_worker_still_finishing(self):
        entered, release = threading.Event(), threading.Event()

        def busy(path, prompt_snapshot=None):
            entered.set()
            release.wait(30)

        self.handler.process_new_image = busy
        self.handler.schedule(self._image("twice.png"))
        worker = self.handler._worker
        self.addCleanup(worker.join, 30)
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(30), "the worker never took the image")

        with self.assertLogs("prompt_manager.image_monitor", "WARNING"):
            self.assertFalse(self.handler.close(timeout=0.05))
            self.assertFalse(self.handler.close(timeout=0.05))
        release.set()
        self.assertTrue(self.handler.close(timeout=30))
        self.assertFalse(worker.is_alive())

    def test_shutdown_on_the_worker_itself_only_signals(self):
        entered, result = threading.Event(), {}

        def stop_from_inside(path, prompt_snapshot=None):
            result["value"] = self.handler.shutdown()
            entered.set()

        self.handler.process_new_image = stop_from_inside
        self.handler.schedule(self._image("self.png"))
        worker = self.handler._worker
        self.assertTrue(entered.wait(30), "the worker never took the image")
        worker.join(30)
        self.assertFalse(result["value"])
        self.assertFalse(worker.is_alive())

    def _live_workers(self):
        return {
            t for t in threading.enumerate() if t.name == "PromptManagerImageWorker"
        }

    def _seal(self, handler, name):
        handler._await_history(os.path.join(self.tmp.name, name))
        handler._on_outputs_sealed(
            "run-1",
            [{"folder_type": "output", "subfolder": "", "filename": name}],
        )

    def test_close_refuses_new_work(self):
        path = self._image("late.png")
        with patch.object(im_mod, "host_output_directory", return_value=self.tmp.name):
            self.assertTrue(self.handler.close())
            before = self._live_workers()
            self.assertFalse(self.handler.schedule(path))
            # The image in hand can still ask for a history re-check and its
            # run can be sealed afterwards, on the history writer's thread.
            self.handler._await_history(path)
            self.assertEqual(self.handler.awaiting_history(), [])
            self._seal(self.handler, "late.png")
            self.handler._schedule_recheck(path)
        self.assertIsNone(self.handler._worker)
        self.assertEqual(list(self.handler._history_due), [])
        # #247: a foreign file's sealed re-check is dropped the same way
        self.handler._await_foreign(path)
        self.assertEqual(self.handler.awaiting_foreign(), [])
        self.handler._schedule_recheck(path, {"filename": "late.png"})
        self.assertEqual(list(self.handler._foreign_due), [])
        self.assertEqual(self._live_workers() - before, set())

    def test_a_sealed_run_starts_a_worker_on_an_open_handler(self):
        self._image("open.png")
        with patch.object(im_mod, "host_output_directory", return_value=self.tmp.name):
            self._seal(self.handler, "open.png")
        self.assertIsNotNone(self.handler._worker)
        self.assertTrue(self.handler.close())

    def test_close_is_idempotent(self):
        self.handler.schedule(self._image("c1.png"))
        self.assertTrue(self.handler.close())
        self.assertTrue(self.handler.close())

    def test_on_created_after_close_takes_no_lock(self):
        self.assertTrue(self.handler.close())
        self.handler.on_created(FakeEvent(self._image("after.png")))
        self.tracker.keyed_snapshot.assert_not_called()
        self.assertEqual(self.handler.pending_count(), 0)

    def test_shutdown_is_idempotent_and_safe_before_start(self):
        fresh = im_mod.ImageGenerationHandler(MagicMock(), self.tracker)
        self.assertTrue(fresh.shutdown())
        self.assertTrue(fresh.shutdown())
        self.assertTrue(fresh.close())
        self.handler.schedule(self._image("s3.png"))
        self.assertTrue(wait_for(lambda: self.handler.pending_count() == 0))
        self.handler.shutdown()
        self.handler.shutdown()

    def test_scheduling_after_shutdown_restarts_the_worker(self):
        self.handler.schedule(self._image("r1.png"))
        self.assertTrue(wait_for(lambda: self.handler.pending_count() == 0))
        old_worker = self.handler._worker
        self.handler.shutdown()
        old_worker.join(timeout=2)
        self.assertFalse(old_worker.is_alive())

        path = self._image("r2.png")
        self.assertTrue(self.handler.schedule(path))
        self.assertTrue(wait_for(lambda: self.handler.pending_count() == 0))
        self.assertIn(path, [p for p, _ in self.processed])
        self.assertIsNot(self.handler._worker, old_worker)
        self.assertTrue(self.handler.close())

    def test_processing_errors_do_not_kill_the_worker(self):
        def explode(path, prompt_snapshot=None):
            self.processed.append(path)
            raise RuntimeError("boom")

        self.handler.process_new_image = explode
        first, second = self._image("f1.png"), self._image("f2.png")
        self.handler.schedule(first)
        self.handler.schedule(second)
        self.assertTrue(wait_for(lambda: self.handler.pending_count() == 0))
        self.assertEqual(self.processed, [first, second])


class TestCloseReleasesHandles(unittest.TestCase):
    """``close`` joins the worker, so the test folder can be removed (#302)."""

    def test_close_releases_handles(self):
        from PIL import Image

        from database.operations import PromptDatabase
        from tests.open_handles import assert_closed
        from utils.prompt_tracker import PromptTracker

        with tempfile.TemporaryDirectory() as folder:
            db = PromptDatabase(os.path.join(folder, "prompts.db"))
            tracker = PromptTracker(db)
            handler = im_mod.ImageGenerationHandler(db, tracker)
            handler.processing_delay = 0
            handler.settle_interval = 0.01
            done = threading.Event()
            real_process = handler.process_new_image

            def process(path, prompt_snapshot=None):
                try:
                    real_process(path, prompt_snapshot=prompt_snapshot)
                finally:
                    done.set()

            handler.process_new_image = process
            path = os.path.join(folder, "real.png")
            Image.new("RGB", (8, 8), "red").save(path)
            try:
                self.assertTrue(handler.schedule(path))
                worker = handler._worker
                self.assertTrue(done.wait(30), "the worker never processed the image")
            finally:
                closed, shut = handler.close(), tracker.shutdown()
                db.model.close_all()
            self.assertTrue(closed)
            self.assertTrue(shut)
            self.assertFalse(worker.is_alive())
            assert_closed(self, folder)


class TestOutputListener(unittest.TestCase):
    """Every settled output is reported to the Images listing (#348, PR 2)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.handler = im_mod.ImageGenerationHandler(MagicMock(), MagicMock())
        self.addCleanup(lambda: self.assertTrue(self.handler.close()))
        self.handler.processing_delay = 0
        self.handler.settle_interval = 0.01
        self.handler.process_new_image = lambda path, prompt_snapshot=None: None
        self.reported = []
        self.handler.output_listener = self.reported.append

    def _image(self, name, content=b"x" * 16):
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as f:
            f.write(content)
        return path

    def test_a_settled_output_is_reported_once_with_its_resolved_path(self):
        path = self._image("a.png")
        self.handler._process_pending(path)
        self.assertEqual(self.reported, [os.path.realpath(path)])

    def test_a_symlinked_spelling_is_reported_resolved(self):
        real_dir = os.path.join(self.tmp.name, "real")
        os.makedirs(real_dir)
        link_dir = os.path.join(self.tmp.name, "link")
        try:
            os.symlink(real_dir, link_dir, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symlinks unavailable: {error}")
        with open(os.path.join(real_dir, "b.png"), "wb") as f:
            f.write(b"x" * 16)
        self.handler._process_pending(os.path.join(link_dir, "b.png"))
        self.assertEqual(
            self.reported, [os.path.realpath(os.path.join(real_dir, "b.png"))]
        )

    def test_it_is_reported_even_when_linking_raises(self):
        def explode(path, prompt_snapshot=None):
            raise RuntimeError("boom")

        self.handler.process_new_image = explode
        path = self._image("c.png")
        with self.assertRaises(RuntimeError):
            self.handler._process_pending(path)
        self.assertEqual(self.reported, [os.path.realpath(path)])
        self.assertEqual(self.handler.pending_count(), 0)

    def test_an_output_that_never_settles_is_not_reported(self):
        self.handler.settle_attempts = 3
        self.handler._process_pending(os.path.join(self.tmp.name, "gone.png"))
        empty = self._image("empty.png", content=b"")
        self.handler._process_pending(empty)
        self.assertEqual(self.reported, [])

    def test_a_video_is_reported_when_its_extension_is_monitored(self):
        self.handler.supported_extensions = (".png", ".mp4")
        path = self._image("clip.mp4")
        self.handler.on_created(FakeEvent(path))
        self.assertTrue(wait_for(lambda: self.reported != []))
        self.assertEqual(self.reported, [os.path.realpath(path)])

    def test_a_failing_listener_is_logged_and_the_worker_carries_on(self):
        def broken(_path):
            raise RuntimeError("listing gone")

        self.handler.output_listener = broken
        first = self._image("d.png")
        with self.assertLogs("prompt_manager.image_monitor", "WARNING") as logs:
            self.handler._process_pending(first)  # no exception
        self.assertIn("Images listing", "\n".join(logs.output))
        self.handler.output_listener = self.reported.append
        second = self._image("e.png")
        self.handler.schedule(second)
        self.assertTrue(wait_for(lambda: self.reported != []))
        self.assertEqual(self.reported, [os.path.realpath(second)])

    def test_no_listener_reports_nothing(self):
        self.handler.output_listener = None
        self.handler._process_pending(self._image("f.png"))  # no exception
        self.assertEqual(self.reported, [])

    def test_the_monitor_wires_running_and_later_handlers(self):
        monitor = im_mod.ImageMonitor(MagicMock(), MagicMock())
        listener = self.reported.append
        monitor.set_output_listener(listener)
        with (
            patch.object(im_mod, "Observer", FakeObserver),
            patch.object(im_mod.ImageMonitor, "_gallery_config", return_value=None),
        ):
            monitor.start_monitoring([self.tmp.name])
        try:
            self.assertIs(monitor.handler.output_listener, listener)
            monitor.set_output_listener(None)
            self.assertIsNone(monitor.output_listener)
            self.assertIsNone(monitor.handler.output_listener)
        finally:
            self.assertTrue(monitor.stop_monitoring())


class TestModuleIsolation(unittest.TestCase):
    """This module must not leave mocks behind for later test modules."""

    def test_comfyui_integration_is_not_replaced_by_a_mock(self):
        module = sys.modules.get("utils.comfyui_integration")
        self.assertFalse(
            isinstance(module, MagicMock),
            "utils.comfyui_integration was replaced by a MagicMock in sys.modules",
        )


class TestMonitorThreadSafety(unittest.TestCase):
    """Singleton creation must be thread-safe."""

    def setUp(self):
        im_mod._monitor_instance = None

    def tearDown(self):
        im_mod._monitor_instance = None

    def test_concurrent_get_image_monitor_returns_same_instance(self):
        """Multiple threads calling get_image_monitor must get the same instance."""
        results = []
        barrier = threading.Barrier(5)

        def get_monitor():
            barrier.wait()
            m = get_image_monitor(MagicMock(), MagicMock())
            results.append(id(m))

        threads = [threading.Thread(target=get_monitor) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(set(results)), 1, "All threads must get the same instance")


if __name__ == "__main__":
    unittest.main()
