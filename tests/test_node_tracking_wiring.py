"""Nodes pass ComfyUI's queued graph to role-aware tracking; the hook registers once."""

import os
import sys
import threading
import unittest
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prompt_manager import PromptManager
from prompt_manager_text import PromptManagerText
from utils import usage_tracking

GRAPH = {"134": {"class_type": "PromptManager", "inputs": {"text": "a cat"}}}


def bare(node_cls):
    """A node without its database, tracker or gallery side effects."""
    node = node_cls.__new__(node_cls)
    node.logger = __import__("logging").getLogger("test.wiring")
    node.comfyui_integration = mock.Mock()
    node._inject_lora_trigger_words = lambda text: text
    node._track_prompt_execution = mock.Mock(return_value=42)
    return node


class TestHiddenInputs(unittest.TestCase):
    def test_both_nodes_request_the_queued_graph_and_their_id(self):
        for node_cls in (PromptManager, PromptManagerText):
            hidden = node_cls.INPUT_TYPES().get("hidden", {})
            self.assertEqual(hidden.get("prompt"), "PROMPT", node_cls.__name__)
            self.assertEqual(hidden.get("unique_id"), "UNIQUE_ID", node_cls.__name__)


class TestNodesForwardGraph(unittest.TestCase):
    def test_prompt_manager_forwards_graph_and_texts(self):
        node = bare(PromptManager)
        clip = mock.Mock()
        node.encode_prompt(
            clip, "a cat", prepend_text="best,", prompt=GRAPH, unique_id="134"
        )

        kwargs = node._track_prompt_execution.call_args.kwargs
        self.assertEqual(kwargs["prompt_graph"], GRAPH)
        self.assertEqual(kwargs["unique_id"], "134")
        self.assertEqual(kwargs["text"], "a cat")
        self.assertEqual(kwargs["encoding_text"], "best, a cat")
        self.assertEqual(kwargs["additional_data"]["final_text"], "best, a cat")

    def test_prompt_manager_text_forwards_graph(self):
        node = bare(PromptManagerText)
        node.process_text("a castle", prompt=GRAPH, unique_id="7")

        kwargs = node._track_prompt_execution.call_args.kwargs
        self.assertEqual((kwargs["prompt_graph"], kwargs["unique_id"]), (GRAPH, "7"))
        self.assertEqual(kwargs["text"], "a castle")

    def test_nodes_still_work_without_graph_context(self):
        node = bare(PromptManager)
        node.encode_prompt(mock.Mock(), "a cat")
        kwargs = node._track_prompt_execution.call_args.kwargs
        self.assertIsNone(kwargs["prompt_graph"])
        self.assertIsNone(kwargs["unique_id"])


class TestQueueHookRegistration(unittest.TestCase):
    def test_registers_once_and_handler_passes_requests_through(self):
        server = mock.Mock()
        db = mock.Mock()
        db.get_prompt_by_hash.return_value = None
        with mock.patch.object(usage_tracking, "_hook_registered", threading.Event()):
            self.assertTrue(usage_tracking.register_queue_hook(server, lambda: db))
            self.assertFalse(usage_tracking.register_queue_hook(server, lambda: db))

        server.add_on_prompt_handler.assert_called_once()
        handler = server.add_on_prompt_handler.call_args.args[0]
        request = {"prompt": GRAPH}
        self.assertIs(handler(request), request)


if __name__ == "__main__":
    unittest.main()
