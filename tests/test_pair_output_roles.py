"""Actual sampler wires determine which component counts as its positive text."""

import unittest

from utils.hashing import generate_prompt_hash
from utils.usage_tracking import queued_prompt_runs, queued_pair_runs


def paired_graph(positive_slot=0, negative_slot=1):
    return {
        "1": {
            "class_type": "PromptManagerPair",
            "inputs": {"text": "cat", "negative_text": "blur"},
        },
        "2": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["3", 0],
                "positive": ["1", positive_slot],
                "negative": ["1", negative_slot],
            },
        },
    }


class TestPairOutputRoles(unittest.TestCase):
    def test_swapped_conditioning_counts_actual_positive_component_only(self):
        graph = paired_graph(1, 0)
        self.assertEqual(
            queued_prompt_runs(graph), [(generate_prompt_hash("blur"), "blur")]
        )
        self.assertEqual(queued_pair_runs(graph), [])

    def test_negative_string_encoded_as_positive_counts_actual_string_component(self):
        graph = paired_graph()
        graph["4"] = {"class_type": "CLIPTextEncode", "inputs": {"text": ["1", 3]}}
        graph["2"]["inputs"]["positive"] = ["4", 0]
        self.assertEqual(
            queued_prompt_runs(graph), [(generate_prompt_hash("blur"), "blur")]
        )
        self.assertEqual(queued_pair_runs(graph), [])

    def test_two_sampler_positive_roles_count_each_component_once(self):
        graph = paired_graph()
        graph["5"] = {
            "class_type": "KSampler",
            "inputs": {"model": ["3", 0], "positive": ["1", 1], "negative": ["1", 0]},
        }
        self.assertEqual(
            {text for _, text in queued_prompt_runs(graph)}, {"cat", "blur"}
        )


class TestPairRoleExecution(unittest.TestCase):
    def setUp(self):
        from tests.test_prompt_pair_node import TestPromptPairNode

        TestPromptPairNode.setUp(self)

    def test_new_swapped_component_consumes_its_queued_pending_use(self):
        self.node.pending_first_use.add(generate_prompt_hash("blur"))
        self.node.encode_prompt(
            self.clip, "cat", "blur", prompt=paired_graph(1, 0), unique_id="1"
        )
        pair = self.db.list_prompt_pairs()["pairs"][0]
        self.assertEqual(
            self.db.get_prompt_by_id(pair["negative_prompt_id"])["run_count"], 1
        )
        self.assertEqual(
            self.db.get_prompt_by_id(pair["positive_prompt_id"])["run_count"], 0
        )
        self.assertEqual(pair["run_count"], 0)

    def test_unknown_negative_link_used_as_positive_counts_at_execution(self):
        graph = paired_graph(1, 0)
        graph["1"]["inputs"]["negative_text"] = ["8", 0]
        graph["8"] = {"class_type": "UnknownBatch", "inputs": {}}
        self.node.encode_prompt(self.clip, "cat", "blur", prompt=graph, unique_id="1")
        pair = self.db.list_prompt_pairs()["pairs"][0]
        self.assertEqual(
            self.db.get_prompt_by_id(pair["negative_prompt_id"])["run_count"], 1
        )
        self.assertEqual(pair["run_count"], 0)
