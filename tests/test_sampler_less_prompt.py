"""``sampler_less_prompt``: the one wired text of a graph with no sampler (#252)."""

import time
import unittest

from utils.prompt_pair_resolver import sampler_less_prompt


def _guided(**nodes):
    """``nodes`` plus a guider consuming node ``"1"``."""
    graph = {"9": {"class_type": "BasicGuider", "inputs": {"conditioning": ["1", 0]}}}
    graph.update(nodes)
    return graph


class TestSamplerLessPrompt(unittest.TestCase):
    def test_not_a_graph_or_too_large_is_absent(self):
        for graph in (None, [], {}, "text"):
            self.assertEqual(sampler_less_prompt(graph)["state"], "absent")
        big = {str(i): {"class_type": "X", "inputs": {}} for i in range(3)}
        self.assertEqual(sampler_less_prompt(big, max_visits=2)["state"], "absent")

    def test_one_literal_text(self):
        graph = _guided(**{"1": {"class_type": "Enc", "inputs": {"text": " a fox "}}})
        self.assertEqual(
            sampler_less_prompt(graph),
            {"state": "resolved", "text": "a fox", "texts": ["a fox"]},
        )

    def test_a_number_literal_is_never_a_prompt(self):
        for value in (2048, "2048", "1.5", " 7 "):
            graph = _guided(**{"1": {"class_type": "Enc", "inputs": {"text": value}}})
            self.assertEqual(sampler_less_prompt(graph)["state"], "absent", value)

    def test_a_long_digit_run_with_a_letter_is_text_and_fast(self):
        text = "9" * 50_000 + "x"
        graph = _guided(**{"1": {"class_type": "Enc", "inputs": {"text": text}}})
        started = time.monotonic()
        self.assertEqual(sampler_less_prompt(graph)["text"], text)
        self.assertLess(time.monotonic() - started, 1.0)

    def test_number_like_words_are_prompts(self):
        for value in ("nan", "inf", "Infinity", "1_000"):
            graph = _guided(**{"1": {"class_type": "Enc", "inputs": {"text": value}}})
            self.assertEqual(sampler_less_prompt(graph)["text"], value)

    def test_an_own_node_with_unreadable_text_beside_another_text_is_ambiguous(self):
        graph = _guided(
            **{
                "1": {"class_type": "PromptManager", "inputs": {"text": ["5", 0]}},
                "5": {"class_type": "LLM", "inputs": {"seed": 3}},
                "2": {"class_type": "Enc", "inputs": {"text": "<lora:x:1>"}},
                "3": {"class_type": "Sink", "inputs": {"c": ["2", 0]}},
            }
        )
        self.assertEqual(sampler_less_prompt(graph)["state"], "ambiguous")

    def test_unhashable_class_type_is_an_unknown_node_not_a_crash(self):
        graph = _guided(**{"1": {"class_type": ["x"], "inputs": {"text": "a fox"}}})
        self.assertEqual(sampler_less_prompt(graph)["text"], "a fox")

    def test_own_negative_node_is_not_authoritative(self):
        graph = _guided(
            **{
                "1": {"class_type": "Enc", "inputs": {"text": "a fox"}},
                "2": {
                    "class_type": "PromptManagerNegative",
                    "inputs": {"text": "blur"},
                },
                "3": {"class_type": "Sink", "inputs": {"c": ["2", 0]}},
            }
        )
        found = sampler_less_prompt(graph)
        self.assertEqual(found["state"], "ambiguous")
        self.assertEqual(found["texts"], ["a fox", "blur"])

    def test_own_node_text_wins_over_an_untraceable_text(self):
        graph = _guided(
            **{
                "1": {"class_type": "PromptManagerText", "inputs": {"text": "a fox"}},
                "2": {"class_type": "Enc", "inputs": {"text": ["5", 0]}},
                "5": {"class_type": "LLM", "inputs": {"seed": 3}},
                "6": {"class_type": "Sink", "inputs": {"c": ["2", 0]}},
            }
        )
        self.assertEqual(sampler_less_prompt(graph)["text"], "a fox")

    def test_two_own_texts_are_ambiguous(self):
        graph = _guided(
            **{
                "1": {"class_type": "PromptManager", "inputs": {"text": "a fox"}},
                "2": {"class_type": "PromptManager", "inputs": {"text": "a cat"}},
                "3": {"class_type": "Sink", "inputs": {"c": ["2", 0]}},
            }
        )
        self.assertEqual(sampler_less_prompt(graph)["state"], "ambiguous")

    def test_only_an_untraceable_text_is_ambiguous_with_nothing_to_show(self):
        graph = _guided(
            **{
                "1": {"class_type": "Enc", "inputs": {"text": ["5", 0]}},
                "5": {"class_type": "LLM", "inputs": {"seed": 3}},
            }
        )
        self.assertEqual(
            sampler_less_prompt(graph),
            {"state": "ambiguous", "text": None, "texts": []},
        )


if __name__ == "__main__":
    unittest.main()
