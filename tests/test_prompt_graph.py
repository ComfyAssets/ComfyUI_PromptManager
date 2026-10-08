"""Tests for utils.prompt_graph: which PromptManager nodes feed a positive input."""

import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import prompt_graph
from utils.prompt_graph import (
    MAX_DEPTH,
    MAX_TEXT_LENGTH,
    MAX_VISITS,
    _upstream_links,
    follows_input,
    has_sampler,
    positive_prompt_nodes,
    resolve_text,
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

    def test_run_prompt_nodes_negative_only_graph_is_empty(self):
        graph = {
            "1": {"class_type": "PromptManager", "inputs": {"text": "bad hands"}},
            "2": {
                "class_type": "KSampler",
                "inputs": {"negative": ["1", 0], "positive": ["9", 0]},
            },
            "9": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat"}},
        }
        self.assertEqual(run_prompt_nodes(graph), [])

    def test_run_prompt_nodes_falls_back_when_no_sampler(self):
        graph = {"1": {"class_type": "PromptManagerText", "inputs": {"text": "x"}}}
        self.assertEqual(run_prompt_nodes(graph), ["1"])


class TestInputKeys(unittest.TestCase):
    """Which input names are followed when tracing the positive prompt upstream."""

    def test_negative_flavoured_keys_are_skipped(self):
        for key in ("negative_prompt", "text_negative", "neg_text", "neg", "negative"):
            self.assertFalse(follows_input(key), key)
            self.assertEqual(_upstream_links({key: ["1", 0]}), [], key)

    def test_positive_and_plain_text_keys_are_followed(self):
        for key in ("text_positive", "positive", "text", "prompt", "text_g"):
            self.assertTrue(follows_input(key), key)
            self.assertEqual(_upstream_links({key: ["1", 0]}), [["1", 0]], key)

    def test_styler_node_only_follows_its_positive_text(self):
        graph = {
            "1": pm("a cat"),
            "2": pm("bad hands"),
            "3": {
                "class_type": "SDXLPromptStyler",
                "inputs": {"text_positive": ["1", 0], "text_negative": ["2", 0]},
            },
            "4": {"class_type": "CLIPTextEncode", "inputs": {"text": ["3", 0]}},
            "5": ksampler("4", "2"),
            "20": LOADER,
        }
        self.assertEqual(positive_prompt_nodes(graph), ["1"])
        self.assertEqual(run_prompt_nodes(graph), ["1"])
        self.assertEqual(resolve_text(graph, "1"), "a cat")


class TestHasSampler(unittest.TestCase):
    def test_structural_sampler_and_guider_are_detected(self):
        self.assertTrue(has_sampler({"5": ksampler("1", "2"), "20": LOADER}))
        guider = {
            "class_type": "BasicGuider",
            "inputs": {"model": ["20", 0], "conditioning": ["1", 0]},
        }
        self.assertTrue(has_sampler({"6": guider, "20": LOADER}))

    def test_sampler_without_model_link_is_detected_by_name(self):
        sampler = {
            "class_type": "KSamplerAdvanced",
            "inputs": {"positive": ["1", 0], "negative": ["2", 0]},
        }
        self.assertTrue(has_sampler({"5": sampler}))

    def test_non_samplers_and_malformed_graphs(self):
        controlnet = {
            "class_type": "ControlNetApplyAdvanced",
            "inputs": {"positive": ["1", 0], "negative": ["2", 0]},
        }
        self.assertFalse(has_sampler({"7": controlnet}))
        exotic = {"class_type": "ExoticSampler", "inputs": {"cond": ["1", 0]}}
        self.assertFalse(has_sampler({"9": exotic}))
        for bad in (None, [], "x", {"1": None}, {"1": {"inputs": None}}, {}):
            self.assertFalse(has_sampler(bad), repr(bad))


class TestAdversarialGraphs(unittest.TestCase):
    """Queued and embedded graphs are untrusted: never hang, never raise."""

    def _assert_fast(self, graph):
        started = time.monotonic()
        for fn in (positive_prompt_nodes, run_prompt_nodes, has_sampler):
            fn(graph)
        resolve_text(graph, "1")
        self.assertLess(time.monotonic() - started, 1.0)

    def test_cyclic_graph_terminates(self):
        graph = {
            "1": pm(["2", 0]),
            "2": {"class_type": "StringConcatenate", "inputs": {"string_a": ["1", 0]}},
            "3": {
                "class_type": "ConditioningCombine",
                "inputs": {"conditioning_1": ["3", 0], "conditioning_2": ["1", 0]},
            },
            "5": ksampler("3", "3"),
            "20": LOADER,
        }
        self._assert_fast(graph)
        self.assertEqual(run_prompt_nodes(graph), ["1"])

    def test_ten_thousand_node_fan_out_terminates(self):
        graph = {"20": LOADER}
        combine_inputs = {}
        for i in range(10_000):
            graph[str(100 + i)] = pm(f"prompt {i}")
            combine_inputs[f"conditioning_{i}"] = [str(100 + i), 0]
        graph["3"] = {"class_type": "ConditioningCombine", "inputs": combine_inputs}
        graph["5"] = ksampler("3", "3")
        self._assert_fast(graph)
        self.assertTrue(run_prompt_nodes(graph))

    def test_non_dict_inputs_and_odd_values(self):
        graph = {
            "1": {"class_type": "PromptManager", "inputs": ["not", "a", "dict"]},
            "2": {"class_type": "KSampler", "inputs": 42},
            "3": {"class_type": None, "inputs": {"positive": "1", "negative": [1]}},
            "4": "just a string",
            "5": {
                "class_type": "KSampler",
                "inputs": {
                    "positive": [None, 0],
                    "negative": [{"x": 1}, 0],
                    "model": [[], 0],
                },
            },
            6: {"class_type": "PromptManager", "inputs": {"text": 7}},
        }
        self._assert_fast(graph)
        for fn in (positive_prompt_nodes, run_prompt_nodes):
            self.assertIsInstance(fn(graph), list)


class TestResolveText(unittest.TestCase):
    """Text known at queue time: typed, or from known pure string nodes."""

    def _linked(self, source):
        return {"1": pm(["2", 0]), "2": source}

    def test_typed_text(self):
        self.assertEqual(resolve_text({"1": pm("  a cat ")}, "1"), "a cat")

    def test_core_primitive_string(self):
        for cls in ("PrimitiveString", "PrimitiveStringMultiline"):
            graph = self._linked({"class_type": cls, "inputs": {"value": "a cat"}})
            self.assertEqual(resolve_text(graph, "1"), "a cat", cls)

    def test_core_string_concatenate(self):
        graph = self._linked(
            {
                "class_type": "StringConcatenate",
                "inputs": {
                    "string_a": "a cat",
                    "string_b": "at dusk",
                    "delimiter": ", ",
                },
            }
        )
        self.assertEqual(resolve_text(graph, "1"), "a cat, at dusk")

    def test_was_text_concatenate_skips_empty_and_cleans_whitespace(self):
        graph = self._linked(
            {
                "class_type": "Text Concatenate",
                "inputs": {
                    "delimiter": " ",
                    "clean_whitespace": "true",
                    "text_a": " a cat ",
                    "text_b": "",
                    "text_c": ["3", 0],
                },
            }
        )
        graph["3"] = {"class_type": "PrimitiveString", "inputs": {"value": "at dusk"}}
        self.assertEqual(resolve_text(graph, "1"), "a cat at dusk")

    def test_pass_through_nodes(self):
        for cls in ("ShowText|pysssss", "Text Multiline"):
            graph = self._linked({"class_type": cls, "inputs": {"text": "a cat"}})
            self.assertEqual(resolve_text(graph, "1"), "a cat", cls)

    def test_unknown_and_batch_sources_are_never_guessed(self):
        for source in (
            {"class_type": "PromptSearchList", "inputs": {"search_text": "cat"}},
            {
                "class_type": "SomeTextReplace",
                "inputs": {"text": "a cat", "find": "cat", "replace": "dog"},
            },
        ):
            self.assertIsNone(
                resolve_text(self._linked(source), "1"), source["class_type"]
            )

    def test_same_source_used_twice_resolves_both_branches(self):
        # StringConcatenate(string_a=X, string_b=X): X is not a cycle, it is
        # simply shared, and must resolve in both branches.
        graph = {
            "1": pm(["2", 0]),
            "2": {
                "class_type": "StringConcatenate",
                "inputs": {
                    "string_a": ["3", 0],
                    "string_b": ["3", 0],
                    "delimiter": "+",
                },
            },
            "3": {"class_type": "PrimitiveString", "inputs": {"value": "x"}},
        }
        self.assertEqual(resolve_text(graph, "1"), "x+x")

    def test_diamond_graph_resolves(self):
        # 1 <- 2 <- (3, 4) and both 3 and 4 read from 5
        graph = {
            "1": pm(["2", 0]),
            "2": {
                "class_type": "StringConcatenate",
                "inputs": {
                    "string_a": ["3", 0],
                    "string_b": ["4", 0],
                    "delimiter": " ",
                },
            },
            "3": {"class_type": "ShowText|pysssss", "inputs": {"text": ["5", 0]}},
            "4": {"class_type": "Text Multiline", "inputs": {"text": ["5", 0]}},
            "5": {"class_type": "PrimitiveString", "inputs": {"value": "cat"}},
        }
        self.assertEqual(resolve_text(graph, "1"), "cat cat")

    def test_indirect_cycle_returns_none(self):
        graph = {
            "1": pm(["2", 0]),
            "2": {"class_type": "ShowText|pysssss", "inputs": {"text": ["3", 0]}},
            "3": {"class_type": "Text Multiline", "inputs": {"text": ["2", 0]}},
        }
        self.assertIsNone(resolve_text(graph, "1"))

    @staticmethod
    def _diamond_chain(levels, leaf):
        """Node i reads node i+1 on both inputs: 2**levels paths, levels nodes."""
        graph = {"0": {"class_type": "PrimitiveString", "inputs": {"value": leaf}}}
        for i in range(1, levels + 1):
            prev = [str(i - 1), 0]
            graph[str(i)] = {
                "class_type": "StringConcatenate",
                "inputs": {"string_a": prev, "string_b": prev, "delimiter": ""},
            }
        graph["pm"] = pm([str(levels), 0])
        return graph

    def _count_expansions(self, graph):
        real = prompt_graph._resolve_string_node
        with mock.patch.object(
            prompt_graph, "_resolve_string_node", side_effect=real
        ) as spy:
            start = time.monotonic()
            text = resolve_text(graph, "pm")
            elapsed = time.monotonic() - start
        return text, spy.call_count, elapsed

    def test_forty_level_diamond_chain_expands_each_node_once(self):
        text, expansions, elapsed = self._count_expansions(self._diamond_chain(40, ""))
        self.assertIsNone(text)  # the leaf is empty, so the prompt is empty
        self.assertLessEqual(expansions, 41)
        self.assertLess(elapsed, 0.5)

    def test_diamond_chain_with_text_stays_within_budget(self):
        # Doubling "a" 400 times would be a 2**400-character string: the
        # length cap turns it into None long before, and the visit budget
        # holds regardless of how the graph is shaped.
        text, expansions, elapsed = self._count_expansions(
            self._diamond_chain(400, "a")
        )
        self.assertIsNone(text)
        self.assertLessEqual(expansions, MAX_VISITS)
        self.assertLess(elapsed, 0.5)

    def test_shallow_diamond_chain_resolves_doubled_text(self):
        text, expansions, _ = self._count_expansions(self._diamond_chain(4, "ab"))
        self.assertEqual(text, "ab" * 16)
        self.assertLessEqual(expansions, 5)

    def test_chain_deeper_than_max_depth_is_none_without_raising(self):
        graph = {"0": {"class_type": "PrimitiveString", "inputs": {"value": "a"}}}
        for i in range(1, 5000):
            graph[str(i)] = {
                "class_type": "ShowText|pysssss",
                "inputs": {"text": [str(i - 1), 0]},
            }
        graph["pm"] = pm(["4999", 0])
        self.assertIsNone(resolve_text(graph, "pm"))
        graph["pm"] = pm([str(MAX_DEPTH - 1), 0])
        self.assertEqual(resolve_text(graph, "pm"), "a")

    def test_resolved_text_longer_than_cap_is_none(self):
        graph = {
            "1": pm(["2", 0]),
            "2": {
                "class_type": "PrimitiveString",
                "inputs": {"value": "x" * (MAX_TEXT_LENGTH + 1)},
            },
        }
        self.assertIsNone(resolve_text(graph, "1"))
        graph["2"]["inputs"]["value"] = "x" * MAX_TEXT_LENGTH
        self.assertEqual(len(resolve_text(graph, "1")), MAX_TEXT_LENGTH)

    def test_cycles_and_missing_links(self):
        graph = {
            "1": pm(["2", 0]),
            "2": {
                "class_type": "StringConcatenate",
                "inputs": {"string_a": ["2", 0], "string_b": "x", "delimiter": ""},
            },
        }
        self.assertIsNone(resolve_text(graph, "1"))
        self.assertIsNone(resolve_text({"1": pm(["9", 0])}, "1"))


if __name__ == "__main__":
    unittest.main()
