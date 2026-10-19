"""Conservative prompt-pair resolution contracts, independent of ComfyUI."""

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
        metadata = {"prompt": parity.build("f17")["prompt"]}
        result = resolve_prompt_pair(metadata, profile=resolver.INSPECTION_PROFILE)
        self.assertEqual(
            (result["state"], result["reason_code"]), ("unresolved", "no-sampler")
        )
        self.assertIn("SamplerCustomAdvanced", result["reason"])
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
