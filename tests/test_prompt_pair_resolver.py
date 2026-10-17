"""Conservative prompt-pair resolution contracts, independent of ComfyUI."""

import unittest

from utils.prompt_pair_resolver import OutputAdapter, resolve_prompt_pair


def graph(positive: str = "portrait", negative: str = "blurry") -> dict:
    return {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": positive}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": negative}},
        "3": {
            "class_type": "KSampler",
            "inputs": {"positive": ["1", 0], "negative": ["2", 0]},
        },
    }


class TestPromptPairResolver(unittest.TestCase):
    def test_resolves_same_sampler_and_preserves_output_provenance(self):
        result = resolve_prompt_pair({"prompt": graph()})
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(result["positive"]["text"], "portrait")
        self.assertEqual(result["negative"]["text"], "blurry")
        self.assertEqual(result["positive"]["provenance"]["node_id"], "1")
        self.assertEqual(result["positive"]["provenance"]["output_slot"], 0)
        self.assertEqual(result["sampler_id"], "3")

    def test_explicit_empty_negative_is_resolved_but_missing_is_absent(self):
        self.assertEqual(resolve_prompt_pair(graph(negative=""))["state"], "resolved")
        self.assertEqual(
            resolve_prompt_pair(graph(negative=""))["negative"]["text"], ""
        )
        source = graph()
        del source["3"]["inputs"]["negative"]
        result = resolve_prompt_pair(source)
        self.assertEqual(result["state"], "absent")
        self.assertEqual(result["positive"]["text"], "portrait")
        self.assertIsNone(result["negative"])

    def test_multiple_samplers_are_ambiguous_even_when_texts_match(self):
        source = graph()
        source["4"] = {
            "class_type": "KSamplerAdvanced",
            "inputs": source["3"]["inputs"],
        }
        result = resolve_prompt_pair(source)
        self.assertEqual(result["state"], "ambiguous")
        self.assertEqual(len(result["candidates"]), 2)
        self.assertIsNone(result["positive"])

    def test_saver_anchor_selects_only_its_sampler(self):
        source = graph()
        source.update(
            {
                "4": {
                    "class_type": "KSampler",
                    "inputs": {"positive": ["2", 0], "negative": ["1", 0]},
                },
                "5": {"class_type": "VAEDecode", "inputs": {"samples": ["4", 0]}},
                "6": {"class_type": "SaveImage", "inputs": {"images": ["5", 0]}},
            }
        )
        result = resolve_prompt_pair(source, output_node_id="6")
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(result["sampler_id"], "4")
        self.assertEqual(result["positive"]["text"], "blurry")

    def test_unknown_anchor_transform_and_multistage_are_unresolved(self):
        source = graph()
        source["5"] = {
            "class_type": "MysteryImageTransform",
            "inputs": {"images": ["3", 0]},
        }
        self.assertEqual(
            resolve_prompt_pair(source, output_node_id="5")["state"], "unresolved"
        )
        source["4"] = {
            "class_type": "KSampler",
            "inputs": {**source["3"]["inputs"], "latent_image": ["3", 0]},
        }
        self.assertEqual(
            resolve_prompt_pair(source, output_node_id="4")["state"], "unresolved"
        )

    def test_combines_unknown_encoders_and_sdxl_channels_are_not_guessed(self):
        for name in ("ConditioningCombine", "CLIPTextEncodeSDXL", "UnknownEncoder"):
            with self.subTest(name=name):
                source = graph()
                source["1"] = {
                    "class_type": name,
                    "inputs": {
                        "text": "guess",
                        "text_g": "global",
                        "text_l": "local",
                        "conditioning_1": ["2", 0],
                    },
                }
                self.assertEqual(resolve_prompt_pair(source)["state"], "unresolved")

    def test_prompt_manager_composition_and_string_slot_are_resolved(self):
        source = graph()
        source["8"] = {
            "class_type": "PromptManagerSplit",
            "inputs": {
                "text": "portrait",
                "prepend_text": " trigger ",
                "append_text": " detail ",
            },
        }
        source["1"]["inputs"]["text"] = ["8", 1]
        result = resolve_prompt_pair(source)
        self.assertEqual(result["positive"]["text"], "trigger portrait detail")
        self.assertIn(
            {"node_id": "8", "output_slot": 1}, result["positive"]["provenance"]["path"]
        )
        source["3"]["inputs"]["positive"] = ["8", 0]
        self.assertEqual(
            resolve_prompt_pair(source)["positive"]["text"], "trigger portrait detail"
        )
        source["1"]["inputs"]["text"] = ["8", 0]
        source["3"]["inputs"]["positive"] = ["1", 0]
        self.assertEqual(resolve_prompt_pair(source)["state"], "unresolved")

    def test_concat_and_reroute_resolve_safe_linked_strings(self):
        source = graph()
        source["8"] = {"class_type": "PrimitiveString", "inputs": {"value": "portrait"}}
        source["9"] = {
            "class_type": "StringConcatenate",
            "inputs": {"string_a": ["8", 0], "string_b": ["8", 0], "delimiter": ", "},
        }
        source["10"] = {"class_type": "Reroute", "inputs": {"value": ["9", 0]}}
        source["1"]["inputs"]["text"] = ["10", 0]
        self.assertEqual(
            resolve_prompt_pair(source)["positive"]["text"], "portrait, portrait"
        )

    def test_slot_adapter_does_not_conflate_two_outputs_of_one_node(self):
        source = graph()
        source["8"] = {
            "class_type": "FuturePair",
            "inputs": {"first": "positive", "second": "negative"},
        }
        source["3"]["inputs"] = {"positive": ["8", 0], "negative": ["8", 1]}
        adapters = {
            ("FuturePair", 0): OutputAdapter("first", "conditioning"),
            ("FuturePair", 1): OutputAdapter("second", "conditioning"),
        }
        result = resolve_prompt_pair(source, adapters=adapters)
        self.assertEqual(result["positive"]["text"], "positive")
        self.assertEqual(result["negative"]["text"], "negative")
        self.assertNotEqual(
            result["positive"]["provenance"]["output_slot"],
            result["negative"]["provenance"]["output_slot"],
        )

    def test_prompt_manager_text_has_only_its_real_string_output_zero(self):
        source = graph()
        source["8"] = {
            "class_type": "PromptManagerText",
            "inputs": {"text": "text node", "prepend_text": "before"},
        }
        source["1"]["inputs"]["text"] = ["8", 0]
        self.assertEqual(
            resolve_prompt_pair(source)["positive"]["text"], "before text node"
        )
        source["1"]["inputs"]["text"] = ["8", 1]
        self.assertEqual(resolve_prompt_pair(source)["state"], "unresolved")
        source["3"]["inputs"]["positive"] = ["8", 0]
        self.assertEqual(resolve_prompt_pair(source)["state"], "unresolved")

    def test_malformed_class_names_and_unknown_samplers_do_not_claim_absence(self):
        for name in ([], {}, None, "UnknownSampler"):
            source = graph()
            source["3"]["class_type"] = name
            self.assertEqual(resolve_prompt_pair(source)["state"], "unresolved")

    def test_pair_adapter_composes_only_positive_and_retains_recipe_text(self):
        source = graph()
        source["8"] = {
            "class_type": "PromptManagerPair",
            "inputs": {
                "text": "positive",
                "negative_text": "negative",
                "prepend_text": "trigger",
                "append_text": "detail",
            },
        }
        source["3"]["inputs"] = {"positive": ["8", 0], "negative": ["8", 1]}
        result = resolve_prompt_pair(source)
        self.assertEqual(result["positive"]["text"], "trigger positive detail")
        self.assertEqual(result["positive"]["storage_text"], "positive")
        self.assertEqual(result["negative"]["text"], "negative")
        self.assertEqual(result["negative"]["storage_text"], "negative")
        source["1"]["inputs"]["text"] = ["8", 2]
        source["2"]["inputs"]["text"] = ["8", 3]
        source["3"]["inputs"] = {"positive": ["1", 0], "negative": ["2", 0]}
        result = resolve_prompt_pair(source)
        self.assertEqual(result["positive"]["storage_text"], "positive")
        self.assertEqual(result["negative"]["text"], "negative")
        source["3"]["inputs"]["negative"] = ["8", 3]
        self.assertEqual(resolve_prompt_pair(source)["state"], "unresolved")

    def test_linked_recipe_and_ordinary_clip_storage_values_are_explicit(self):
        source = graph()
        result = resolve_prompt_pair(source)
        self.assertEqual(result["positive"]["storage_text"], "portrait")
        source["8"] = {
            "class_type": "PrimitiveString",
            "inputs": {"value": "linked main"},
        }
        source["1"] = {
            "class_type": "PromptManager",
            "inputs": {"text": ["8", 0], "prepend_text": "before"},
        }
        result = resolve_prompt_pair(source)
        self.assertEqual(result["positive"]["text"], "before linked main")
        self.assertEqual(result["positive"]["storage_text"], "linked main")
        a1111 = resolve_prompt_pair(
            {
                "parameters": (
                    "portrait\nNegative prompt: blurry\n" "Steps: 20, Sampler: Euler"
                )
            }
        )
        self.assertEqual(a1111["positive"]["storage_text"], "portrait")
        self.assertEqual(a1111["negative"]["storage_text"], "blurry")

    def test_cycles_depth_visits_and_text_growth_are_bounded(self):
        source = graph()
        source["8"] = {"class_type": "Reroute", "inputs": {"value": ["8", 0]}}
        source["1"]["inputs"]["text"] = ["8", 0]
        self.assertEqual(resolve_prompt_pair(source)["state"], "unresolved")
        self.assertEqual(
            resolve_prompt_pair(graph(), max_visits=1)["state"], "unresolved"
        )
        self.assertEqual(
            resolve_prompt_pair(graph(), max_depth=0)["state"], "unresolved"
        )
        self.assertEqual(
            resolve_prompt_pair(graph(), max_text_length=3)["state"], "unresolved"
        )

    def test_malformed_metadata_and_links_never_raise(self):
        for value in (
            None,
            [],
            "raw text",
            {},
            {"prompt": []},
            {"prompt": {"1": None}},
        ):
            with self.subTest(value=value):
                self.assertIn(
                    resolve_prompt_pair(value)["state"], ("absent", "unresolved")
                )
        for link in (["missing", 0], ["1", -1], ["1", True], ["1", 17], ["1"], 5):
            source = graph()
            source["3"]["inputs"]["positive"] = link
            self.assertEqual(resolve_prompt_pair(source)["state"], "unresolved")

    def test_a1111_explicit_parameters_and_empty_negative(self):
        for negative in ("bad anatomy\nblurry", ""):
            result = resolve_prompt_pair(
                {
                    "parameters": (
                        f"portrait\nNegative prompt: {negative}\n"
                        "Steps: 20, Sampler: Euler, Seed: 1"
                    )
                }
            )
            self.assertEqual(result["state"], "resolved")
            self.assertEqual(result["negative"]["text"], negative)
            self.assertEqual(result["positive"]["provenance"]["format"], "a1111")
        self.assertEqual(
            resolve_prompt_pair({"parameters": "portrait\nSteps: 20, Sampler: Euler"})[
                "state"
            ],
            "absent",
        )
        self.assertEqual(
            resolve_prompt_pair({"parameters": "unstructured arbitrary text"})["state"],
            "unresolved",
        )

    def test_was_concat_preserves_configured_whitespace_and_rejects_bad_delimiters(
        self,
    ):
        source = graph()
        source["8"] = {
            "class_type": "Text Concatenate",
            "inputs": {
                "text_a": "  portrait ",
                "text_b": "",
                "text_c": "studio",
                "delimiter": ", ",
            },
        }
        source["1"]["inputs"]["text"] = ["8", 0]
        self.assertEqual(
            resolve_prompt_pair(source)["positive"]["text"], "portrait, studio"
        )
        source["8"]["inputs"]["clean_whitespace"] = "false"
        self.assertEqual(
            resolve_prompt_pair(source)["positive"]["text"], "  portrait , studio"
        )
        source["8"]["inputs"]["delimiter"] = []
        self.assertEqual(resolve_prompt_pair(source)["state"], "unresolved")
        source["8"]["inputs"] = {1: "bad key"}
        self.assertEqual(resolve_prompt_pair(source)["state"], "unresolved")

    def test_valid_empty_latent_and_reroute_but_not_unknown_or_cyclic_latents(self):
        source = graph()
        source["8"] = {"class_type": "EmptyLatentImage", "inputs": {}}
        source["9"] = {"class_type": "Reroute", "inputs": {"value": ["8", 0]}}
        source["3"]["inputs"]["latent_image"] = ["9", 0]
        self.assertEqual(resolve_prompt_pair(source)["state"], "resolved")
        source["9"]["inputs"]["value"] = ["9", 0]
        self.assertEqual(resolve_prompt_pair(source)["state"], "unresolved")
        source["3"]["inputs"]["latent_image"] = ["8", 0]
        source["8"]["class_type"] = "UnknownLatentTransform"
        self.assertEqual(resolve_prompt_pair(source)["state"], "unresolved")
        source["3"]["inputs"]["latent_image"] = None
        self.assertEqual(resolve_prompt_pair(source)["state"], "unresolved")

    def test_cyclic_or_invalid_output_anchors_never_guess(self):
        source = graph()
        source["8"] = {"class_type": "Reroute", "inputs": {"value": ["8", 0]}}
        self.assertEqual(
            resolve_prompt_pair(source, output_node_id="8")["state"], "unresolved"
        )
        self.assertEqual(
            resolve_prompt_pair(source, output_node_id="missing")["state"], "unresolved"
        )
        source["8"] = {"class_type": "SaveImage", "inputs": {"images": ["3", 1]}}
        self.assertEqual(
            resolve_prompt_pair(source, output_node_id="8")["state"], "unresolved"
        )
        source["8"] = {"class_type": "SaveImage", "inputs": {"images": ["3", 0]}}
        self.assertEqual(
            resolve_prompt_pair(source, output_node_id="8", max_depth=1)["state"],
            "unresolved",
        )

    def test_malformed_reroute_empty_positive_and_duplicate_string_ids(self):
        source = graph(positive="")
        self.assertEqual(resolve_prompt_pair(source)["state"], "unresolved")
        source = graph()
        source["8"] = {
            "class_type": "Reroute",
            "inputs": {"value": ["1", 0], "input": ["2", 0]},
        }
        source["3"]["inputs"]["positive"] = ["8", 0]
        self.assertEqual(resolve_prompt_pair(source)["state"], "unresolved")
        source[3] = source["3"]
        self.assertEqual(resolve_prompt_pair(source)["state"], "unresolved")

    def test_a1111_malformed_ambiguous_and_bounded_parameters(self):
        self.assertEqual(resolve_prompt_pair({"parameters": None})["state"], "absent")
        self.assertEqual(
            resolve_prompt_pair({"parameters": "x" * 20}, max_text_length=10)["state"],
            "unresolved",
        )
        self.assertEqual(
            resolve_prompt_pair(
                {"parameters": "\nNegative prompt: bad\nSteps: 20, Sampler: Euler"}
            )["state"],
            "unresolved",
        )
        self.assertEqual(
            resolve_prompt_pair(
                {
                    "parameters": (
                        "portrait\nNegative prompt: bad\nNegative prompt: worse\n"
                        "Steps: 20, Sampler: Euler"
                    )
                }
            )["state"],
            "ambiguous",
        )
        self.assertEqual(
            resolve_prompt_pair(
                {
                    "parameters": (
                        "portrait\nNegative prompt: bad\n"
                        "Steps: 20, Sampler: Euler\nextra text"
                    )
                }
            )["state"],
            "unresolved",
        )

    def test_exponential_concat_text_is_bounded_before_building_a_large_string(self):
        source = graph()
        source["8"] = {"class_type": "PrimitiveString", "inputs": {"value": "xx"}}
        for index in range(9, 19):
            source[str(index)] = {
                "class_type": "StringConcatenate",
                "inputs": {
                    "string_a": [str(index - 1), 0],
                    "string_b": [str(index - 1), 0],
                },
            }
        source["1"]["inputs"]["text"] = ["18", 0]
        result = resolve_prompt_pair(source, max_text_length=100)
        self.assertEqual(result["state"], "unresolved")
        self.assertIn("length limit", result["reason"])
        self.assertEqual(
            resolve_prompt_pair(graph(), max_depth=1)["state"], "unresolved"
        )

    def test_api_graph_is_authoritative_over_unrelated_parameters(self):
        source = graph()
        source["1"]["class_type"] = "UnknownEncoder"
        result = resolve_prompt_pair(
            {
                "prompt": source,
                "parameters": (
                    "fallback\nNegative prompt: wrong\n" "Steps: 20, Sampler: Euler"
                ),
            }
        )
        self.assertEqual(result["state"], "unresolved")


if __name__ == "__main__":
    unittest.main()
