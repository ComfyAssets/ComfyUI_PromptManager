"""Unknown nodes between the prompt and the sampler pass through (#381).

The inspection resolver no longer needs a node class on an allow-list to read
through it. An output the semantics table does not describe passes through: a
``text`` input is the text, a ``positive``/``negative`` input routes by output
slot, a ``conditioning`` input is followed alone, otherwise every linked input
except core type inputs (``pass_through.skip_inputs``) is followed. One text,
or several equal texts, resolve; PromptManager nodes are authoritative; several
different texts are one candidate each. Ingestion (the default profile) is
unchanged. Every graph here is synthetic, with neutral texts.
"""

import json
import unittest

from utils import prompt_pair_resolver as resolver
from utils.prompt_inspector import inspect_prompt_evidence
from utils.prompt_pair_resolver import resolve_prompt_pair

try:
    from tests import unknown_node_graphs as graphs
except ImportError:  # discovered with ``-s tests``
    import unknown_node_graphs as graphs

POSITIVE, OTHER, NEGATIVE = graphs.POSITIVE, graphs.OTHER, graphs.NEGATIVE
any_switch_graph = graphs.any_switch_graph
_encode, _pair, _with = graphs.encode, graphs.pair, graphs.with_nodes


def _inspect(graph, **options):
    return resolve_prompt_pair(
        {"prompt": graph}, profile=resolver.INSPECTION_PROFILE, **options
    )


def _texts(result):
    return tuple(
        result[role]["text"] if isinstance(result[role], dict) else None
        for role in ("positive", "negative")
    )


class TestTheReportedGraph(unittest.TestCase):
    def test_prompt_manager_pair_through_any_switch_resolves(self):
        result = _inspect(any_switch_graph())
        self.assertEqual(result["state"], "resolved", result["reason"])
        self.assertEqual(_texts(result), (POSITIVE, ""))
        self.assertEqual(result["sampler_id"], "17")
        self.assertIsNone(result["reason_code"])
        path = [step["node_id"] for step in result["positive"]["provenance"]["path"]]
        self.assertEqual(path, ["15", "10"])

    def test_the_inspector_shows_the_prompt(self):
        evidence = inspect_prompt_evidence({"prompt": json.dumps(any_switch_graph())})
        self.assertEqual(evidence["resolution"]["state"], "resolved")
        self.assertEqual(evidence["prompts"]["positive"]["status"], "resolved")
        self.assertEqual(evidence["prompts"]["positive"]["text"], POSITIVE)

    def test_ingestion_is_unchanged(self):
        result = resolve_prompt_pair({"prompt": any_switch_graph()})
        self.assertEqual(result["state"], "unresolved")
        # Its first failure is the custom latent node, as before this change.
        self.assertEqual(result["reason"], "Unsupported latent ancestry")

    def test_the_inspection_version_moves(self):
        self.assertEqual(resolver.INSPECTION_RESOLVER_VERSION, 2)
        self.assertEqual(resolver.RESOLVER_VERSION, 1)


class TestSourcesThroughUnknownNodes(unittest.TestCase):
    def test_several_inputs_with_one_text_resolve(self):
        graph = any_switch_graph(any_01=["10", 0], any_02=["10", 0])
        self.assertEqual(_texts(_inspect(graph)), (POSITIVE, ""))

    def test_equal_texts_from_different_nodes_resolve(self):
        graph = _with(
            any_switch_graph(any_01=["20", 0], any_02=["21", 0]),
            **{"20": _encode(OTHER), "21": _encode(OTHER)},
        )
        result = _inspect(graph)
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(_texts(result)[0], OTHER)

    def test_different_texts_are_candidates_not_a_refusal(self):
        graph = _with(
            any_switch_graph(any_01=["20", 0], any_02=["21", 0]),
            **{"20": _encode(POSITIVE), "21": _encode(OTHER)},
        )
        result = _inspect(graph)
        self.assertEqual(result["state"], "ambiguous")
        self.assertEqual(result["reason"], resolver.SEVERAL_SOURCES_REASON)
        self.assertEqual(
            [_texts(item) for item in result["candidates"]],
            [(POSITIVE, ""), (OTHER, "")],
        )
        for item in result["candidates"]:
            self.assertEqual(item["state"], "resolved")
            self.assertEqual(item["sampler_id"], "17")

    def test_prompt_manager_is_authoritative(self):
        graph = _with(
            any_switch_graph(any_01=["20", 0], any_02=["10", 0]),
            **{"20": _encode(OTHER)},
        )
        result = _inspect(graph)
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(_texts(result), (POSITIVE, ""))

    def test_prompt_manager_wins_over_an_unreadable_branch(self):
        graph = _with(
            any_switch_graph(any_01=["20", 0], any_02=["10", 0]),
            **{
                "20": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {"text": ["21", 0], "clip": ["7", 0]},
                },
                "21": {"class_type": "PromptSearchList", "inputs": {"search": "x"}},
            },
        )
        self.assertEqual(_texts(_inspect(graph)), (POSITIVE, ""))

    def test_an_unreadable_branch_blocks_without_prompt_manager(self):
        graph = _with(
            any_switch_graph(any_01=["20", 0], any_02=["22", 0]),
            **{
                "20": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {"text": ["21", 0], "clip": ["7", 0]},
                },
                "21": {"class_type": "PromptSearchList", "inputs": {"search": "x"}},
                "22": _encode(OTHER),
            },
        )
        result = _inspect(graph)
        self.assertEqual(result["state"], "unresolved")
        self.assertEqual(result["reason_code"], "runtime-value")

    def test_chained_unknown_nodes(self):
        graph = _with(
            any_switch_graph(any_01=["20", 0]),
            **{
                "20": {
                    "class_type": "Router Node (vendor)",
                    "inputs": {"in": ["10", 0]},
                }
            },
        )
        self.assertEqual(_texts(_inspect(graph)), (POSITIVE, ""))

    def test_no_text_names_the_unknown_node(self):
        graph = any_switch_graph(any_01=["7", 0])
        result = _inspect(graph)
        self.assertEqual(result["state"], "unresolved")
        self.assertEqual(result["reason_code"], "unsupported-node")
        self.assertEqual(
            result["node"], {"id": "15", "class_type": "Any Switch (rgthree)"}
        )

    def test_another_sampler_is_never_entered(self):
        graph = any_switch_graph(any_01=["17", 0], any_02=["10", 0])
        graph["30"] = {
            "class_type": "KSampler",
            "inputs": {**graph["17"]["inputs"], "positive": ["31", 0]},
        }
        graph["31"] = _encode(OTHER)
        graph["15"]["inputs"]["any_01"] = ["30", 0]
        result = _inspect(graph)
        found = {item["sampler_id"]: _texts(item) for item in result["candidates"]}
        self.assertEqual(found["17"], (POSITIVE, ""))


class TestLegacyShapedRules(unittest.TestCase):
    def test_a_text_input_is_the_text(self):
        graph = _with(
            any_switch_graph(any_01=["20", 0]),
            **{
                "20": {
                    "class_type": "VendorTextEncode",
                    "inputs": {"text": OTHER, "clip": ["9", 1]},
                }
            },
        )
        self.assertEqual(_texts(_inspect(graph))[0], OTHER)

    def test_role_inputs_route_by_output_slot(self):
        graph = any_switch_graph()
        graph["20"] = {
            "class_type": "VendorControlApply",
            "inputs": {
                "positive": ["21", 0],
                "negative": ["22", 0],
                "control_net": ["8", 0],
                "image": ["8", 0],
            },
        }
        graph["21"] = _encode(POSITIVE)
        graph["22"] = _encode(NEGATIVE)
        graph["17"]["inputs"]["positive"] = ["20", 0]
        graph["17"]["inputs"]["negative"] = ["20", 1]
        self.assertEqual(_texts(_inspect(graph)), (POSITIVE, NEGATIVE))

    def test_a_conditioning_input_is_followed_alone(self):
        graph = _with(
            any_switch_graph(any_01=["20", 0]),
            **{
                "20": {
                    "class_type": "VendorStyleApply",
                    "inputs": {"conditioning": ["10", 0], "style": ["21", 0]},
                },
                "21": _encode(OTHER),
            },
        )
        self.assertEqual(_texts(_inspect(graph)), (POSITIVE, ""))

    def test_core_type_inputs_are_skipped(self):
        # The vendor encoder's ``clip`` reaches a loader with its own ``text``:
        # that text is a lora tag, never the prompt.
        graph = _with(
            any_switch_graph(any_01=["20", 0]),
            **{
                "20": {
                    "class_type": "VendorPromptEncode",
                    "inputs": {"prompt": ["21", 0], "clip": ["22", 1]},
                },
                "21": {"class_type": "PrimitiveString", "inputs": {"value": OTHER}},
                "22": {
                    "class_type": "VendorLoraLoader",
                    "inputs": {"text": "<lora:x:1>", "clip": ["7", 0]},
                },
            },
        )
        result = _inspect(graph)
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(_texts(result)[0], OTHER)


class TestBounds(unittest.TestCase):
    def test_a_long_unknown_chain_reaches_the_depth_limit(self):
        graph = any_switch_graph(any_01=["100", 0])
        for index in range(100, 100 + resolver.INSPECTION_PROFILE.max_depth + 2):
            graph[str(index)] = {
                "class_type": "Router Node (vendor)",
                "inputs": {"in": [str(index + 1), 0]},
            }
        graph[str(index + 1)] = _pair()
        result = _inspect(graph)
        self.assertEqual(result["state"], "unresolved")
        self.assertEqual(result["reason_code"], "depth-limit")

    def test_many_different_sources_hit_the_candidate_cap(self):
        inputs = {}
        graph = any_switch_graph()
        for index in range(resolver._MAX_BRANCH_CANDIDATES + 1):
            graph[str(200 + index)] = _encode(f"{OTHER} {index}")
            inputs[f"any_{index:02}"] = [str(200 + index), 0]
        graph["15"]["inputs"] = inputs
        result = _inspect(graph)
        self.assertEqual(result["state"], "unresolved")
        self.assertEqual(result["reason_code"], "work-limit")


if __name__ == "__main__":
    unittest.main()


class TestGeneratedAndTransformedText(unittest.TestCase):
    def test_a_seeded_text_node_is_generated_at_run_time(self):
        result = _inspect(graphs.generated_text_graph())
        self.assertEqual(result["state"], "unresolved")
        self.assertEqual(result["reason_code"], "runtime-value")
        self.assertEqual(
            result["node"], {"id": "30", "class_type": "VendorTextGenerate"}
        )

    def test_the_recorded_text_is_authoritative(self):
        # The record is what ran: a generated or transformed text is never
        # checked against the generator's or transform's inputs.
        for name, records in graphs.recorded_shapes().items():
            with self.subTest(shape=name):
                metadata = {
                    "prompt": json.loads(records["prompt"]),
                    "comfyassets_prompt_pairs": records["comfyassets_prompt_pairs"],
                }
                result = resolve_prompt_pair(
                    metadata, profile=resolver.INSPECTION_PROFILE
                )
                self.assertEqual(result["state"], "resolved", result["reason"])
                self.assertEqual(_texts(result), (graphs.GENERATED, ""))
                self.assertEqual(
                    result["positive"]["provenance"]["text_source"], "execution"
                )

    def test_a_seeded_conditioning_node_still_passes_through(self):
        graph = _with(
            any_switch_graph(any_01=["20", 0]),
            **{
                "20": {
                    "class_type": "VendorConditioningNoise",
                    "inputs": {"conditioning": ["10", 0], "seed": 3},
                }
            },
        )
        self.assertEqual(_texts(_inspect(graph)), (POSITIVE, ""))
