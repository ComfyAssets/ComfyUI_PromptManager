"""
Tests for issue #123: UnboundLocalError when Text input is empty.

Verifies that PromptManagerText.process_text() and PromptManager.encode_prompt()
handle empty/blank text inputs without raising UnboundLocalError on extended_tags.
"""

import os
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@patch("prompt_manager_base.get_image_monitor")
@patch("prompt_manager_base.get_prompt_tracker")
@patch("prompt_manager_base.get_comfyui_integration")
@patch("prompt_manager_base.PromptDatabase")
class TestPromptManagerTextEmptyInput(unittest.TestCase):
    """Test PromptManagerText with empty/blank text inputs (issue #123)."""

    def _make_node(self, mock_db_class, mock_integration, mock_tracker, mock_monitor):
        mock_db = Mock()
        mock_db_class.return_value = mock_db
        mock_db.save_prompt.return_value = 1
        mock_db.get_prompt_by_hash.return_value = None

        from prompt_manager_text import PromptManagerText

        return PromptManagerText()

    def test_empty_string(self, mock_db, mock_integ, mock_tracker, mock_monitor):
        """Empty string should not raise UnboundLocalError."""
        node = self._make_node(mock_db, mock_integ, mock_tracker, mock_monitor)
        result = node.process_text(text="")
        self.assertIsInstance(result, tuple)
        self.assertEqual(result[0], "")

    def test_whitespace_only(self, mock_db, mock_integ, mock_tracker, mock_monitor):
        """Whitespace-only string should not raise UnboundLocalError."""
        node = self._make_node(mock_db, mock_integ, mock_tracker, mock_monitor)
        result = node.process_text(text="   ")
        self.assertIsInstance(result, tuple)

    def test_none_coerced_empty(self, mock_db, mock_integ, mock_tracker, mock_monitor):
        """Falsy text value should not raise UnboundLocalError."""
        node = self._make_node(mock_db, mock_integ, mock_tracker, mock_monitor)
        # ComfyUI may pass empty string for unconnected inputs
        result = node.process_text(text="", category="", tags="")
        self.assertIsInstance(result, tuple)

    def test_empty_text_with_prepend_append(
        self, mock_db, mock_integ, mock_tracker, mock_monitor
    ):
        """Empty main text with prepend/append should still work."""
        node = self._make_node(mock_db, mock_integ, mock_tracker, mock_monitor)
        result = node.process_text(text="", prepend_text="before", append_text="after")
        self.assertIsInstance(result, tuple)
        self.assertIn("before", result[0])
        self.assertIn("after", result[0])

    def test_valid_text_still_works(
        self, mock_db, mock_integ, mock_tracker, mock_monitor
    ):
        """Normal text input should continue to work correctly."""
        node = self._make_node(mock_db, mock_integ, mock_tracker, mock_monitor)
        result = node.process_text(text="a beautiful sunset")
        self.assertIsInstance(result, tuple)
        self.assertEqual(result[0], "a beautiful sunset")


@patch("prompt_manager_base.get_image_monitor")
@patch("prompt_manager_base.get_prompt_tracker")
@patch("prompt_manager_base.get_comfyui_integration")
@patch("prompt_manager_base.PromptDatabase")
class TestPromptManagerEmptyInput(unittest.TestCase):
    """Test PromptManager (CLIP variant) with empty/blank text inputs (issue #123)."""

    def _make_node_and_clip(
        self, mock_db_class, mock_integration, mock_tracker, mock_monitor
    ):
        mock_db = Mock()
        mock_db_class.return_value = mock_db
        mock_db.save_prompt.return_value = 1
        mock_db.get_prompt_by_hash.return_value = None

        mock_clip = Mock()
        mock_clip.tokenize.return_value = "mock_tokens"
        mock_clip.encode_from_tokens_scheduled.return_value = "mock_cond"

        from prompt_manager import PromptManager

        return PromptManager(), mock_clip

    def test_empty_string(self, mock_db, mock_integ, mock_tracker, mock_monitor):
        """Empty string should not raise UnboundLocalError."""
        node, clip = self._make_node_and_clip(
            mock_db, mock_integ, mock_tracker, mock_monitor
        )
        result = node.encode_prompt(clip=clip, text="")
        self.assertIsInstance(result, tuple)

    def test_whitespace_only(self, mock_db, mock_integ, mock_tracker, mock_monitor):
        """Whitespace-only string should not raise UnboundLocalError."""
        node, clip = self._make_node_and_clip(
            mock_db, mock_integ, mock_tracker, mock_monitor
        )
        result = node.encode_prompt(clip=clip, text="   ")
        self.assertIsInstance(result, tuple)

    def test_empty_text_with_prepend_append(
        self, mock_db, mock_integ, mock_tracker, mock_monitor
    ):
        """Empty main text with prepend/append should still work."""
        node, clip = self._make_node_and_clip(
            mock_db, mock_integ, mock_tracker, mock_monitor
        )
        result = node.encode_prompt(
            clip=clip, text="", prepend_text="before", append_text="after"
        )
        self.assertIsInstance(result, tuple)
        self.assertIn("before", result[1])
        self.assertIn("after", result[1])

    def test_valid_text_still_works(
        self, mock_db, mock_integ, mock_tracker, mock_monitor
    ):
        """Normal text input should continue to work correctly."""
        node, clip = self._make_node_and_clip(
            mock_db, mock_integ, mock_tracker, mock_monitor
        )
        result = node.encode_prompt(clip=clip, text="a beautiful sunset")
        self.assertIsInstance(result, tuple)
        self.assertEqual(result[1], "a beautiful sunset")


if __name__ == "__main__":
    unittest.main()
