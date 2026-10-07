"""Tests for utils.prompt_graph: which PromptManager nodes feed a positive input."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.prompt_graph import (
    is_text_linked,
    literal_text,
    positive_prompt_nodes,
    run_prompt_nodes,
)


def pm(text="a cat", **inputs):
    return {"class_type": "PromptManager", "inputs": {"text": text, **inputs}}


def ksampler(positive, negative, model="20"):
    return {
        "class_type": "KSampler",
        "inputs": {
            "positive": [positive, 0],
            "negative": [negative, 0],
            "model": [model, 0],
        },
    }


LOADER = {
    "class_type": "CheckpointLoaderSimple",
    "inputs": {"ckpt_name": "m.safetensors"},
}


class TestPositivePromptNodes(unittest.TestCase):
    def test_positive_and_negative_prompt_manager_nodes(self):
        # Negative listed first, as in the #75 / pmtest workflow
        graph = {
            "176": pm("blurry"),
            "134": pm("a cat"),
            "5": ksampler("134", "176"),
            "20": LOADER,
        }
        self.assertEqual(positive_prompt_nodes(graph), ["134"])

    def test_negative_only_prompt_manager_is_never_positive(self):
        graph = {
            "176": pm("blurry"),
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat"}},
            "5": ksampler("6", "176"),
            "20": LOADER,
        }
        self.assertEqual(positive_prompt_nodes(graph), [])

    def test_through_controlnet_and_combine_pass_throughs(self):
        graph = {
            "1": pm("subject"),
            "2": pm("style"),
            "3": pm("blurry"),
            "4": {
                "class_type": "ConditioningCombine",
                "inputs": {"conditioning_1": ["1", 0], "conditioning_2": ["2", 0]},
            },
            "5": {
                "class_type": "ControlNetApplyAdvanced",
                "inputs": {"positive": ["4", 0], "negative": ["3", 0], "strength": 1.0},
            },
            "6": {
                "class_type": "KSampler",
                "inputs": {
                    "positive": ["5", 0],
                    "negative": ["5", 1],
                    "model": ["20", 0],
                },
            },
            "20": LOADER,
        }
        self.assertEqual(sorted(positive_prompt_nodes(graph)), ["1", "2"])

    def test_cfg_guider_and_basic_guider(self):
        graph = {
            "1": pm("fox"),
            "2": pm("watermark"),
            "3": {
                "class_type": "CFGGuider",
                "inputs": {
                    "positive": ["1", 0],
                    "negative": ["2", 0],
                    "model": ["20", 0],
                    "cfg": 5,
                },
            },
            "4": pm("owl"),
            "5": {
                "class_type": "BasicGuider",
                "inputs": {"conditioning": ["4", 0], "model": ["20", 0]},
            },
            "20": LOADER,
        }
        self.assertEqual(sorted(positive_prompt_nodes(graph)), ["1", "4"])

    def test_prompt_manager_text_wired_into_an_encoder(self):
        graph = {
            "1": {"class_type": "PromptManagerText", "inputs": {"text": "a castle"}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": ["1", 0]}},
            "3": {"class_type": "PromptManagerText", "inputs": {"text": "lowres"}},
            "4": {"class_type": "CLIPTextEncode", "inputs": {"text": ["3", 0]}},
            "5": ksampler("2", "4"),
            "20": LOADER,
        }
        self.assertEqual(positive_prompt_nodes(graph), ["1"])

    def test_node_used_as_both_positive_and_negative_counts_as_positive(self):
        graph = {"1": pm("same"), "5": ksampler("1", "1"), "20": LOADER}
        self.assertEqual(positive_prompt_nodes(graph), ["1"])

    def test_two_samplers_sharing_a_prompt_list_it_once(self):
        graph = {
            "1": pm("hero"),
            "2": pm("blurry"),
            "5": ksampler("1", "2"),
            "14": ksampler("1", "2"),
            "20": LOADER,
        }
        self.assertEqual(positive_prompt_nodes(graph), ["1"])

    def test_graph_without_sampler_or_malformed_input(self):
        self.assertEqual(positive_prompt_nodes({"1": pm()}), [])
        for bad in (None, [], "x", {"1": None}, {"1": {"inputs": None}}):
            self.assertEqual(positive_prompt_nodes(bad), [], repr(bad))

    def test_cycles_terminate(self):
        loop = {
            "class_type": "Weird",
            "inputs": {
                "conditioning": ["9", 0],
                "conditioning_1": ["9", 0],
                "positive": ["9", 0],
            },
        }
        graph = {"9": loop, "5": ksampler("9", "9"), "20": LOADER}
        self.assertEqual(positive_prompt_nodes(graph), [])


class TestRunPromptNodes(unittest.TestCase):
    """The prompt nodes that represent a run, with a safe fallback."""

    def test_uses_positive_nodes_when_found(self):
        graph = {
            "176": pm("blurry"),
            "134": pm("a cat"),
            "5": ksampler("134", "176"),
            "20": LOADER,
        }
        self.assertEqual(run_prompt_nodes(graph), ["134"])

    def test_unrecognised_sampler_falls_back_to_every_prompt_manager_node(self):
        graph = {
            "1": pm("a cat"),
            "9": {"class_type": "ExoticSampler", "inputs": {"cond": ["1", 0]}},
            "2": {"class_type": "PromptManagerText", "inputs": {"text": "x"}},
        }
        self.assertEqual(sorted(run_prompt_nodes(graph)), ["1", "2"])

    def test_malformed_graph(self):
        self.assertEqual(run_prompt_nodes(None), [])


class TestNodeText(unittest.TestCase):
    def test_literal_and_linked_text(self):
        graph = {
            "1": pm("  a cat  "),
            "2": pm(["7", 0]),
            "7": {"class_type": "PromptSearchList", "inputs": {}},
        }
        self.assertEqual(literal_text(graph, "1"), "a cat")
        self.assertIsNone(literal_text(graph, "2"))
        self.assertFalse(is_text_linked(graph, "1"))
        self.assertTrue(is_text_linked(graph, "2"))

    def test_missing_nodes(self):
        self.assertIsNone(literal_text({}, "1"))
        self.assertFalse(is_text_linked({}, "1"))
        self.assertIsNone(literal_text({"1": pm("   ")}, "1"))


if __name__ == "__main__":
    unittest.main()
