"""Card facts from header records: the shared legacy parser, bounded and exact."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.card_seed import (  # noqa: E402
    MAX_RECORD_BYTES,
    card_seed,
    header_params,
    parsed_positive,
)
from utils.generation_params import (  # noqa: E402
    GenerationParamsError,
    extract_from_records,
    extract_generation_params,
)
from utils.inspection_response import (  # noqa: E402
    MAX_PROJECTION_DEPTH,
    project_graph,
)

try:
    from tests.generation_params_cases import CASES, encoder, ksampler  # noqa: E402
except ImportError:  # discovered with tests/ as the top-level directory
    from generation_params_cases import CASES, encoder, ksampler  # noqa: E402


def graph(*samplers, extra=None):
    nodes = {"6": encoder("a lantern at dusk"), "7": encoder("blurry")}
    for index, sampler in enumerate(samplers):
        nodes[str(10 + index)] = sampler
    nodes.update(extra or {})
    return {"prompt": json.dumps(nodes)}


def seeded(seed, **settings):
    return ksampler(("6", 0), ("7", 0), seed=seed, **settings)


def nested_workflow(depth=900):
    """A small workflow whose node and link ids nest ``depth`` arrays deep."""
    nested = "[" * depth + "1" + "]" * depth
    return (
        '{"nodes":[{"id":%s,"type":"KSampler","widgets_values":[1]}],'
        '"links":[[%s,1,0,2,0]]}' % (nested, nested)
    )


def info_seed(info):
    return card_seed(header_params(info))


class CardSeed(unittest.TestCase):
    def test_card_seed_single_sampler(self):
        info = graph(seeded(42001))
        self.assertEqual(info_seed(info), 42001)
        self.assertEqual(parsed_positive(header_params(info)), "a lantern at dusk")

    def test_card_seed_two_different_seeds_follows_shared_parser(self):
        # Ruling: no separate heuristic; the card shows what the legacy parser
        # (and so the inspector) shows for several samplers.
        info = graph(seeded(111), seeded(222))
        expected = extract_from_records(info["prompt"], None)["seed"]
        self.assertIn(expected, (111, 222))
        self.assertEqual(info_seed(info), expected)

    def test_card_seed_linked_input_follows_shared_parser(self):
        primitive = {"class_type": "PrimitiveNode", "inputs": {"value": 555}}
        info = graph(seeded(["20", 0]), extra={"20": primitive})
        self.assertEqual(info_seed(info), 555)
        dangling = graph(seeded(["99", 0]))
        self.assertEqual(
            extract_from_records(dangling["prompt"], None)["seed"], "Unknown"
        )
        self.assertIsNone(info_seed(dangling))

    def test_card_seed_advanced_sampler_noise_seed(self):
        advanced = seeded(None)
        advanced["class_type"] = "KSamplerAdvanced"
        del advanced["inputs"]["seed"]
        advanced["inputs"]["noise_seed"] = 77
        self.assertEqual(info_seed(graph(advanced)), 77)

    def test_card_seed_oversized_record_is_none(self):
        padding = "x" * (MAX_RECORD_BYTES + 1)
        big_prompt = graph(seeded(1), extra={"30": encoder(padding)})
        self.assertGreater(len(big_prompt["prompt"]), MAX_RECORD_BYTES)
        self.assertIsNone(header_params(big_prompt))
        big_workflow = {**graph(seeded(1)), "workflow": json.dumps({"x": padding})}
        self.assertIsNone(header_params(big_workflow))
        # Bytes, not characters: two-byte text over the budget is oversized.
        wide = "é" * (MAX_RECORD_BYTES // 2 + 1)
        self.assertLessEqual(len(wide), MAX_RECORD_BYTES)
        self.assertIsNone(header_params({"prompt": json.dumps({"a": wide})}))
        self.assertEqual(info_seed(graph(seeded(1))), 1)

    def test_records_past_the_inspection_projection_bound_give_nothing(self):
        # The inspector hands the browser parser only graphs its projection
        # parsed; a deeper graph is "limited" there, so the card skips it too.
        self.assertEqual(
            project_graph(nested_workflow(), "workflow")["status"], "limited"
        )
        self.assertIsNone(header_params({"workflow": nested_workflow()}))
        # A limited workflow beside a parsed prompt leaves the prompt's facts.
        both = header_params({**graph(seeded(1)), "workflow": nested_workflow()})
        self.assertEqual(card_seed(both), 1)

    def test_depth_bound_is_the_inspectors(self):
        def deep(levels):
            nested = "[" * levels + "1" + "]" * levels
            extra = '{"class_type":"Note","inputs":{"v":%s}}' % nested
            text = graph(seeded(3))["prompt"]
            return {"prompt": text[:-1] + ',"30":' + extra + "}"}

        # root object, node object, inputs object: three levels before "v".
        inside = MAX_PROJECTION_DEPTH - 3
        self.assertEqual(info_seed(deep(inside)), 3)
        self.assertIsNone(header_params(deep(inside + 1)))

    def test_strict_projection_rules_apply(self):
        duplicate = '{"10": {"class_type": "KSampler", "inputs": {}}, "10": {}}'
        self.assertEqual(project_graph(duplicate, "prompt")["status"], "malformed")
        self.assertIsNone(header_params({"prompt": duplicate}))

    def test_record_size_is_utf8_bytes_at_both_bounds(self):
        frame = '{"a":"%s"}'
        ascii_at = frame % ("x" * (MAX_RECORD_BYTES - 8))
        self.assertEqual(len(ascii_at), MAX_RECORD_BYTES)
        self.assertIsNotNone(header_params({"prompt": ascii_at}))
        four_byte = frame % ("\U0001f600" * ((MAX_RECORD_BYTES - 8) // 4))
        self.assertLessEqual(len(four_byte.encode()), MAX_RECORD_BYTES)
        self.assertIsNotNone(header_params({"prompt": four_byte}))
        padded = four_byte + " " * (MAX_RECORD_BYTES - len(four_byte.encode()) + 1)
        self.assertIsNone(header_params({"prompt": padded}))

    def test_placeholder_and_non_integer_seeds_are_none(self):
        for value in ("randomize", " 12", True, 1.5, 7.0, 1e21, None):
            with self.subTest(seed=value):
                self.assertIsNone(info_seed(graph(seeded(value))))
        self.assertIsNone(card_seed({"seed": "Unknown"}))
        self.assertIsNone(card_seed({"seed": 7.0}))
        self.assertEqual(info_seed(graph(seeded(-3))), -3)

    def test_digit_string_seed_is_what_the_inspector_reads(self):
        # The projection carries big integers as text and keeps text as text;
        # the inspector shows a digit string as the seed, and so does the card.
        self.assertEqual(info_seed(graph(seeded("123"))), 123)

    def test_seed_beyond_double_precision_is_the_recorded_integer(self):
        # JSON.parse would round it; the projection carries the record's digits.
        case = CASES["seed-beyond-double-precision"]
        rounded = extract_from_records(case["prompt"], None)["seed"]
        self.assertEqual(rounded, float(4974052037430000001))
        self.assertEqual(info_seed({"prompt": case["prompt"]}), 4974052037430000001)
        self.assertEqual(info_seed(graph(seeded(-(2**60) - 1))), -(2**60) - 1)

    def test_workflow_only_record(self):
        case = next(
            case
            for case in CASES.values()
            if case["prompt"] is None
            and case["workflow"] is not None
            and isinstance(extract_from_records(None, case["workflow"])["seed"], int)
        )
        expected = extract_from_records(None, case["workflow"])["seed"]
        self.assertEqual(info_seed({"workflow": case["workflow"]}), expected)

    def test_records_the_parser_cannot_use_give_nothing(self):
        self.assertIsNone(header_params({}))
        self.assertIsNone(header_params(None))
        self.assertIsNone(header_params({"parameters": "a cat\nSteps: 20, Seed: 9"}))
        self.assertIsNone(header_params({"prompt": b"{}"}))
        self.assertIsNone(card_seed(None))
        self.assertIsNone(parsed_positive(None))
        # The browser parser throws here; the card shows nothing.
        thrown = CASES["null-workflow-node"]
        self.assertIsNone(header_params(thrown))
        unparsable = header_params(CASES["unparsable-records"])
        self.assertIsNone(card_seed(unparsable))
        self.assertIsNone(parsed_positive(unparsable))

    def test_every_shared_case_reads_the_projected_graphs(self):
        for name, case in CASES.items():
            with self.subTest(case=name):
                info = {k: v for k, v in case.items() if v is not None}
                params = header_params(info)
                graphs = {key: project_graph(text, key) for key, text in info.items()}
                values = {
                    key: graph["value"]
                    for key, graph in graphs.items()
                    if graph["status"] == "parsed"
                }
                if not values:
                    self.assertIsNone(params)
                    continue
                try:
                    expected = extract_generation_params(values)
                except GenerationParamsError:
                    self.assertIsNone(params)
                    continue
                self.assertEqual(params, expected)
                # Prompt text is the same as from a plain parse of the records.
                if len(values) == len(info):
                    raw = extract_from_records(case["prompt"], case["workflow"])
                    self.assertEqual(params["positivePrompt"], raw["positivePrompt"])


if __name__ == "__main__":
    unittest.main()
