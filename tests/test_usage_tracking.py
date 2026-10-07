"""Run counting and node roles: only positive PromptManager nodes count and link.

Runs are counted when a prompt is queued (ComfyUI's on-prompt hook), because
ComfyUI skips unchanged nodes, so node execution can't be relied on to count.
"""

import os
import sys
import tempfile
import unittest
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.operations import PromptDatabase
from prompt_manager_base import PromptManagerBase
from utils.hashing import generate_prompt_hash
from utils.prompt_tracker import PromptTracker
from utils.usage_tracking import PendingFirstUse, handle_queued_prompt

LOADER = {
    "class_type": "CheckpointLoaderSimple",
    "inputs": {"ckpt_name": "m.safetensors"},
}


def workflow(positive_text="a cat", negative_text="blurry"):
    """API graph with a positive (#134) and a negative (#176) PromptManager node."""
    return {
        "176": {"class_type": "PromptManager", "inputs": {"text": negative_text}},
        "134": {"class_type": "PromptManager", "inputs": {"text": positive_text}},
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "positive": ["134", 0],
                "negative": ["176", 0],
                "model": ["20", 0],
            },
        },
        "20": LOADER,
    }


class DbTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = PromptDatabase(self.path)
        self.pending = PendingFirstUse()

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(self.path + suffix):
                os.unlink(self.path + suffix)

    def _save(self, text):
        return self.db.save_prompt(text=text, prompt_hash=generate_prompt_hash(text))

    def _runs(self, prompt_id):
        return self.db.get_prompt_by_id(prompt_id)["run_count"]

    def _queue(self, graph):
        return handle_queued_prompt(
            {"prompt": graph, "client_id": "x"}, self.db, self.pending
        )


class TestQueueHook(DbTestCase):
    def test_counts_the_positive_prompt_only(self):
        positive, negative = self._save("a cat"), self._save("blurry")
        self._queue(workflow())
        self.assertEqual(self._runs(positive), 1)
        self.assertEqual(self._runs(negative), 0)

    def test_requeue_counts_again_even_when_comfyui_skips_the_cached_node(self):
        positive = self._save("a cat")
        self._queue(workflow())
        self._queue(workflow())  # no node execution in between
        self.assertEqual(self._runs(positive), 2)

    def test_new_prompt_is_counted_when_the_node_first_saves_it(self):
        self._queue(workflow(positive_text="a brand new prompt"))
        self.assertTrue(
            self.pending.consume(generate_prompt_hash("a brand new prompt"))
        )
        self.assertFalse(
            self.pending.consume(generate_prompt_hash("a brand new prompt"))
        )

    def test_same_text_in_two_positive_nodes_counts_once(self):
        positive = self._save("a cat")
        graph = workflow()
        graph["135"] = {"class_type": "PromptManager", "inputs": {"text": "a cat"}}
        graph["4"] = {
            "class_type": "ConditioningCombine",
            "inputs": {"conditioning_1": ["134", 0], "conditioning_2": ["135", 0]},
        }
        graph["5"]["inputs"]["positive"] = ["4", 0]
        self._queue(graph)
        self.assertEqual(self._runs(positive), 1)

    def test_text_from_a_known_string_node_is_counted_at_queue_time(self):
        positive = self._save("a cat")
        graph = workflow()
        graph["134"]["inputs"]["text"] = ["8", 0]
        graph["8"] = {"class_type": "PrimitiveString", "inputs": {"value": "a cat"}}
        self._queue(graph)
        self._queue(graph)  # cached re-run: the node won't execute
        self.assertEqual(self._runs(positive), 2)

    def test_new_prompt_queued_twice_before_first_run_counts_twice(self):
        self._queue(workflow(positive_text="queued twice"))
        self._queue(workflow(positive_text="queued twice"))
        self.assertEqual(self.pending.consume(generate_prompt_hash("queued twice")), 2)

    def test_linked_text_is_left_to_node_execution(self):
        positive = self._save("from a batch")
        graph = workflow()
        graph["134"]["inputs"]["text"] = ["7", 0]
        graph["7"] = {"class_type": "PromptSearchList", "inputs": {}}
        self._queue(graph)
        self.assertEqual(self._runs(positive), 0)

    def test_returns_the_request_unchanged_and_never_raises(self):
        request = {"prompt": workflow(), "client_id": "x", "extra_data": {}}
        self.assertIs(handle_queued_prompt(request, self.db, self.pending), request)
        for bad in (None, {}, {"prompt": "x"}, {"prompt": None}, []):
            self.assertIs(handle_queued_prompt(bad, self.db, self.pending), bad)
        broken_db = mock.Mock()
        broken_db.get_prompt_by_hash.side_effect = RuntimeError("db locked")
        self.assertIs(handle_queued_prompt(request, broken_db, self.pending), request)


class TestPendingFirstUse(unittest.TestCase):
    def test_entries_expire(self):
        pending = PendingFirstUse(ttl_seconds=10)
        with mock.patch("utils.usage_tracking.time.monotonic", return_value=100.0):
            pending.add("h")
        with mock.patch("utils.usage_tracking.time.monotonic", return_value=111.0):
            self.assertFalse(pending.consume("h"))

    def test_consume_returns_how_many_runs_were_pending(self):
        pending = PendingFirstUse()
        pending.add("h")
        pending.add("h")
        pending.add("h")
        self.assertEqual(pending.consume("h"), 3)
        self.assertEqual(pending.consume("h"), 0)

    def test_size_is_capped(self):
        pending = PendingFirstUse(max_entries=3)
        for h in ("a", "b", "c", "d"):
            pending.add(h)
        self.assertFalse(pending.consume("a"))  # oldest evicted
        self.assertTrue(pending.consume("d"))


class FakeTracker:
    def __init__(self):
        self.calls = []

    def set_current_prompt(self, prompt_text, additional_data=None, push_to_queue=True):
        self.calls.append({"text": prompt_text, "push_to_queue": push_to_queue})
        return "exec-1"


class TestNodeRoles(DbTestCase):
    def _node(self):
        node = PromptManagerBase.__new__(PromptManagerBase)
        node.db = self.db
        node.prompt_tracker = FakeTracker()
        node.logger = __import__("logging").getLogger("test.usage_tracking")
        node.pending_first_use = self.pending
        return node

    def _run(self, node, graph, unique_id, text):
        return node._track_prompt_execution(
            text=text,
            encoding_text=text,
            category=None,
            tags=None,
            additional_data={},
            prompt_graph=graph,
            unique_id=unique_id,
        )

    def test_negative_node_saves_but_never_becomes_the_current_prompt(self):
        node = self._node()
        prompt_id = self._run(node, workflow(), "176", "blurry")
        self.assertIsNotNone(self.db.get_prompt_by_id(prompt_id))
        self.assertEqual(node.prompt_tracker.calls, [])
        self.assertEqual(self._runs(prompt_id), 0)

    def test_positive_typed_text_is_current_without_queue_push(self):
        node = self._node()
        self._run(node, workflow(), "134", "a cat")
        self.assertEqual(
            node.prompt_tracker.calls, [{"text": "a cat", "push_to_queue": False}]
        )

    def test_first_save_after_queue_hook_counts_one_run(self):
        self._queue(workflow(positive_text="never seen before"))
        prompt_id = self._run(
            self._node(),
            workflow(positive_text="never seen before"),
            "134",
            "never seen before",
        )
        self.assertEqual(self._runs(prompt_id), 1)

    def test_new_prompt_queued_twice_counts_both_runs_on_first_save(self):
        graph = workflow(positive_text="double queued")
        self._queue(graph)
        self._queue(graph)
        prompt_id = self._run(self._node(), graph, "134", "double queued")
        self.assertEqual(self._runs(prompt_id), 2)

    def test_text_resolved_at_queue_time_is_not_counted_again_or_queued(self):
        graph = workflow()
        graph["134"]["inputs"]["text"] = ["8", 0]
        graph["8"] = {"class_type": "PrimitiveString", "inputs": {"value": "a cat"}}
        positive = self._save("a cat")
        self._queue(graph)
        node = self._node()
        self._run(node, graph, "134", "a cat")
        self.assertEqual(self._runs(positive), 1)
        self.assertEqual(
            node.prompt_tracker.calls, [{"text": "a cat", "push_to_queue": False}]
        )

    def test_resolution_mismatch_falls_back_to_counting_and_queueing(self):
        # Hook guessed "a cat" but the node received different text: trust the node
        graph = workflow()
        graph["134"]["inputs"]["text"] = ["8", 0]
        graph["8"] = {"class_type": "PrimitiveString", "inputs": {"value": "a cat"}}
        node = self._node()
        prompt_id = self._run(node, graph, "134", "actually a dog")
        self.assertEqual(self._runs(prompt_id), 1)
        self.assertEqual(
            node.prompt_tracker.calls,
            [{"text": "actually a dog", "push_to_queue": True}],
        )

    def test_existing_prompt_is_not_counted_twice_by_hook_and_node(self):
        positive = self._save("a cat")
        self._queue(workflow())
        self._run(self._node(), workflow(), "134", "a cat")
        self.assertEqual(self._runs(positive), 1)

    def test_positive_linked_text_pushes_queue_and_counts_each_item(self):
        graph = workflow()
        graph["134"]["inputs"]["text"] = ["7", 0]
        graph["7"] = {"class_type": "PromptSearchList", "inputs": {}}
        node = self._node()
        first = self._run(node, graph, "134", "batch item one")
        self._run(node, graph, "134", "batch item one")
        self.assertEqual(self._runs(first), 2)
        self.assertTrue(all(c["push_to_queue"] for c in node.prompt_tracker.calls))

    def test_without_graph_context_keeps_legacy_behaviour(self):
        node = self._node()
        self._run(node, None, None, "standalone")
        self.assertEqual(
            node.prompt_tracker.calls, [{"text": "standalone", "push_to_queue": True}]
        )


class TestTrackerQueueFlag(unittest.TestCase):
    def setUp(self):
        self.tracker = PromptTracker(mock.Mock())

    def test_push_to_queue_false_keeps_the_batch_queue_empty(self):
        self.tracker.set_current_prompt("a cat", {"prompt_id": 1}, push_to_queue=False)
        self.assertIsNone(self.tracker.pop_next_prompt())
        self.assertEqual(self.tracker.get_current_prompt()["id"], 1)

    def test_default_still_pushes(self):
        self.tracker.set_current_prompt("a cat", {"prompt_id": 1})
        self.assertEqual(self.tracker.pop_next_prompt()["id"], 1)


if __name__ == "__main__":
    unittest.main()
