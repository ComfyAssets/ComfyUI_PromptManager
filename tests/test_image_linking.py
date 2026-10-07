"""Image linking picks the positive prompt, preferring the image's own metadata."""

import os
import sys
import tempfile
import unittest
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.operations import PromptDatabase
from utils.hashing import generate_prompt_hash
from utils.image_monitor import ImageGenerationHandler
from utils.prompt_tracker import PromptTracker

LOADER = {
    "class_type": "CheckpointLoaderSimple",
    "inputs": {"ckpt_name": "m.safetensors"},
}


def graph(positive_text="a cat", negative_text="blurry"):
    # Negative listed first: dict order must not decide which prompt an image gets
    return {
        "176": {"class_type": "PromptManager", "inputs": {"text": negative_text}},
        "134": {"class_type": "PromptManager", "inputs": {"text": positive_text}},
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "positive": ["134", 0],
                "negative": ["176", 0],
                "model": ["20", 0],
            },
        },
        "20": LOADER,
    }


class LinkingTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        fd, self.image = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        self.db = PromptDatabase(self.db_path)
        self.tracker = PromptTracker(self.db)
        self.handler = ImageGenerationHandler(self.db, self.tracker)
        self.linked = []
        self.handler.link_image_to_prompt = (
            lambda path, prompt, meta: self.linked.append(prompt["id"])
        )

    def tearDown(self):
        for path in (
            self.image,
            self.db_path,
            self.db_path + "-wal",
            self.db_path + "-shm",
        ):
            if os.path.exists(path):
                os.unlink(path)

    def _save(self, text):
        return self.db.save_prompt(text=text, prompt_hash=generate_prompt_hash(text))

    def _process(self, metadata):
        with mock.patch.object(
            self.handler.metadata_extractor, "extract_metadata", return_value=metadata
        ):
            self.handler.process_new_image(self.image)
        return self.linked[-1] if self.linked else None


class TestMetadataLinking(LinkingTestCase):
    def test_links_the_positive_prompt_not_the_first_prompt_manager_node(self):
        positive = self._save("a cat")
        self._save("blurry")
        self.assertEqual(self._process({"prompt": graph()}), positive)

    def test_image_metadata_wins_over_a_stale_queue_entry(self):
        positive = self._save("a cat")
        stale = self._save("previous run")
        self.tracker.set_current_prompt("previous run", {"prompt_id": stale})

        self.assertEqual(self._process({"prompt": graph()}), positive)
        self.assertEqual(
            self.tracker.pop_next_prompt()["id"], stale, "queue left intact"
        )

    def test_text_from_a_known_string_node_links_from_metadata(self):
        positive = self._save("a cat")
        linked_graph = graph()
        linked_graph["134"]["inputs"]["text"] = ["8", 0]
        linked_graph["8"] = {
            "class_type": "PrimitiveString",
            "inputs": {"value": "a cat"},
        }
        self.assertEqual(self._process({"prompt": linked_graph}), positive)

    def test_batch_items_with_linked_text_use_the_queue(self):
        first, second = self._save("item one"), self._save("item two")
        batch_graph = graph()
        batch_graph["134"]["inputs"]["text"] = ["7", 0]
        batch_graph["7"] = {"class_type": "PromptSearchList", "inputs": {}}
        self.tracker.set_current_prompt("item one", {"prompt_id": first})
        self.tracker.set_current_prompt("item two", {"prompt_id": second})

        self.assertEqual(self._process({"prompt": batch_graph}), first)
        self.assertEqual(self._process({"prompt": batch_graph}), second)

    def test_api_graph_present_skips_the_role_unaware_workflow_fallback(self):
        negative = self._save("blurry")
        batch_graph = graph()
        batch_graph["134"]["inputs"]["text"] = ["7", 0]
        metadata = {
            "prompt": batch_graph,
            "text_encoder_nodes": [
                {"type": "PromptManager", "inputs": [], "widgets_values": ["blurry"]}
            ],
        }
        self.assertIsNone(self.handler._find_prompt_from_metadata(metadata))
        self.assertNotEqual(self._process(metadata), negative)

    def test_workflow_fallback_still_used_without_an_api_graph(self):
        positive = self._save("from workflow only")
        metadata = {
            "text_encoder_nodes": [
                {
                    "type": "PromptManager",
                    "inputs": [],
                    "widgets_values": ["from workflow only"],
                }
            ]
        }
        self.assertEqual(
            self.handler._find_prompt_from_metadata(metadata)["id"], positive
        )


if __name__ == "__main__":
    unittest.main()
