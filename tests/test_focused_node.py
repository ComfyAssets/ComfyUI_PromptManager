"""Focused shares encoding and tracking while exposing composition sockets."""

import logging
import unittest
import unittest.mock as mock

from prompt_manager import PromptManager, PromptManagerFocused
from utils.prompt_graph import positive_prompt_nodes, run_prompt_nodes


class TestFocusedNode(unittest.TestCase):
    def test_focused_keeps_the_execution_contract_and_visible_string_inputs(self):
        self.assertTrue(issubclass(PromptManagerFocused, PromptManager))
        self.assertEqual(PromptManagerFocused.RETURN_TYPES, PromptManager.RETURN_TYPES)
        self.assertEqual(PromptManagerFocused.FUNCTION, PromptManager.FUNCTION)
        focused = PromptManagerFocused.INPUT_TYPES()
        for name in ("prepend_text", "append_text"):
            self.assertTrue(focused["optional"][name][1]["forceInput"])
            self.assertNotIn(
                "forceInput", PromptManager.INPUT_TYPES()["optional"][name][1]
            )
        self.assertEqual(focused["hidden"]["unique_id"], "UNIQUE_ID")

    def test_focused_encodes_composition_and_tracks_main_text_and_graph(self):
        node = PromptManagerFocused.__new__(PromptManagerFocused)
        node.logger = logging.getLogger("test.focused")
        node.comfyui_integration = mock.Mock()
        node._inject_lora_trigger_words = lambda text: text
        node._track_prompt_execution = mock.Mock(return_value=42)
        clip = mock.Mock()
        graph = {"1": {"class_type": "PromptManagerFocused", "inputs": {"text": "cat"}}}
        conditioning, text = node.encode_prompt(
            clip,
            "cat",
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
        self.assertEqual(tracked["prompt_graph"], graph)

    def test_focused_is_traced_as_positive_and_not_as_the_negative_run(self):
        graph = {
            "1": {"class_type": "PromptManagerFocused", "inputs": {"text": "cat"}},
            "2": {"class_type": "PromptManagerFocused", "inputs": {"text": "blur"}},
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["4", 0],
                    "positive": ["1", 0],
                    "negative": ["2", 0],
                },
            },
        }
        self.assertEqual(positive_prompt_nodes(graph), ["1"])
        self.assertEqual(run_prompt_nodes(graph), ["1"])
