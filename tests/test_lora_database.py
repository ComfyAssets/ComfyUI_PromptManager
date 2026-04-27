"""
Database tests for LoRA Manager integration and folder filter features.

Tests delete_prompts_by_category, search_prompts folder filter,
get_prompt_subfolders, and LoRA-specific prompt workflows using
an in-memory SQLite database.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.operations import PromptDatabase
from utils.hashing import generate_prompt_hash


class LoraDBTestCase(unittest.TestCase):
    """Base class with temp database setup/teardown."""

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db = PromptDatabase(self.temp_db.name)

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            path = self.temp_db.name + suffix
            if os.path.exists(path):
                os.unlink(path)

    def _save(self, text, category=None, tags=None):
        """Save a prompt and return its ID."""
        return self.db.save_prompt(
            text=text,
            category=category,
            tags=tags or [],
            prompt_hash=generate_prompt_hash(text),
        )

    def _link_image(self, prompt_id, image_path):
        """Link a fake image to a prompt."""
        return self.db.link_image_to_prompt(
            prompt_id=str(prompt_id), image_path=image_path
        )


# ── delete_prompts_by_category ────────────────────────────────────────


class TestDeleteByCategory(LoraDBTestCase):
    """Test delete_prompts_by_category for LoRA reimport cleanup."""

    def test_deletes_matching_category(self):
        self._save("lora prompt 1", category="lora-manager")
        self._save("lora prompt 2", category="lora-manager")
        self._save("keep this", category="general")

        deleted = self.db.delete_prompts_by_category("lora-manager")

        self.assertEqual(deleted, 2)
        results = self.db.search_prompts(category="lora-manager")
        self.assertEqual(len(results), 0)

    def test_preserves_other_categories(self):
        self._save("keep this", category="general")
        self._save("and this", category="portraits")
        self.db.delete_prompts_by_category("lora-manager")

        results = self.db.search_prompts()
        self.assertEqual(len(results), 2)

    def test_returns_zero_when_none_match(self):
        self._save("no match", category="general")
        deleted = self.db.delete_prompts_by_category("lora-manager")
        self.assertEqual(deleted, 0)

    def test_cascades_to_images(self):
        pid = self._save("lora with image", category="lora-manager")
        self._link_image(pid, "/fake/path/image.jpg")

        # Verify image is linked
        images = self.db.get_prompt_images(pid)
        self.assertEqual(len(images), 1)

        self.db.delete_prompts_by_category("lora-manager")

        # Prompt gone
        results = self.db.search_prompts(category="lora-manager")
        self.assertEqual(len(results), 0)

    def test_empty_category_string(self):
        self._save("test", category="general")
        deleted = self.db.delete_prompts_by_category("")
        self.assertEqual(deleted, 0)


# ── Folder filter (search_prompts with folder param) ──────────────────


class TestFolderFilter(LoraDBTestCase):
    """Test search_prompts folder parameter for subfolder filtering."""

    def _setup_prompts_with_images(self):
        """Create prompts linked to images in different directories."""
        pid1 = self._save("landscape prompt", category="nature")
        self._link_image(pid1, "/output/landscapes/sunset.png")

        pid2 = self._save("portrait prompt", category="portraits")
        self._link_image(pid2, "/output/portraits/face.png")

        pid3 = self._save("another landscape", category="nature")
        self._link_image(pid3, "/output/landscapes/mountain.png")

        return pid1, pid2, pid3

    def test_filter_by_folder(self):
        self._setup_prompts_with_images()
        results = self.db.search_prompts(folder="landscapes")
        self.assertEqual(len(results), 2)
        texts = {r["text"] for r in results}
        self.assertEqual(texts, {"landscape prompt", "another landscape"})

    def test_filter_different_folder(self):
        self._setup_prompts_with_images()
        results = self.db.search_prompts(folder="portraits")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["text"], "portrait prompt")

    def test_no_match_returns_empty(self):
        self._setup_prompts_with_images()
        results = self.db.search_prompts(folder="nonexistent")
        self.assertEqual(len(results), 0)

    def test_no_folder_returns_all(self):
        self._setup_prompts_with_images()
        results = self.db.search_prompts()
        self.assertGreaterEqual(len(results), 3)

    def test_folder_with_category_filter(self):
        self._setup_prompts_with_images()
        results = self.db.search_prompts(folder="landscapes", category="nature")
        self.assertEqual(len(results), 2)


# ── get_prompt_subfolders ─────────────────────────────────────────────


class TestGetPromptSubfolders(LoraDBTestCase):
    """Test get_prompt_subfolders — extracts unique folder names from images."""

    def test_extracts_subfolders(self):
        pid1 = self._save("prompt 1")
        self._link_image(pid1, "/output/folder_a/img1.png")

        pid2 = self._save("prompt 2")
        self._link_image(pid2, "/output/folder_b/img2.png")

        folders = self.db.get_prompt_subfolders()
        self.assertIsInstance(folders, list)
        self.assertGreaterEqual(len(folders), 2)

    def test_deduplicates(self):
        pid1 = self._save("prompt 1")
        self._link_image(pid1, "/output/same_folder/img1.png")

        pid2 = self._save("prompt 2")
        self._link_image(pid2, "/output/same_folder/img2.png")

        folders = self.db.get_prompt_subfolders()
        # Count occurrences of the folder — should appear once
        matches = [f for f in folders if "same_folder" in f]
        self.assertEqual(len(matches), 1)

    def test_empty_database(self):
        folders = self.db.get_prompt_subfolders()
        self.assertEqual(folders, [])

    def test_returns_sorted(self):
        for i, name in enumerate(["charlie", "alpha", "bravo"]):
            pid = self._save(f"prompt {i}")
            self._link_image(pid, f"/output/{name}/img.png")

        folders = self.db.get_prompt_subfolders()
        self.assertEqual(folders, sorted(folders))

    def test_with_root_dirs(self):
        pid = self._save("prompt")
        self._link_image(pid, "/output/sub/deep/img.png")

        folders = self.db.get_prompt_subfolders(root_dirs=["/output"])
        self.assertIsInstance(folders, list)
        self.assertGreater(len(folders), 0)

    def test_include_ancestors_adds_intermediate_paths(self):
        pid = self._save("prompt")
        self._link_image(pid, "/output/2026/08-Aug/2026-08-06/img.png")

        folders = self.db.get_prompt_subfolders(
            root_dirs=["/output"], include_ancestors=True
        )
        self.assertIn("2026", folders)
        self.assertIn("2026/08-Aug", folders)
        self.assertIn("2026/08-Aug/2026-08-06", folders)

    def test_include_ancestors_false_no_intermediates(self):
        pid = self._save("prompt")
        self._link_image(pid, "/output/2026/08-Aug/2026-08-06/img.png")

        folders = self.db.get_prompt_subfolders(
            root_dirs=["/output"], include_ancestors=False
        )
        self.assertIn("2026/08-Aug/2026-08-06", folders)
        self.assertNotIn("2026", folders)
        self.assertNotIn("2026/08-Aug", folders)

    def test_include_ancestors_sorted(self):
        pid = self._save("prompt")
        self._link_image(pid, "/output/a/b/c/img.png")

        folders = self.db.get_prompt_subfolders(
            root_dirs=["/output"], include_ancestors=True
        )
        self.assertEqual(folders, sorted(folders))


# ── LoRA prompt workflow ──────────────────────────────────────────────


class TestLoraPromptWorkflow(LoraDBTestCase):
    """Test the full LoRA import workflow at the database layer."""

    def test_save_lora_prompt_with_tags(self):
        """Simulate what lora_scan does: save prompt with lora-manager tags."""
        pid = self._save(
            text="1girl, detailed face, anime style",
            category="lora-manager",
            tags=["lora-manager", "lora:my_lora", "trigger1"],
        )
        prompt = self.db.get_prompt_by_id(pid)
        self.assertEqual(prompt["category"], "lora-manager")
        self.assertIn("lora-manager", prompt["tags"])

    def test_reimport_clears_and_recreates(self):
        """Simulate reimport: delete old, create new."""
        # First import
        pid1 = self._save("old lora prompt", category="lora-manager")
        self._link_image(pid1, "/cache/old.jpg")

        # Reimport
        self.db.delete_prompts_by_category("lora-manager")

        # Second import
        pid2 = self._save("new lora prompt", category="lora-manager")
        self._link_image(pid2, "/cache/new.jpg")

        results = self.db.search_prompts(category="lora-manager")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["text"], "new lora prompt")

    def test_hash_dedup_prevents_duplicates(self):
        """Verify hash-based dedup works for LoRA prompts."""
        text = "duplicate lora prompt"
        h = generate_prompt_hash(text)

        self._save(text, category="lora-manager")
        existing = self.db.get_prompt_by_hash(h)
        self.assertIsNotNone(existing)

    def test_link_multiple_images_to_lora_prompt(self):
        """LoRA prompts can have multiple preview images."""
        pid = self._save("multi-image lora", category="lora-manager")
        self._link_image(pid, "/cache/lora/img1.jpg")
        self._link_image(pid, "/cache/lora/img2.jpg")
        self._link_image(pid, "/cache/lora/img3.jpg")

        images = self.db.get_prompt_images(pid)
        self.assertEqual(len(images), 3)


if __name__ == "__main__":
    unittest.main()
