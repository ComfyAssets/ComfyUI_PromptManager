"""Card facts from header records: the shared legacy parser, bounded and exact."""

import json
import os
import sys
import unittest
from unittest.mock import patch

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

    def test_placeholder_and_non_number_seeds_are_none(self):
        for value in ("123", "randomize", True, 1.5, None):
            with self.subTest(seed=value):
                self.assertIsNone(info_seed(graph(seeded(value))))
        self.assertIsNone(card_seed({"seed": "Unknown"}))
        self.assertIsNone(card_seed({"seed": float("inf")}))
        self.assertIsNone(card_seed({"seed": 1e21}))
        self.assertEqual(card_seed({"seed": -3}), -3)
        self.assertEqual(card_seed({"seed": 7.0}), 7)

    def test_seed_beyond_double_precision_is_the_recorded_integer(self):
        # JSON.parse rounds it; the inspector shows the record's own digits.
        case = CASES["seed-beyond-double-precision"]
        rounded = extract_from_records(case["prompt"], None)["seed"]
        self.assertEqual(rounded, float(4974052037430000001))
        self.assertEqual(info_seed({"prompt": case["prompt"]}), 4974052037430000001)

    def test_big_seed_stays_rounded_when_the_exact_parse_disagrees(self):
        # Only a seed that rounds to the parsed value replaces it; otherwise the
        # card prints the double as the browser does (String(2**60)).
        browser = 1152921504606847000
        self.assertEqual(card_seed({"seed": float(2**60)}), browser)
        with patch(
            "utils.card_seed.extract_generation_params",
            return_value={"seed": 2**61},
        ):
            params = header_params(graph(seeded(2**60 + 1)))
        self.assertEqual(card_seed(params), browser)
        with patch(
            "utils.card_seed.extract_generation_params",
            side_effect=GenerationParamsError("throws"),
        ):
            params = header_params(graph(seeded(2**60 + 1)))
        self.assertEqual(card_seed(params), browser)
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

    def test_every_shared_case_matches_the_parser(self):
        for name, case in CASES.items():
            with self.subTest(case=name):
                info = {k: v for k, v in case.items() if v is not None}
                params = header_params(info)
                if params is None:
                    continue
                parsed = extract_from_records(case["prompt"], case["workflow"])
                if name == "seed-beyond-double-precision":
                    parsed = {**parsed, "seed": 4974052037430000001}
                self.assertEqual(params, parsed)
                positive = params["positivePrompt"]
                usable = (
                    isinstance(positive, str)
                    and positive != "No prompt found"
                    and positive.strip()
                )
                self.assertEqual(parsed_positive(params), positive if usable else None)


if __name__ == "__main__":
    unittest.main()
