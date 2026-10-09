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


import threading  # noqa: E402
import unittest.mock as mock  # noqa: E402

from utils import comfyui_integration  # noqa: E402
from utils.comfyui_integration import get_comfyui_integration  # noqa: E402


class IntegrationTestCase(unittest.TestCase):
    def setUp(self):
        ComfyUIMetadataIntegration._instance = None
        comfyui_integration._integration_instance = None
        # Patch the module's own ``time`` name rather than ``time.time`` globally:
        # background threads from other tests (image monitor, watchdog) would
        # otherwise tick this clock and make fresh registrations look stale.
        self.clock = mock.patch.object(
            comfyui_integration, "time", types.SimpleNamespace(time=self._now)
        )
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self._ticks = 1000.0
        self.integration = get_comfyui_integration()

    def tearDown(self):
        ComfyUIMetadataIntegration._instance = None
        comfyui_integration._integration_instance = None

    def _now(self):
        self._ticks += 1.0
        return self._ticks


class TestSingletonAndRegistry(IntegrationTestCase):
    def test_singleton_keeps_registrations_across_constructions(self):
        self.assertIs(get_comfyui_integration(), self.integration)
        self.assertIs(ComfyUIMetadataIntegration(), self.integration)
        self.integration.register_prompt("n1", "hello", {"category": "x"})
        ComfyUIMetadataIntegration()  # __init__ runs again: must not reset state
        self.assertEqual(self.integration.get_current_prompt_text("n1"), "hello")

    def test_latest_prompt_of_this_thread_wins_without_a_node_id(self):
        self.integration.register_prompt("n1", "first", {})
        self.integration.register_prompt("n2", "second", {})
        self.assertEqual(self.integration.get_current_prompt_text(), "second")
        self.assertEqual(self.integration.get_current_prompt_text("missing"), "second")
        self.assertEqual(self.integration.get_current_prompt_text("n1"), "first")

    def test_nothing_registered_returns_none(self):
        self.assertIsNone(self.integration.get_current_prompt_text())
        self.assertIsNone(self.integration.get_current_prompt_text("n1"))


class TestCrossThreadLookup(IntegrationTestCase):
    def _register_in_thread(self, text):
        worker = threading.Thread(
            target=self.integration.register_prompt, args=("w1", text, {})
        )
        worker.start()
        worker.join()

    def test_recent_prompt_from_another_thread_is_used(self):
        self._register_in_thread("from worker")
        self.assertEqual(self.integration.get_current_prompt_text(), "from worker")

    def test_stale_prompt_from_another_thread_is_ignored(self):
        self._register_in_thread("from worker")
        self._ticks += 400.0
        self.assertIsNone(self.integration.get_current_prompt_text())

    def test_global_entry_of_this_thread_is_found_without_thread_local(self):
        self.integration.register_prompt("n1", "mine", {})
        self.integration._thread_local = threading.local()
        self.assertEqual(self.integration.get_current_prompt_text(), "mine")

    def test_cleanup_removes_old_registrations_only(self):
        self._register_in_thread("old")
        self._ticks += 700.0
        self.integration.register_prompt("n2", "new", {})
        self.integration.cleanup_old_prompts(max_age_seconds=600)
        texts = sorted(v["text"] for v in self.integration._current_prompts.values())
        self.assertEqual(texts, ["new"])
        self.integration.cleanup_old_prompts(max_age_seconds=600)  # nothing to do
        self.assertEqual(len(self.integration._current_prompts), 1)


if __name__ == "__main__":
    unittest.main()
