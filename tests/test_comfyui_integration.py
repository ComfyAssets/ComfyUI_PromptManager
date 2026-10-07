"""Tests for utils.comfyui_integration."""

import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.comfyui_integration import ComfyUIMetadataIntegration


class TestSaveImageNotPatched(unittest.TestCase):
    """Regression for #75: SaveImage must save each node's own prompt inputs.

    The old SaveImage patch wrote the last-run PromptManager prompt into every
    PromptManager node, so negative prompts were saved as copies of the positive.
    """

    def setUp(self):
        def save_images(
            self_node,
            images,
            filename_prefix="ComfyUI",
            prompt=None,
            extra_pnginfo=None,
        ):
            return {"prompt": prompt}

        self.original_save_images = save_images
        fake_nodes = types.ModuleType("nodes")
        fake_nodes.SaveImage = type("SaveImage", (), {"save_images": save_images})
        self._saved_nodes = sys.modules.get("nodes")
        sys.modules["nodes"] = fake_nodes
        self.fake_nodes = fake_nodes
        ComfyUIMetadataIntegration._instance = None

    def tearDown(self):
        ComfyUIMetadataIntegration._instance = None
        if self._saved_nodes is None:
            sys.modules.pop("nodes", None)
        else:
            sys.modules["nodes"] = self._saved_nodes

    def test_save_images_is_left_untouched(self):
        ComfyUIMetadataIntegration()
        self.assertIs(self.fake_nodes.SaveImage.save_images, self.original_save_images)

    def test_registered_prompt_does_not_rewrite_saved_metadata(self):
        integration = ComfyUIMetadataIntegration()
        integration.register_prompt("pm_1", "positive final text", {})
        prompt = {
            "39": {"class_type": "PromptManager", "inputs": {"text": "a cat"}},
            "40": {"class_type": "PromptManager", "inputs": {"text": "blurry"}},
        }

        result = self.fake_nodes.SaveImage().save_images([], prompt=prompt)

        self.assertEqual(result["prompt"]["39"]["inputs"]["text"], "a cat")
        self.assertEqual(result["prompt"]["40"]["inputs"]["text"], "blurry")

    def test_prompt_registry_still_available_to_nodes(self):
        integration = ComfyUIMetadataIntegration()
        integration.register_prompt("pm_1", "hello", {})
        self.assertEqual(integration.get_current_prompt_text("pm_1"), "hello")


if __name__ == "__main__":
    unittest.main()
