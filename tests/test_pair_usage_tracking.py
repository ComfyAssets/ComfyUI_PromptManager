"""Paired usage is counted at dequeue, including cached node executions."""

import logging
import os
import tempfile
import unittest
from unittest.mock import Mock

try:
    from tests.open_handles import assert_closed
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed

from database.operations import PromptDatabase
from utils import usage_tracking
from utils.prompt_graph import run_prompt_nodes
from utils.usage_tracking import (
    PendingFirstUse,
    QueuedRuns,
    handle_queued_prompt,
    on_dequeue,
)

try:
    from tests.test_prompt_pair_node import pair_graph
except ImportError:
    from test_prompt_pair_node import pair_graph


class TestPairUsageTracking(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(assert_closed, self, self.tmp.name)
        self.db = PromptDatabase(os.path.join(self.tmp.name, "pairs.db"))
        self.addCleanup(self.db.close_all)
        self.queued = QueuedRuns()
        self.pair_queued = QueuedRuns()
        self.pending = PendingFirstUse()
        self.pair_pending = PendingFirstUse()

    def submit(self, graph):
        return handle_queued_prompt(
            {"prompt": graph}, self.queued, pair_queued=self.pair_queued
        )

    def dequeue(self, request):
        on_dequeue(
            request["prompt_id"],
            self.queued,
            lambda: self.db,
            self.pending,
            pair_queued=self.pair_queued,
            pair_pending=self.pair_pending,
        )

    def node(self):
        from prompt_manager_pair import PromptManagerPair

        node = PromptManagerPair.__new__(PromptManagerPair)
        node.db = self.db
        node.logger = logging.getLogger("test.pair_usage")
        node.pending_first_use = self.pending
        node.pending_pair_first_use = self.pair_pending
        node._inject_lora_trigger_words = lambda text: text
        node.prompt_tracker = Mock()
        node.comfyui_integration = Mock()
        return node

    def execute(self, graph, text="cat", negative="blur"):
        return self.node().encode_prompt(
            Mock(), text, negative_text=negative, prompt=graph, unique_id="1"
        )

    def test_existing_pair_and_positive_count_on_every_dequeue_without_encoding(self):
        pid = self.db.save_prompt_pair("cat", "blur")
        for _ in range(3):
            self.dequeue(self.submit(pair_graph()))
        pair = self.db.get_prompt_pair(pid)
        self.assertEqual(pair["run_count"], 3)
        self.assertEqual(
            self.db.get_prompt_by_id(pair["positive_prompt_id"])["run_count"], 3
        )
        self.assertEqual(
            self.db.get_prompt_by_id(pair["negative_prompt_id"])["run_count"], 0
        )

    def test_execution_records_a_cached_run_receipt_for_both_editors(self):
        from utils.cached_runs import Facts

        node = self.node()
        node.cached_runs = Mock()
        node.cached_runs.replayed.return_value = False
        node.encode_prompt(
            Mock(),
            "cat",
            negative_text="",
            prompt=pair_graph(negative=""),
            unique_id="1",
        )
        node.cached_runs.record_item.assert_called_once_with(
            "1", ("cat", ""), Facts(usage_tracking.generate_prompt_hash("cat"), None)
        )

    def test_validation_rejection_never_counts_or_saves(self):
        self.submit(pair_graph())
        self.assertEqual(self.db.list_prompt_pairs()["total"], 0)
        self.assertEqual(self.db.search_prompts(), [])

    def test_new_pair_pending_uses_are_consumed_once_when_first_encoding_saves(self):
        graph = pair_graph()
        self.dequeue(self.submit(graph))
        self.dequeue(self.submit(graph))
        self.execute(graph)
        pair = self.db.list_prompt_pairs()["pairs"][0]
        self.assertEqual(pair["run_count"], 2)
        self.assertEqual(
            self.db.get_prompt_by_id(pair["positive_prompt_id"])["run_count"], 2
        )
        self.execute(graph)
        self.assertEqual(self.db.get_prompt_pair(pair["id"])["run_count"], 2)

    def test_negative_only_changes_create_distinct_pair_counts_and_positive_counts(
        self,
    ):
        self.db.save_prompt_pair("cat", "blur")
        self.db.save_prompt_pair("cat", "watermark")
        self.dequeue(self.submit(pair_graph()))
        self.dequeue(self.submit(pair_graph(negative="watermark")))
        self.assertEqual(
            [p["run_count"] for p in self.db.list_prompt_pairs()["pairs"]], [1, 1]
        )
        positive = self.db.get_prompt_by_hash(
            usage_tracking.generate_prompt_hash("cat")
        )
        self.assertEqual(positive["run_count"], 2)

    def test_negative_output_slots_are_not_positive_prompt_sources(self):
        for slot in (1, 3):
            with self.subTest(slot=slot):
                self.assertEqual(
                    run_prompt_nodes(pair_graph(positive_slot=slot, negative_slot=0)),
                    [],
                )

    def test_mismatched_negative_does_not_count_unused_intrinsic_pair(self):
        pid = self.db.save_prompt_pair("cat", "blur")
        graph = pair_graph()
        graph["2"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "another negative"},
        }
        graph["5"]["inputs"]["negative"] = ["2", 0]
        self.dequeue(self.submit(graph))
        self.execute(graph)
        self.assertEqual(self.db.get_prompt_pair(pid)["run_count"], 0)
        self.assertEqual(
            self.db.get_prompt_by_hash(usage_tracking.generate_prompt_hash("cat"))[
                "run_count"
            ],
            1,
        )

    def test_unknown_batch_text_counts_each_execution_once_and_tracks_fifo(self):
        graph = pair_graph(text=["2", 0])
        graph["2"] = {"class_type": "PromptSearchList", "inputs": {}}
        self.submit(graph)
        node = self.node()
        node.encode_prompt(
            Mock(), "batch cat", negative_text="blur", prompt=graph, unique_id="1"
        )
        pair = self.db.list_prompt_pairs()["pairs"][0]
        self.assertEqual(pair["run_count"], 1)
        self.assertEqual(
            self.db.get_prompt_by_id(pair["positive_prompt_id"])["run_count"], 1
        )
        self.assertTrue(
            node.prompt_tracker.set_current_prompt.call_args.kwargs["push_to_queue"]
        )

    def test_standalone_pair_counts_cached_dequeues(self):
        pid = self.db.save_prompt_pair("cat", "")
        graph = {
            "1": {
                "class_type": "PromptManagerPair",
                "inputs": {"text": "cat", "negative_text": ""},
            }
        }
        self.dequeue(self.submit(graph))
        self.dequeue(self.submit(graph))
        self.assertEqual(self.db.get_prompt_pair(pid)["run_count"], 2)

    def test_paired_string_outputs_through_encoders_keep_roles(self):
        from utils.prompt_graph import intrinsic_pair_nodes

        graph = pair_graph()
        graph["2"] = {"class_type": "CLIPTextEncode", "inputs": {"text": ["1", 2]}}
        graph["3"] = {"class_type": "CLIPTextEncode", "inputs": {"text": ["1", 3]}}
        graph["4"] = {"class_type": "Reroute", "inputs": {"value": ["2", 0]}}
        graph["5"]["inputs"].update(positive=["4", 0], negative=["3", 0])
        self.assertEqual(intrinsic_pair_nodes(graph), ["1"])
        pid = self.db.save_prompt_pair("cat", "blur")
        self.dequeue(self.submit(graph))
        self.assertEqual(self.db.get_prompt_pair(pid)["run_count"], 1)
        graph["3"]["inputs"]["text"] = ["1", 2]
        self.assertEqual(intrinsic_pair_nodes(graph), [])

    def test_two_samplers_using_same_pair_count_one_run_and_dequeue_is_idempotent(self):
        graph = pair_graph()
        graph["6"] = {**graph["5"]}
        pid = self.db.save_prompt_pair("cat", "blur")
        request = self.submit(graph)
        self.dequeue(request)
        self.dequeue(request)
        self.assertEqual(self.db.get_prompt_pair(pid)["run_count"], 1)

    def test_new_negative_with_existing_positive_counts_pending_pair_only_once(self):
        self.db.save_prompt_pair("cat", "old negative")
        graph = pair_graph(negative="new negative")
        self.dequeue(self.submit(graph))
        self.execute(graph, negative="new negative")
        pair = next(
            p
            for p in self.db.list_prompt_pairs()["pairs"]
            if p["negative_text"] == "new negative"
        )
        self.assertEqual(pair["run_count"], 1)
        self.assertEqual(
            self.db.get_prompt_by_id(pair["positive_prompt_id"])["run_count"], 1
        )

    def test_unknown_negative_counts_at_execution_without_double_positive_count(self):
        graph = pair_graph(negative=["2", 0])
        graph["2"] = {"class_type": "PromptSearchList", "inputs": {}}
        self.dequeue(self.submit(graph))
        self.execute(graph, negative="batch negative")
        pair = self.db.list_prompt_pairs()["pairs"][0]
        self.assertEqual(pair["run_count"], 1)
        self.assertEqual(
            self.db.get_prompt_by_id(pair["positive_prompt_id"])["run_count"], 1
        )

    def test_cyclic_unknown_and_oversized_pair_graphs_are_bounded(self):
        from utils.prompt_graph import intrinsic_pair_nodes
        from utils.prompt_pair_resolver import resolve_text_input

        self.assertEqual(intrinsic_pair_nodes(None), [])
        self.assertEqual(intrinsic_pair_nodes({str(n): {} for n in range(501)}), [])
        graph = pair_graph()
        graph["2"] = {"class_type": "Reroute", "inputs": {"value": ["2", 0]}}
        graph["5"]["inputs"]["positive"] = ["2", 0]
        self.assertEqual(intrinsic_pair_nodes(graph), [])
        self.assertIsNone(resolve_text_input(None, "1", "text"))
        self.assertIsNone(resolve_text_input({1: {}, "1": {}}, "1", "text"))
        self.assertIsNone(resolve_text_input(graph, "missing", "text"))
        self.assertIsNone(resolve_text_input(pair_graph(), "1", "text", max_depth=0))
        graph["1"]["inputs"]["text"] = ["1", 3]
        self.assertEqual(resolve_text_input(graph, "1", "text"), "blur")
        self.assertIsNone(resolve_text_input(graph, "1", "text", max_text_length=2))

    def test_replacing_client_prompt_id_clears_obsolete_pair_records(self):
        pid = self.db.save_prompt_pair("cat", "blur")
        request = {"prompt_id": "same-id", "prompt": pair_graph()}
        handle_queued_prompt(request, self.queued, pair_queued=self.pair_queued)
        replacement = pair_graph()
        replacement["5"]["inputs"]["positive"] = ["1", 1]
        replacement["5"]["inputs"]["negative"] = ["1", 0]
        request = {"prompt_id": "same-id", "prompt": replacement}
        handle_queued_prompt(request, self.queued, pair_queued=self.pair_queued)
        self.dequeue(request)
        self.assertEqual(self.db.get_prompt_pair(pid)["run_count"], 0)
