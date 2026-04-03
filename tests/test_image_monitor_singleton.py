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
import threading
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock ComfyUI server before importing anything that touches config
_mock_server = MagicMock()
_mock_server.PromptServer.instance.routes = MagicMock()
sys.modules["server"] = _mock_server

# Mock comfyui_integration to avoid import issues
sys.modules["utils.comfyui_integration"] = MagicMock()

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
        """Different db/tracker args on subsequent calls still return the same instance."""
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
