"""
Comprehensive database layer tests for PromptManager.

Tests all CRUD operations, tag junction tables, pagination,
search, statistics, image linking, and edge cases using
an in-memory SQLite database.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.operations import PromptDatabase
from utils.hashing import generate_prompt_hash


class DatabaseTestCase(unittest.TestCase):
    """Base class with temp database setup/teardown."""

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db = PromptDatabase(self.temp_db.name)

    def tearDown(self):
        if os.path.exists(self.temp_db.name):
            os.unlink(self.temp_db.name)
        wal = self.temp_db.name + "-wal"
        shm = self.temp_db.name + "-shm"
        for f in (wal, shm):
            if os.path.exists(f):
                os.unlink(f)

    def _save(self, text="Test prompt", category=None, tags=None, rating=None, notes=None):
        """Helper to save a prompt and return its ID."""
        return self.db.save_prompt(
            text=text,
            category=category,
            tags=tags or [],
            rating=rating,
            notes=notes,
            prompt_hash=generate_prompt_hash(text),
        )


class TestPromptCRUD(DatabaseTestCase):
    """Test basic create, read, update, delete operations."""

    def test_save_and_retrieve(self):
        pid = self._save("A beautiful sunset", category="nature", tags=["sunset", "sky"], rating=5)
        prompt = self.db.get_prompt_by_id(pid)
        self.assertEqual(prompt["text"], "A beautiful sunset")
        self.assertEqual(prompt["category"], "nature")
        self.assertIn("sunset", prompt["tags"])
        self.assertIn("sky", prompt["tags"])
        self.assertEqual(prompt["rating"], 5)

    def test_save_minimal(self):
        pid = self._save("Minimal prompt")
        prompt = self.db.get_prompt_by_id(pid)
        self.assertEqual(prompt["text"], "Minimal prompt")
        self.assertIsNone(prompt["category"])
        self.assertEqual(prompt["tags"], [])
        self.assertIsNone(prompt["rating"])

    def test_get_nonexistent_prompt(self):
        result = self.db.get_prompt_by_id(99999)
        self.assertIsNone(result)

    def test_get_by_hash(self):
        text = "Hash test prompt"
        h = generate_prompt_hash(text)
        self._save(text)
        found = self.db.get_prompt_by_hash(h)
        self.assertIsNotNone(found)
        self.assertEqual(found["text"], text)

    def test_get_by_hash_nonexistent(self):
        result = self.db.get_prompt_by_hash("nonexistent_hash_value")
        self.assertIsNone(result)

    def test_update_metadata(self):
        pid = self._save("Updatable prompt", category="old", tags=["old_tag"], rating=2)
        self.db.update_prompt_metadata(pid, category="new", tags=["new_tag"], rating=5, notes="updated")
        prompt = self.db.get_prompt_by_id(pid)
        self.assertEqual(prompt["category"], "new")
        self.assertIn("new_tag", prompt["tags"])
        self.assertNotIn("old_tag", prompt["tags"])
        self.assertEqual(prompt["rating"], 5)
        self.assertEqual(prompt["notes"], "updated")

    def test_update_partial_metadata(self):
        pid = self._save("Partial update", category="keep", tags=["keep_tag"], rating=3)
        self.db.update_prompt_metadata(pid, rating=1)
        prompt = self.db.get_prompt_by_id(pid)
        self.assertEqual(prompt["rating"], 1)
        # Category and tags should be unchanged
        self.assertEqual(prompt["category"], "keep")
        self.assertIn("keep_tag", prompt["tags"])

    def test_delete_prompt(self):
        pid = self._save("To be deleted")
        result = self.db.delete_prompt(pid)
        self.assertTrue(result)
        self.assertIsNone(self.db.get_prompt_by_id(pid))

    def test_delete_nonexistent(self):
        result = self.db.delete_prompt(99999)
        self.assertFalse(result)


class TestTagJunctionTables(DatabaseTestCase):
    """Test normalized tag storage via junction tables."""

    def test_tags_stored_in_junction_table(self):
        pid = self._save("Tagged prompt", tags=["alpha", "beta"])
        prompt = self.db.get_prompt_by_id(pid)
        self.assertEqual(sorted(prompt["tags"]), ["alpha", "beta"])

    def test_get_all_tags(self):
        self._save("P1", tags=["a", "b"])
        self._save("P2", tags=["b", "c"])
        all_tags = self.db.get_all_tags()
        self.assertEqual(sorted(all_tags), ["a", "b", "c"])

    def test_get_tags_with_counts(self):
        self._save("P1", tags=["common", "rare"])
        self._save("P2", tags=["common"])
        self._save("P3", tags=["common", "other"])
        result = self.db.get_tags_with_counts()
        counts_dict = {t["name"]: t["count"] for t in result["tags"]}
        self.assertEqual(counts_dict["common"], 3)
        self.assertEqual(counts_dict["rare"], 1)
        self.assertEqual(counts_dict["other"], 1)

    def test_set_prompt_tags(self):
        pid = self._save("Retaggable", tags=["old"])
        self.db.set_prompt_tags(pid, ["new1", "new2"])
        prompt = self.db.get_prompt_by_id(pid)
        self.assertEqual(sorted(prompt["tags"]), ["new1", "new2"])

    def test_set_empty_tags(self):
        pid = self._save("Clear tags", tags=["remove_me"])
        self.db.set_prompt_tags(pid, [])
        prompt = self.db.get_prompt_by_id(pid)
        self.assertEqual(prompt["tags"], [])

    def test_rename_tag(self):
        self._save("P1", tags=["old_name"])
        self._save("P2", tags=["old_name", "other"])
        self.db.rename_tag_all_prompts("old_name", "new_name")
        all_tags = self.db.get_all_tags()
        self.assertIn("new_name", all_tags)
        self.assertNotIn("old_name", all_tags)

    def test_delete_tag(self):
        self._save("P1", tags=["keep", "remove"])
        self._save("P2", tags=["remove"])
        self.db.delete_tag_all_prompts("remove")
        all_tags = self.db.get_all_tags()
        self.assertIn("keep", all_tags)
        self.assertNotIn("remove", all_tags)

    def test_merge_tags(self):
        self._save("P1", tags=["target"])
        self._save("P2", tags=["source1"])
        self._save("P3", tags=["source2", "target"])
        self.db.merge_tags(["source1", "source2"], "target")
        all_tags = self.db.get_all_tags()
        self.assertIn("target", all_tags)
        self.assertNotIn("source1", all_tags)
        self.assertNotIn("source2", all_tags)
        # All prompts should have the target tag
        result = self.db.get_tags_with_counts()
        counts = {t["name"]: t["count"] for t in result["tags"]}
        self.assertEqual(counts["target"], 3)

    def test_bulk_add_tags(self):
        p1 = self._save("P1", tags=["existing"])
        p2 = self._save("P2")
        self.db.bulk_add_tags([p1, p2], ["bulk1", "bulk2"])
        prompt1 = self.db.get_prompt_by_id(p1)
        prompt2 = self.db.get_prompt_by_id(p2)
        self.assertIn("bulk1", prompt1["tags"])
        self.assertIn("bulk2", prompt1["tags"])
        self.assertIn("existing", prompt1["tags"])
        self.assertIn("bulk1", prompt2["tags"])

    def test_untagged_prompts(self):
        self._save("Tagged", tags=["has_tag"])
        self._save("Untagged1")
        self._save("Untagged2")
        count = self.db.get_untagged_prompts_count()
        self.assertEqual(count, 2)
        result = self.db.get_untagged_prompts()
        self.assertEqual(len(result["prompts"]), 2)


class TestSearch(DatabaseTestCase):
    """Test search and filter operations."""

    def setUp(self):
        super().setUp()
        self._save("Beautiful mountain landscape", category="nature", tags=["mountain", "landscape"], rating=5)
        self._save("City skyline at night", category="urban", tags=["city", "night"], rating=4)
        self._save("Portrait of an artist", category="portrait", tags=["person", "art"], rating=3)
        self._save("Abstract geometric shapes", category="abstract", tags=["art", "geometric"], rating=2)

    def test_search_by_text(self):
        results = self.db.search_prompts(text="mountain")
        self.assertEqual(len(results), 1)
        self.assertIn("mountain", results[0]["text"])

    def test_search_by_text_case_insensitive(self):
        results = self.db.search_prompts(text="MOUNTAIN")
        self.assertEqual(len(results), 1)

    def test_search_by_category(self):
        results = self.db.search_prompts(category="urban")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["category"], "urban")

    def test_search_by_tag(self):
        results = self.db.search_prompts(tags=["art"])
        self.assertEqual(len(results), 2)

    def test_search_by_multiple_tags(self):
        results = self.db.search_prompts(tags=["art", "geometric"])
        self.assertEqual(len(results), 1)
        self.assertIn("geometric", results[0]["text"].lower())

    def test_search_by_rating_min(self):
        results = self.db.search_prompts(rating_min=4)
        self.assertEqual(len(results), 2)

    def test_search_by_rating_range(self):
        results = self.db.search_prompts(rating_min=3, rating_max=4)
        self.assertEqual(len(results), 2)

    def test_search_combined_filters(self):
        results = self.db.search_prompts(text="landscape", rating_min=4)
        self.assertEqual(len(results), 1)

    def test_search_no_results(self):
        results = self.db.search_prompts(text="nonexistent_query_xyz")
        self.assertEqual(len(results), 0)

    def test_search_with_limit(self):
        results = self.db.search_prompts(limit=2)
        self.assertEqual(len(results), 2)


class TestPagination(DatabaseTestCase):
    """Test pagination in get_recent_prompts."""

    def setUp(self):
        super().setUp()
        for i in range(15):
            self._save(f"Prompt number {i:02d}")

    def test_first_page(self):
        result = self.db.get_recent_prompts(limit=5, offset=0)
        self.assertEqual(len(result["prompts"]), 5)
        self.assertEqual(result["total"], 15)
        self.assertTrue(result["has_more"])
        self.assertEqual(result["page"], 1)
        self.assertEqual(result["total_pages"], 3)

    def test_middle_page(self):
        result = self.db.get_recent_prompts(limit=5, offset=5)
        self.assertEqual(len(result["prompts"]), 5)
        self.assertTrue(result["has_more"])
        self.assertEqual(result["page"], 2)

    def test_last_page(self):
        result = self.db.get_recent_prompts(limit=5, offset=10)
        self.assertEqual(len(result["prompts"]), 5)
        self.assertFalse(result["has_more"])
        self.assertEqual(result["page"], 3)

    def test_beyond_last_page(self):
        result = self.db.get_recent_prompts(limit=5, offset=20)
        self.assertEqual(len(result["prompts"]), 0)
        self.assertFalse(result["has_more"])

    def test_empty_database(self):
        # Use a fresh empty DB
        empty_db_file = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        empty_db_file.close()
        try:
            empty_db = PromptDatabase(empty_db_file.name)
            result = empty_db.get_recent_prompts(limit=10, offset=0)
            self.assertEqual(result["total"], 0)
            self.assertEqual(len(result["prompts"]), 0)
            self.assertFalse(result["has_more"])
        finally:
            os.unlink(empty_db_file.name)

    def test_total_count_is_integer(self):
        result = self.db.get_recent_prompts(limit=5)
        self.assertIsInstance(result["total"], int)
        self.assertIsInstance(result["has_more"], bool)


class TestStatistics(DatabaseTestCase):
    """Test get_statistics method."""

    def test_empty_database_statistics(self):
        stats = self.db.get_statistics()
        self.assertEqual(stats["total_prompts"], 0)
        self.assertEqual(stats["total_categories"], 0)
        self.assertIsNone(stats.get("average_rating") or stats.get("avg_rating"))
        self.assertEqual(stats["total_tags"], 0)

    def test_populated_statistics(self):
        self._save("P1", category="cat_a", tags=["t1", "t2"], rating=4)
        self._save("P2", category="cat_b", tags=["t2", "t3"], rating=2)
        self._save("P3", category="cat_a", tags=["t1"])
        stats = self.db.get_statistics()
        self.assertEqual(stats["total_prompts"], 3)
        self.assertEqual(stats["total_categories"], 2)
        self.assertEqual(stats["total_tags"], 3)


class TestCategories(DatabaseTestCase):
    """Test category operations."""

    def test_get_prompts_by_category(self):
        self._save("P1", category="nature")
        self._save("P2", category="nature")
        self._save("P3", category="urban")
        results = self.db.get_prompts_by_category("nature")
        self.assertEqual(len(results), 2)

    def test_get_all_categories(self):
        self._save("P1", category="nature")
        self._save("P2", category="urban")
        self._save("P3", category="nature")
        categories = self.db.get_all_categories()
        self.assertEqual(sorted(categories), ["nature", "urban"])


class TestTopRated(DatabaseTestCase):
    """Test top-rated prompt retrieval."""

    def test_get_top_rated(self):
        self._save("Low", rating=1)
        self._save("High", rating=5)
        self._save("Mid", rating=3)
        self._save("Unrated")
        results = self.db.get_top_rated_prompts(limit=2)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["rating"], 5)
        self.assertEqual(results[1]["rating"], 3)


class TestDuplicateDetection(DatabaseTestCase):
    """Test duplicate handling."""

    def test_same_hash_detected(self):
        text = "Duplicate content"
        h = generate_prompt_hash(text)
        pid1 = self.db.save_prompt(text=text, prompt_hash=h)
        existing = self.db.get_prompt_by_hash(h)
        self.assertIsNotNone(existing)
        self.assertEqual(existing["id"], pid1)

    def test_cleanup_duplicates(self):
        # Save multiple prompts first
        self._save("Unique 1")
        self._save("Unique 2")
        removed = self.db.cleanup_duplicates()
        self.assertEqual(removed, 0)


class TestImageOperations(DatabaseTestCase):
    """Test image linking and retrieval."""

    def _link_image(self, prompt_id, path="/fake/path/image.png"):
        return self.db.link_image_to_prompt(
            prompt_id=str(prompt_id),
            image_path=path,
        )

    def test_save_and_get_image(self):
        pid = self._save("Prompt with image")
        img_id = self._link_image(pid, "/fake/path/image.png")
        self.assertIsNotNone(img_id)
        images = self.db.get_prompt_images(str(pid))
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]["filename"], "image.png")

    def test_image_count(self):
        pid = self._save("Multi-image prompt")
        for i in range(3):
            self._link_image(pid, f"/fake/path/img{i}.png")
        images = self.db.get_prompt_images(str(pid))
        self.assertEqual(len(images), 3)

    def test_delete_prompt_cascades_images(self):
        pid = self._save("Cascade test")
        self._link_image(pid, "/fake/path/img.png")
        self.db.delete_prompt(pid)
        images = self.db.get_prompt_images(str(pid))
        self.assertEqual(len(images), 0)


class TestEdgeCases(DatabaseTestCase):
    """Test edge cases and boundary conditions."""

    def test_special_characters_in_text(self):
        pid = self._save("Prompt with 'quotes' and \"double quotes\" and <html>")
        prompt = self.db.get_prompt_by_id(pid)
        self.assertIn("quotes", prompt["text"])

    def test_unicode_text(self):
        pid = self._save("日本語テスト prompt with émojis 🎨")
        prompt = self.db.get_prompt_by_id(pid)
        self.assertIn("日本語", prompt["text"])

    def test_very_long_text(self):
        long_text = "word " * 1000
        pid = self._save(long_text.strip())
        prompt = self.db.get_prompt_by_id(pid)
        self.assertEqual(prompt["text"], long_text.strip())

    def test_tag_with_special_characters(self):
        pid = self._save("Special tags", tags=["tag-with-dash", "tag_with_underscore", "tag.with.dots"])
        prompt = self.db.get_prompt_by_id(pid)
        self.assertEqual(len(prompt["tags"]), 3)

    def test_empty_category_string(self):
        pid = self._save("Empty cat", category="")
        prompt = self.db.get_prompt_by_id(pid)
        # Empty string category is stored as-is
        self.assertIn(prompt["category"], ["", None])

    def test_rating_boundary_values(self):
        p1 = self._save("Rating 1", rating=1)
        p5 = self._save("Rating 5", rating=5)
        self.assertEqual(self.db.get_prompt_by_id(p1)["rating"], 1)
        self.assertEqual(self.db.get_prompt_by_id(p5)["rating"], 5)


class TestPreviewImages(DatabaseTestCase):
    """Test _attach_preview_images functionality."""

    def test_preview_images_attached(self):
        pid = self._save("Preview test")
        for i in range(5):
            self.db.link_image_to_prompt(
                prompt_id=str(pid),
                image_path=f"/fake/img{i}.png",
            )
        result = self.db.get_recent_prompts(limit=10)
        prompt = result["prompts"][0]
        # Should have preview images (max 3) and total count
        self.assertIn("images", prompt)
        self.assertLessEqual(len(prompt["images"]), 3)
        self.assertEqual(prompt["image_count"], 5)


if __name__ == "__main__":
    unittest.main()
