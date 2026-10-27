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
        graph = shapes.hires_fix(
            second_positive=shapes.POSITIVE, filter_class="MysteryImageEffect"
        )
        result = _inspect(graph)
        self.assertEqual(result["state"], "ambiguous")
        later = next(
            item for item in result["candidates"] if item["sampler_id"] == "20"
        )
        self.assertEqual(later["reason"], resolver.MULTISTAGE_UNATTRIBUTED_REASON)
        # Stages on the very same conditioning links are one prompt (#381).
        result = _inspect(shapes.hires_fix(filter_class="MysteryImageEffect"))
        self.assertEqual(result["state"], "resolved")

    def test_a_second_earlier_sampler_in_the_ancestry_stays_unattributed(self):
        graph = shapes.hires_fix()
        # Its own encoder: the same links would be one prompt (#381).
        graph["33"] = shapes.encode(shapes.POSITIVE)
        graph["30"] = shapes.ksampler(["33", 0], ["3", 0], ["4", 0])
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
                # D126: the positive says a later stage added conditioning.
                self.assertEqual(
                    result["positive"]["provenance"]["text_source"],
                    resolver.ADDED_CONDITIONING_SOURCE,
                )
                self.assertEqual(
                    result["negative"]["provenance"]["text_source"],
                    "zeroed-conditioning",
                )

    def test_any_added_conditioning_is_labelled_never_measured(self):
        # Ruling I2: a whole other prompt combined in is labelled the same way;
        # no length or "short trigger" heuristic.
        graph = shapes.flux_three_stage()
        graph["31"] = shapes.encode(shapes.OTHER)
        result = _inspect(graph)
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(_texts(result)[0], shapes.POSITIVE)
        self.assertEqual(
            result["positive"]["provenance"]["text_source"],
            resolver.ADDED_CONDITIONING_SOURCE,
        )

    def test_trigger_words_and_added_conditioning_carry_both_labels(self):
        graph = shapes.flux_three_stage()
        graph["7"] = {
            "class_type": "Lora Loader (LoraManager)",
            "inputs": {"model": ["1", 0], "clip": ["2", 0], "text": "x"},
        }
        graph["8"] = shapes.trigger_toggle()
        graph["30"]["inputs"]["prepend_text"] = ["8", 0]
        result = _inspect(graph)
        self.assertEqual(result["state"], "resolved")
        self.assertEqual(
            result["positive"]["provenance"]["text_source"],
            resolver.RUNTIME_AND_ADDED_SOURCE,
        )

    def test_a_custom_advanced_last_stage_keeps_its_failure(self):
        # Review M2: a guider's combined positive is not floored.
        graph = shapes.flux_three_stage()
        graph["60"] = {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {"guider": ["63", 0], "latent_image": ["66", 0]},
        }
        graph["63"] = {
            "class_type": "CFGGuider",
            "inputs": {"positive": ["35", 0], "negative": ["33", 0]},
        }
        graph["66"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["51", 0]}}
        graph["67"] = {"class_type": "VAEDecode", "inputs": {"samples": ["60", 0]}}
        graph["61"]["inputs"]["images"] = ["67", 0]
        result = _inspect(graph, output_node_id="61")
        self.assertEqual(
            (result["state"], result["reason_code"]), ("unresolved", "composite")
        )

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


class TestRunTimePromptManagerParts(unittest.TestCase):
    """Maintainer option A: a run-time prepend or append is left out, labelled."""

    def test_trigger_words_prepend_resolves_to_the_prompt_manager_text(self):
        for anchor in (None, "22"):
            with self.subTest(anchor=anchor):
                result = _inspect(
                    shapes.hires_fix_with_trigger_words(), output_node_id=anchor
                )
                self.assertEqual(result["state"], "resolved")
                self.assertEqual(_texts(result), (shapes.POSITIVE, shapes.NEGATIVE))
                self.assertEqual(result["reason"], resolver.RUNTIME_PARTS_REASON)
                provenance = result["positive"]["provenance"]
                self.assertEqual(
                    provenance["text_source"], resolver.RUNTIME_PARTS_SOURCE
                )
                self.assertEqual(result["positive"]["storage_text"], shapes.POSITIVE)
                # The negative is untouched by the positive's run-time part.
                self.assertEqual(
                    result["negative"]["provenance"]["text_source"], "graph-inferred"
                )

    def test_append_and_a_single_sampler_are_labelled_too(self):
        graph = shapes.hires_fix_with_trigger_words()
        graph["2"]["inputs"]["prepend_text"] = "masterpiece"
        graph["2"]["inputs"]["append_text"] = ["8", 0]
        graph["20"]["inputs"]["latent_image"] = ["4", 0]
        result = _inspect(graph, output_node_id="22")
        self.assertEqual(result["state"], "resolved")
        # The literal prepend is still combined exactly as before.
        self.assertEqual(_texts(result)[0], "masterpiece " + shapes.POSITIVE)
        self.assertEqual(result["reason"], resolver.RUNTIME_PARTS_REASON)

    def test_the_inspector_shows_the_prompt_and_its_source(self):
        evidence = inspect_prompt_evidence(
            {"prompt": json.dumps(shapes.hires_fix_with_trigger_words())}
        )
        self.assertEqual(evidence["resolution"]["state"], "resolved")
        self.assertEqual(
            evidence["prompts"]["positive"],
            {
                "status": "resolved",
                "text": shapes.POSITIVE,
                "source": resolver.RUNTIME_PARTS_SOURCE,
            },
        )

    def test_literal_and_traceable_parts_are_combined_as_before(self):
        graph = shapes.hires_fix_with_trigger_words()
        graph["8"] = {"class_type": "PrimitiveString", "inputs": {"value": "trig9word"}}
        result = _inspect(graph)
        self.assertEqual(_texts(result)[0], "trig9word " + shapes.POSITIVE)
        self.assertEqual(result["reason"], "")
        self.assertEqual(
            result["positive"]["provenance"]["text_source"], "graph-inferred"
        )

    def test_only_a_run_time_feeder_is_left_out(self):
        # An unknown feeder stays unsupported, naming it.
        graph = shapes.hires_fix_with_trigger_words()
        graph["8"]["class_type"] = "MysteryWords"
        result = _inspect(graph)
        self.assertEqual(result["reason_code"], "unsupported-node")
        self.assertEqual(result["node"]["class_type"], "MysteryWords")
        # A run-time node deeper inside the part makes the whole part run time,
        # but it is not a direct feeder: never leave out a traced literal.
        graph = shapes.hires_fix_with_trigger_words()
        graph["9"] = {
            "class_type": "StringConcatenate",
            "inputs": {"string_a": "kept", "string_b": ["8", 0], "delimiter": ""},
        }
        graph["2"]["inputs"]["prepend_text"] = ["9", 0]
        result = _inspect(graph)
        self.assertEqual(result["reason_code"], "runtime-value")

    def test_the_main_text_from_a_run_time_node_is_never_left_out(self):
        graph = shapes.hires_fix_with_trigger_words()
        graph["2"]["inputs"]["prepend_text"] = ""
        graph["2"]["inputs"]["text"] = ["8", 0]
        result = _inspect(graph)
        self.assertEqual(result["reason_code"], "runtime-value")

    def test_the_label_survives_nodes_between_the_prompt_manager_and_sampler(self):
        # Review C1: encoders, chained PromptManagers and concatenations.
        expected = {
            "text-encode": shapes.POSITIVE,
            "chained": shapes.POSITIVE,
            "concat": shapes.POSITIVE + ", extra",
        }
        for shape, text in expected.items():
            with self.subTest(shape=shape):
                result = _inspect(shapes.trigger_words_through(shape))
                self.assertEqual(result["state"], "resolved")
                self.assertEqual(_texts(result)[0], text)
                self.assertEqual(result["reason"], resolver.RUNTIME_PARTS_REASON)
                self.assertEqual(
                    result["positive"]["provenance"]["text_source"],
                    resolver.RUNTIME_PARTS_SOURCE,
                )

    def test_a_partial_prompt_never_equals_a_complete_one(self):
        # Review C2: never collapse a branch that left out trigger words with
        # one that did not, nor compare such stages as equal.
        result = _inspect(shapes.branches_with_and_without_trigger_words())
        self.assertEqual(
            (result["state"], result["reason"]),
            ("ambiguous", "Multiple sampler branches"),
        )
        graph = shapes.hires_fix_with_trigger_words()
        graph["9"] = shapes.prompt_manager(shapes.POSITIVE)
        graph["20"]["inputs"]["positive"] = ["9", 0]
        result = _inspect(graph, output_node_id="22")
        self.assertEqual(
            (result["state"], result["reason_code"]),
            ("ambiguous", "multistage-differs"),
        )
        # Two PromptManagers leaving out two different toggles differ too.
        graph["8b"] = shapes.trigger_toggle("otherword")
        graph["9"]["inputs"]["prepend_text"] = ["8b", 0]
        result = _inspect(graph, output_node_id="22")
        self.assertEqual(result["reason_code"], "multistage-differs")

    def test_only_listed_run_time_classes_are_left_out(self):
        # Review M1: a JoinStringMulti with a linked list flag is run time
        # too, but it is not a listed run-time node: never left out.
        graph = shapes.hires_fix()
        graph["9"] = {
            "class_type": "JoinStringMulti",
            "inputs": {
                "inputcount": 2,
                "delimiter": " ",
                "return_list": ["98", 0],
                "string_1": "static words",
                "string_2": "more",
            },
        }
        graph["98"] = {"class_type": "PrimitiveBoolean", "inputs": {"value": False}}
        graph["2"]["inputs"]["prepend_text"] = ["9", 0]
        result = _inspect(graph)
        self.assertEqual(
            (result["state"], result["reason_code"]), ("unresolved", "runtime-value")
        )
        self.assertEqual(result["node"]["class_type"], "JoinStringMulti")

    def test_ingestion_is_unchanged(self):
        result = resolve_prompt_pair({"prompt": shapes.hires_fix_with_trigger_words()})
        self.assertEqual(result["state"], "ambiguous")


class TestStillAmbiguous(unittest.TestCase):
    def test_edit_batch_with_distinct_prompts_stays_ambiguous(self):
        result = _inspect(shapes.edit_batch(4))
        self.assertEqual(result["state"], "ambiguous")
        self.assertEqual(result["reason"], "Multiple sampler branches")
        self.assertEqual(len({_texts(item)[0] for item in result["candidates"]}), 4)


if __name__ == "__main__":
    unittest.main()
