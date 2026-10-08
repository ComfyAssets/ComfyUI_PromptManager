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
    """Records what the monitor does with the watchdog observer."""

    def __init__(self):
        self.scheduled = []
        self.started = False
        self.stopped = False
        self.joined = False

    def schedule(self, handler, path, recursive=False):
        self.scheduled.append((handler, path, recursive))

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def join(self, timeout=None):
        self.joined = True

    def is_alive(self):
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
        self.tracker.get_current_prompt.return_value = {"id": 7}
        self.handler = im_mod.ImageGenerationHandler(MagicMock(), self.tracker)
        self.handler.processing_delay = 0
        self.handler.settle_interval = 0.01
        self.processed = []
        self.handler.process_new_image = lambda path, prompt_snapshot=None: (
            self.processed.append((path, prompt_snapshot))
        )

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
        self.assertEqual(self.processed, [(path, {"id": 7})])

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
