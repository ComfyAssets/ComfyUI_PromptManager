"""Multi-stage chains joined through image-space pass-through nodes.

A hires-fix or refiner run decodes sampler A's latent, filters, upscales and
scales the image, then encodes it again for sampler B. The semantics table's
``lineage`` section lists the nodes through which the latent or image carries
on unchanged in kind. When B's latent descends from A only through those nodes
and the stages share one pair, the chain resolves to it. A last stage that adds
conditioning to the base prompt (ConditioningCombine of the base and a style
trigger) keeps the base pair: the floor legacy linked. Everything else stays
ambiguous or unresolved. Ingestion (the default profile) is unchanged (D1).
"""

import json
import unittest

from utils import prompt_pair_resolver as resolver
from utils.prompt_inspector import inspect_prompt_evidence
from utils.prompt_pair_resolver import resolve_prompt_pair

try:
    from tests import passthrough_graphs as shapes
except ImportError:  # discovered with ``-s tests``
    import passthrough_graphs as shapes


def _inspect(graph, **options):
    return resolve_prompt_pair(
        {"prompt": graph}, profile=resolver.INSPECTION_PROFILE, **options
    )


def _texts(result):
    return tuple(
        result[role]["text"] if isinstance(result[role], dict) else None
        for role in ("positive", "negative")
    )


class TestHiresFixChain(unittest.TestCase):
    def test_shared_pair_resolves_through_image_space_pass_through(self):
        graph = shapes.hires_fix()
        for anchor in (None, "22"):
            with self.subTest(anchor=anchor):
                result = _inspect(graph, output_node_id=anchor)
                self.assertEqual(result["state"], "resolved")
                self.assertEqual(result["sampler_id"], "20")
                self.assertEqual(_texts(result), (shapes.POSITIVE, shapes.NEGATIVE))
                self.assertIsNone(result["reason_code"])

    def test_the_earlier_stage_is_judged_in_its_chain(self):
        # Sampler 10 only feeds sampler 20, so the file is 20's: one candidate.
        result = _inspect(shapes.hires_fix())
        self.assertEqual(result["candidates"], [])

    def test_inspector_shows_the_shared_prompt(self):
        evidence = inspect_prompt_evidence({"prompt": json.dumps(shapes.hires_fix())})
        self.assertEqual(evidence["resolution"]["state"], "resolved")
        self.assertEqual(
            [evidence["prompts"][role]["text"] for role in ("positive", "negative")],
            [shapes.POSITIVE, shapes.NEGATIVE],
        )

    def test_different_stage_prompts_are_multistage_ambiguous(self):
        result = _inspect(shapes.hires_fix(second_positive=shapes.OTHER))
        self.assertEqual(result["state"], "ambiguous")
        self.assertEqual(result["reason_code"], "multistage-differs")
        self.assertEqual(
            sorted(_texts(item)[0] for item in result["candidates"]),
            sorted([shapes.POSITIVE, shapes.OTHER]),
        )

    def test_an_unlisted_image_node_breaks_the_lineage(self):
        # The walk still finds the earlier sampler, but not through listed
        # pass-through nodes: the stages are never compared.
        result = _inspect(shapes.hires_fix(filter_class="MysteryImageEffect"))
        self.assertEqual(result["state"], "ambiguous")
        later = next(
            item for item in result["candidates"] if item["sampler_id"] == "20"
        )
        self.assertEqual(later["reason"], resolver.MULTISTAGE_UNATTRIBUTED_REASON)

    def test_a_second_earlier_sampler_in_the_ancestry_stays_unattributed(self):
        graph = shapes.hires_fix()
        graph["30"] = shapes.ksampler(["2", 0], ["3", 0], ["4", 0])
        graph["31"] = {"class_type": "VAEDecode", "inputs": {"samples": ["30", 0]}}
        graph["32"] = {
            "class_type": "ImageBlend",
            "inputs": {"image1": ["14", 0], "image2": ["31", 0], "blend_factor": 0.5},
        }
        graph["16"]["inputs"]["image"] = ["32", 0]
        result = _inspect(graph, output_node_id="22")
        self.assertEqual(result["state"], "unresolved")
        self.assertEqual(result["reason"], resolver.MULTISTAGE_UNATTRIBUTED_REASON)

    def test_a_direct_latent_link_is_a_chain_too(self):
        graph = shapes.hires_fix()
        graph["20"]["inputs"]["latent_image"] = ["10", 0]
        result = _inspect(graph, output_node_id="22")
        self.assertEqual((result["state"], result["sampler_id"]), ("resolved", "20"))

    def test_an_unreadable_earlier_stage_blocks_the_chain(self):
        graph = shapes.hires_fix(second_positive=shapes.POSITIVE)
        graph["10"]["inputs"]["positive"] = ["6", 0]
        graph["6"] = {"class_type": "MysteryPrompt", "inputs": {"seed": 1}}
        result = _inspect(graph, output_node_id="22")
        self.assertEqual(result["state"], "unresolved")
        self.assertEqual(result["reason_code"], "unsupported-node")
        self.assertEqual(result["node"]["class_type"], "MysteryPrompt")

    def test_generated_text_stays_untraceable(self):
        result = _inspect(shapes.generated_text_chain())
        self.assertEqual(result["state"], "unresolved")
        self.assertEqual(result["reason_code"], "unsupported-node")
        self.assertEqual(result["node"]["class_type"], "TextGenerate")

    def test_ingestion_is_unchanged(self):
        # D1: the default profile keeps its historical latent rule.
        result = resolve_prompt_pair({"prompt": shapes.hires_fix()})
        self.assertEqual(result["state"], "ambiguous")
        self.assertEqual(
            {item["reason"] for item in result["candidates"]},
            {"Unsupported latent ancestry"},
        )


class TestFluxThreeStage(unittest.TestCase):
    def test_added_style_conditioning_keeps_the_base_pair(self):
        graph = shapes.flux_three_stage()
        for anchor in (None, "61"):
            with self.subTest(anchor=anchor):
                result = _inspect(graph, output_node_id=anchor)
                self.assertEqual(result["state"], "resolved")
                self.assertEqual(_texts(result), (shapes.POSITIVE, ""))
                self.assertEqual(result["reason"], resolver.ADDED_CONDITIONING_REASON)
                self.assertEqual(result["sampler_id"], "50")

    def test_concat_also_adds_to_the_base_prompt(self):
        result = _inspect(shapes.flux_three_stage(composite="ConditioningConcat"))
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(_texts(result), (shapes.POSITIVE, ""))

    def test_an_average_blends_the_base_away(self):
        result = _inspect(shapes.flux_three_stage(composite="ConditioningAverage"))
        self.assertEqual(
            (result["state"], result["reason_code"]), ("unresolved", "composite")
        )

    def test_a_combine_without_the_base_prompt_is_not_the_base_pair(self):
        graph = shapes.flux_three_stage(combined=(("37", 0), ("31", 0)))
        graph["37"] = shapes.encode(shapes.OTHER)
        result = _inspect(graph)
        self.assertEqual(
            (result["state"], result["reason_code"]), ("unresolved", "composite")
        )

    def test_a_different_last_negative_is_not_the_base_pair(self):
        graph = shapes.flux_three_stage()
        graph["38"] = shapes.encode("watermark")
        graph["60"]["inputs"]["negative"] = ["38", 0]
        result = _inspect(graph)
        self.assertEqual(
            (result["state"], result["reason_code"]), ("unresolved", "composite")
        )

    def test_different_base_stages_are_never_floored(self):
        graph = shapes.flux_three_stage()
        graph["39"] = shapes.encode(shapes.OTHER)
        graph["40"]["inputs"]["positive"] = ["39", 0]
        result = _inspect(graph)
        self.assertNotEqual(result["state"], "resolved")

    def test_a_rerouted_combine_is_followed(self):
        graph = shapes.flux_three_stage()
        graph["39"] = {"class_type": "Reroute", "inputs": {"input": ["35", 0]}}
        graph["60"]["inputs"]["positive"] = ["39", 0]
        result = _inspect(graph)
        self.assertEqual(result["reason"], resolver.ADDED_CONDITIONING_REASON)

    def test_an_unreadable_last_negative_or_encoder_parts_keep_the_failure(self):
        graph = shapes.flux_three_stage()
        graph["38"] = {"class_type": "MysteryNegative", "inputs": {"seed": 1}}
        graph["60"]["inputs"]["negative"] = ["38", 0]
        self.assertEqual(_inspect(graph)["reason_code"], "composite")
        # Differing encoder parts are a composite too, but not an added stage.
        graph = shapes.flux_three_stage()
        graph["39"] = {
            "class_type": "CLIPTextEncodeSDXL",
            "inputs": {"text_g": shapes.POSITIVE, "text_l": shapes.STYLE},
        }
        graph["60"]["inputs"]["positive"] = ["39", 0]
        result = _inspect(graph)
        self.assertEqual(
            (result["state"], result["reason_code"]), ("unresolved", "composite")
        )
        self.assertEqual(result["node"]["class_type"], "CLIPTextEncodeSDXL")

    def test_inspector_shows_the_base_prompt(self):
        evidence = inspect_prompt_evidence(
            {"prompt": json.dumps(shapes.flux_three_stage())}
        )
        self.assertEqual(evidence["resolution"]["state"], "resolved")
        self.assertEqual(evidence["prompts"]["positive"]["text"], shapes.POSITIVE)


class TestStillAmbiguous(unittest.TestCase):
    def test_edit_batch_with_distinct_prompts_stays_ambiguous(self):
        result = _inspect(shapes.edit_batch(4))
        self.assertEqual(result["state"], "ambiguous")
        self.assertEqual(result["reason"], "Multiple sampler branches")
        self.assertEqual(len({_texts(item)[0] for item in result["candidates"]}), 4)


if __name__ == "__main__":
    unittest.main()
