"""The concept fixture files carry the concept inspector facts (app.js:400-414)."""

import json
import math
import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image

try:
    from tests.open_handles import assert_closed
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed

from tests.browser.workspace_concept_fixture import (
    CONCEPT_MODEL,
    CONCEPT_PROMPTS,
    MAX_EPOCH_SECONDS,
    _instant,
    write_concept_png,
)
from utils.prompt_inspector import inspect_prompt_evidence, read_inspection_file


class TestWorkspaceConceptFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(assert_closed, self, self.tmp.name)

    def write(self, prompt_index, round_index):
        prompt = CONCEPT_PROMPTS[prompt_index]
        path = Path(self.tmp.name) / f"{prompt.id}_{round_index + 1:05}.png"
        image = write_concept_png(path, prompt_index, round_index)
        return path, image

    def test_each_image_carries_execution_evidence_and_the_concept_facts(self):
        for prompt_index, round_index in ((0, 0), (0, 2), (3, 1), (5, 3)):
            with self.subTest(prompt=prompt_index, round=round_index):
                prompt = CONCEPT_PROMPTS[prompt_index]
                path, image = self.write(prompt_index, round_index)
                negative = prompt.negatives[round_index % len(prompt.negatives)][1]
                inspection = read_inspection_file(path)
                evidence = inspect_prompt_evidence(inspection.metadata)
                # D21: "execution" is the source the panel badges "Execution recorded".
                self.assertEqual(
                    evidence["prompts"]["positive"],
                    {
                        "status": "resolved",
                        "text": prompt.positive,
                        "source": "execution",
                    },
                )
                self.assertEqual(evidence["prompts"]["negative"]["text"], negative)
                self.assertEqual(evidence["prompts"]["negative"]["source"], "execution")
                graph = json.loads(inspection.metadata["prompt"])
                sampler = next(
                    node["inputs"]
                    for node in graph.values()
                    if node["class_type"] == "KSampler"
                )
                self.assertEqual(
                    sampler["seed"], 42001 + prompt_index * 100 + round_index
                )
                self.assertEqual(sampler["seed"], image["seed"])
                self.assertEqual(
                    (sampler["sampler_name"], sampler["scheduler"]),
                    ("dpmpp_2m", "karras"),
                )
                self.assertEqual((sampler["steps"], sampler["cfg"]), (28, 6.5))
                loader = graph[sampler["model"][0]]
                self.assertEqual(loader["class_type"], "CheckpointLoaderSimple")
                self.assertEqual(loader["inputs"]["ckpt_name"], CONCEPT_MODEL)
                self.assertEqual(CONCEPT_MODEL, "illustrative-model.safetensors")
                with Image.open(path) as source:
                    self.assertEqual(source.size, (image["width"], image["height"]))

    def test_concept_dimensions_follow_the_prompt_index(self):
        expected = [(1536, 1152), (1024, 1536), (1280, 1280)]
        for prompt_index, size in enumerate(expected):
            _, image = self.write(prompt_index, 1)
            self.assertEqual((image["width"], image["height"]), size)


class TestConceptFixtureInstant(unittest.TestCase):
    """One "now" for every relative time the fixture writes (PR #284's midnight)."""

    def test_a_chosen_instant_is_kept(self):
        self.assertEqual(_instant(1_792_713_598), 1_792_713_598.0)
        self.assertEqual(_instant(1_792_713_598.25), 1_792_713_598.25)

    def test_the_wall_clock_when_omitted(self):
        before = time.time()
        now = _instant(None)
        self.assertLessEqual(before, now)
        self.assertLessEqual(now, time.time())

    def test_a_non_number_is_refused(self):
        for value in (True, "1792713598", [1]):
            with self.subTest(value=value), self.assertRaises(TypeError):
                _instant(value)

    def test_an_instant_out_of_range_is_refused(self):
        for value in (0, -1, MAX_EPOCH_SECONDS, math.inf, math.nan):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _instant(value)


if __name__ == "__main__":
    unittest.main()
