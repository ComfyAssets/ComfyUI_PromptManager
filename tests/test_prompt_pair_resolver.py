"""Conservative prompt-pair resolution contracts, independent of ComfyUI."""

import collections
import json
import random
import subprocess  # nosec B404 - fixed interpreter command, no shell
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from utils import inspection_semantics
from utils import prompt_pair_resolver as resolver
from utils.prompt_pair_resolver import OutputAdapter, resolve_prompt_pair

try:
    from tests import inspection_parity as parity
except ImportError:  # discovered with ``-s tests``
    import inspection_parity as parity


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


def _f03_metadata() -> dict:
    return {"prompt": parity.build("f03")["prompt"]}


def _vector(name: str) -> dict:
    return next(v for v in parity.load_vectors() if v["name"] == name)["expect"]


def _diamond(prefix: str = "d", leaves: int = 575, middles: int = 24) -> dict:
    """A ~600-node string diamond: 24 fans over 575 leaves, one leaf shared."""
    letters = "abcdefghijklmnopqrstuvwxyz"
    nodes = {
        f"{prefix}l{index}": {"class_type": "PrimitiveString", "inputs": {"value": "a"}}
        for index in range(leaves)
    }
    for middle in range(middles):
        nodes[f"{prefix}m{middle}"] = {
            "class_type": "Text Concatenate",
            "inputs": {
                f"text_{letters[index]}": [
                    f"{prefix}l{(middle * middles + index) % leaves}",
                    0,
                ]
                for index in range(middles)
            },
        }
    nodes[f"{prefix}r"] = {
        "class_type": "Text Concatenate",
        "inputs": {
            f"text_{letters[index]}": [f"{prefix}m{index}", 0]
            for index in range(middles)
        },
    }
    return nodes


def _diamond_samplers(count: int) -> dict:
    """``count`` samplers, each encoding the same diamond through its own encoder."""
    source = {"neg": {"class_type": "CLIPTextEncode", "inputs": {"text": "bad"}}}
    source.update(_diamond())
    for index in range(count):
        source[f"e{index}"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": ["dr", 0]},
        }
        source[f"s{index}"] = {
            "class_type": "KSampler",
            "inputs": {"positive": [f"e{index}", 0], "negative": ["neg", 0]},
        }
    return source


def _distinct_diamond_samplers(count: int, chain: int = 60) -> dict:
    """``count`` samplers, each with its own 590-node diamond, sharing a latent chain.

    Real work exceeds the shared 20,000-visit total while the graph stays under
    the node ceiling: every candidate re-walks the (unmemoized) latent chain.
    """
    source = {
        "neg": {"class_type": "CLIPTextEncode", "inputs": {"text": "bad"}},
        "z0": {"class_type": "EmptyLatentImage", "inputs": {}},
    }
    for index in range(1, chain + 1):
        source[f"z{index}"] = {
            "class_type": "Reroute",
            "inputs": {"value": [f"z{index - 1}", 0]},
        }
    for index in range(count):
        prefix = f"d{index}_"
        source.update(_diamond(prefix, leaves=565))
        source[f"e{index}"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": [f"{prefix}r", 0]},
        }
        source[f"s{index}"] = {
            "class_type": "KSampler",
            "inputs": {
                "positive": [f"e{index}", 0],
                "negative": ["neg", 0],
                "latent_image": [f"z{chain}", 0],
            },
        }
    return source


def _depth_straddling_samplers() -> dict:
    """A at depth 9 below its sampler, B four Reroutes deeper on the same chain."""
    source = {"p": {"class_type": "PrimitiveString", "inputs": {"value": "cat"}}}
    previous = "p"
    for index in range(6):
        source[f"c{index}"] = {
            "class_type": "Reroute",
            "inputs": {"value": [previous, 0]},
        }
        previous = f"c{index}"
    source["encA"] = {"class_type": "CLIPTextEncode", "inputs": {"text": [previous, 0]}}
    source["A"] = {
        "class_type": "KSampler",
        "inputs": {"positive": ["encA", 0], "negative": ["encA", 0]},
    }
    for index in range(4):
        source[f"b{index}"] = {
            "class_type": "Reroute",
            "inputs": {"value": [previous, 0]},
        }
        previous = f"b{index}"
    source["encB"] = {"class_type": "CLIPTextEncode", "inputs": {"text": [previous, 0]}}
    source["B"] = {
        "class_type": "KSampler",
        "inputs": {"positive": ["encB", 0], "negative": ["encB", 0]},
    }
    return source


def _nested_shared_chain_samplers() -> dict:
    """A, B and C share one chain; each later sampler hangs below a memo hit."""
    source = {"p": {"class_type": "PrimitiveString", "inputs": {"value": "cat"}}}

    def chain(start: str, prefix: str, count: int) -> str:
        previous = start
        for index in range(count):
            source[f"{prefix}{index}"] = {
                "class_type": "Reroute",
                "inputs": {"value": [previous, 0]},
            }
            previous = f"{prefix}{index}"
        return previous

    def sampler(name: str, text_source: str) -> None:
        source[f"enc{name}"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": [text_source, 0]},
        }
        source[name] = {
            "class_type": "KSampler",
            "inputs": {"positive": [f"enc{name}", 0], "negative": [f"enc{name}", 0]},
        }

    top = chain("p", "c", 6)  # A memoizes c5 with height 6
    sampler("A", top)
    middle = chain(top, "r", 1)  # r0 is built over a memo hit on c5
    sampler("B", middle)
    sampler("C", chain(middle, "d", 3))  # C reaches r0 three Reroutes deeper
    return source


def _random_graph(rng: random.Random) -> dict:
    ids = [str(index) for index in range(rng.randrange(5, 60))]
    classes = (
        "StringConcatenate",
        "Text Concatenate",
        "Reroute",
        "PrimitiveString",
        "CLIPTextEncode",
        "KSampler",
        "PromptManager",
        "EmptyLatentImage",
    )

    def link():
        return [rng.choice(ids), 0]

    source = {}
    for node_id in ids:
        name = rng.choice(classes)
        if name == "StringConcatenate":
            inputs = {"string_a": link(), "string_b": rng.choice([link(), "x"])}
        elif name == "Text Concatenate":
            inputs = {"text_a": link(), "text_b": link(), "delimiter": ""}
        elif name == "Reroute":
            inputs = {"value": link()}
        elif name == "PrimitiveString":
            inputs = {"value": rng.choice(["a", " b "])}
        elif name == "CLIPTextEncode":
            inputs = {"text": rng.choice([link(), "p"])}
        elif name == "KSampler":
            inputs = {"positive": link(), "negative": link()}
            if rng.random() < 0.3:
                inputs["latent_image"] = link()
        elif name == "PromptManager":
            inputs = {"text": rng.choice(["q", link()]), "prepend_text": ""}
        else:
            inputs = {}
        source[node_id] = {"class_type": name, "inputs": inputs}
    return source


def _outcomes(result: dict) -> dict:
    items = result["candidates"] or [result]
    return {
        item["sampler_id"]: (item["state"], item["reason_code"], item["reason"])
        for item in items
    }


class _CountingInputs(dict):
    """Node inputs that count how often a resolver iterates (scans) their keys."""

    scans = 0

    def __iter__(self):
        type(self).scans += 1
        return super().__iter__()


class _CountingText(str):
    """A literal that tallies the characters a resolver strips from it."""

    stripped = 0

    def strip(self, *args):
        type(self).stripped += len(self)
        return super().strip(*args)


class TestResolverProfiles(unittest.TestCase):
    """Inspection bounds work, not graph size; the default profile is pinned."""

    def test_inspection_profile_resolves_reachable_branch_in_506_node_graph(self):
        expected = _vector("f03-default")
        self.assertGreater(len(_f03_metadata()["prompt"]), 500)
        result = resolve_prompt_pair(
            _f03_metadata(), profile=resolver.INSPECTION_PROFILE
        )
        self.assertEqual(result["state"], "resolved")
        self.assertIsNone(result["reason_code"])
        self.assertEqual(result["positive"]["text"], parity.POS)
        self.assertEqual(result["negative"]["text"], parity.NEG)
        self.assertEqual(expected["state"], "unresolved")  # ingestion baseline

    def test_default_profile_keeps_500_node_gate(self):
        for result in (
            resolve_prompt_pair(_f03_metadata()),
            resolve_prompt_pair(_f03_metadata(), profile=resolver.DEFAULT_PROFILE),
        ):
            self.assertEqual(result["state"], "unresolved")
            self.assertEqual(result["reason"], "Resolution limit reached")
            self.assertIsNone(result["reason_code"])
            self.assertNotIn("node", result)

    def test_total_visit_budget_is_shared_across_candidates(self):
        source = _distinct_diamond_samplers(33)
        self.assertEqual(resolver.INSPECTION_PROFILE.total_visits, 20_000)
        self.assertLess(len(source), resolver.INSPECTION_PROFILE.node_ceiling)
        result = resolve_prompt_pair(
            {"prompt": source}, profile=resolver.INSPECTION_PROFILE
        )
        # While selection is off (until PR3) many samplers are always ambiguous;
        # PR3 must add "work-limit never upgrades to resolved" once one is chosen.
        self.assertNotEqual(result["state"], "resolved")
        self.assertEqual(result["state"], "ambiguous")
        candidates = result["candidates"]
        self.assertEqual(len(candidates), 33)
        self.assertEqual(candidates[0]["state"], "resolved")
        codes = [candidate["reason_code"] for candidate in candidates]
        first_limited = codes.index("work-limit")
        self.assertGreater(first_limited, 0)
        self.assertTrue(all(code == "work-limit" for code in codes[first_limited:]))
        self.assertTrue(
            all(c["state"] == "unresolved" for c in candidates[first_limited:])
        )
        # The default profile keeps its historical gate on the same graph.
        self.assertEqual(
            resolve_prompt_pair({"prompt": source})["reason"],
            "Resolution limit reached",
        )

    def test_memo_hit_respects_the_depth_left_at_the_reusing_candidate(self):
        source = _depth_straddling_samplers()
        reverse = dict(reversed(list(source.items())))
        profile = resolver.INSPECTION_PROFILE
        forward = _outcomes(
            resolve_prompt_pair({"prompt": source}, profile=profile, max_depth=10)
        )
        backward = _outcomes(
            resolve_prompt_pair({"prompt": reverse}, profile=profile, max_depth=10)
        )
        self.assertEqual(forward, backward)
        self.assertEqual(forward["A"][:2], ("resolved", None))
        self.assertEqual(forward["B"][:2], ("unresolved", "depth-limit"))
        anchored = resolve_prompt_pair(
            {"prompt": source}, profile=profile, max_depth=10, output_node_id="B"
        )
        self.assertEqual(
            (anchored["state"], anchored["reason_code"]), ("unresolved", "depth-limit")
        )

    def test_memo_height_propagates_through_a_nested_memo_hit(self):
        source = _nested_shared_chain_samplers()
        reverse = dict(reversed(list(source.items())))
        profile = resolver.INSPECTION_PROFILE
        for graph in (source, reverse):
            outcomes = _outcomes(
                resolve_prompt_pair({"prompt": graph}, profile=profile, max_depth=12)
            )
            self.assertEqual(outcomes["C"][:2], ("unresolved", "depth-limit"))
        anchored = resolve_prompt_pair(
            {"prompt": source}, profile=profile, max_depth=12, output_node_id="C"
        )
        self.assertEqual(
            (anchored["state"], anchored["reason_code"]), ("unresolved", "depth-limit")
        )

    def test_candidate_outcomes_match_anchored_and_ignore_node_order(self):
        profile = resolver.INSPECTION_PROFILE
        limits = ({}, {"max_depth": 4}, {"max_depth": 8}, {"max_visits": 6})
        compared = 0
        for seed in range(240, 246):  # 6,000 deterministic graphs, about 1.5 s
            rng = random.Random(seed)
            for _ in range(1_000):
                source = _random_graph(rng)
                kwargs = rng.choice(limits)
                base = _outcomes(
                    resolve_prompt_pair({"prompt": source}, profile=profile, **kwargs)
                )
                items = list(source.items())
                rng.shuffle(items)
                shuffled = _outcomes(
                    resolve_prompt_pair(
                        {"prompt": dict(items)}, profile=profile, **kwargs
                    )
                )
                self.assertEqual(base, shuffled, (seed, source, kwargs))
                if len(base) < 2:
                    continue
                for sampler_id, outcome in base.items():
                    anchored = resolve_prompt_pair(
                        {"prompt": source},
                        profile=profile,
                        output_node_id=sampler_id,
                        **kwargs,
                    )
                    self.assertEqual(
                        _outcomes(anchored)[sampler_id],
                        outcome,
                        (seed, sampler_id, source, kwargs),
                    )
                    compared += 1
        self.assertGreater(compared, 1_000)

    def test_provenance_entry_budget_reports_the_path_limit(self):
        letters = "abcdefghijklmnopqrstuvwxyz"
        source = {
            "p": {"class_type": "PrimitiveString", "inputs": {"value": "cat"}},
            "neg": {"class_type": "CLIPTextEncode", "inputs": {"text": "bad"}},
        }
        previous = "p"
        for index in range(60):
            source[f"c{index}"] = {
                "class_type": "Reroute",
                "inputs": {"value": [previous, 0]},
            }
            previous = f"c{index}"
        for index in range(500):
            source[f"w{index}"] = {
                "class_type": "Text Concatenate",
                "inputs": {f"text_{c}": [previous, 0] for c in letters},
            }
            source[f"e{index}"] = {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": [f"w{index}", 0]},
            }
            source[f"s{index}"] = {
                "class_type": "KSampler",
                "inputs": {"positive": [f"e{index}", 0], "negative": ["neg", 0]},
            }
        result = resolve_prompt_pair(
            {"prompt": source}, profile=resolver.INSPECTION_PROFILE
        )
        reasons = [candidate["reason"] for candidate in result["candidates"]]
        self.assertEqual(result["candidates"][0]["state"], "resolved")
        first = reasons.index("Provenance path limit reached")
        self.assertGreater(first, 0)
        self.assertLessEqual(
            set(reasons[first:]),
            {"Provenance path limit reached", "Resolution visit limit reached"},
        )
        self.assertEqual(reasons[first], "Provenance path limit reached")
        self.assertEqual(
            {c["reason_code"] for c in result["candidates"][first:]}, {"work-limit"}
        )
        # A small entry budget pins the same reason on one candidate.
        small = replace(resolver.INSPECTION_PROFILE, max_path_entries=10)
        result = resolve_prompt_pair({"prompt": source}, profile=small)
        self.assertEqual(
            (result["candidates"][0]["reason"], result["candidates"][0]["reason_code"]),
            ("Provenance path limit reached", "work-limit"),
        )

    def test_shared_node_is_scanned_once_however_many_candidates_use_it(self):
        _CountingInputs.scans = 0
        fat = _CountingInputs({"text": "portrait"})
        fat.update({f"extra_{index}": 0 for index in range(20_000)})
        source = {
            "fat": {"class_type": "CLIPTextEncode", "inputs": fat},
            "neg": {"class_type": "CLIPTextEncode", "inputs": {"text": "bad"}},
        }
        for index in range(1_000):
            source[f"s{index}"] = {
                "class_type": "KSampler",
                "inputs": {"positive": ["fat", 0], "negative": ["neg", 0]},
            }
        result = resolve_prompt_pair(
            {"prompt": source}, profile=resolver.INSPECTION_PROFILE
        )
        states = {candidate["state"] for candidate in result["candidates"]}
        self.assertEqual(states, {"resolved"})
        # Work is O(nodes), not O(candidates x node size).
        self.assertLessEqual(_CountingInputs.scans, 2)

    def test_stripped_concatenation_text_is_charged_to_a_work_budget(self):
        _CountingText.stripped = 0
        letters = "abcdefghijklmnopqrstuvwxyz"
        hub = _CountingText(" " * 100_000)
        source = graph()
        source["hub"] = {"class_type": "PrimitiveString", "inputs": {"value": hub}}
        for leaf in range(300):
            source[f"l{leaf}"] = {
                "class_type": "Text Concatenate",
                "inputs": {f"text_{c}": ["hub", 0] for c in letters},
            }
        for middle in range(12):
            source[f"m{middle}"] = {
                "class_type": "Text Concatenate",
                "inputs": {
                    f"text_{letters[i]}": [f"l{middle * 25 + i}", 0] for i in range(25)
                },
            }
        source["root"] = {
            "class_type": "Text Concatenate",
            "inputs": {f"text_{letters[i]}": [f"m{i}", 0] for i in range(12)},
        }
        source["1"]["inputs"]["text"] = ["root", 0]
        result = resolve_prompt_pair(source, profile=resolver.INSPECTION_PROFILE)
        self.assertEqual(
            (result["state"], result["reason_code"]), ("unresolved", "work-limit")
        )
        self.assertLessEqual(
            _CountingText.stripped, resolver.INSPECTION_PROFILE.max_scanned_units
        )

    def test_per_candidate_visit_budget_resets_for_each_sampler(self):
        source = _diamond_samplers(3)
        result = resolve_prompt_pair(
            {"prompt": source}, profile=resolver.INSPECTION_PROFILE, max_visits=700
        )
        self.assertEqual(
            [candidate["state"] for candidate in result["candidates"]],
            ["resolved"] * 3,
        )
        result = resolve_prompt_pair(
            {"prompt": source}, profile=resolver.INSPECTION_PROFILE, max_visits=300
        )
        self.assertEqual(
            [candidate["reason_code"] for candidate in result["candidates"]],
            ["work-limit"] * 3,
        )

    def test_node_ceiling_reports_graph_ceiling(self):
        source = graph()
        for index in range(20_001 - len(source)):
            source[f"p{index}"] = {"class_type": "PrimitiveInt", "inputs": {"value": 1}}
        self.assertEqual(len(source), 20_001)
        result = resolve_prompt_pair(
            {"prompt": source}, profile=resolver.INSPECTION_PROFILE
        )
        self.assertEqual(result["reason_code"], "graph-ceiling")
        self.assertNotEqual(result["state"], "resolved")
        del source["p0"]
        result = resolve_prompt_pair(
            {"prompt": source}, profile=resolver.INSPECTION_PROFILE
        )
        self.assertEqual(result["state"], "resolved")

    def test_prompt_graph_without_supported_sampler_is_unresolved_not_absent(self):
        # f17 with its sampler renamed to a class the inspector does not know.
        source = parity.build("f17")["prompt"]
        source["13"]["class_type"] = "FutureSampler"
        metadata = {"prompt": source}
        result = resolve_prompt_pair(metadata, profile=resolver.INSPECTION_PROFILE)
        self.assertEqual(
            (result["state"], result["reason_code"]), ("unresolved", "no-sampler")
        )
        self.assertIn("FutureSampler", result["reason"])
        self.assertIsNone(result["positive"])
        default = resolve_prompt_pair(metadata)
        self.assertEqual(default["state"], "absent")
        self.assertEqual(default["reason"], "No supported sampler pair found")
        self.assertIsNone(default["reason_code"])

    def test_no_sampler_reason_names_at_most_three_sampler_like_classes(self):
        source = {
            str(index): {
                "class_type": f"FutureSampler{index % 5}",
                "inputs": {"latent_image": ["x", 0]},
            }
            for index in range(10)
        }
        result = resolve_prompt_pair(
            {"prompt": source}, profile=resolver.INSPECTION_PROFILE
        )
        self.assertEqual(result["reason_code"], "no-sampler")
        self.assertEqual(
            result["reason"],
            "No supported sampler pair found "
            "(saw FutureSampler0, FutureSampler1, FutureSampler2)",
        )

    def test_absent_only_without_prompt_or_parameters(self):
        for value in (None, [], "raw text", {}, {"parameters": None}):
            with self.subTest(value=value):
                result = resolve_prompt_pair(value, profile=resolver.INSPECTION_PROFILE)
                self.assertEqual(result["state"], "absent")
                self.assertIsNone(result["positive"])
        note = {"1": {"class_type": "Note", "inputs": {"text": "hello"}}}
        for value in (
            {"prompt": note},
            note,
            {"prompt": {}},
            {"prompt": []},
            {"prompt": {"1": None}},
            {"parameters": "unstructured arbitrary text"},
        ):
            with self.subTest(value=value):
                result = resolve_prompt_pair(value, profile=resolver.INSPECTION_PROFILE)
                self.assertEqual(result["state"], "unresolved")
                self.assertIsNotNone(result["reason_code"])
        self.assertEqual(resolve_prompt_pair({"prompt": note})["state"], "absent")
        # A pair-level "absent" names the missing negative; it always has a positive.
        source = graph()
        del source["3"]["inputs"]["negative"]
        result = resolve_prompt_pair(source, profile=resolver.INSPECTION_PROFILE)
        self.assertEqual(result["state"], "absent")
        self.assertEqual(result["positive"]["text"], "portrait")

    def test_existing_limits_map_to_reason_codes(self):
        cases = (
            ({"max_visits": 1}, "work-limit", "Resolution visit limit reached"),
            ({"max_depth": 0}, "depth-limit", None),
            ({"max_depth": 1}, "depth-limit", "Resolution depth limit reached"),
            ({"max_text_length": 3}, "text-limit", None),
        )
        for limits, code, reason in cases:
            with self.subTest(limits=limits):
                default = resolve_prompt_pair(graph(), **limits)
                self.assertEqual(default["state"], "unresolved")
                self.assertIsNone(default["reason_code"])
                result = resolve_prompt_pair(
                    graph(), profile=resolver.INSPECTION_PROFILE, **limits
                )
                self.assertEqual(result["state"], "unresolved")
                self.assertEqual(result["reason_code"], code)
                if reason:
                    self.assertEqual(result["reason"], reason)
        result = resolve_prompt_pair(
            {"parameters": "x" * 20},
            profile=resolver.INSPECTION_PROFILE,
            max_text_length=10,
        )
        self.assertEqual(result["reason_code"], "text-limit")

    def test_unsupported_node_names_the_blocking_node(self):
        source = graph()
        source["1"]["class_type"] = "UnknownEncoder"
        result = resolve_prompt_pair(source, profile=resolver.INSPECTION_PROFILE)
        self.assertEqual(result["reason_code"], "unsupported-node")
        self.assertEqual(result["node"], {"id": "1", "class_type": "UnknownEncoder"})
        default = resolve_prompt_pair(source)
        self.assertIsNone(default["reason_code"])
        self.assertNotIn("node", default)
        self.assertEqual(default["reason"], result["reason"])

    def test_default_profile_emits_only_a_null_reason_code_beyond_todays_keys(self):
        source = graph()
        source["4"] = {"class_type": "KSampler", "inputs": source["3"]["inputs"]}
        for value in (graph(), source, {"parameters": "a\nSteps: 1, Sampler: x"}):
            result = resolve_prompt_pair(value)
            self.assertEqual(
                set(result),
                {
                    "state",
                    "positive",
                    "negative",
                    "sampler_id",
                    "reason",
                    "candidates",
                    "reason_code",
                },
            )
            self.assertIsNone(result["reason_code"])
            for candidate in result["candidates"]:
                self.assertNotIn("node", candidate)
                self.assertIsNone(candidate["reason_code"])

    def test_diamond_provenance_paths_are_bounded_in_inspection(self):
        source = graph()
        source["8"] = {"class_type": "PrimitiveString", "inputs": {"value": ""}}
        for index in range(9, 49):
            source[str(index)] = {
                "class_type": "StringConcatenate",
                "inputs": {
                    "string_a": [str(index - 1), 0],
                    "string_b": [str(index - 1), 0],
                    "delimiter": "",
                },
            }
        source["1"]["inputs"]["text"] = ["48", 0]
        result = resolve_prompt_pair(source, profile=resolver.INSPECTION_PROFILE)
        self.assertEqual(result["state"], "unresolved")
        self.assertEqual(result["reason_code"], "work-limit")
        # A memo hit charges its unfolded cost: the 2^k unfolded visits exhaust
        # the 2,000 allowance (per-entry paths never outgrow the allowance).
        self.assertEqual(result["reason"], "Resolution visit limit reached")


def _guided(guider: dict = None) -> dict:
    """``f07b`` (SamplerCustomAdvanced + CFGGuider), optionally another guider."""
    source = parity.build("f07b")["prompt"]
    if guider is not None:
        source["21"] = guider
    return source


def _basic_guided() -> dict:
    """``f17`` with its BasicGuider reading the CLIPTextEncode directly."""
    source = parity.build("f17")["prompt"]
    source["22"]["inputs"]["conditioning"] = ["6", 0]
    del source["26"]
    return source


def _inspect(source: dict, **options) -> dict:
    return resolve_prompt_pair(
        {"prompt": source}, profile=resolver.INSPECTION_PROFILE, **options
    )


class TestGuiderSamplers(unittest.TestCase):
    """SamplerCustomAdvanced follows its guider through the semantics table."""

    def test_sampler_custom_advanced_with_cfg_guider_resolves(self):
        result = _inspect(_guided())
        self.assertEqual(result["state"], "resolved")
        self.assertIsNone(result["reason_code"])
        self.assertEqual(result["sampler_id"], "3")
        self.assertEqual(result["positive"]["text"], parity.POS)
        self.assertEqual(result["negative"]["text"], parity.NEG)
        provenance = result["positive"]["provenance"]
        self.assertEqual(
            (provenance["sampler_id"], provenance["guider_id"]), ("3", "21")
        )
        self.assertEqual(provenance["node_id"], "6")
        self.assertEqual(provenance["path"], [{"node_id": "6", "output_slot": 0}])
        self.assertEqual(result["negative"]["provenance"]["node_id"], "7")
        # The saver anchor reaches the same sampler.
        anchored = _inspect(_guided(), output_node_id="9")
        self.assertEqual(anchored, result)

    def test_basic_guider_negative_is_absent_by_design(self):
        from utils.prompt_inspector import inspect_prompt_evidence

        result = _inspect(_basic_guided())
        self.assertEqual(result["state"], "absent")
        self.assertEqual(result["reason"], "Guider has no negative input")
        self.assertIsNone(result["reason_code"])
        self.assertEqual(result["positive"]["text"], parity.POS)
        self.assertIsNone(result["negative"])
        evidence = inspect_prompt_evidence({"prompt": _basic_guided()})
        self.assertEqual(evidence["prompts"]["positive"]["status"], "resolved")
        self.assertEqual(evidence["prompts"]["positive"]["text"], parity.POS)
        self.assertEqual(evidence["prompts"]["negative"]["status"], "absent")
        self.assertEqual(
            evidence["resolution"]["reason"], "Guider has no negative input"
        )

    def test_basic_guider_empty_positive_is_not_absent(self):
        source = _basic_guided()
        source["6"]["inputs"]["text"] = "  "
        result = _inspect(source)
        self.assertEqual(result["state"], "unresolved")
        self.assertEqual(result["reason"], "Positive prompt is empty")

    def test_dual_cfg_guider_is_unsupported_node(self):
        dual = {
            "class_type": "DualCFGGuider",
            "inputs": {
                "model": ["4", 0],
                "cond1": ["6", 0],
                "cond2": ["6", 0],
                "negative": ["7", 0],
                "cfg_conds": 8.0,
                "cfg_cond2_negative": 8.0,
            },
        }
        result = _inspect(_guided(dual))
        self.assertEqual(
            (result["state"], result["reason_code"], result["node"]["class_type"]),
            ("unresolved", "unsupported-node", "DualCFGGuider"),
        )
        self.assertEqual(result["node"], {"id": "21", "class_type": "DualCFGGuider"})
        self.assertEqual(result["sampler_id"], "3")
        self.assertIsNone(result["positive"])

    def test_unsupported_node_names_first_blocking_node(self):
        source = _guided()
        source["30"] = {
            "class_type": "FutureConditioner",
            "inputs": {"conditioning": ["6", 0]},
        }
        source["31"] = {
            "class_type": "OtherConditioner",
            "inputs": {"conditioning": ["7", 0]},
        }
        source["21"]["inputs"]["positive"] = ["30", 0]
        source["21"]["inputs"]["negative"] = ["31", 0]
        result = _inspect(source)
        self.assertEqual(result["reason_code"], "unsupported-node")
        self.assertEqual(
            result["node"], {"id": "30", "class_type": "FutureConditioner"}
        )
        # With the positive fixed, the negative's node is the first blocker.
        source["21"]["inputs"]["positive"] = ["6", 0]
        result = _inspect(source)
        self.assertEqual(result["node"], {"id": "31", "class_type": "OtherConditioner"})
        self.assertEqual(result["positive"]["text"], parity.POS)

    def test_default_profile_ignores_semantics(self):
        for source in (_guided(), _basic_guided()):
            result = resolve_prompt_pair({"prompt": source})
            self.assertIn(result["state"], ("unresolved", "absent"))
            self.assertEqual(result["reason"], "No supported sampler pair found")
            self.assertIsNone(result["reason_code"])
            self.assertIsNone(result["positive"])
        # Without semantics the inspection bounds alone do not follow guiders.
        plain = replace(resolver.INSPECTION_PROFILE, semantics=False)
        result = resolve_prompt_pair({"prompt": _guided()}, profile=plain)
        self.assertEqual(result["reason_code"], "no-sampler")

    def test_guider_reached_through_a_reroute(self):
        source = _guided()
        source["40"] = {"class_type": "Reroute", "inputs": {"value": ["21", 0]}}
        source["3"]["inputs"]["guider"] = ["40", 0]
        result = _inspect(source)
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(result["positive"]["provenance"]["guider_id"], "21")

    def test_malformed_guider_links_and_slots_never_guess(self):
        cases = {
            "missing": None,
            "literal": "CFGGuider",
            "dangling": ["99", 0],
            "slot": ["21", 1],
        }
        for label, value in cases.items():
            with self.subTest(label=label):
                source = _guided()
                if value is None:
                    del source["3"]["inputs"]["guider"]
                else:
                    source["3"]["inputs"]["guider"] = value
                result = _inspect(source)
                self.assertEqual(result["state"], "unresolved")
                self.assertIsNone(result["positive"])
                expected = "unsupported-node" if label == "slot" else "malformed"
                self.assertEqual(result["reason_code"], expected)
        source = _guided()
        source["21"]["inputs"]["model"] = ["21", 0]
        source["40"] = {"class_type": "Reroute", "inputs": {"value": ["40", 0]}}
        source["3"]["inputs"]["guider"] = ["40", 0]
        self.assertEqual(_inspect(source)["reason"], "Cyclic guider ancestry")
        source["40"]["inputs"] = {"value": ["21", 0], "input": ["21", 0]}
        result = _inspect(source)
        self.assertEqual(result["reason"], "Reroute input is not uniquely identifiable")
        self.assertEqual(result["reason_code"], "malformed")

    def test_guider_hops_draw_from_the_work_budgets(self):
        self.assertEqual(_inspect(_guided(), max_visits=2)["reason_code"], "work-limit")
        self.assertEqual(_inspect(_guided())["state"], "resolved")
        source = _guided()
        previous = "21"
        for index in range(70):
            source[f"r{index}"] = {
                "class_type": "Reroute",
                "inputs": {"value": [previous, 0]},
            }
            previous = f"r{index}"
        source["3"]["inputs"]["guider"] = [previous, 0]
        self.assertEqual(_inspect(source)["reason_code"], "depth-limit")

    def test_custom_advanced_upstream_of_a_sampler_is_multistage(self):
        source = _guided()
        source["41"] = {
            "class_type": "KSampler",
            "inputs": {
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["3", 0],
            },
        }
        source["8"]["inputs"]["samples"] = ["41", 0]
        result = _inspect(source, output_node_id="9")
        self.assertEqual(result["sampler_id"], "41")
        self.assertEqual(result["state"], "unresolved")
        self.assertEqual(result["reason"], "Multistage sampler ancestry is ambiguous")
        unanchored = _inspect(source)
        self.assertEqual(unanchored["state"], "ambiguous")
        self.assertEqual(
            sorted(item["sampler_id"] for item in unanchored["candidates"]),
            ["3", "41"],
        )


WOLF = "a grey wolf in fog"


def _encoded(text: str) -> dict:
    return {"class_type": "CLIPTextEncode", "inputs": {"text": text, "clip": ["4", 1]}}


def _with_positive(node: dict, extra: dict = None) -> dict:
    """``f02``'s KSampler graph whose positive input reads ``node`` (as node 6)."""
    source = parity.build("f02")["prompt"]
    source["6"] = node
    source.update(extra or {})
    return source


def _if_else(condition, on_true, on_false) -> dict:
    return {
        "class_type": "easy ifElse",
        "inputs": {"boolean": condition, "on_true": on_true, "on_false": on_false},
    }


def _image_exists() -> dict:
    return {"class_type": "ImageExists", "inputs": {"image": ["13", 0]}}


def _sides(result: dict) -> tuple:
    return tuple(
        result[role]["text"] if isinstance(result[role], dict) else None
        for role in ("positive", "negative")
    )


class TestConditioningSemantics(unittest.TestCase):
    """Pass-throughs, zeroed conditioning, selectors and composites (inspection)."""

    def test_flux_guidance_passthrough_resolves(self):
        result = _inspect(parity.build("f06c")["prompt"])
        self.assertEqual(result["state"], "resolved")
        self.assertIsNone(result["reason_code"])
        self.assertEqual(_sides(result), (parity.POS, parity.NEG))
        provenance = result["positive"]["provenance"]
        self.assertEqual(
            provenance["path"],
            [{"node_id": "6", "output_slot": 0}, {"node_id": "10", "output_slot": 0}],
        )
        self.assertEqual(provenance["text_source"], "graph-inferred")
        # f17: BasicGuider <- FluxGuidance <- CLIPTextEncode, negative absent.
        guided = _inspect(parity.build("f17")["prompt"])
        self.assertEqual(guided["state"], "absent")
        self.assertEqual(guided["reason"], "Guider has no negative input")
        self.assertEqual(guided["positive"]["text"], parity.POS)

    def test_controlnet_apply_advanced_maps_slots(self):
        source = parity.build("f06e")["prompt"]
        result = _inspect(source)
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(_sides(result), (parity.POS, parity.NEG))
        self.assertEqual(
            result["negative"]["provenance"]["path"],
            [{"node_id": "7", "output_slot": 1}, {"node_id": "11", "output_slot": 0}],
        )
        # Slot 0 of the negative's node is its positive output, never the negative.
        source["3"]["inputs"]["negative"] = ["7", 0]
        self.assertEqual(_sides(_inspect(source)), (parity.POS, parity.POS))
        # An output slot the table does not list is unsupported, named.
        source["3"]["inputs"]["negative"] = ["7", 2]
        result = _inspect(source)
        self.assertEqual(result["reason_code"], "unsupported-node")
        self.assertEqual(
            result["node"], {"id": "7", "class_type": "ControlNetApplyAdvanced"}
        )

    def test_reference_latent_and_set_area_pass_through(self):
        chain = [
            ("ReferenceLatent", {"latent": ["5", 0]}),
            ("ConditioningSetArea", {"width": 64, "height": 64, "x": 0, "y": 0}),
            ("ConditioningSetAreaPercentage", {"width": 1.0, "height": 1.0}),
            ("ConditioningSetAreaStrength", {"strength": 1.0}),
            ("ConditioningSetMask", {"mask": ["40", 0], "strength": 1.0}),
            ("ConditioningSetTimestepRange", {"start": 0.0, "end": 1.0}),
            ("ControlNetApply", {"control_net": ["41", 0], "strength": 1.0}),
        ]
        extra, previous = {"10": _encoded(parity.POS)}, "10"
        for index, (name, inputs) in enumerate(chain):
            node_id = f"2{index}"
            extra[node_id] = {
                "class_type": name,
                "inputs": {**inputs, "conditioning": [previous, 0]},
            }
            previous = node_id
        source = _with_positive(extra.pop(previous), extra)
        result = _inspect(source)
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(_sides(result), (parity.POS, parity.NEG))
        path = result["positive"]["provenance"]["path"]
        self.assertEqual(
            [entry["node_id"] for entry in path],
            ["6", "25", "24", "23", "22", "21", "20", "10"],
        )

    def test_inpaint_model_conditioning_slots(self):
        inpaint = {
            "class_type": "InpaintModelConditioning",
            "inputs": {
                "positive": ["10", 0],
                "negative": ["11", 0],
                "vae": ["4", 2],
                "pixels": ["13", 0],
                "mask": ["14", 0],
                "noise_mask": True,
            },
        }
        extra = {"10": _encoded(parity.POS), "11": _encoded(parity.NEG)}
        source = _with_positive(inpaint, extra)
        source["3"]["inputs"]["negative"] = ["6", 1]
        result = _inspect(source)
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(_sides(result), (parity.POS, parity.NEG))
        self.assertEqual(result["positive"]["provenance"]["output_slot"], 0)
        self.assertEqual(result["negative"]["provenance"]["output_slot"], 1)

    def test_zeroed_negative_is_explicit_empty_with_distinct_source(self):
        from utils.prompt_inspector import inspect_prompt_evidence

        source = parity.build("f23")["prompt"]
        result = _inspect(source)
        self.assertEqual(result["state"], "resolved")
        negative = result["negative"]
        self.assertEqual((negative["text"], negative["storage_text"]), ("", ""))
        self.assertEqual(negative["provenance"]["text_source"], "zeroed-conditioning")
        self.assertEqual(
            negative["provenance"]["path"], [{"node_id": "7", "output_slot": 0}]
        )
        self.assertEqual(result["positive"]["text"], parity.POS)
        self.assertEqual(
            result["positive"]["provenance"]["text_source"], "graph-inferred"
        )
        evidence = inspect_prompt_evidence({"prompt": source})["prompts"]
        self.assertEqual(
            evidence["negative"],
            {"status": "resolved", "text": "", "source": "zeroed-conditioning"},
        )
        # The source survives pass-throughs and reroutes in front of it.
        source["30"] = {
            "class_type": "FluxGuidance",
            "inputs": {"guidance": 3.5, "conditioning": ["7", 0]},
        }
        source["31"] = {"class_type": "Reroute", "inputs": {"value": ["30", 0]}}
        source["3"]["inputs"]["negative"] = ["31", 0]
        negative = _inspect(source)["negative"]
        self.assertEqual(negative["text"], "")
        self.assertEqual(negative["provenance"]["text_source"], "zeroed-conditioning")
        # A zeroed positive is an empty positive, never a resolved prompt.
        source["3"]["inputs"]["positive"] = ["7", 0]
        result = _inspect(source)
        self.assertEqual(result["state"], "unresolved")
        self.assertEqual(result["reason"], "Positive prompt is empty")

    def test_if_else_literal_boolean_selects_branch(self):
        source = parity.build("f20")["prompt"]
        result = _inspect(source)
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(_sides(result), (parity.POS, parity.NEG))
        self.assertEqual(
            [entry["node_id"] for entry in result["positive"]["provenance"]["path"]],
            ["6", "10"],
        )
        source["6"]["inputs"]["boolean"] = False
        self.assertEqual(_inspect(source)["positive"]["text"], WOLF)
        # The branch that is not taken is never traced.
        source["6"]["inputs"]["on_true"] = ["99", 0]
        self.assertEqual(_inspect(source)["state"], "resolved")
        for literal in (1, "true", None):
            with self.subTest(literal=literal):
                source["6"]["inputs"]["boolean"] = literal
                result = _inspect(source)
                self.assertEqual(result["state"], "unresolved")
                self.assertEqual(result["reason_code"], "malformed")
                self.assertIsNone(result["positive"])

    def test_if_else_linked_boolean_converging_branches_resolve(self):
        guidance = {
            "class_type": "FluxGuidance",
            "inputs": {"guidance": 3.5, "conditioning": ["10", 0]},
        }
        extra = {"10": _encoded(parity.POS), "11": guidance, "12": _image_exists()}
        source = _with_positive(_if_else(["12", 1], ["11", 0], ["10", 0]), extra)
        result = _inspect(source)
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(_sides(result), (parity.POS, parity.NEG))
        self.assertEqual(result["sampler_id"], "3")
        self.assertEqual(result["candidates"], [])
        # Two encoders with the same text converge as well.
        source["11"] = _encoded(parity.POS)
        self.assertEqual(_inspect(source)["state"], "resolved")

    def test_if_else_linked_boolean_diverging_branches_are_ambiguous_with_candidates(
        self,
    ):
        extra = {
            "10": _encoded(parity.POS),
            "11": _encoded(WOLF),
            "12": _image_exists(),
        }
        source = _with_positive(_if_else(["12", 1], ["10", 0], ["11", 0]), extra)
        result = _inspect(source)
        self.assertEqual(result["state"], "ambiguous")
        self.assertEqual(result["sampler_id"], "3")
        self.assertIsNone(result["positive"])
        self.assertIsNone(result["negative"])
        candidates = result["candidates"]
        self.assertEqual([item["state"] for item in candidates], ["resolved"] * 2)
        self.assertEqual(
            [_sides(item) for item in candidates],
            [(parity.POS, parity.NEG), (WOLF, parity.NEG)],
        )
        self.assertEqual(
            [
                item["positive"]["provenance"]["path"][-1]["node_id"]
                for item in candidates
            ],
            ["10", "11"],
        )
        # One selector read by both roles is one runtime choice, not four pairs.
        source["3"]["inputs"]["negative"] = ["6", 0]
        candidates = _inspect(source)["candidates"]
        self.assertEqual(
            [_sides(item) for item in candidates],
            [(parity.POS, parity.POS), (WOLF, WOLF)],
        )
        # Independent selectors on each role multiply.
        source["7"] = _if_else(["16", 1], ["10", 0], ["14", 0])
        source["14"] = _encoded(parity.NEG)
        source["16"] = _image_exists()
        source["3"]["inputs"]["negative"] = ["7", 0]
        candidates = _inspect(source)["candidates"]
        self.assertEqual(len(candidates), 4)
        self.assertEqual(len({_sides(item) for item in candidates}), 4)

    def test_selectors_sharing_one_condition_choose_together(self):
        extra = {
            "10": _encoded("pos A"),
            "11": _encoded("pos B"),
            "14": _encoded("neg A"),
            "15": _encoded("neg B"),
            "12": _image_exists(),
            "16": _image_exists(),
        }
        source = _with_positive(_if_else(["12", 1], ["10", 0], ["11", 0]), extra)
        source["7"] = _if_else(["12", 1], ["14", 0], ["15", 0])
        result = _inspect(source)
        self.assertEqual(result["state"], "ambiguous")
        # One runtime value drives both selectors: never "pos A" with "neg B".
        self.assertEqual(
            [_sides(item) for item in result["candidates"]],
            [("pos A", "neg A"), ("pos B", "neg B")],
        )
        # Another output slot of the same node is another value.
        source["7"]["inputs"]["boolean"] = ["12", 0]
        self.assertEqual(len(_inspect(source)["candidates"]), 4)
        # Two independent conditions give the full cross product.
        source["7"]["inputs"]["boolean"] = ["16", 1]
        candidates = _inspect(source)["candidates"]
        self.assertEqual(
            sorted(_sides(item) for item in candidates),
            [
                ("pos A", "neg A"),
                ("pos A", "neg B"),
                ("pos B", "neg A"),
                ("pos B", "neg B"),
            ],
        )

    def test_diverging_branch_that_cannot_be_traced_is_never_guessed(self):
        extra = {
            "10": _encoded(parity.POS),
            "11": {"class_type": "FutureEncoder", "inputs": {"text": WOLF}},
            "12": _image_exists(),
        }
        source = _with_positive(_if_else(["12", 1], ["10", 0], ["11", 0]), extra)
        result = _inspect(source)
        self.assertEqual(result["state"], "unresolved")
        self.assertEqual(result["node"], {"id": "11", "class_type": "FutureEncoder"})
        self.assertIsNone(result["positive"])

    def test_selector_branches_are_bounded(self):
        extra, previous = {"12": _image_exists()}, None
        for index in range(6):
            extra[f"a{index}"] = _encoded(f"left {index}")
            extra[f"b{index}"] = _encoded(f"right {index}")
            # Each selector reads its own condition: independent choices.
            extra[f"e{index}"] = _image_exists()
            extra[f"s{index}"] = _if_else(
                [f"e{index}", 1], [f"a{index}", 0], [f"b{index}", 0]
            )
            extra[f"c{index}"] = {
                "class_type": "ConditioningCombine",
                "inputs": {
                    "conditioning_1": [f"s{index}", 0],
                    "conditioning_2": [previous or f"s{index}", 0],
                },
            }
            previous = f"c{index}"
        source = _with_positive(
            {"class_type": "Reroute", "inputs": {"value": [previous, 0]}}, extra
        )
        result = _inspect(source)
        self.assertEqual(result["state"], "unresolved")
        self.assertEqual(result["reason_code"], "work-limit")
        self.assertEqual(result["candidates"], [])
        # Branch tracing draws from the per-candidate visit allowance too.
        small = _with_positive(
            _if_else(["12", 1], ["10", 0], ["11", 0]),
            {"10": _encoded(parity.POS), "11": _encoded(WOLF), "12": _image_exists()},
        )
        self.assertEqual(_inspect(small, max_visits=3)["reason_code"], "work-limit")

    def test_conditioning_combine_distinct_texts_is_composite(self):
        source = parity.build("f06d")["prompt"]
        result = _inspect(source)
        self.assertEqual(
            (result["state"], result["reason_code"]), ("unresolved", "composite")
        )
        self.assertEqual(
            result["node"], {"id": "6", "class_type": "ConditioningCombine"}
        )
        self.assertIsNone(result["positive"])
        for name, inputs in (
            ("ConditioningConcat", ("conditioning_to", "conditioning_from")),
            ("ConditioningAverage", ("conditioning_to", "conditioning_from")),
        ):
            with self.subTest(name=name):
                source["6"] = {
                    "class_type": name,
                    "inputs": {inputs[0]: ["10", 0], inputs[1]: ["11", 0]},
                }
                result = _inspect(source)
                self.assertEqual(result["reason_code"], "composite")
                self.assertEqual(result["node"], {"id": "6", "class_type": name})
        # A zeroed part is a different text too.
        source["12"] = {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["10", 0]},
        }
        source["6"]["inputs"]["conditioning_from"] = ["12", 0]
        self.assertEqual(_inspect(source)["reason_code"], "composite")

    def test_conditioning_combine_identical_texts_resolve(self):
        source = parity.build("f06d")["prompt"]
        source["11"]["inputs"]["text"] = parity.POS
        result = _inspect(source)
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(_sides(result), (parity.POS, parity.NEG))
        source["6"]["inputs"]["conditioning_2"] = ["10", 0]
        self.assertEqual(_inspect(source)["state"], "resolved")
        # A missing part is malformed, never "the other part".
        del source["6"]["inputs"]["conditioning_2"]
        result = _inspect(source)
        self.assertEqual(result["state"], "unresolved")
        self.assertEqual(result["reason_code"], "malformed")

    def test_selector_sources_and_string_branches(self):
        # Converging branches with different text sources name no single source.
        source = parity.build("f23")["prompt"]
        source["12"] = _image_exists()
        source["14"] = _encoded("")
        source["15"] = _if_else(["12", 1], ["7", 0], ["14", 0])
        source["3"]["inputs"]["negative"] = ["15", 0]
        negative = _inspect(source)["negative"]
        self.assertEqual(negative["text"], "")
        self.assertEqual(negative["provenance"]["text_source"], "graph-inferred")
        source["3"]["inputs"]["negative"] = ["7", 0]
        source["15"]["inputs"]["on_false"] = ["7", 0]
        source["3"]["inputs"]["negative"] = ["15", 0]
        negative = _inspect(source)["negative"]
        self.assertEqual(negative["provenance"]["text_source"], "zeroed-conditioning")
        # A selector also forwards string inputs, including literal text.
        source = parity.build("f02")["prompt"]
        source["12"] = _image_exists()
        source["20"] = _if_else(["12", 1], "same words", "same words")
        source["6"]["inputs"]["text"] = ["20", 0]
        result = _inspect(source)
        self.assertEqual(result["positive"]["text"], "same words")
        source["20"]["inputs"]["on_false"] = "other words"
        result = _inspect(source)
        self.assertEqual(result["state"], "ambiguous")
        self.assertEqual(
            [item["positive"]["text"] for item in result["candidates"]],
            ["same words", "other words"],
        )

    def test_default_profile_keeps_conditioning_nodes_unsupported(self):
        for name in ("f06c", "f06d", "f06e", "f20", "f23"):
            with self.subTest(fixture=name):
                result = resolve_prompt_pair({"prompt": parity.build(name)["prompt"]})
                self.assertEqual(result["state"], "unresolved")
                self.assertEqual(
                    result["reason"], "Unsupported node semantics or output slot"
                )
                self.assertIsNone(result["reason_code"])


def _primitive(value: str) -> dict:
    return {"class_type": "PrimitiveString", "inputs": {"value": value}}


def _text_encoded(link: list) -> dict:
    """A CLIPTextEncode (node 6 in ``_with_positive``) reading a linked string."""
    return {"class_type": "CLIPTextEncode", "inputs": {"text": link, "clip": ["4", 1]}}


def _string_positive(node: dict, extra: dict = None) -> dict:
    """``f02`` whose positive encoder reads string node 10 (``node``)."""
    return _with_positive(_text_encoded(["10", 0]), {"10": node, **(extra or {})})


def _multiline(text, flag) -> dict:
    return {
        "class_type": "StringConstantMultiline",
        "inputs": {"string": text, "strip_newlines": flag},
    }


def _join(count, delimiter, return_list=False, **strings) -> dict:
    return {
        "class_type": "JoinStringMulti",
        "inputs": {
            "inputcount": count,
            "delimiter": delimiter,
            "return_list": return_list,
            **strings,
        },
    }


def _sd3(clip_l: str, clip_g: str, t5xxl: str) -> dict:
    return {
        "class_type": "CLIPTextEncodeSD3",
        "inputs": {
            "clip": ["4", 1],
            "clip_l": clip_l,
            "clip_g": clip_g,
            "t5xxl": t5xxl,
            "empty_padding": "none",
        },
    }


def _positive(result: dict) -> tuple:
    return result["state"], _role_text_of(result, "positive"), result["reason_code"]


def _role_text_of(result: dict, role: str):
    side = result[role]
    return side["text"] if isinstance(side, dict) else None


F01B_POSITIVE = f"{parity.POS}, Scene 0: unique shot description soft winter light,"


class TestEncoderAndStringSemantics(unittest.TestCase):
    """Text encoders, string sources and runtime nodes (inspection, Task 2.4)."""

    def test_sdxl_equal_parts_resolve(self):
        result = _inspect(parity.build("f06a")["prompt"])
        self.assertEqual(result["state"], "resolved")
        self.assertIsNone(result["reason_code"])
        self.assertEqual(_sides(result), (parity.POS, parity.NEG))
        provenance = result["positive"]["provenance"]
        self.assertEqual(provenance["path"], [{"node_id": "6", "output_slot": 0}])
        self.assertEqual(provenance["text_source"], "graph-inferred")
        self.assertEqual(result["positive"]["storage_text"], parity.POS)
        # Linked parts are followed; equal texts still resolve, path deduplicated.
        source = parity.build("f06a")["prompt"]
        source["6"]["inputs"]["text_g"] = ["10", 0]
        source["6"]["inputs"]["text_l"] = ["10", 0]
        source["10"] = _primitive(parity.POS)
        result = _inspect(source)
        self.assertEqual(_sides(result), (parity.POS, parity.NEG))
        self.assertEqual(
            result["positive"]["provenance"]["path"],
            [{"node_id": "6", "output_slot": 0}, {"node_id": "10", "output_slot": 0}],
        )

    def test_sdxl_distinct_parts_are_composite(self):
        source = parity.build("f06a")["prompt"]
        source["6"]["inputs"]["text_l"] = "a second style"
        result = _inspect(source)
        self.assertEqual(result["state"], "unresolved")
        self.assertEqual(result["reason_code"], "composite")
        self.assertEqual(result["reason"], "Encoder parts have different texts")
        self.assertEqual(
            result["node"], {"id": "6", "class_type": "CLIPTextEncodeSDXL"}
        )
        self.assertIsNone(result["positive"])
        # Whitespace is text: emptiness is exact, never trimmed into agreement.
        source["6"]["inputs"]["text_l"] = " "
        self.assertEqual(_inspect(source)["reason_code"], "composite")
        source["6"]["inputs"]["text_l"] = parity.POS + " "
        self.assertEqual(_inspect(source)["reason_code"], "composite")

    def test_sdxl_single_nonempty_part_resolves(self):
        for empty in ("text_l", "text_g"):
            with self.subTest(empty=empty):
                source = parity.build("f06a")["prompt"]
                source["6"]["inputs"][empty] = ""
                result = _inspect(source)
                self.assertEqual(result["state"], "resolved")
                self.assertEqual(_sides(result), (parity.POS, parity.NEG))
                self.assertEqual(result["positive"]["storage_text"], parity.POS)
        # A linked empty part counts as empty; both paths are recorded.
        source = parity.build("f06a")["prompt"]
        source["6"]["inputs"]["text_l"] = ["10", 0]
        source["10"] = _primitive("")
        result = _inspect(source)
        self.assertEqual(_sides(result), (parity.POS, parity.NEG))
        self.assertEqual(
            result["positive"]["provenance"]["path"],
            [{"node_id": "6", "output_slot": 0}, {"node_id": "10", "output_slot": 0}],
        )
        # All parts empty: an empty positive, never a resolved prompt.
        source["6"]["inputs"]["text_g"] = ""
        self.assertEqual(_inspect(source)["reason"], "Positive prompt is empty")
        # A missing part is malformed, never read as empty.
        source = parity.build("f06a")["prompt"]
        del source["6"]["inputs"]["text_l"]
        self.assertEqual(_inspect(source)["reason_code"], "malformed")

    def test_flux_and_sd3_encoders(self):
        result = _inspect(parity.build("f06b")["prompt"])
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(_sides(result), (parity.POS, parity.NEG))
        source = parity.build("f06b")["prompt"]
        source["6"]["inputs"]["t5xxl"] = "a longer natural language description"
        result = _inspect(source)
        self.assertEqual(result["reason_code"], "composite")
        self.assertEqual(result["node"]["class_type"], "CLIPTextEncodeFlux")
        cases = [
            ((parity.POS, parity.POS, parity.POS), ("resolved", parity.POS, None)),
            (("", "", parity.POS), ("resolved", parity.POS, None)),
            (("", parity.POS, ""), ("resolved", parity.POS, None)),
            ((parity.POS, "", WOLF), ("unresolved", None, "composite")),
            ((parity.POS, parity.POS, WOLF), ("unresolved", None, "composite")),
            # Spec 7.2: only "all equal" or "exactly one non-empty" resolve.
            ((parity.POS, parity.POS, ""), ("unresolved", None, "composite")),
        ]
        for parts, expected in cases:
            with self.subTest(parts=parts):
                self.assertEqual(
                    _positive(_inspect(_with_positive(_sd3(*parts)))), expected
                )

    def test_qwen_edit_plus_prompt(self):
        extra = {
            "13": {"class_type": "LoadImage", "inputs": {"image": "reference.png"}}
        }
        for name in ("TextEncodeQwenImageEditPlus", "TextEncodeQwenImageEdit"):
            with self.subTest(node=name):
                inputs = {"clip": ["4", 1], "prompt": parity.POS, "vae": ["4", 2]}
                images = ("image1", "image2") if name.endswith("Plus") else ("image",)
                inputs.update({image: ["13", 0] for image in images})
                node = {"class_type": name, "inputs": inputs}
                result = _inspect(_with_positive(node, extra))
                self.assertEqual(result["state"], "resolved")
                self.assertEqual(_sides(result), (parity.POS, parity.NEG))
                self.assertEqual(
                    result["positive"]["provenance"]["path"],
                    [{"node_id": "6", "output_slot": 0}],
                )

    def test_qwen_image21_two_outputs(self):
        source = parity.build("f24")["prompt"]
        result = _inspect(source)
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(_sides(result), (parity.POS, parity.NEG))
        self.assertEqual(
            result["negative"]["provenance"]["path"],
            [{"node_id": "6", "output_slot": 1}],
        )
        self.assertEqual(result["negative"]["storage_text"], parity.NEG)
        # Slot 2 is the latent output, never a prompt.
        source["3"]["inputs"]["negative"] = ["6", 2]
        result = _inspect(source)
        self.assertEqual(result["reason_code"], "unsupported-node")
        self.assertEqual(
            result["node"], {"id": "6", "class_type": "TextEncodeQwenImage21"}
        )

    def test_kiko_text_encode_plain_text(self):
        node = {"class_type": "KikoTextEncode", "inputs": {"clip": ["4", 1]}}
        node["inputs"]["text"] = parity.POS
        result = _inspect(_with_positive(node))
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(_sides(result), (parity.POS, parity.NEG))
        self.assertEqual(result["positive"]["storage_text"], parity.POS)
        # No composition: a linked text arrives unchanged.
        node["inputs"]["text"] = ["10", 0]
        extra = {"10": {"class_type": "PrimitiveStringMultiline"}}
        extra["10"]["inputs"] = {"value": "  " + parity.POS + "\n"}
        result = _inspect(_with_positive(node, extra))
        self.assertEqual(result["positive"]["text"], "  " + parity.POS + "\n")

    def test_string_constant_multiline_strip_newlines_exact(self):
        result = _inspect(_string_positive(_multiline("a\n b \n", True)))
        self.assertEqual(_positive(result), ("resolved", "a b", None))
        self.assertEqual(result["positive"]["storage_text"], "a b")
        self.assertEqual(
            result["positive"]["provenance"]["path"],
            [{"node_id": "6", "output_slot": 0}, {"node_id": "10", "output_slot": 0}],
        )
        result = _inspect(_string_positive(_multiline("a\n b \n", False)))
        self.assertEqual(_positive(result), ("resolved", "a\n b \n", None))
        # Only "\n" is removed; other whitespace inside the text is kept.
        result = _inspect(_string_positive(_multiline("\ta\r\n\tb\n\n", True)))
        self.assertEqual(result["positive"]["text"], "a\r\tb")
        # A linked text is followed; a linked flag is unsupported (not guessed).
        linked = _string_positive(
            _multiline(["11", 0], True), {"11": _primitive("x\ny")}
        )
        self.assertEqual(_inspect(linked)["positive"]["text"], "xy")
        flag = _string_positive(
            _multiline("a\nb", ["12", 0]),
            {"12": {"class_type": "PrimitiveBoolean", "inputs": {"value": True}}},
        )
        result = _inspect(flag)
        self.assertEqual(result["reason_code"], "unsupported-node")
        self.assertEqual(
            result["node"], {"id": "10", "class_type": "StringConstantMultiline"}
        )
        for value in ("true", 1, None):
            with self.subTest(flag=value):
                result = _inspect(_string_positive(_multiline("a\nb", value)))
                self.assertEqual(result["reason_code"], "malformed")
        missing = _multiline("a", True)
        del missing["inputs"]["strip_newlines"]
        self.assertEqual(
            _inspect(_string_positive(missing))["reason_code"], "malformed"
        )
        # StringConstant is the literal strategy: its string, unchanged.
        constant = {"class_type": "StringConstant", "inputs": {"string": " x\n"}}
        result = _inspect(_string_positive(constant))
        self.assertEqual(_positive(result), ("resolved", " x\n", None))

    def test_join_string_multi_exact_semantics(self):
        strings = {
            "string_1": ["11", 0],
            "string_2": "b",
            "string_3": ["13", 0],
            "string_4": ["14", 0],
            "string_6": "beyond the count",
        }
        extra = {
            "11": _primitive(""),
            "13": _primitive(""),
            "14": _primitive(" d "),
        }
        join = _join(5, "\\n|", **strings)
        result = _inspect(_string_positive(join, extra))
        # string_1 is kept although empty; empty and missing later parts are
        # skipped; inputs past ``inputcount`` are ignored; the delimiter is
        # used exactly as written (no escape processing, no trimming).
        self.assertEqual(_positive(result), ("resolved", "\\n|b\\n| d ", None))
        self.assertEqual(result["positive"]["storage_text"], "\\n|b\\n| d ")
        self.assertEqual(
            result["positive"]["provenance"]["path"],
            [
                {"node_id": "6", "output_slot": 0},
                {"node_id": "10", "output_slot": 0},
                {"node_id": "11", "output_slot": 0},
                {"node_id": "13", "output_slot": 0},
                {"node_id": "14", "output_slot": 0},
            ],
        )
        # One link read by several parts is listed once in the provenance path.
        twice = _join(3, " ", string_1=["11", 0], string_2=["11", 0], string_3="c")
        result = _inspect(_string_positive(twice, {"11": _primitive("w")}))
        self.assertEqual(result["positive"]["text"], "w w c")
        self.assertEqual(
            result["positive"]["provenance"]["path"],
            [{"node_id": "6", "output_slot": 0}, {"node_id": "10", "output_slot": 0}]
            + [{"node_id": "11", "output_slot": 0}],
        )
        two = _join(2, " ", string_1="a red fox", string_2="in snow")
        self.assertEqual(
            _inspect(_string_positive(two))["positive"]["text"], "a red fox in snow"
        )
        # Settings must be literal values the node would accept.
        linked = {"12": {"class_type": "PrimitiveInt", "inputs": {"value": 2}}}
        for field, value, code in (
            ("inputcount", ["12", 0], "unsupported-node"),
            ("delimiter", ["12", 0], "unsupported-node"),
            ("inputcount", 0, "malformed"),
            ("inputcount", 1001, "malformed"),
            ("inputcount", True, "malformed"),
            ("inputcount", "2", "malformed"),
            ("delimiter", 1, "malformed"),
            ("return_list", "false", "malformed"),
            ("return_list", 0, "malformed"),
        ):
            with self.subTest(field=field, value=value):
                node = _join(2, " ", string_1="a", string_2="b")
                node["inputs"][field] = value
                result = _inspect(_string_positive(node, linked))
                self.assertEqual(result["reason_code"], code)
                if code == "unsupported-node":
                    self.assertEqual(result["node"]["class_type"], "JoinStringMulti")
        for field in ("inputcount", "delimiter", "return_list", "string_1"):
            with self.subTest(missing=field):
                node = _join(2, " ", string_1="a", string_2="b")
                del node["inputs"][field]
                result = _inspect(_string_positive(node))
                self.assertEqual(result["reason_code"], "malformed")

    def test_join_string_multi_return_list_is_runtime_value(self):
        source = parity.build("f21")["prompt"]
        result = _inspect(source)
        self.assertEqual(result["state"], "unresolved")
        self.assertEqual(result["reason_code"], "runtime-value")
        self.assertEqual(result["reason"], "Text is decided at run time")
        self.assertEqual(result["node"], {"id": "10", "class_type": "JoinStringMulti"})
        # A linked list flag is a run-time value too.
        source["10"]["inputs"]["return_list"] = ["20", 0]
        source["20"] = {"class_type": "PrimitiveBoolean", "inputs": {"value": False}}
        self.assertEqual(_inspect(source)["reason_code"], "runtime-value")
        # With the flag literally false the same parts join.
        source["10"]["inputs"]["return_list"] = False
        self.assertEqual(_inspect(source)["positive"]["text"], "a red fox, in snow")

    def test_prompt_search_list_is_runtime_value(self):
        from utils.prompt_inspector import inspect_prompt_evidence

        source = parity.build("f22")["prompt"]
        result = _inspect(source)
        self.assertEqual(result["state"], "unresolved")
        self.assertEqual(result["reason_code"], "runtime-value")
        self.assertEqual(result["node"], {"id": "10", "class_type": "PromptSearchList"})
        self.assertIsNone(result["positive"])
        # Never a list item: every output slot is decided at run time.
        source["6"]["inputs"]["text"] = ["10", 3]
        self.assertEqual(_inspect(source)["reason_code"], "runtime-value")
        evidence = inspect_prompt_evidence({"prompt": parity.build("f22")["prompt"]})
        self.assertEqual(evidence["prompts"]["positive"]["status"], "unresolved")
        self.assertIsNone(evidence["prompts"]["positive"]["text"])

    # TODO(PR3): flip with the f01b vector once output ancestry excludes the
    # preview-only FaceDetailer (D19); the latent origin is accepted since 2.5.
    @unittest.expectedFailure
    def test_f01b_single_flux2_branch_resolves_with_absent_negative(self):
        result = _inspect(parity.build("f01b")["prompt"])
        self.assertEqual(result["state"], "absent")
        self.assertEqual(result["positive"]["text"], F01B_POSITIVE)
        self.assertIsNone(result["negative"])

    def test_f01b_branch_text_is_the_exact_join(self):
        # Anchored at the branch's saver, over its EmptyFlux2LatentImage origin:
        # the join, multiline strings and encoder alone give the exact text.
        source = parity.build("f01b")["prompt"]
        self.assertEqual(source["1300:7"]["class_type"], "EmptyFlux2LatentImage")
        result = _inspect(source, output_node_id="1400")
        self.assertEqual(result["state"], "absent")
        self.assertEqual(result["reason"], "Guider has no negative input")
        self.assertEqual(result["positive"]["text"], F01B_POSITIVE)
        self.assertEqual(result["positive"]["storage_text"], F01B_POSITIVE)
        self.assertIsNone(result["negative"])

    def test_encoder_and_string_outputs_keep_their_kinds(self):
        sdxl = parity.build("f06a")["prompt"]["6"]
        # A conditioning output read as a string, and a string output read as
        # conditioning, are malformed links, not unsupported nodes.
        join = _join(2, " ", string_1=["11", 0], string_2="b")
        result = _inspect(_string_positive(join, {"11": sdxl}))
        self.assertEqual(result["reason_code"], "malformed")
        self.assertEqual(result["reason"], "Output slot has an incompatible type")
        for node in (_multiline("a", True), _join(2, " ", string_1="a")):
            with self.subTest(node=node["class_type"]):
                result = _inspect(_with_positive(node))
                self.assertEqual(result["reason_code"], "malformed")

    def test_unverified_text_nodes_stay_unsupported_and_named(self):
        # TextBoxMira was verified in its source in Task 2.7b and is listed.
        for name in ("LoRAfromText", "RegexReplace", "XYZPrompt"):
            with self.subTest(node=name):
                node = {"class_type": name, "inputs": {"text": parity.POS}}
                result = _inspect(_string_positive(node))
                self.assertEqual(result["reason_code"], "unsupported-node")
                self.assertEqual(result["node"], {"id": "10", "class_type": name})

    def test_string_strategies_bound_every_intermediate(self):
        limited = replace(resolver.INSPECTION_PROFILE, max_text_length=10)
        join = _join(2, " ", string_1="abcdef", string_2="ghijk")
        result = resolve_prompt_pair(
            {"prompt": _string_positive(join)}, profile=limited
        )
        self.assertEqual(result["reason_code"], "text-limit")
        # Exactly at the limit is fine.
        join["inputs"]["string_2"] = "ghi"
        result = resolve_prompt_pair(
            {"prompt": _string_positive(join)}, profile=limited
        )
        self.assertEqual(result["positive"]["text"], "abcdef ghi")
        # The scanned-character budget covers stripping, joining and comparing.
        long_text = "x\n" * 5_000
        cases = {
            "strip": _string_positive(_multiline(long_text, True)),
            "join": _string_positive(
                _join(2, " ", string_1=long_text, string_2=long_text)
            ),
            "parts": _with_positive(_sd3(long_text, long_text, long_text)),
        }
        scanned = replace(resolver.INSPECTION_PROFILE, max_scanned_units=8_000)
        for label, source in cases.items():
            with self.subTest(strategy=label):
                self.assertEqual(_inspect(source)["state"], "resolved")
                result = resolve_prompt_pair({"prompt": source}, profile=scanned)
                self.assertEqual(result["reason_code"], "work-limit")

    def test_listed_strategies_without_code_here_are_unsupported(self):
        # ``pm-compose`` and ``concat`` are schema-valid but implemented by the
        # existing adapters only; a table row using them is never guessed at.
        table = inspection_semantics.load_semantics()
        encoders = dict(table.encoders)
        encoders[("CLIPTextEncodeSDXL", 0)] = {
            "strategy": "pm-compose",
            "inputs": ("text",),
        }
        strings = dict(table.strings)
        strings[("StringConstant", 0)] = {"strategy": "concat", "inputs": ("string",)}
        patched = replace(table, encoders=encoders, strings=strings)
        constant = {"class_type": "StringConstant", "inputs": {"string": "x"}}
        with mock.patch.object(resolver, "_inspection_semantics", return_value=patched):
            for source, name in (
                (parity.build("f06a")["prompt"], "CLIPTextEncodeSDXL"),
                (_string_positive(constant), "StringConstant"),
            ):
                with self.subTest(node=name):
                    result = _inspect(source)
                    self.assertEqual(result["reason_code"], "unsupported-node")
                    self.assertEqual(result["node"]["class_type"], name)

    def test_unreadable_string_sources_keep_pair_execution_evidence(self):
        from utils.pair_execution import EXECUTION_KEY

        record = {
            "text": "recorded words",
            "negative_text": "neg",
            "positive_text": "recorded words",
            "negative_effective_text": "neg",
            "prepend_text": "",
            "append_text": "",
        }
        evidence = {
            EXECUTION_KEY: {"version": 1, "nodes": {"8": {"records": [record]}}}
        }

        def paired(source: dict, extra: dict) -> dict:
            graph = parity.build("f02")["prompt"]
            graph["8"] = {
                "class_type": "PromptManagerPair",
                "inputs": {
                    "text": ["10", 0],
                    "negative_text": "neg",
                    "prepend_text": "",
                    "append_text": "",
                },
            }
            graph["3"]["inputs"]["positive"] = ["8", 0]
            graph["3"]["inputs"]["negative"] = ["8", 1]
            graph.update({"10": source, **extra})
            return graph

        flag = {"11": {"class_type": "PrimitiveBoolean", "inputs": {"value": True}}}
        unknown = {"11": {"class_type": "FutureNode", "inputs": {}}}
        # Text that cannot be read statically is unverifiable, not a mismatch:
        # the pair's unique execution record still decides (as it did before
        # these nodes were listed).
        cases = {
            "linked strip flag": (_multiline("recorded words", ["11", 0]), flag),
            "linked delimiter": (
                _join(2, ["11", 0], string_1="recorded", string_2="words"),
                unknown,
            ),
            "linked count": (
                _join(["11", 0], " ", string_1="recorded", string_2="words"),
                unknown,
            ),
            "list output": (
                _join(2, " ", True, string_1="recorded", string_2="words"),
                {},
            ),
            "search list": ({"class_type": "PromptSearchList", "inputs": {}}, {}),
        }
        for label, (source, extra) in cases.items():
            with self.subTest(source=label):
                result = resolve_prompt_pair(
                    {"prompt": paired(source, extra), **evidence},
                    profile=resolver.INSPECTION_PROFILE,
                )
                self.assertEqual(result["state"], "resolved")
                self.assertEqual(_sides(result), ("recorded words", "neg"))
                provenance = result["positive"]["provenance"]
                self.assertEqual(provenance["text_source"], "execution")
        # A source that does resolve is still verified against the record.
        readable = _join(2, " ", string_1="other", string_2="words")
        result = resolve_prompt_pair(
            {"prompt": paired(readable, {}), **evidence},
            profile=resolver.INSPECTION_PROFILE,
        )
        self.assertEqual(result["reason_code"], "malformed")
        self.assertEqual(
            result["reason"], "Execution evidence does not match recorded node inputs"
        )
        matching = _join(2, " ", string_1="recorded", string_2="words")
        result = resolve_prompt_pair(
            {"prompt": paired(matching, {}), **evidence},
            profile=resolver.INSPECTION_PROFILE,
        )
        self.assertEqual(result["positive"]["provenance"]["text_source"], "execution")

    def test_default_profile_keeps_encoder_and_string_nodes_unsupported(self):
        for name in ("f06a", "f06b", "f21", "f22", "f24"):
            with self.subTest(fixture=name):
                result = resolve_prompt_pair({"prompt": parity.build(name)["prompt"]})
                self.assertEqual(result["state"], "unresolved")
                self.assertEqual(
                    result["reason"], "Unsupported node semantics or output slot"
                )
                self.assertIsNone(result["reason_code"])


def _detailed(same_prompt: bool = False) -> dict:
    """``f19``: KSampler 3 -> VAEDecode 8 -> FaceDetailer 50 -> SaveImage 9."""
    source = parity.build("f19")["prompt"]
    if same_prompt:
        source["50"]["inputs"]["positive"] = ["6", 0]
    return source


def _detailer(image: list, positive: list, negative: list = None) -> dict:
    return {
        "class_type": "FaceDetailer",
        "inputs": {
            "image": image,
            "model": ["4", 0],
            "positive": positive,
            "negative": negative or ["7", 0],
            "seed": 1,
            "steps": 8,
        },
    }


class TestDetailerStages(unittest.TestCase):
    """D19: a detailer is a sampler stage judged with its upstream sampler."""

    def test_detailer_with_the_upstream_prompt_resolves(self):
        result = _inspect(_detailed(same_prompt=True))
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(result["sampler_id"], "50")
        self.assertEqual(_sides(result), (parity.POS, parity.NEG))
        self.assertEqual(result["candidates"], [])
        self.assertEqual(
            _inspect(_detailed(same_prompt=True), output_node_id="9"), result
        )

    def test_detailer_with_a_different_prompt_is_ambiguous_with_both_stages(self):
        from utils.prompt_inspector import inspect_prompt_evidence

        result = _inspect(_detailed())
        self.assertEqual(
            (result["state"], result["reason_code"]),
            ("ambiguous", "multistage-differs"),
        )
        self.assertEqual(result["sampler_id"], "50")
        self.assertIsNone(result["positive"])
        self.assertIsNone(result["negative"])
        self.assertEqual(
            [(item["sampler_id"], _sides(item)) for item in result["candidates"]],
            [
                ("50", (parity.DETAILER_POS, parity.NEG)),
                ("3", (parity.POS, parity.NEG)),
            ],
        )
        self.assertEqual(_inspect(_detailed(), output_node_id="9"), result)
        # Neither the upstream prompt nor the detailer prompt is shown as the answer.
        prompts = inspect_prompt_evidence({"prompt": _detailed()})["prompts"]
        self.assertEqual(
            [prompts[role]["status"] for role in ("positive", "negative")],
            ["ambiguous", "ambiguous"],
        )
        self.assertNotIn(parity.POS, str(prompts))

    def test_detailer_of_a_loaded_image_is_a_single_stage(self):
        source = _detailed()
        source["13"] = {"class_type": "LoadImage", "inputs": {"image": "input.png"}}
        source["50"]["inputs"]["image"] = ["13", 0]
        source["9"]["inputs"]["images"] = ["50", 0]
        source["8"]["inputs"]["samples"] = ["3", 0]
        source["14"] = {"class_type": "SaveImage", "inputs": {"images": ["8", 0]}}
        result = _inspect(source, output_node_id="9")
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(_sides(result), (parity.DETAILER_POS, parity.NEG))
        self.assertEqual(result["sampler_id"], "50")

    def test_raw_and_detailed_saves_stay_ambiguous_without_an_anchor(self):
        source = _detailed(same_prompt=True)
        source["14"] = {"class_type": "SaveImage", "inputs": {"images": ["8", 0]}}
        result = _inspect(source)
        self.assertEqual(result["state"], "ambiguous")
        self.assertEqual(result["reason"], "Multiple sampler branches")
        self.assertEqual(
            sorted(item["sampler_id"] for item in result["candidates"]), ["3", "50"]
        )
        raw = _inspect(source, output_node_id="14")
        self.assertEqual((raw["state"], raw["sampler_id"]), ("resolved", "3"))
        detailed = _inspect(source, output_node_id="9")
        self.assertEqual(
            (detailed["state"], detailed["sampler_id"]), ("resolved", "50")
        )

    def test_a_preview_of_the_upstream_image_is_not_another_output(self):
        source = _detailed()
        source["14"] = {"class_type": "PreviewImage", "inputs": {"images": ["8", 0]}}
        result = _inspect(source)
        self.assertEqual(result["reason_code"], "multistage-differs")
        self.assertEqual(len(result["candidates"]), 2)

    def test_chained_detailers_compare_every_stage(self):
        source = _detailed(same_prompt=True)
        source["51"] = _detailer(["50", 0], ["6", 0])
        source["9"]["inputs"]["images"] = ["51", 0]
        result = _inspect(source)
        self.assertEqual((result["state"], result["sampler_id"]), ("resolved", "51"))
        source["50"]["inputs"]["positive"] = ["10", 0]
        result = _inspect(source)
        self.assertEqual(result["reason_code"], "multistage-differs")
        self.assertEqual(
            [item["sampler_id"] for item in result["candidates"]], ["51", "50", "3"]
        )

    def test_unresolved_stage_blocks_the_chain(self):
        source = _detailed(same_prompt=True)
        source["30"] = {"class_type": "FutureConditioner", "inputs": {"c": ["6", 0]}}
        source["3"]["inputs"]["positive"] = ["30", 0]
        source["50"]["inputs"]["positive"] = ["10", 0]
        result = _inspect(source)
        self.assertEqual((result["state"], result["sampler_id"]), ("unresolved", "50"))
        self.assertEqual(
            result["node"], {"id": "30", "class_type": "FutureConditioner"}
        )
        self.assertIsNone(result["positive"])

    def test_malformed_and_cyclic_detailer_ancestry_never_guesses(self):
        source = _detailed()
        del source["50"]["inputs"]["image"]
        result = _inspect(source, output_node_id="9")
        self.assertEqual(
            (result["state"], result["reason_code"]), ("unresolved", "malformed")
        )
        source = _detailed()
        source["8"]["inputs"]["samples"] = ["51", 0]
        source["51"] = _detailer(["50", 0], ["6", 0])
        result = _inspect(source, output_node_id="9")
        self.assertEqual(result["state"], "unresolved")
        self.assertEqual(result["reason_code"], "malformed")

    def test_detailer_image_ancestry_is_walked_generically(self):
        source = _detailed(same_prompt=True)
        source["60"] = {
            "class_type": "ImageBlend",
            "inputs": {"image1": ["8", 0], "image2": ["8", 0], "blend_factor": 0.5},
        }
        source["61"] = {
            "class_type": "ImageBlend",
            "inputs": {"image1": ["8", 0], "image2": ["60", 0]},
        }
        source["62"] = {"class_type": "ImageScale", "inputs": {"image": ["60", 0]}}
        source["50"]["inputs"]["image"] = ["61", 0]
        source["63"] = {"class_type": "PreviewImage", "inputs": {"images": ["62", 0]}}
        source["junk"] = "not a node"
        result = _inspect(source)
        self.assertEqual((result["state"], result["sampler_id"]), ("resolved", "50"))
        # A detailer whose image ancestry returns to itself is malformed.
        source = _detailed()
        source["64"] = {"class_type": "ImageScale", "inputs": {"image": ["50", 0]}}
        source["50"]["inputs"]["image"] = ["64", 0]
        result = _inspect(source, output_node_id="9")
        self.assertEqual(result["reason"], "Cyclic sampler stage ancestry")
        self.assertEqual(result["reason_code"], "malformed")

    def test_detailer_stage_walks_draw_from_the_work_budget(self):
        self.assertEqual(
            _inspect(_detailed(), max_visits=6)["reason_code"], "work-limit"
        )
        # Too little budget to absorb the upstream sampler keeps both as
        # candidates: more ambiguity, never a chosen prompt.
        starved = _inspect(_detailed(), max_visits=3)
        self.assertEqual(starved["state"], "ambiguous")
        self.assertEqual(len(starved["candidates"]), 2)
        self.assertIsNone(starved["positive"])
        source = _detailed()
        previous = "8"
        for index in range(70):
            source[f"u{index}"] = {
                "class_type": "ImageScale",
                "inputs": {"image": [previous, 0]},
            }
            previous = f"u{index}"
        source["50"]["inputs"]["image"] = [previous, 0]
        result = _inspect(source, output_node_id="9")
        self.assertEqual(result["reason_code"], "depth-limit")

    def test_long_detailer_chains_are_depth_limited(self):
        for count in (70, 600):
            source = _detailed(same_prompt=True)
            previous = "50"
            for index in range(count):
                node_id = f"d{index}"
                source[node_id] = _detailer([previous, 0], ["6", 0])
                previous = node_id
            source["9"]["inputs"]["images"] = [previous, 0]
            for options in ({}, {"output_node_id": "9"}):
                with self.subTest(count=count, options=options):
                    result = _inspect(source, **options)
                    self.assertEqual(result["state"], "unresolved")
                    self.assertEqual(result["reason_code"], "depth-limit")
                    self.assertIsNone(result["positive"])
        # A chain within the bound still resolves.
        source = _detailed(same_prompt=True)
        previous = "50"
        for index in range(20):
            source[f"d{index}"] = _detailer([previous, 0], ["6", 0])
            previous = f"d{index}"
        source["9"]["inputs"]["images"] = [previous, 0]
        result = _inspect(source, output_node_id="9")
        self.assertEqual((result["state"], result["sampler_id"]), ("resolved", "d19"))

    def test_default_profile_keeps_the_upstream_sampler(self):
        # Ingestion is unchanged (D1): the detailer is not a sampler there.
        result = resolve_prompt_pair({"prompt": _detailed()})
        self.assertEqual((result["state"], result["sampler_id"]), ("resolved", "3"))
        self.assertEqual(_sides(result), (parity.POS, parity.NEG))


MULTISTAGE = "Multistage sampler ancestry is ambiguous"
OTHER = "a white owl at dusk"


def _latent_from(node: dict, extra: dict = None) -> dict:
    """``f02``'s KSampler graph whose ``latent_image`` reads ``node`` (as node 5)."""
    return parity._ksampler_graph(
        parity._enc(parity.POS), parity._enc(parity.NEG), latent=node, extra=extra
    )


def _image_to_image(positive: str = OTHER) -> dict:
    """KSampler 3 -> VAEDecode 8 -> VAEEncode 60 -> KSampler 61 -> SaveImage 63."""
    source = _latent_from(None)
    source.update(
        {
            "60": {
                "class_type": "VAEEncode",
                "inputs": {"pixels": ["8", 0], "vae": ["4", 2]},
            },
            "62": parity._enc(positive),
            "61": {
                "class_type": "KSampler",
                "inputs": {
                    **source["3"]["inputs"],
                    "positive": ["62", 0],
                    "latent_image": ["60", 0],
                    "denoise": 0.6,
                },
            },
            "64": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["61", 0], "vae": ["4", 2]},
            },
            "63": {
                "class_type": "SaveImage",
                "inputs": {"filename_prefix": "img2img", "images": ["64", 0]},
            },
        }
    )
    # The first stage's own output is only a preview.
    source["9"]["class_type"] = "PreviewImage"
    return source


class TestLatentAncestry(unittest.TestCase):
    """Any latent origin without an upstream sampler is a single stage."""

    def test_vaeencode_from_load_image_is_single_stage(self):
        source = parity.build("f10")["prompt"]
        for anchor in (None, "9"):
            with self.subTest(anchor=anchor):
                result = _inspect(source, output_node_id=anchor)
                self.assertEqual(result["state"], "resolved")
                self.assertIsNone(result["reason_code"])
                self.assertEqual(result["sampler_id"], "3")
                self.assertEqual(_sides(result), (parity.POS, parity.NEG))
        # Ingestion is unchanged (D1).
        legacy = resolve_prompt_pair({"prompt": source})
        self.assertEqual(legacy["state"], "unresolved")
        self.assertEqual(legacy["reason"], "Unsupported latent ancestry")

    def test_model_specific_empty_latents_are_single_stage(self):
        size = {"width": 1024, "height": 1024, "batch_size": 1}
        origins = {
            "f11": parity.build("f11")["prompt"],
            "EmptyLatentBatch": _latent_from(
                {"class_type": "EmptyLatentBatch", "inputs": dict(size)}
            ),
            "EmptyFlux2LatentImage": _latent_from(
                {"class_type": "EmptyFlux2LatentImage", "inputs": dict(size)}
            ),
            "SetLatentNoiseMask": _latent_from(
                {
                    "class_type": "SetLatentNoiseMask",
                    "inputs": {"samples": ["60", 0], "mask": ["50", 1]},
                },
                {
                    "50": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
                    "60": {
                        "class_type": "VAEEncode",
                        "inputs": {"pixels": ["50", 0], "vae": ["4", 2]},
                    },
                },
            ),
            "Reroute": _latent_from(
                {"class_type": "Reroute", "inputs": {"value": ["60", 0]}},
                {"60": {"class_type": "EmptyLatentBatch", "inputs": dict(size)}},
            ),
        }
        for name in ("InpaintModelConditioning", "WanImageToVideo"):
            # Slots 0 and 1 carry the conditioning, slot 2 is the latent origin.
            source = _latent_from(
                {
                    "class_type": name,
                    "inputs": {
                        "positive": ["6", 0],
                        "negative": ["7", 0],
                        "vae": ["4", 2],
                        "pixels": ["50", 0],
                        "start_image": ["50", 0],
                        "mask": ["50", 1],
                        "width": 832,
                        "height": 480,
                    },
                },
                {"50": {"class_type": "LoadImage", "inputs": {"image": "a.png"}}},
            )
            source["3"]["inputs"].update(
                positive=["5", 0], negative=["5", 1], latent_image=["5", 2]
            )
            origins[name] = source
        for name, source in origins.items():
            with self.subTest(origin=name):
                result = _inspect(source)
                self.assertEqual(result["state"], "resolved")
                self.assertEqual(result["sampler_id"], "3")
                self.assertEqual(_sides(result), (parity.POS, parity.NEG))
                self.assertEqual(_inspect(source, output_node_id="9"), result)

    def test_upstream_sampler_through_vaedecode_vaeencode_is_multistage(self):
        for positive in (OTHER, parity.POS):
            with self.subTest(positive=positive):
                result = _inspect(_image_to_image(positive), output_node_id="63")
                # Until PR3 compares stage texts, never one stage's prompt.
                self.assertEqual(result["state"], "unresolved")
                self.assertEqual(result["sampler_id"], "61")
                self.assertEqual(result["reason"], MULTISTAGE)
                self.assertIsNone(result["reason_code"])
                self.assertIsNone(result["positive"])
                self.assertIsNone(result["negative"])
        # An upstream sampler reached through a non-latent input is a stage too.
        source = _latent_from(
            {
                "class_type": "InpaintModelConditioning",
                "inputs": {
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "vae": ["4", 2],
                    "pixels": ["71", 0],
                    "mask": ["50", 1],
                },
            },
            {
                "50": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
                "70": {
                    "class_type": "KSampler",
                    "inputs": {
                        "model": ["4", 0],
                        "positive": ["6", 0],
                        "negative": ["7", 0],
                        "latent_image": ["72", 0],
                    },
                },
                "71": {
                    "class_type": "VAEDecode",
                    "inputs": {"samples": ["70", 0], "vae": ["4", 2]},
                },
                "72": {"class_type": "EmptyLatentImage", "inputs": {}},
            },
        )
        source["3"]["inputs"]["latent_image"] = ["5", 2]
        result = _inspect(source, output_node_id="9")
        self.assertEqual(
            (result["state"], result["reason"]), ("unresolved", MULTISTAGE)
        )
        # A latent that reaches its own sampler is malformed, not a stage.
        source = _latent_from(
            {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["3", 0]}}
        )
        result = _inspect(source, output_node_id="9")
        self.assertEqual(
            (result["state"], result["reason_code"]), ("unresolved", "malformed")
        )
        self.assertEqual(result["reason"], "Cyclic latent ancestry")
        source["3"]["inputs"]["latent_image"] = None
        self.assertEqual(_inspect(source)["reason_code"], "malformed")

    def test_unlisted_pipe_and_custom_samplers_in_the_ancestry_are_unsupported(self):
        # Review I-1: stages whose inputs are pipes, embeds or sampler parts
        # (easy-use, Impact pipes, video and custom samplers) must never be
        # walked through to a silent single-stage pick of the final prompt.
        easy = _latent_from(None)
        easy["3"]["inputs"]["positive"] = ["62", 0]
        easy.update(
            {
                "62": parity._enc(OTHER),
                "20": {
                    "class_type": "easy fullLoader",
                    "inputs": {"positive": "first pass", "negative": "bad"},
                },
                "21": {"class_type": "easy preSampling", "inputs": {"pipe": ["20", 0]}},
                "22": {"class_type": "easy kSampler", "inputs": {"pipe": ["21", 0]}},
                "23": {
                    "class_type": "VAEEncode",
                    "inputs": {"pixels": ["22", 1], "vae": ["4", 2]},
                },
            }
        )
        easy["3"]["inputs"]["latent_image"] = ["23", 0]
        pipes = _latent_from(None)
        pipes.update(
            {
                "50": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
                "20": {
                    "class_type": "easy fullLoader",
                    "inputs": {"positive": "segs prompt", "negative": "n"},
                },
                "24": {
                    "class_type": "easy pipeToBasicPipe",
                    "inputs": {"pipe": ["20", 0]},
                },
                "26": {
                    "class_type": "BasicPipeToDetailerPipe",
                    "inputs": {"basic_pipe": ["24", 0]},
                },
                "25": {
                    "class_type": "FaceDetailerPipe",
                    "inputs": {"image": ["50", 0], "detailer_pipe": ["26", 0]},
                },
                "23": {
                    "class_type": "VAEEncode",
                    "inputs": {"pixels": ["25", 0], "vae": ["4", 2]},
                },
            }
        )
        pipes["3"]["inputs"]["latent_image"] = ["23", 0]
        cases = {"easy": (easy, "22", "easy kSampler")}
        # Breadth-first, the first unlisted pipe consumer met is named.
        cases["pipes"] = (pipes, "25", "FaceDetailerPipe")
        for label, embeds in (
            ("text_embeds", "WanVideoSampler"),
            ("hyvid_embeds", "HyVideoSampler"),
            ("sigmas", "MysteryCustomSampler"),
            ("noise", "MysteryNoiseSampler"),
            ("sampler", "MysterySamplerStage"),
        ):
            source = _latent_from(None)
            source.update(
                {
                    "30": {"class_type": "MysteryEmbeds", "inputs": {}},
                    "31": {"class_type": embeds, "inputs": {label: ["30", 0]}},
                    "32": {
                        "class_type": "VAEDecode",
                        "inputs": {"samples": ["31", 0], "vae": ["4", 2]},
                    },
                    "23": {
                        "class_type": "VAEEncode",
                        "inputs": {"pixels": ["32", 0], "vae": ["4", 2]},
                    },
                }
            )
            source["3"]["inputs"]["latent_image"] = ["23", 0]
            cases[label] = (source, "31", embeds)
        for label, (source, node_id, class_type) in cases.items():
            with self.subTest(case=label):
                result = _inspect(source)
                self.assertEqual(result["state"], "unresolved")
                self.assertEqual(result["reason_code"], "unsupported-node")
                self.assertEqual(result["reason"], "Unsupported latent ancestry")
                self.assertEqual(
                    result["node"], {"id": node_id, "class_type": class_type}
                )
                self.assertIsNone(result["positive"])
        # A LoRA in a walked CLIP chain is not a stage (``model`` is not one).
        source = _latent_from(
            {
                "class_type": "InpaintModelConditioning",
                "inputs": {
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "vae": ["4", 2],
                    "pixels": ["50", 0],
                    "mask": ["50", 1],
                },
            },
            {
                "50": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
                "40": {
                    "class_type": "LoraLoader",
                    "inputs": {"model": ["4", 0], "clip": ["4", 1]},
                },
            },
        )
        source["6"]["inputs"]["clip"] = ["40", 1]
        source["3"]["inputs"].update(
            positive=["5", 0], negative=["5", 1], latent_image=["5", 2]
        )
        self.assertEqual(_inspect(source)["state"], "resolved")

    def test_unlisted_model_driven_samplers_in_the_ancestry_are_unsupported(self):
        # Re-review I-1b: a sampler linking ``model`` with conditioning or a
        # latent under other names (XlabsSampler, generic video samplers).
        xlabs = _latent_from(None)
        xlabs["3"]["inputs"]["positive"] = ["62", 0]
        xlabs.update(
            {
                "62": parity._enc(OTHER),
                "30": {
                    "class_type": "CLIPTextEncodeFlux",
                    "inputs": {
                        "clip": ["4", 1],
                        "t5xxl": "first stage",
                        "clip_l": "first stage",
                        "guidance": 3.5,
                    },
                },
                "31": parity._enc("neg"),
                "22": {
                    "class_type": "XlabsSampler",
                    "inputs": {
                        "model": ["4", 0],
                        "conditioning": ["30", 0],
                        "neg_conditioning": ["31", 0],
                        "noise_seed": 1,
                    },
                },
                "28": {
                    "class_type": "VAEDecode",
                    "inputs": {"samples": ["22", 0], "vae": ["4", 2]},
                },
                "23": {
                    "class_type": "VAEEncode",
                    "inputs": {"pixels": ["28", 0], "vae": ["4", 2]},
                },
            }
        )
        xlabs["3"]["inputs"]["latent_image"] = ["23", 0]
        video = _latent_from(None)
        video.update(
            {
                "40": {"class_type": "EmptyLatentImage", "inputs": {}},
                "22": {
                    "class_type": "SomeVideoSampler",
                    "inputs": {"model": ["4", 0], "samples": ["40", 0], "seed": 1},
                },
                "23": {
                    "class_type": "LatentUpscale",
                    "inputs": {"samples": ["22", 0], "width": 768, "height": 768},
                },
            }
        )
        video["3"]["inputs"]["latent_image"] = ["23", 0]
        reference = _latent_from(None)
        reference.update(
            {
                "22": {
                    "class_type": "MysteryLatentStage",
                    "inputs": {"model": ["4", 0], "latent": ["5", 0]},
                },
            }
        )
        reference["3"]["inputs"]["latent_image"] = ["22", 0]
        cases = {
            "XlabsSampler": (xlabs, "22"),
            "SomeVideoSampler": (video, "22"),
            "MysteryLatentStage": (reference, "22"),
        }
        for class_type, (source, node_id) in cases.items():
            with self.subTest(case=class_type):
                result = _inspect(source)
                self.assertEqual(result["state"], "unresolved")
                self.assertEqual(result["reason_code"], "unsupported-node")
                self.assertEqual(
                    result["node"], {"id": node_id, "class_type": class_type}
                )
                self.assertIsNone(result["positive"])
        # ``model`` alone (ModelSampling*) or with ``clip`` (LoraLoader) is a
        # model patch, never a stage.
        patched = _latent_from(
            {
                "class_type": "MysteryLatentFromModel",
                "inputs": {"model": ["41", 0], "vae": ["4", 2]},
            },
            {
                "40": {
                    "class_type": "LoraLoader",
                    "inputs": {"model": ["4", 0], "clip": ["4", 1]},
                },
                "41": {
                    "class_type": "ModelSamplingFlux",
                    "inputs": {"model": ["40", 0], "max_shift": 1.15},
                },
            },
        )
        self.assertEqual(_inspect(patched)["state"], "resolved")

    def test_unlisted_stage_in_a_detailer_image_ancestry_is_unsupported(self):
        # Review M-1: the detailer's upstream-stage walk uses the same rule.
        source = _detailed(same_prompt=True)
        source.update(
            {
                "20": {"class_type": "easy fullLoader", "inputs": {"positive": "x"}},
                "22": {"class_type": "easy kSampler", "inputs": {"pipe": ["20", 0]}},
            }
        )
        source["50"]["inputs"]["image"] = ["22", 1]
        result = _inspect(source, output_node_id="9")
        self.assertEqual(result["reason_code"], "unsupported-node")
        self.assertEqual(result["node"], {"id": "22", "class_type": "easy kSampler"})

    def test_unclassified_sampler_shaped_latent_node_is_unsupported_and_named(self):
        # An unlisted node with sampler inputs may itself be an earlier stage:
        # never call the chain single-stage across it.
        source = _latent_from(
            {
                "class_type": "MysteryLatentSampler",
                "inputs": {
                    "model": ["4", 0],
                    "positive": ["6", 0],
                    "latent_image": ["60", 0],
                },
            },
            {"60": {"class_type": "EmptyLatentImage", "inputs": {}}},
        )
        result = _inspect(source)
        self.assertEqual(result["state"], "unresolved")
        self.assertEqual(result["reason_code"], "unsupported-node")
        self.assertEqual(result["reason"], "Unsupported latent ancestry")
        self.assertEqual(
            result["node"], {"id": "5", "class_type": "MysteryLatentSampler"}
        )
        self.assertIsNone(result["positive"])
        # The rule holds at any depth (review M-3): behind a VAEEncode.
        source = _latent_from(
            {
                "class_type": "VAEEncode",
                "inputs": {"pixels": ["60", 0], "vae": ["4", 2]},
            },
            {
                "50": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
                "60": {
                    # Task 2.7b lists UltimateSDUpscale; an unlisted upscaler
                    # of the same shape keeps this rule's coverage.
                    "class_type": "MysteryUpscaler",
                    "inputs": {
                        "image": ["50", 0],
                        "model": ["4", 0],
                        "positive": ["6", 0],
                        "negative": ["7", 0],
                    },
                },
            },
        )
        result = _inspect(source, output_node_id="9")
        self.assertEqual(result["reason_code"], "unsupported-node")
        self.assertEqual(result["node"], {"id": "60", "class_type": "MysteryUpscaler"})
        # The listed stage is now an earlier sampler stage in the ancestry.
        source["60"]["class_type"] = "UltimateSDUpscale"
        result = _inspect(source, output_node_id="9")
        self.assertEqual(result["reason"], resolver.MULTISTAGE_UNATTRIBUTED_REASON)

    def test_latent_walk_budget_exhaustion_is_work_limit(self):
        # 2,100 distinct images in the latent ancestry: more than one
        # candidate's 2,000 visits, and never reported as single stage.
        extra = {
            f"i{index}": {"class_type": "LoadImage", "inputs": {"image": f"{index}"}}
            for index in range(2_100)
        }
        extra["50"] = {
            "class_type": "ImageBatchMulti",
            "inputs": {f"image_{index}": [f"i{index}", 0] for index in range(2_100)},
        }
        source = _latent_from(
            {
                "class_type": "VAEEncode",
                "inputs": {"pixels": ["50", 0], "vae": ["4", 2]},
            },
            extra,
        )
        result = _inspect(source)
        self.assertEqual(result["state"], "unresolved")
        self.assertEqual(result["reason_code"], "work-limit")
        self.assertIsNone(result["positive"])
        self.assertEqual(
            _inspect(parity.build("f10")["prompt"], max_visits=2)["reason_code"],
            "work-limit",
        )
        # A chain longer than the depth limit is ``depth-limit``.
        source = _latent_from({"class_type": "EmptyLatentImage", "inputs": {}})
        previous = "5"
        for index in range(70):
            source[f"u{index}"] = {
                "class_type": "LatentUpscaleBy",
                "inputs": {"samples": [previous, 0], "scale_by": 1.0},
            }
            previous = f"u{index}"
        source["3"]["inputs"]["latent_image"] = [previous, 0]
        self.assertEqual(_inspect(source)["reason_code"], "depth-limit")
        source["3"]["inputs"]["latent_image"] = ["u10", 0]
        self.assertEqual(_inspect(source)["state"], "resolved")


class _CountingDeque(collections.deque):
    """Counts every item queued by the resolver's breadth-first walks."""

    queued = 0

    def append(self, item):
        _CountingDeque.queued += 1
        super().append(item)

    def extend(self, items):
        for item in items:
            self.append(item)


def _dense_layers(
    prefix: str, bottom: list, layers: int = 4, width: int = 40, fan: int = None
):
    """A hub over ``layers`` x ``width`` nodes; each links to ``fan`` (default:
    all) nodes of the next layer, so every node is shared by many consumers."""
    nodes = {}
    fan = fan or width
    for layer in range(layers):
        for index in range(width):
            below = (
                [
                    [f"{prefix}{layer + 1}_{(index + step) % width}", 0]
                    for step in range(fan)
                ]
                if layer + 1 < layers
                else [bottom]
            )
            nodes[f"{prefix}{layer}_{index}"] = {
                "class_type": "DenseBlend",
                "inputs": {f"in_{k}": link for k, link in enumerate(below)},
            }
    # One hub reads the whole first layer.
    nodes[f"{prefix}top"] = {
        "class_type": "DenseBlend",
        "inputs": {f"in_{k}": [f"{prefix}0_{k}", 0] for k in range(width)},
    }
    return nodes, [f"{prefix}top", 0], layers * width + 1


class TestBreadthFirstWalkWork(unittest.TestCase):
    """Dense shared fan-in: each node is queued at most once per walk."""

    def counted(self, source: dict, **options) -> tuple:
        _CountingDeque.queued = 0
        with mock.patch.object(resolver, "deque", _CountingDeque):
            result = _inspect(source, **options)
        return result, _CountingDeque.queued

    def test_dense_latent_ancestry_queues_each_node_once(self):
        source = _latent_from({"class_type": "EmptyLatentImage", "inputs": {}})
        dense, top, count = _dense_layers("L", ["5", 0])
        source.update(dense)
        source["3"]["inputs"]["latent_image"] = top
        result, queued = self.counted(source)
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(_sides(result), (parity.POS, parity.NEG))
        # The dense layers plus the empty latent, each queued once.
        self.assertLessEqual(queued, count + 1)

    def test_dense_detailer_image_ancestry_queues_each_node_once(self):
        source = _detailed(same_prompt=True)
        expected = _inspect(source, output_node_id="9")
        dense, top, count = _dense_layers("D", source["50"]["inputs"]["image"])
        source.update(dense)
        source["50"]["inputs"]["image"] = top
        result, queued = self.counted(source, output_node_id="9")
        self.assertEqual(_outcomes(result), _outcomes(expected))
        # Dense layers + the decoder + its sampler, plus the latent walk of
        # that sampler (one EmptyLatentImage).
        self.assertLessEqual(queued, count + 3)

    def test_dense_fan_in_beyond_the_budget_is_work_limit(self):
        source = _latent_from({"class_type": "EmptyLatentImage", "inputs": {}})
        dense, top, count = _dense_layers("W", ["5", 0], layers=3, width=800, fan=40)
        source.update(dense)
        source["3"]["inputs"]["latent_image"] = top
        result, queued = self.counted(source)
        self.assertEqual(result["reason_code"], "work-limit")
        self.assertLessEqual(queued, count + 1)


def _wan_selector_graph(width: int, selectors: int, latent_origin: bool) -> dict:
    """Review I-3: WanImageToVideo over ``width``-input joins and LoRAs, with
    ``selectors`` diverging runtime selectors on the positive side."""
    source = _latent_from(None)
    source["50"] = {"class_type": "LoadImage", "inputs": {"image": "a.png"}}
    clip = ["4", 1]
    for index in range(5):
        model = ["4", 0] if index == 0 else [f"lo{index - 1}", 0]
        source[f"lo{index}"] = {
            "class_type": "LoraLoader",
            "inputs": {"model": model, "clip": clip, "lora_name": f"l{index}"},
        }
        clip = [f"lo{index}", 1]
    ends = []
    for prefix, text in (("P", "pos"), ("N", "neg")):
        parts = {}
        for index in range(width):
            source[f"{prefix}l{index}"] = _multiline(f"{text} {index}", False)
            parts[f"string_{index + 1}"] = [f"{prefix}l{index}", 0]
        source[f"{prefix}j"] = _join(width, " ", **parts)
        source[f"{prefix}e"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": [f"{prefix}j", 0], "clip": clip},
        }
        ends.append(f"{prefix}e")
    previous = ends[0]
    for index in range(selectors):
        source[f"alt{index}"] = _encoded(f"alternative {index}")
        source[f"ie{index}"] = {
            "class_type": "ImageExists",
            "inputs": {"image": ["50", 0]},
        }
        source[f"sel{index}"] = _if_else(
            [f"ie{index}", 1], [previous, 0], [f"alt{index}", 0]
        )
        previous = f"sel{index}"
    source["5"] = {
        "class_type": "WanImageToVideo",
        "inputs": {
            "positive": [previous, 0],
            "negative": [ends[1], 0],
            "vae": ["4", 2],
            "start_image": ["50", 0],
            "width": 832,
            "height": 480,
        },
    }
    source["3"]["inputs"].update(positive=["5", 0], negative=["5", 1])
    if latent_origin:
        source["3"]["inputs"]["latent_image"] = ["5", 2]
    else:
        source["5x"] = {"class_type": "EmptyHunyuanLatentVideo", "inputs": {}}
        source["3"]["inputs"]["latent_image"] = ["5x", 0]
    return source


class TestLatentWalkReuse(unittest.TestCase):
    """Review I-3: one latent walk per sampler per resolution, reuse charged."""

    def counted(self, source: dict) -> tuple:
        walks, visits = [], []
        walk, spend = resolver._GraphResolver.latent_stages, resolver._Budget.spend

        def counted_walk(self, *args):
            walks.append(args[0])
            return walk(self, *args)

        def counted_spend(self):
            visits.append(1)
            return spend(self)

        with (
            mock.patch.object(resolver._GraphResolver, "latent_stages", counted_walk),
            mock.patch.object(resolver._Budget, "spend", counted_spend),
        ):
            result = _inspect(source)
        return result, len(walks), len(visits)

    def test_selector_runs_reuse_one_latent_walk(self):
        result, walks, visits = self.counted(_wan_selector_graph(100, 3, True))
        self.assertEqual(result["state"], "ambiguous")
        self.assertEqual(len(result["candidates"]), 8)
        self.assertEqual(walks, 1)
        separate, _, baseline = self.counted(_wan_selector_graph(100, 3, False))
        self.assertEqual(_outcomes(separate).keys(), _outcomes(result).keys())
        # One walk over the conditioning subgraph on top of the text tracing.
        self.assertLess(visits, baseline + 300)

    def test_reused_walk_cost_is_charged_to_each_candidate(self):
        # Two samplers share one latent origin: the second candidate is charged
        # the walk's cost, so its outcome does not depend on tracing order.
        source = _latent_from(
            {
                "class_type": "VAEEncode",
                "inputs": {"pixels": ["50", 0], "vae": ["4", 2]},
            },
            {"50": {"class_type": "LoadImage", "inputs": {"image": "a.png"}}},
        )
        source["41"] = {"class_type": "KSampler", "inputs": dict(source["3"]["inputs"])}
        source["42"] = {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["41", 0], "vae": ["4", 2]},
        }
        source["43"] = {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "b", "images": ["42", 0]},
        }
        # Both candidates share the latent walk's cached cost.
        full = _inspect(source)
        self.assertEqual(
            [item["state"] for item in full["candidates"]], ["resolved", "resolved"]
        )
        for budget in range(1, 12):
            with self.subTest(max_visits=budget):
                both = _inspect(source, max_visits=budget)
                states = [
                    (item["state"], item["reason_code"]) for item in both["candidates"]
                ]
                alone = [
                    _inspect(source, max_visits=budget, output_node_id=anchor)
                    for anchor in ("9", "43")
                ]
                self.assertEqual(
                    states, [(item["state"], item["reason_code"]) for item in alone]
                )


REPO = Path(__file__).resolve().parents[1]
SEMANTICS_UNAVAILABLE = "Inspection node semantics are unavailable"


class TestSemanticsIsolation(unittest.TestCase):
    """Only inspection reads the semantics table; a bad table never breaks ingestion."""

    def resolve_with_table(self, text: str, *values):
        # Windows: the table is written and closed before it is read or removed.
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "semantics.json"
            path.write_text(text, encoding="utf-8")
            inspection_semantics.get_semantics.cache_clear()
            try:
                with (
                    mock.patch.object(inspection_semantics, "SEMANTICS_PATH", path),
                    mock.patch.object(resolver, "_LOGGER") as logger,
                ):
                    self.logged = logger.error.call_args_list
                    return [
                        resolve_prompt_pair(value, profile=profile)
                        for value in values
                        for profile in (
                            resolver.DEFAULT_PROFILE,
                            resolver.INSPECTION_PROFILE,
                        )
                    ]
            finally:
                inspection_semantics.get_semantics.cache_clear()

    def test_stage_shaped_inputs_come_from_the_table(self):
        # Review I-2: the stage-walk input lists live in the semantics table, so
        # a key added there changes the inspection walk and never the default one.
        source = _latent_from(None)
        source.update(
            {
                "30": {"class_type": "MysteryEmbeds", "inputs": {}},
                "31": {"class_type": "MysteryStage", "inputs": {"stage_x": ["30", 0]}},
                "32": {
                    "class_type": "VAEDecode",
                    "inputs": {"samples": ["31", 0], "vae": ["4", 2]},
                },
                "23": {
                    "class_type": "VAEEncode",
                    "inputs": {"pixels": ["32", 0], "vae": ["4", 2]},
                },
            }
        )
        source["3"]["inputs"]["latent_image"] = ["23", 0]
        table = json.loads(
            (REPO / "web" / "js" / "inspection-semantics.json").read_text("utf-8")
        )
        plain_default, plain = self.resolve_with_table(json.dumps(table), source)
        table["stage_inputs"]["shaped"].append("stage_x")
        default, inspected = self.resolve_with_table(json.dumps(table), source)
        self.assertEqual(plain["state"], "resolved")
        self.assertEqual(inspected["state"], "unresolved")
        self.assertEqual(inspected["reason_code"], "unsupported-node")
        self.assertEqual(inspected["node"], {"id": "31", "class_type": "MysteryStage"})
        self.assertEqual(default, plain_default)

    def test_corrupt_table_leaves_default_resolution_working(self):
        for text in ("{not json", "[" * 100_000, "{}"):
            with self.subTest(text=text[:10]):
                default, inspected = self.resolve_with_table(text, graph())
                self.assertEqual(default["state"], "resolved")
                self.assertEqual(default["positive"]["text"], "portrait")
                self.assertEqual(inspected["state"], "unresolved")
                self.assertEqual(inspected["reason"], SEMANTICS_UNAVAILABLE)
                self.assertIsNone(inspected["reason_code"])
                self.assertIsNone(inspected["positive"])
                self.assertEqual(len(self.logged), 1)

    def test_corrupt_table_only_affects_graph_inspection(self):
        parameters = {"parameters": "cat\nNegative prompt: dog\nSteps: 1, Sampler: x"}
        results = self.resolve_with_table("{not json", parameters, {})
        self.assertEqual([item["state"] for item in results[:2]], ["resolved"] * 2)
        self.assertEqual([item["state"] for item in results[2:]], ["absent"] * 2)
        self.assertEqual(self.logged, [])

    def test_corrupt_table_is_a_clear_inspector_outcome(self):
        from utils.prompt_inspector import inspect_prompt_evidence

        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "semantics.json"
            path.write_text("{not json", encoding="utf-8")
            inspection_semantics.get_semantics.cache_clear()
            try:
                with (
                    mock.patch.object(inspection_semantics, "SEMANTICS_PATH", path),
                    self.assertLogs("prompt_manager.prompt_pair_resolver", "ERROR"),
                ):
                    evidence = inspect_prompt_evidence({"prompt": graph()})
            finally:
                inspection_semantics.get_semantics.cache_clear()
        self.assertEqual(evidence["resolution"]["state"], "unresolved")
        self.assertEqual(evidence["resolution"]["reason"], SEMANTICS_UNAVAILABLE)
        self.assertEqual(
            [evidence["prompts"][role]["status"] for role in ("positive", "negative")],
            ["unresolved", "unresolved"],
        )

    def test_default_resolution_never_imports_the_semantics_table(self):
        code = (
            "import sys\n"
            "from utils.prompt_pair_resolver import resolve_prompt_pair\n"
            "graph = {'1': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'a'}},"
            " '3': {'class_type': 'KSampler',"
            " 'inputs': {'positive': ['1', 0], 'negative': ['1', 0]}}}\n"
            "assert resolve_prompt_pair(graph)['state'] == 'resolved'\n"
            "assert 'utils.inspection_semantics' not in sys.modules\n"
        )
        # Fixed interpreter and literal code, no shell.
        completed = subprocess.run(  # nosec B603
            [sys.executable, "-c", code],
            cwd=REPO,
            capture_output=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
