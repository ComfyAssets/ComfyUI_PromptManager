"""
Tests for ComfyUI node execution contract.

Ensures all nodes are properly configured for ComfyUI's execution engine:
- OUTPUT_NODE = True so nodes are always included in the execution graph
- IS_CHANGED returns correct values for cache invalidation

Regression tests for: https://github.com/ComfyAssets/ComfyUI_PromptManager/issues/120
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prompt_manager import PromptManager
from prompt_manager_text import PromptManagerText
from prompt_search_list import PromptSearchList

ALL_NODE_CLASSES = [PromptManager, PromptManagerText, PromptSearchList]


class TestOutputNode(unittest.TestCase):
    """OUTPUT_NODE = True is required so ComfyUI always includes these nodes
    in the execution graph. Without it, nodes with side effects (database
    writes, execution tracking) can be silently skipped when downstream
    nodes are cached."""

    def test_prompt_manager_is_output_node(self):
        self.assertIs(PromptManager.OUTPUT_NODE, True)

    def test_prompt_manager_text_is_output_node(self):
        self.assertIs(PromptManagerText.OUTPUT_NODE, True)

    def test_prompt_search_list_is_output_node(self):
        self.assertIs(PromptSearchList.OUTPUT_NODE, True)


class TestIsChangedPromptManager(unittest.TestCase):
    """IS_CHANGED for PromptManager should return a deterministic hash
    based on text inputs, so the node re-runs only when inputs change."""

    def test_same_inputs_return_same_hash(self):
        result1 = PromptManager.IS_CHANGED(
            clip=None, text="hello", prepend_text="pre", append_text="post"
        )
        result2 = PromptManager.IS_CHANGED(
            clip=None, text="hello", prepend_text="pre", append_text="post"
        )
        self.assertEqual(result1, result2)

    def test_different_text_returns_different_hash(self):
        result1 = PromptManager.IS_CHANGED(clip=None, text="hello")
        result2 = PromptManager.IS_CHANGED(clip=None, text="world")
        self.assertNotEqual(result1, result2)

    def test_different_prepend_returns_different_hash(self):
        result1 = PromptManager.IS_CHANGED(clip=None, text="hello", prepend_text="a")
        result2 = PromptManager.IS_CHANGED(clip=None, text="hello", prepend_text="b")
        self.assertNotEqual(result1, result2)

    def test_different_append_returns_different_hash(self):
        result1 = PromptManager.IS_CHANGED(clip=None, text="hello", append_text="a")
        result2 = PromptManager.IS_CHANGED(clip=None, text="hello", append_text="b")
        self.assertNotEqual(result1, result2)

    def test_returns_string(self):
        result = PromptManager.IS_CHANGED(clip=None, text="hello")
        self.assertIsInstance(result, str)


class TestIsChangedPromptManagerText(unittest.TestCase):
    """IS_CHANGED for PromptManagerText should behave identically to
    PromptManager — deterministic hash based on text inputs."""

    def test_same_inputs_return_same_hash(self):
        result1 = PromptManagerText.IS_CHANGED(
            text="hello", prepend_text="pre", append_text="post"
        )
        result2 = PromptManagerText.IS_CHANGED(
            text="hello", prepend_text="pre", append_text="post"
        )
        self.assertEqual(result1, result2)

    def test_different_text_returns_different_hash(self):
        result1 = PromptManagerText.IS_CHANGED(text="hello")
        result2 = PromptManagerText.IS_CHANGED(text="world")
        self.assertNotEqual(result1, result2)

    def test_different_prepend_returns_different_hash(self):
        result1 = PromptManagerText.IS_CHANGED(text="hello", prepend_text="a")
        result2 = PromptManagerText.IS_CHANGED(text="hello", prepend_text="b")
        self.assertNotEqual(result1, result2)

    def test_different_append_returns_different_hash(self):
        result1 = PromptManagerText.IS_CHANGED(text="hello", append_text="a")
        result2 = PromptManagerText.IS_CHANGED(text="hello", append_text="b")
        self.assertNotEqual(result1, result2)

    def test_returns_string(self):
        result = PromptManagerText.IS_CHANGED(text="hello")
        self.assertIsInstance(result, str)


class TestIsChangedPromptSearchList(unittest.TestCase):
    """IS_CHANGED for PromptSearchList should always return a unique value
    so the node re-runs every time (database contents may have changed)."""

    def test_returns_different_value_on_successive_calls(self):
        result1 = PromptSearchList.IS_CHANGED()
        time.sleep(0.01)
        result2 = PromptSearchList.IS_CHANGED()
        self.assertNotEqual(result1, result2)

    def test_returns_numeric(self):
        result = PromptSearchList.IS_CHANGED()
        self.assertIsInstance(result, float)


if __name__ == "__main__":
    unittest.main()
