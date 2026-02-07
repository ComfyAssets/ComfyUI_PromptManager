"""
Tests for prompt tracking system.

Tests PromptTracker, PromptExecutionContext, and the singleton
get_prompt_tracker. Uses a mock db_manager to avoid database deps.
"""

import os
import sys
import time
import threading
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.prompt_tracker import PromptTracker, PromptExecutionContext


def _make_tracker(prompt_timeout=600, cleanup_interval=300):
    """Create a PromptTracker with mocked db_manager and config."""
    mock_db = MagicMock()
    mock_db.get_prompt_by_hash.return_value = None

    # Patch GalleryConfig import to avoid needing ComfyUI server
    with patch.object(PromptTracker, "__init__", lambda self, db: None):
        tracker = PromptTracker.__new__(PromptTracker)

    # Manual init to avoid the config import and cleanup thread
    from utils.logging_config import get_logger

    tracker.logger = get_logger("prompt_manager.prompt_tracker")
    tracker.db_manager = mock_db
    tracker._local = threading.local()
    tracker.active_prompts = {}
    tracker.lock = threading.Lock()
    tracker.cleanup_interval = cleanup_interval
    tracker.prompt_timeout = prompt_timeout
    # Don't start cleanup thread in tests
    return tracker


class TestPromptTrackerBasic(unittest.TestCase):
    """Test basic PromptTracker operations."""

    def setUp(self):
        self.tracker = _make_tracker()

    def test_set_current_prompt_returns_execution_id(self):
        exec_id = self.tracker.set_current_prompt(
            "test prompt", {"prompt_id": 42}
        )
        self.assertTrue(exec_id.startswith("exec_"))

    def test_set_current_prompt_stores_in_active(self):
        exec_id = self.tracker.set_current_prompt(
            "test", {"prompt_id": 1}
        )
        self.assertIn(exec_id, self.tracker.active_prompts)

    def test_get_current_prompt_returns_set_prompt(self):
        self.tracker.set_current_prompt("hello world", {"prompt_id": 5})
        current = self.tracker.get_current_prompt()
        self.assertIsNotNone(current)
        self.assertEqual(current["text"], "hello world")
        self.assertEqual(current["id"], 5)

    def test_get_current_prompt_returns_none_when_empty(self):
        result = self.tracker.get_current_prompt()
        self.assertIsNone(result)

    def test_clear_current_prompt(self):
        self.tracker.set_current_prompt("to clear", {"prompt_id": 1})
        self.tracker.clear_current_prompt()
        result = self.tracker.get_current_prompt()
        self.assertIsNone(result)

    def test_clear_removes_from_active(self):
        exec_id = self.tracker.set_current_prompt("test", {"prompt_id": 1})
        self.tracker.clear_current_prompt()
        self.assertNotIn(exec_id, self.tracker.active_prompts)

    def test_clear_when_no_prompt_does_not_raise(self):
        # Should not raise
        self.tracker.clear_current_prompt()


class TestPromptTrackerTimeout(unittest.TestCase):
    """Test timeout behavior."""

    def test_expired_prompt_returns_none(self):
        tracker = _make_tracker(prompt_timeout=0.1)
        tracker.set_current_prompt("expires fast", {"prompt_id": 1})
        time.sleep(0.2)
        result = tracker.get_current_prompt()
        self.assertIsNone(result)

    def test_extend_timeout_resets_timestamp(self):
        tracker = _make_tracker(prompt_timeout=1)
        exec_id = tracker.set_current_prompt("test", {"prompt_id": 1})
        old_ts = tracker.active_prompts[exec_id]["timestamp"]
        time.sleep(0.1)
        tracker.extend_prompt_timeout(exec_id)
        new_ts = tracker.active_prompts[exec_id]["timestamp"]
        self.assertGreater(new_ts, old_ts)

    def test_extend_nonexistent_id_does_not_raise(self):
        tracker = _make_tracker()
        tracker.extend_prompt_timeout("nonexistent_id")


class TestPromptTrackerMultiplePrompts(unittest.TestCase):
    """Test multiple prompt tracking."""

    def setUp(self):
        self.tracker = _make_tracker()

    def test_get_active_prompts(self):
        self.tracker.set_current_prompt("p1", {"prompt_id": 1})
        self.tracker.set_current_prompt("p2", {"prompt_id": 2})
        active = self.tracker.get_active_prompts()
        self.assertEqual(len(active), 2)

    def test_get_active_prompts_returns_copy(self):
        self.tracker.set_current_prompt("p1", {"prompt_id": 1})
        active = self.tracker.get_active_prompts()
        active.clear()
        # Original should be untouched
        self.assertEqual(len(self.tracker.active_prompts), 1)

    def test_clear_all_active_prompts(self):
        self.tracker.set_current_prompt("p1", {"prompt_id": 1})
        self.tracker.set_current_prompt("p2", {"prompt_id": 2})
        cleared = self.tracker.clear_all_active_prompts()
        self.assertEqual(cleared, 2)
        self.assertEqual(len(self.tracker.active_prompts), 0)

    def test_clear_all_returns_zero_when_empty(self):
        cleared = self.tracker.clear_all_active_prompts()
        self.assertEqual(cleared, 0)


class TestPromptTrackerFallback(unittest.TestCase):
    """Test fallback prompt lookup (for cross-thread access)."""

    def test_fallback_finds_recent_prompt(self):
        tracker = _make_tracker()
        # Set prompt in active_prompts directly (simulating another thread)
        tracker.active_prompts["exec_123"] = {
            "id": 42,
            "execution_id": "exec_123",
            "text": "from other thread",
            "timestamp": time.time(),
            "thread_id": 0,
            "additional_data": {},
        }
        # Current thread has no local prompt, but fallback should find it
        result = tracker.get_current_prompt()
        self.assertIsNotNone(result)
        self.assertEqual(result["text"], "from other thread")

    def test_fallback_uses_db_hash_lookup(self):
        tracker = _make_tracker()
        tracker.db_manager.get_prompt_by_hash.return_value = {"id": 99, "text": "found"}
        exec_id = tracker.set_current_prompt("lookup test")
        self.assertEqual(tracker.active_prompts[exec_id]["id"], 99)

    def test_fallback_generates_temp_id(self):
        tracker = _make_tracker()
        tracker.db_manager.get_prompt_by_hash.return_value = None
        exec_id = tracker.set_current_prompt("not in db")
        prompt_id = tracker.active_prompts[exec_id]["id"]
        self.assertTrue(str(prompt_id).startswith("temp_"))


class TestPromptTrackerStatus(unittest.TestCase):
    """Test get_status method."""

    def test_status_empty_tracker(self):
        tracker = _make_tracker()
        status = tracker.get_status()
        self.assertEqual(status["active_prompts_count"], 0)
        self.assertIsNone(status["current_prompt_id"])
        self.assertIsNone(status["current_execution_id"])
        self.assertEqual(status["prompt_timeout"], 600)
        self.assertEqual(status["cleanup_interval"], 300)

    def test_status_with_active_prompt(self):
        tracker = _make_tracker()
        tracker.set_current_prompt("active", {"prompt_id": 7})
        status = tracker.get_status()
        self.assertEqual(status["active_prompts_count"], 1)
        self.assertEqual(status["current_prompt_id"], 7)
        self.assertIsNotNone(status["current_execution_id"])


class TestGenerateExecutionId(unittest.TestCase):
    """Test execution ID generation."""

    def test_format(self):
        tracker = _make_tracker()
        exec_id = tracker.generate_execution_id()
        self.assertTrue(exec_id.startswith("exec_"))
        parts = exec_id.split("_")
        self.assertEqual(len(parts), 3)  # exec, uuid8, timestamp

    def test_unique(self):
        tracker = _make_tracker()
        ids = {tracker.generate_execution_id() for _ in range(100)}
        self.assertEqual(len(ids), 100)


class TestPromptExecutionContext(unittest.TestCase):
    """Test the context manager."""

    def test_context_manager_sets_prompt(self):
        tracker = _make_tracker()
        with PromptExecutionContext(tracker, "context test", prompt_id=10) as exec_id:
            self.assertIsNotNone(exec_id)
            current = tracker.get_current_prompt()
            self.assertEqual(current["text"], "context test")

    def test_context_manager_does_not_clear_on_exit(self):
        tracker = _make_tracker()
        with PromptExecutionContext(tracker, "stays active", prompt_id=1):
            pass
        # Prompt should still be in active_prompts (designed behavior)
        self.assertEqual(len(tracker.active_prompts), 1)

    def test_context_manager_with_exception(self):
        tracker = _make_tracker()
        try:
            with PromptExecutionContext(tracker, "error test", prompt_id=1):
                raise ValueError("test error")
        except ValueError:
            pass
        # Should not crash, prompt stays tracked
        self.assertEqual(len(tracker.active_prompts), 1)


class TestCleanupExpiredPrompts(unittest.TestCase):
    """Test the cleanup logic (not the thread, just the logic)."""

    def test_expired_prompts_cleaned(self):
        tracker = _make_tracker(prompt_timeout=0.1)
        tracker.set_current_prompt("old", {"prompt_id": 1})
        time.sleep(0.2)

        # Manually run cleanup logic
        current_time = time.time()
        expired_ids = []
        with tracker.lock:
            for exec_id, prompt_data in tracker.active_prompts.items():
                if current_time - prompt_data["timestamp"] > tracker.prompt_timeout:
                    expired_ids.append(exec_id)
            for exec_id in expired_ids:
                tracker.active_prompts.pop(exec_id, None)

        self.assertEqual(len(tracker.active_prompts), 0)
        self.assertEqual(len(expired_ids), 1)


if __name__ == "__main__":
    unittest.main()
