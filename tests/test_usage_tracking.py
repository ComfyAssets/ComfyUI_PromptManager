"""Run counting and node roles: only positive PromptManager nodes count and link.

Runs are resolved when a prompt is queued (ComfyUI's on-prompt hook, which runs
before validation) and counted when ComfyUI dequeues the prompt for execution,
because ComfyUI skips unchanged nodes, so node execution can't be relied on to
count, and a rejected or cancelled queue entry must not count either.
"""

import os
import sys
import tempfile
import threading
import unittest
import unittest.mock as mock
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.operations import PromptDatabase
from prompt_manager_base import PromptManagerBase
from utils.hashing import generate_prompt_hash
from utils.prompt_tracker import PromptTracker
from utils import usage_tracking
from utils.usage_tracking import (
    PendingFirstUse,
    QueuedRuns,
    _wrap_queue_get,
    count_queued_prompt,
    handle_queued_prompt,
    on_dequeue,
    register_queue_hook,
)

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


class FakePromptQueue:
    """Stand-in for execution.PromptQueue: get() pops preset results."""

    def __init__(self):
        self.results = []
        self.get_calls = []

    def get(self, timeout=None):
        self.get_calls.append(timeout)
        return self.results.pop(0) if self.results else None


class FakeServer:
    """Stand-in for PromptServer: on-prompt handlers plus an optional queue."""

    def __init__(self, with_queue=True):
        self.handlers = []
        if with_queue:
            self.prompt_queue = FakePromptQueue()

    def add_on_prompt_handler(self, handler):
        self.handlers.append(handler)

    def on_prompt(self, json_data):
        for handler in self.handlers:
            json_data = handler(json_data)
        return json_data


def queue_item(prompt_id, graph):
    """What PromptQueue.get returns for a prompt that passed validation."""
    return ((0, prompt_id, graph, {}, [], False), 1)


class DbTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = PromptDatabase(self.path)
        self.pending = PendingFirstUse()
        self.queued = QueuedRuns()

    def tearDown(self):
        close = getattr(self.db, "close", None)
        if callable(close):
            close()
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(self.path + suffix):
                os.unlink(self.path + suffix)

    def _save(self, text):
        return self.db.save_prompt(text=text, prompt_hash=generate_prompt_hash(text))

    def _runs(self, prompt_id):
        return self.db.get_prompt_by_id(prompt_id)["run_count"]

    def _queue(self, graph):
        """Queue a graph and let ComfyUI dequeue it for execution."""
        request = handle_queued_prompt({"prompt": graph, "client_id": "x"}, self.queued)
        on_dequeue(
            request.get("prompt_id", ""), self.queued, lambda: self.db, self.pending
        )
        return request


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

    def test_returns_the_request_and_never_raises(self):
        request = {"prompt": workflow(), "client_id": "x", "extra_data": {}}
        self.assertIs(handle_queued_prompt(request, self.queued), request)
        for bad in (None, {}, {"prompt": "x"}, {"prompt": None}, []):
            self.assertIs(handle_queued_prompt(bad, self.queued), bad)
        broken_db = mock.Mock()
        broken_db.get_prompt_by_hash.side_effect = RuntimeError("db locked")
        with self.assertLogs("prompt_manager.usage_tracking", level="WARNING"):
            on_dequeue(
                request["prompt_id"], self.queued, lambda: broken_db, self.pending
            )

    def test_graph_without_prompt_manager_nodes_is_not_stashed_or_tagged(self):
        request = {"prompt": {"20": LOADER}}
        self.assertIs(handle_queued_prompt(request, self.queued), request)
        self.assertNotIn("prompt_id", request)
        self.assertEqual(len(self.queued), 0)


class TestCountAtDequeue(DbTestCase):
    """End to end through the registered hook and the wrapped queue."""

    def setUp(self):
        super().setUp()
        self.server = FakeServer()
        self._register(self.server)

    def _register(self, server):
        with mock.patch.object(usage_tracking, "_hook_registered", threading.Event()):
            return register_queue_hook(
                server, lambda: self.db, pending=self.pending, queued=self.queued
            )

    def _submit(self, graph, prompt_id=None):
        request = {"prompt": graph, "client_id": "x"}
        if prompt_id is not None:
            request["prompt_id"] = prompt_id
        return self.server.on_prompt(request)

    def _dequeue(self, request):
        self.server.prompt_queue.results.append(
            queue_item(request["prompt_id"], request["prompt"])
        )
        return self.server.prompt_queue.get(timeout=1.0)

    def test_hook_adds_a_lowercase_uuid4_prompt_id_when_absent(self):
        request = self._submit(workflow())
        prompt_id = request["prompt_id"]
        self.assertEqual(str(uuid.UUID(prompt_id, version=4)), prompt_id)
        self.assertEqual(prompt_id, prompt_id.lower())

    def test_hook_keeps_an_existing_prompt_id(self):
        request = self._submit(workflow(), prompt_id="client-chosen")
        self.assertEqual(request["prompt_id"], "client-chosen")

    def test_hook_alone_does_not_count(self):
        positive = self._save("a cat")
        self._submit(workflow())
        self.assertEqual(self._runs(positive), 0)

    def test_dequeue_counts_exactly_once(self):
        positive = self._save("a cat")
        request = self._submit(workflow())
        result = self._dequeue(request)
        self.assertEqual(result[0][1], request["prompt_id"])
        self.assertEqual(self.server.prompt_queue.get_calls, [1.0])
        self.assertEqual(self._runs(positive), 1)
        self._dequeue(request)  # a second dequeue of the same id has nothing left
        self.assertEqual(self._runs(positive), 1)

    def test_two_queued_prompts_are_each_counted_once(self):
        positive = self._save("a cat")
        first = self._submit(workflow())
        second = self._submit(workflow())
        self.assertNotEqual(first["prompt_id"], second["prompt_id"])
        self._dequeue(first)
        self.assertEqual(self._runs(positive), 1)
        self._dequeue(second)
        self.assertEqual(self._runs(positive), 2)

    def test_rejected_prompt_is_never_counted(self):
        positive = self._save("a cat")
        self._submit(workflow())  # validation fails: never dequeued
        other = self._submit(workflow())
        self._dequeue(other)
        self.assertEqual(self._runs(positive), 1)

    def test_get_returning_none_is_a_noop(self):
        positive = self._save("a cat")
        self._submit(workflow())
        self.assertIsNone(self.server.prompt_queue.get(timeout=0.1))
        self.assertEqual(self._runs(positive), 0)

    def test_unknown_text_is_pending_only_after_dequeue(self):
        prompt_hash = generate_prompt_hash("never saved")
        request = self._submit(workflow(positive_text="never saved"))
        self.assertEqual(self.pending.consume(prompt_hash), 0)
        self._dequeue(request)
        self.assertEqual(self.pending.consume(prompt_hash), 1)

    def test_shorter_and_longer_queue_items_are_tolerated(self):
        positive = self._save("a cat")
        request = self._submit(workflow())
        self.server.prompt_queue.results.append(
            ((0, request["prompt_id"], request["prompt"], {}, []), 1)
        )
        self.server.prompt_queue.get()
        self.assertEqual(self._runs(positive), 1)
        request = self._submit(workflow())
        self.server.prompt_queue.results.append(
            ((0, request["prompt_id"], request["prompt"], {}, [], False, "x"), 2)
        )
        self.server.prompt_queue.get()
        self.assertEqual(self._runs(positive), 2)

    def test_malformed_queue_item_is_logged_and_returned(self):
        item = (("not", "a", "tuple"),)
        self.server.prompt_queue.results.append(item)
        self.server.prompt_queue.results.append("garbage")
        with self.assertLogs("prompt_manager.usage_tracking", level="WARNING"):
            self.assertEqual(self.server.prompt_queue.get(), item)
            self.assertEqual(self.server.prompt_queue.get(), "garbage")

    def test_db_factory_raising_at_dequeue_is_logged_not_raised(self):
        def broken():
            raise RuntimeError("PromptManager database unavailable")

        queued = QueuedRuns()
        server = FakeServer()
        with mock.patch.object(usage_tracking, "_hook_registered", threading.Event()):
            register_queue_hook(server, broken, pending=self.pending, queued=queued)
        request = server.on_prompt({"prompt": workflow()})
        server.prompt_queue.results.append(queue_item(request["prompt_id"], {}))
        with self.assertLogs("prompt_manager.usage_tracking", level="WARNING") as logs:
            self.assertIsNotNone(server.prompt_queue.get())
        self.assertIn("database unavailable", "\n".join(logs.output))

    def test_server_without_prompt_queue_counts_at_queue_time_and_warns_once(self):
        positive = self._save("a cat")
        server = FakeServer(with_queue=False)
        with self.assertLogs("prompt_manager.usage_tracking", level="WARNING") as logs:
            self.assertTrue(self._register(server))
        warnings = [line for line in logs.output if line.startswith("WARNING")]
        self.assertEqual(len(warnings), 1)
        server.on_prompt({"prompt": workflow()})
        self.assertEqual(self._runs(positive), 1)
        server.on_prompt({"prompt": workflow(positive_text="new text")})
        self.assertEqual(self.pending.consume(generate_prompt_hash("new text")), 1)

    def test_server_whose_queue_has_no_get_uses_legacy_counting(self):
        positive = self._save("a cat")
        server = FakeServer(with_queue=False)
        server.prompt_queue = object()
        with self.assertLogs("prompt_manager.usage_tracking", level="WARNING"):
            self._register(server)
        server.on_prompt({"prompt": workflow()})
        self.assertEqual(self._runs(positive), 1)

    def test_legacy_counting_calls_db_factory_inside_the_guard(self):
        def broken():
            raise RuntimeError("PromptManager database unavailable")

        request = {"prompt": workflow()}
        with self.assertLogs("prompt_manager.usage_tracking", level="WARNING"):
            self.assertIs(count_queued_prompt(request, broken, self.pending), request)

    def test_wrapping_twice_does_not_double_count(self):
        positive = self._save("a cat")
        prompt_queue = self.server.prompt_queue
        wrapped_get = prompt_queue.get
        calls = []
        self.assertFalse(_wrap_queue_get(prompt_queue, calls.append))
        self.assertIs(prompt_queue.get, wrapped_get)
        request = self._submit(workflow())
        self._dequeue(request)
        self.assertEqual(self._runs(positive), 1)
        self.assertEqual(calls, [])

    def test_wrap_rejects_queues_without_a_callable_get(self):
        self.assertFalse(_wrap_queue_get(object(), lambda prompt_id: None))
        self.assertFalse(_wrap_queue_get(None, lambda prompt_id: None))

    def test_wrapper_reports_the_dequeued_prompt_id(self):
        prompt_queue = FakePromptQueue()
        seen = []
        self.assertTrue(_wrap_queue_get(prompt_queue, seen.append))
        prompt_queue.results.append(queue_item("abc", {}))
        self.assertEqual(prompt_queue.get(timeout=2.5), queue_item("abc", {}))
        self.assertEqual(prompt_queue.get_calls, [2.5])
        self.assertEqual(seen, ["abc"])

    def test_dequeue_callback_errors_are_logged_not_raised(self):
        prompt_queue = FakePromptQueue()

        def explode(prompt_id):
            raise ValueError("boom")

        _wrap_queue_get(prompt_queue, explode)
        prompt_queue.results.append(queue_item("abc", {}))
        with self.assertLogs("prompt_manager.usage_tracking", level="WARNING"):
            self.assertIsNotNone(prompt_queue.get())


class TestQueuedRuns(unittest.TestCase):
    def test_pop_returns_runs_once(self):
        queued = QueuedRuns()
        queued.put("p1", [("h1", "text one")])
        self.assertEqual(queued.pop("p1"), [("h1", "text one")])
        self.assertEqual(queued.pop("p1"), [])
        self.assertEqual(queued.pop("missing"), [])

    def test_bound_evicts_oldest(self):
        queued = QueuedRuns(max_entries=3)
        for i in range(4):
            queued.put(f"p{i}", [(f"h{i}", f"text {i}")])
        self.assertEqual(len(queued), 3)
        self.assertEqual(queued.pop("p0"), [])
        self.assertEqual(queued.pop("p3"), [("h3", "text 3")])

    def test_default_bound_is_one_thousand(self):
        queued = QueuedRuns()
        for i in range(1001):
            queued.put(str(i), [("h", "t")])
        self.assertEqual(len(queued), 1000)
        self.assertEqual(queued.pop("0"), [])
        self.assertEqual(queued.pop("1000"), [("h", "t")])

    def test_requeue_of_the_same_id_replaces_the_runs(self):
        queued = QueuedRuns()
        queued.put("p1", [("h1", "one")])
        queued.put("p1", [("h2", "two")])
        self.assertEqual(queued.pop("p1"), [("h2", "two")])

    def test_empty_runs_are_not_stored(self):
        queued = QueuedRuns()
        queued.put("p1", [])
        self.assertEqual(len(queued), 0)


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
