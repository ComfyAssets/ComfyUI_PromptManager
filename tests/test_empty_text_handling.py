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
        """Empty main text with prepend/append should produce single space, not double."""
        node = self._make_node(mock_db, mock_integ, mock_tracker, mock_monitor)
        result = node.process_text(text="", prepend_text="before", append_text="after")
        self.assertIsInstance(result, tuple)
        self.assertEqual(result[0], "before after")
        self.assertNotIn("  ", result[0])

    def test_valid_text_still_works(
        self, mock_db, mock_integ, mock_tracker, mock_monitor
    ):
        """Normal text input should continue to work correctly."""
        node = self._make_node(mock_db, mock_integ, mock_tracker, mock_monitor)
        result = node.process_text(text="a beautiful sunset")
        self.assertIsInstance(result, tuple)
        self.assertEqual(result[0], "a beautiful sunset")

    def test_prepend_text_append_all_present(
        self, mock_db, mock_integ, mock_tracker, mock_monitor
    ):
        """All three parts should combine with single spaces."""
        node = self._make_node(mock_db, mock_integ, mock_tracker, mock_monitor)
        result = node.process_text(
            text="main prompt", prepend_text="before", append_text="after"
        )
        self.assertEqual(result[0], "before main prompt after")

    def test_only_prepend(self, mock_db, mock_integ, mock_tracker, mock_monitor):
        """Prepend with empty text should output just prepend."""
        node = self._make_node(mock_db, mock_integ, mock_tracker, mock_monitor)
        result = node.process_text(text="", prepend_text="before")
        self.assertEqual(result[0], "before")


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
        """Empty main text with prepend/append should produce single space, not double."""
        node, clip = self._make_node_and_clip(
            mock_db, mock_integ, mock_tracker, mock_monitor
        )
        result = node.encode_prompt(
            clip=clip, text="", prepend_text="before", append_text="after"
        )
        self.assertIsInstance(result, tuple)
        self.assertEqual(result[1], "before after")
        self.assertNotIn("  ", result[1])

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

    def test_prepend_text_append_all_present(
        self, mock_db, mock_integ, mock_tracker, mock_monitor
    ):
        """All three parts should combine with single spaces."""
        node, clip = self._make_node_and_clip(
            mock_db, mock_integ, mock_tracker, mock_monitor
        )
        result = node.encode_prompt(
            clip=clip, text="main prompt", prepend_text="before", append_text="after"
        )
        self.assertEqual(result[1], "before main prompt after")


@patch("prompt_manager_base.get_image_monitor")
@patch("prompt_manager_base.get_prompt_tracker")
@patch("prompt_manager_base.get_comfyui_integration")
@patch("prompt_manager_base.PromptDatabase")
class TestConnectedTextInput(unittest.TestCase):
    """Test that externally connected text is preserved in output (issue #123).

    When text comes from a connected node (not the widget), the Python side
    must include it in the final output. The JS fix ensures the connected
    value reaches Python; these tests verify Python handles it correctly.
    """

    def _make_text_node(self, mock_db_class, mock_integ, mock_tracker, mock_monitor):
        mock_db = Mock()
        mock_db_class.return_value = mock_db
        mock_db.save_prompt.return_value = 1
        mock_db.get_prompt_by_hash.return_value = None
        from prompt_manager_text import PromptManagerText

        return PromptManagerText()

    def _make_clip_node(self, mock_db_class, mock_integ, mock_tracker, mock_monitor):
        mock_db = Mock()
        mock_db_class.return_value = mock_db
        mock_db.save_prompt.return_value = 1
        mock_db.get_prompt_by_hash.return_value = None
        mock_clip = Mock()
        mock_clip.tokenize.return_value = "mock_tokens"
        mock_clip.encode_from_tokens_scheduled.return_value = "mock_cond"
        from prompt_manager import PromptManager

        return PromptManager(), mock_clip

    def test_text_node_connected_text_with_prepend_append(
        self, mock_db, mock_integ, mock_tracker, mock_monitor
    ):
        """Simulates screenshot: connected text + prepend + append must all appear."""
        node = self._make_text_node(mock_db, mock_integ, mock_tracker, mock_monitor)
        result = node.process_text(
            text="This is the prompt",
            prepend_text="This is prepend text",
            append_text="This is append text",
        )
        self.assertEqual(
            result[0],
            "This is prepend text This is the prompt This is append text",
        )

    def test_text_node_connected_text_only(
        self, mock_db, mock_integ, mock_tracker, mock_monitor
    ):
        """Connected text without prepend/append passes through unchanged."""
        node = self._make_text_node(mock_db, mock_integ, mock_tracker, mock_monitor)
        result = node.process_text(text="This is the prompt")
        self.assertEqual(result[0], "This is the prompt")

    def test_clip_node_connected_text_with_prepend_append(
        self, mock_db, mock_integ, mock_tracker, mock_monitor
    ):
        """Same scenario for PromptManager (CLIP variant)."""
        node, clip = self._make_clip_node(
            mock_db, mock_integ, mock_tracker, mock_monitor
        )
        result = node.encode_prompt(
            clip=clip,
            text="This is the prompt",
            prepend_text="This is prepend text",
            append_text="This is append text",
        )
        self.assertEqual(
            result[1],
            "This is prepend text This is the prompt This is append text",
        )

    def test_clip_node_connected_text_only(
        self, mock_db, mock_integ, mock_tracker, mock_monitor
    ):
        """Connected text without prepend/append passes through unchanged."""
        node, clip = self._make_clip_node(
            mock_db, mock_integ, mock_tracker, mock_monitor
        )
        result = node.encode_prompt(clip=clip, text="This is the prompt")
        self.assertEqual(result[1], "This is the prompt")


if __name__ == "__main__":
    unittest.main()
