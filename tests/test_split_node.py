"""Split changes presentation while inheriting Focused execution and storage."""

import logging
import unittest
from unittest import mock

from prompt_manager import PromptManagerFocused, PromptManagerSplit


class TestSplitNode(unittest.TestCase):
    def test_split_preserves_focused_schema_encoding_and_change_detection(self):
        self.assertTrue(issubclass(PromptManagerSplit, PromptManagerFocused))
        self.assertEqual(
            PromptManagerSplit.INPUT_TYPES(), PromptManagerFocused.INPUT_TYPES()
        )
        self.assertEqual(
            PromptManagerSplit.RETURN_TYPES, PromptManagerFocused.RETURN_TYPES
        )
        self.assertEqual(PromptManagerSplit.FUNCTION, PromptManagerFocused.FUNCTION)
        self.assertIs(
            PromptManagerSplit.encode_prompt, PromptManagerFocused.encode_prompt
        )
        self.assertEqual(
            PromptManagerSplit.IS_CHANGED(None, "cat", category="Animals"),
            PromptManagerFocused.IS_CHANGED(None, "cat", category="Animals"),
        )

    def test_split_encodes_composition_and_persists_main_text_with_metadata(self):
        node = PromptManagerSplit.__new__(PromptManagerSplit)
        node.logger = logging.getLogger("test.split")
        node.comfyui_integration = mock.Mock()
        node._inject_lora_trigger_words = lambda text: text
        node._track_prompt_execution = mock.Mock(return_value=42)
        clip = mock.Mock()
        graph = {"1": {"class_type": "PromptManagerSplit", "inputs": {"text": "cat"}}}
        conditioning, text = node.encode_prompt(
            clip,
            "cat",
            category="Animals",
            tags="studio",
            prepend_text="trigger",
            append_text="detailed",
            prompt=graph,
            unique_id="1",
        )
        self.assertEqual(text, "trigger cat detailed")
        clip.tokenize.assert_called_once_with(text)
        self.assertIs(conditioning, clip.encode_from_tokens_scheduled.return_value)
        tracked = node._track_prompt_execution.call_args.kwargs
        self.assertEqual(tracked["text"], "cat")
        self.assertEqual(tracked["category"], "Animals")
        self.assertIn("studio", tracked["tags"])
        self.assertEqual(tracked["prompt_graph"], graph)
        self.assertEqual(tracked["unique_id"], "1")
