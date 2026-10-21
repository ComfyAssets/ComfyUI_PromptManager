"""Live metadata annotation uses the same conservative ingestion as scan."""

import logging
import tempfile
import unittest
from pathlib import Path

try:
    from tests.open_handles import assert_closed
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed

from database.operations import PromptDatabase
from utils.hashing import generate_prompt_hash
from utils.image_monitor import ImageGenerationHandler


def metadata(positive="cat", negative="blur"):
    return {
        "prompt": {
            "1": {"class_type": "PromptManager", "inputs": {"text": positive}},
            "2": {"class_type": "CLIPTextEncode", "inputs": {"text": negative}},
            "3": {
                "class_type": "KSampler",
                "inputs": {"positive": ["1", 0], "negative": ["2", 0]},
            },
        }
    }


class TestPairImageLinking(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = PromptDatabase(str(Path(self.temp.name) / "db.sqlite"))
        self.path = str(Path(self.temp.name) / "image.png")
        self.pid = self.db.save_prompt("cat", prompt_hash=generate_prompt_hash("cat"))
        self.handler = ImageGenerationHandler.__new__(ImageGenerationHandler)
        self.handler.db_manager = self.db
        self.handler.logger = logging.getLogger("test.pairlink")

    def tearDown(self):
        self.db.close_all()
        assert_closed(self, self.temp.name)
        self.temp.cleanup()

    def test_exact_metadata_creates_pair_link_without_counting_a_run(self):
        self.handler.link_image_to_prompt(self.path, {"id": self.pid}, metadata())
        self.handler.link_image_to_prompt(self.path, {"id": self.pid}, metadata())
        pairs = self.db.list_prompt_pairs()["pairs"]
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["image_count"], 1)
        self.assertEqual(pairs[0]["run_count"], 0)

    def test_parallel_identical_branches_create_their_shared_pair(self):
        # #249: two samplers on one positive and one negative are one certain pair.
        meta = metadata()
        meta["prompt"]["4"] = {
            "class_type": "KSampler",
            "inputs": {"positive": ["1", 0], "negative": ["2", 0]},
        }
        self.handler.link_image_to_prompt(self.path, {"id": self.pid}, meta)
        pairs = self.db.list_prompt_pairs()["pairs"]
        self.assertEqual(
            [(p["positive_text"], p["negative_text"]) for p in pairs], [("cat", "blur")]
        )
        self.assertEqual(pairs[0]["image_count"], 1)
        self.assertEqual(pairs[0]["run_count"], 0)

    def test_stale_context_or_ambiguous_metadata_does_not_create_pair(self):
        meta = metadata(positive="dog")
        self.handler.link_image_to_prompt(self.path, {"id": self.pid}, meta)
        self.assertEqual(self.db.list_prompt_pairs()["total"], 0)
        meta = metadata()
        # Two samplers with different positives (#249: identical ones resolve).
        meta["prompt"]["5"] = {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "dog"},
        }
        meta["prompt"]["4"] = {
            "class_type": "KSampler",
            "inputs": {"positive": ["5", 0], "negative": ["2", 0]},
        }
        self.handler.link_image_to_prompt(self.path + "2", {"id": self.pid}, meta)
        self.assertEqual(self.db.list_prompt_pairs()["total"], 0)
