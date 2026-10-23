"""Tests for utils.generation_params, the Python port of the browser parser.

Expected values are what web/js/comfy-metadata.js extractGenerationParams
returns for the same input (tests/test_generation_params_js_parity.py checks
that directly when Node is available).
"""

import json
import os
import shutil
import subprocess  # nosec B404 - runs a fixed Node one-liner
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.generation_params import (  # noqa: E402
    PLACEHOLDERS,
    GenerationParamsError,
    extract_generation_params,
    extract_from_records,
    parse_js_json,
    parse_page_json,
)

try:
    from tests.generation_params_cases import CASES  # noqa: E402
except ImportError:  # discovered with tests/ as the top-level directory
    from generation_params_cases import CASES  # noqa: E402


def run(name):
    return extract_from_records(CASES[name]["prompt"], CASES[name]["workflow"])


class TracesPrompts(unittest.TestCase):
    def test_sampler_prompts_and_settings(self):
        self.assertEqual(
            run("basic"),
            {
                "positivePrompt": "a cat on a windowsill",
                "negativePrompt": "blurry, watermark",
                "checkpoint": "example.safetensors",
                "steps": 20,
                "cfgScale": 7,
                "sampler": "euler",
                "seed": 5,
            },
        )

    def test_prompt_manager_joins_prepend_and_append_once(self):
        result = run("prompt-manager-prepend-append")
        self.assertEqual(result["positivePrompt"], "masterpiece a cat 4k")
        # The text already starts with the prepend text, so it is not repeated.
        self.assertEqual(result["negativePrompt"], "masterpiece, a dog 4k")

    def test_linked_text_joins_text_inputs_with_the_delimiter(self):
        result = run("linked-text-concat")
        self.assertEqual(result["positivePrompt"], "first part, second part")
        self.assertEqual(result["negativePrompt"], PLACEHOLDERS["negativePrompt"])

    def test_prompt_manager_pair_outputs(self):
        result = run("prompt-manager-pair")
        self.assertEqual(result["positivePrompt"], "pair positive")
        self.assertEqual(result["negativePrompt"], "pair negative")

    def test_conditioning_passthrough_follows_the_role(self):
        result = run("conditioning-passthrough")
        self.assertEqual(result["positivePrompt"], "through a controlnet")
        self.assertEqual(result["negativePrompt"], "negative through it")

    def test_several_samplers_sharing_one_prompt_pair(self):
        result = run("shared-prompt-pipeline")
        self.assertEqual(result["positivePrompt"], "a lighthouse at dusk, oil painting")
        self.assertEqual(result["negativePrompt"], "blurry, watermark")
        self.assertEqual(
            (result["seed"], result["steps"], result["sampler"]), (111, 4, "lcm")
        )

    def test_overwritten_prompt_manager_text_is_recovered_from_widgets(self):
        result = run("overwritten-prompt-manager-text")
        self.assertEqual(result["positivePrompt"], "the real positive")
        self.assertEqual(result["negativePrompt"], "the real negative")


class ChoosesTheSampler(unittest.TestCase):
    def test_first_sampler_follows_javascript_key_order(self):
        # Integer-like IDs come first in ascending order, then the others.
        result = run("first-sampler-in-js-key-order")
        self.assertEqual(result["positivePrompt"], "from sampler five")
        self.assertEqual(result["seed"], 55)

    def test_custom_sampler_reads_its_guider_and_split_settings(self):
        result = run("custom-sampler-cfg-guider")
        self.assertEqual(result["positivePrompt"], "a red fox in snow")
        self.assertEqual(result["negativePrompt"], "lowres")
        self.assertEqual(
            (result["seed"], result["steps"], result["cfgScale"], result["sampler"]),
            (42, 8, 3.5, "dpmpp_2m"),
        )

    def test_a_guider_without_prompt_inputs_leaves_prompts_unknown(self):
        result = run("custom-sampler-basic-guider")
        self.assertEqual(result["positivePrompt"], PLACEHOLDERS["positivePrompt"])
        # KSamplerSelect still matches the loose /ksampler/ settings lookup.
        self.assertEqual(result["sampler"], "dpmpp_2m")
        self.assertEqual(result["seed"], PLACEHOLDERS["seed"])

    def test_no_sampler_reads_loose_settings(self):
        result = run("no-sampler-loose-settings")
        self.assertEqual(result["positivePrompt"], PLACEHOLDERS["positivePrompt"])
        self.assertEqual(
            (result["seed"], result["steps"], result["cfgScale"], result["sampler"]),
            (9, 12, 2, "ddim"),
        )
        self.assertEqual(result["checkpoint"], "example.safetensors")

    def test_linked_seed_prefers_the_input_of_the_same_name(self):
        self.assertEqual(run("primitive-seed")["seed"], 77)


class ReadsTheWorkflow(unittest.TestCase):
    def test_workflow_only_file(self):
        self.assertEqual(
            run("workflow-only"),
            {
                "positivePrompt": "a tidepool at noon",
                "negativePrompt": "noisy",
                "checkpoint": "example.safetensors",
                "steps": 25,
                "cfgScale": 6.5,
                "sampler": "dpmpp_2m",
                "seed": 314,
            },
        )


class StaysBounded(unittest.TestCase):
    def test_cycles_terminate(self):
        result = run("conditioning-cycle")
        self.assertEqual(result["positivePrompt"], PLACEHOLDERS["positivePrompt"])

    def test_long_chains_resolve_within_the_visit_limit(self):
        self.assertEqual(
            run("chain-within-visit-limit")["positivePrompt"], "deep chain text"
        )

    def test_chains_beyond_the_visit_limit_stop_without_recursion_errors(self):
        result = run("chain-beyond-visit-limit")
        self.assertEqual(result["positivePrompt"], PLACEHOLDERS["positivePrompt"])
        self.assertEqual(result["negativePrompt"], "deep chain text")

    def test_the_visit_limit_boundary_matches_the_browser_parser(self):
        # 499 pass-through nodes and the encoder are exactly 500 visits.
        self.assertEqual(
            run("chain-at-last-visit")["positivePrompt"], "deep chain text"
        )
        self.assertEqual(
            run("chain-one-past-last-visit")["positivePrompt"],
            PLACEHOLDERS["positivePrompt"],
        )

    def test_very_long_chains_do_not_exhaust_the_python_stack(self):
        graph = {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "t"}}}
        for index in range(5000):
            graph[str(10 + index)] = {
                "class_type": "P",
                "inputs": {"conditioning": [str(9 + index if index else 1), 0]},
            }
        graph["2"] = {
            "class_type": "KSampler",
            "inputs": {
                "positive": ["5009", 0],
                "negative": ["1", 0],
                "model": ["1", 0],
            },
        }
        result = extract_generation_params({"prompt": graph})
        self.assertEqual(result["positivePrompt"], PLACEHOLDERS["positivePrompt"])
        self.assertEqual(result["negativePrompt"], "t")


class DeeplyNestedValues(unittest.TestCase):
    def test_deeply_nested_values_do_not_exhaust_the_python_stack(self):
        result = run("deeply-nested-values")
        self.assertEqual(result["positivePrompt"], "nested neighbours")


class Placeholders(unittest.TestCase):
    def test_blank_text_is_a_placeholder_and_zero_seed_is_kept(self):
        result = run("blank-text-and-zero-seed")
        self.assertEqual(result["positivePrompt"], PLACEHOLDERS["positivePrompt"])
        self.assertEqual(result["negativePrompt"], "kept negative")
        self.assertEqual(result["seed"], 0)

    def test_nothing_recorded(self):
        self.assertEqual(run("nothing"), dict(PLACEHOLDERS))
        self.assertEqual(run("unparsable-records"), dict(PLACEHOLDERS))


class MalformedInput(unittest.TestCase):
    def test_a_null_workflow_node_fails_like_the_browser_parser(self):
        with self.assertRaises(GenerationParamsError):
            run("null-workflow-node")


class ParsesLikeJavaScript(unittest.TestCase):
    def test_rejects_non_standard_numbers(self):
        for text in ('{"a": NaN}', '{"a": Infinity}', "[-Infinity]", "not json", ""):
            with self.subTest(text=text):
                self.assertIsNone(parse_js_json(text))

    def test_large_integers_round_like_doubles(self):
        self.assertEqual(
            parse_js_json('{"seed": 9007199254740993}'), {"seed": 9007199254740992.0}
        )
        self.assertEqual(
            parse_js_json('{"seed": 9007199254740991}'), {"seed": 9007199254740991}
        )

    def test_seeds_beyond_double_precision_round_like_the_browser(self):
        self.assertEqual(
            run("seed-beyond-double-precision")["seed"], 4974052037430000001.0
        )

    def test_page_parse_reads_nan_numbers_as_null(self):
        # gallery.js and admin.js replace NaN with null before JSON.parse.
        self.assertEqual(
            parse_page_json('{"a": NaN, "b": [NaN], "t": "NaN"}'),
            {"a": None, "b": [None], "t": "NaN"},
        )
        self.assertEqual(
            parse_page_json('{"seed": 9007199254740993}'), {"seed": 9007199254740992.0}
        )
        for text in ('{"a": Infinity}', "[-Infinity]", "not json", "", None):
            with self.subTest(text=text):
                self.assertIsNone(parse_page_json(text))

    @unittest.skipUnless(shutil.which("node"), "Node is not installed")
    def test_page_parse_equals_the_legacy_pages_for_nan_numbers(self):
        # gallery.js/admin.js: two NaN regexes, then JSON.parse (Infinity throws).
        texts = [
            '{"1": {"is_changed": [NaN]}, "2": {"is_changed": NaN, "v": 1.5}}',
            '{"a":NaN,"b":[NaN, 2]}',
            '{"a": Infinity}',
            '{"seed": 9007199254740993}',
        ]
        script = (
            "const texts = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
            "console.log(JSON.stringify(texts.map((t) => { try { return {v: JSON.parse("
            "t.replace(/:\\s*NaN\\b/g, ': null').replace(/\\bNaN\\b/g, 'null'))}; }"
            " catch (e) { return null; } })));"
        )
        completed = subprocess.run(  # nosec B603 B607 - fixed argv, no shell
            ["node", "-e", script],
            input=json.dumps(texts),
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        expected = json.loads(completed.stdout)
        for text, page in zip(texts, expected):
            with self.subTest(text=text):
                self.assertEqual(parse_page_json(text), page and page["v"])

    def test_duplicate_keys_keep_the_last_value(self):
        self.assertEqual(parse_js_json('{"a": 1, "a": 2}'), {"a": 2})


if __name__ == "__main__":
    unittest.main()
