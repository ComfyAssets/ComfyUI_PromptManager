"""Saver anchoring by file name and shared-conditioning stages (#381).

An inspected file's name anchors the walk on the one saver whose
``filename_prefix`` (literal or traced) makes ``<prefix>_<digits>[_]``; the
samplers in that saver's image ancestry are the candidates. When no saver or
more than one matches, nothing is guessed: the file stays "several possible
prompts". A later stage reading the same positive and negative links as the
earlier stage has the same prompt whatever its latent ancestry. Shapes come
from the maintainer's runs (``tests/saver_anchor_graphs.py``).
"""

import json
import unittest

from utils import prompt_pair_resolver as resolver
from utils.prompt_inspector import inspect_prompt_evidence

try:
    from tests import saver_anchor_graphs as shapes
except ImportError:  # discovered with ``-s tests``
    import saver_anchor_graphs as shapes


def _inspect(graph, file_name=None):
    return resolver.resolve_prompt_pair(
        {"prompt": graph}, profile=resolver.INSPECTION_PROFILE, file_name=file_name
    )


def _positive(result):
    side = result["positive"]
    return side["text"] if isinstance(side, dict) else None


class TestSaverAnchor(unittest.TestCase):
    def test_the_file_name_picks_its_branch(self):
        for index, text in enumerate(shapes.BRANCH_TEXTS, start=1):
            for name in (
                f"s45_branch_{index}_00002_.png",
                f"s45_branch_{index}_00002.webp",
            ):
                with self.subTest(file=name):
                    result = _inspect(shapes.branch_bench(), name)
                    self.assertEqual(result["state"], "resolved")
                    self.assertEqual(_positive(result), text)
                    self.assertEqual(result["sampler_id"], str(index * 10 + 1))

    def test_unanchored_branches_stay_several(self):
        self.assertEqual(_inspect(shapes.branch_bench())["state"], "ambiguous")

    def test_a_traced_prefix_anchors(self):
        result = _inspect(shapes.linked_prefix_bench(), "s45_branch_3_00007_.png")
        self.assertEqual(_positive(result), shapes.BRANCH_TEXTS[2])

    def test_an_unknown_saver_class_anchors_too(self):
        result = _inspect(
            shapes.branch_bench(saver="VendorSaveImage"), "s45_branch_2_00001_.png"
        )
        self.assertEqual(_positive(result), shapes.BRANCH_TEXTS[1])

    def test_no_guessing_when_the_name_matches_none_or_several(self):
        cases = {
            "no saver matches": (shapes.branch_bench(), "photo_00001_.png"),
            "not a counter": (shapes.branch_bench(), "s45_branch_3_final.png"),
            "prefix of another branch": (
                shapes.branch_bench(),
                "s45_branch_00001_.png",
            ),
            "shared prefix": (shapes.shared_prefix(), "IMG_GEN_00371_.png"),
        }
        for label, (graph, name) in cases.items():
            with self.subTest(case=label):
                result = _inspect(graph, name)
                self.assertEqual(result["state"], "ambiguous")
                self.assertEqual(len(result["candidates"]), len(shapes.BRANCH_TEXTS))

    def test_the_inspector_passes_the_file_name(self):
        records = {"prompt": json.dumps(shapes.branch_bench())}
        evidence = inspect_prompt_evidence(records, file_name="s45_branch_4_00009_.png")
        self.assertEqual(
            evidence["prompts"]["positive"]["text"], shapes.BRANCH_TEXTS[3]
        )
        self.assertEqual(
            inspect_prompt_evidence(records)["resolution"]["state"], "ambiguous"
        )

    def test_ingestion_ignores_the_file_name(self):
        result = resolver.resolve_prompt_pair(
            {"prompt": shapes.branch_bench()}, file_name="s45_branch_1_00001_.png"
        )
        self.assertEqual(result["state"], "ambiguous")


class TestSharedConditioningStages(unittest.TestCase):
    def test_a_later_stage_on_the_same_links_resolves(self):
        for name in (None, "final_00003_.png"):
            with self.subTest(file=name):
                result = _inspect(shapes.two_stage_same_conditioning(), name)
                self.assertEqual(result["state"], "resolved", result["reason"])
                self.assertEqual(_positive(result), shapes.BRANCH_TEXTS[0])

    def test_different_links_stay_unattributed(self):
        graph = shapes.two_stage_same_conditioning(second_positive=["91", 0])
        result = _inspect(graph, "final_00003_.png")
        self.assertEqual(result["state"], "unresolved")
        self.assertEqual(result["reason"], resolver.MULTISTAGE_UNATTRIBUTED_REASON)


if __name__ == "__main__":
    unittest.main()


class TestReviewFindings(unittest.TestCase):
    """Shapes from the independent review of the saver-anchor commit."""

    def test_a_skipped_stage_never_hides_its_own_earlier_prompt(self):
        result = _inspect(shapes.hidden_earlier_prompt(), "out_00001_.png")
        self.assertNotEqual(result["state"], "resolved")
        self.assertEqual(result["reason"], resolver.MULTISTAGE_UNATTRIBUTED_REASON)

    def test_a_skipped_stage_never_hides_its_override(self):
        result = _inspect(shapes.hidden_earlier_prompt(override=True), "out_00001_.png")
        self.assertNotEqual(result["state"], "resolved")

    def test_an_unfinished_prefix_scan_anchors_nothing(self):
        graph = shapes.deep_prefixes(9)
        for visits in range(10, 30):
            with self.subTest(visits=visits):
                result = resolver.resolve_prompt_pair(
                    {"prompt": graph},
                    profile=resolver.INSPECTION_PROFILE,
                    max_visits=visits,
                    file_name="dup_00001_.png",
                )
                self.assertNotEqual(result["state"], "resolved")
