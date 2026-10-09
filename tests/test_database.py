"""
Comprehensive database layer tests for PromptManager.

Tests all CRUD operations, tag junction tables, pagination,
search, statistics, image linking, and edge cases using
an in-memory SQLite database.
"""

import csv
import json
import os
import sqlite3
from contextlib import closing
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database.models import PromptModel  # noqa: E402

try:
    from tests.open_handles import assert_closed  # noqa: E402
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed  # noqa: E402

from database.operations import PromptDatabase, _resolve_db_path
from utils.hashing import generate_prompt_hash


class DatabaseTestCase(unittest.TestCase):
    """Base class with temp database setup/teardown."""

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db = PromptDatabase(self.temp_db.name)

    def tearDown(self):
        PromptModel.close_all_instances()
        assert_closed(self, self.temp_db.name)
        for suffix in ("", "-wal", "-shm"):
            path = self.temp_db.name + suffix
            if os.path.exists(path):
                os.unlink(path)

    def _save(
        self, text="Test prompt", category=None, tags=None, rating=None, notes=None
    ):
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

    def test_save_existing_prompt_returns_its_id_when_check_raced(self):
        # Two callers check the hash, both see nothing, both insert: the second
        # insert hits the UNIQUE(hash) constraint and must yield the first id.
        text = "raced prompt"
        prompt_hash = generate_prompt_hash(text)
        original = self.db.get_prompt_by_hash
        calls = []

        def racing_lookup(h):
            calls.append(h)
            return None if len(calls) == 1 else original(h)

        first_id = self._save(text)
        with patch.object(self.db, "get_prompt_by_hash", racing_lookup):
            self.assertIsNone(self.db.get_prompt_by_hash(prompt_hash))
            second_id = self.db.save_prompt(text=text, prompt_hash=prompt_hash)

        self.assertEqual(second_id, first_id)
        self.assertEqual(len(self.db.search_prompts(text=text)), 1)

    def test_save_without_hash_never_collides(self):
        first = self.db.save_prompt(text="no hash a")
        second = self.db.save_prompt(text="no hash b")
        self.assertNotEqual(first, second)

    def test_save_and_retrieve(self):
        pid = self._save(
            "A beautiful sunset", category="nature", tags=["sunset", "sky"], rating=5
        )
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
        self.db.update_prompt_metadata(
            pid, category="new", tags=["new_tag"], rating=5, notes="updated"
        )
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


class TestTagSuggestions(DatabaseTestCase):
    """suggest_tags(prefix, with_tags, limit): autocomplete narrowed by co-occurrence.

    Context tags restrict suggestions to tags seen on the same prompts.
    """

    def setUp(self):
        super().setUp()
        self._save("P1", tags=["asian", "portrait", "smile"])
        self._save("P2", tags=["asian", "portrait"])
        self._save("P3", tags=["portrait", "dog"])
        self._save("P4", tags=["asian", "dog"])
        self._save("P5", tags=["snow_day"])

    def _names(self, *args, **kwargs):
        return [(t["name"], t["count"]) for t in self.db.suggest_tags(*args, **kwargs)]

    def test_no_context_lists_every_tag_by_frequency_then_name(self):
        self.assertEqual(
            self._names("", []),
            [("asian", 3), ("portrait", 3), ("dog", 2), ("smile", 1), ("snow_day", 1)],
        )

    def test_prefix_is_case_insensitive_and_anchored_at_the_start(self):
        self.assertEqual(self._names("P", []), [("portrait", 3)])
        self.assertEqual(self._names("s", []), [("smile", 1), ("snow_day", 1)])
        self.assertEqual(self._names("ort", []), [])

    def test_context_tags_narrow_to_co_occurring_tags_and_exclude_themselves(self):
        self.assertEqual(
            self._names("", ["asian"]), [("portrait", 2), ("dog", 1), ("smile", 1)]
        )
        self.assertEqual(self._names("", ["asian", "portrait"]), [("smile", 1)])
        self.assertEqual(self._names("S", ["asian"]), [("smile", 1)])
        self.assertEqual(self._names("", ["ASIAN", "dog"]), [])

    def test_unknown_context_tag_yields_nothing(self):
        self.assertEqual(self._names("", ["unicorn"]), [])

    def test_limit_and_like_wildcards(self):
        self.assertEqual(len(self._names("", [], limit=2)), 2)
        self.assertEqual(self._names("%", []), [])
        self.assertEqual(self._names("snow_", []), [("snow_day", 1)])
        self.assertEqual(self._names("snow%", []), [])


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
        self._save(
            "Beautiful mountain landscape",
            category="nature",
            tags=["mountain", "landscape"],
            rating=5,
        )
        self._save(
            "City skyline at night", category="urban", tags=["city", "night"], rating=4
        )
        self._save(
            "Portrait of an artist",
            category="portrait",
            tags=["person", "art"],
            rating=3,
        )
        self._save(
            "Abstract geometric shapes",
            category="abstract",
            tags=["art", "geometric"],
            rating=2,
        )

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
        empty_db = PromptDatabase(empty_db_file.name)
        try:
            result = empty_db.get_recent_prompts(limit=10, offset=0)
            self.assertEqual(result["total"], 0)
            self.assertEqual(len(result["prompts"]), 0)
            self.assertFalse(result["has_more"])
        finally:
            empty_db.close_all()
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(empty_db_file.name + suffix):
                    os.unlink(empty_db_file.name + suffix)

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


class TestImageUniquenessByPath(DatabaseTestCase):
    """ComfyUI writes same-named files into different date folders."""

    def _link(self, prompt_id, path):
        return self.db.link_image_to_prompt(prompt_id=prompt_id, image_path=path)

    def test_same_filename_in_different_folders_both_link(self):
        pid = self._save("two folders")
        first = self._link(pid, os.path.join("out", "2026-01-01", "a.png"))
        second = self._link(pid, os.path.join("out", "2026-01-02", "a.png"))

        self.assertTrue(first)
        self.assertTrue(second)
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.db.get_prompt_images(pid)), 2)

    def test_same_path_spelled_differently_links_once(self):
        pid = self._save("one file")
        first = self._link(pid, os.path.join("out", "x", "..", "a.png"))
        second = self._link(pid, os.path.join(".", "out", "a.png"))

        self.assertTrue(first)
        self.assertEqual(second, 0)
        images = self.db.get_prompt_images(pid)
        self.assertEqual(len(images), 1)
        expected = os.path.normcase(
            os.path.normpath(os.path.abspath(os.path.join("out", "a.png")))
        )
        self.assertEqual(images[0]["file_path"], expected)

    def test_same_file_can_link_to_two_prompts(self):
        a = self._save("prompt a")
        b = self._save("prompt b")
        path = os.path.join("out", "shared.png")
        self.assertTrue(self._link(a, path))
        self.assertTrue(self._link(b, path))


class TestLinkImageMetadataCoercion(DatabaseTestCase):
    """file_info from the request body is coerced; junk never reaches a column."""

    XSS = '"><img src=x>'

    def _link(self, file_info):
        pid = self._save("coerce")
        path = f"/out/c{len(self.db.get_prompt_images(pid))}.png"
        image_id = self.db.link_image_to_prompt(pid, path, {"file_info": file_info})
        self.assertGreater(image_id, 0, "image must still link")
        return self.db.get_image_by_id(image_id)

    def test_string_payloads_are_stored_as_null(self):
        row = self._link({"size": self.XSS, "dimensions": self.XSS, "format": self.XSS})

        self.assertIsNone(row["file_size"])
        self.assertIsNone(row["width"])
        self.assertIsNone(row["height"])
        self.assertIsNone(row["format"])

    def test_numbers_and_known_formats_are_kept(self):
        row = self._link({"size": 1234, "dimensions": [640, 480], "format": "png"})

        self.assertEqual(row["file_size"], 1234)
        self.assertEqual(row["width"], 640)
        self.assertEqual(row["height"], 480)
        self.assertEqual(row["format"], "PNG")

    def test_numeric_strings_are_accepted(self):
        row = self._link({"size": "99", "dimensions": ["8", "16"], "format": "webp"})

        self.assertEqual((row["file_size"], row["width"], row["height"]), (99, 8, 16))
        self.assertEqual(row["format"], "WEBP")

    def test_out_of_range_values_are_dropped(self):
        too_big = self._link({"size": 10**12 + 1, "dimensions": [65536, 65535]})
        negative = self._link({"size": -1, "dimensions": [-1, 0]})

        self.assertIsNone(too_big["file_size"])
        self.assertIsNone(too_big["width"])
        self.assertEqual(too_big["height"], 65535)
        self.assertIsNone(negative["file_size"])
        self.assertIsNone(negative["width"])
        self.assertEqual(negative["height"], 0)

    def test_unknown_format_and_short_dimensions_are_dropped(self):
        row = self._link({"dimensions": [640], "format": "svg"})

        self.assertIsNone(row["width"])
        self.assertIsNone(row["height"])
        self.assertIsNone(row["format"])

    def test_non_dict_metadata_still_links(self):
        pid = self._save("junk metadata")

        first = self.db.link_image_to_prompt(pid, "/out/a.png", "not a dict")
        second = self.db.link_image_to_prompt(
            pid, "/out/b.png", {"file_info": ["not", "a", "dict"]}
        )

        self.assertGreater(first, 0)
        self.assertGreater(second, 0)
        for row in self.db.get_prompt_images(pid):
            self.assertIsNone(row["width"])
            self.assertIsNone(row["format"])


class TestSearchEscapesLikeWildcards(DatabaseTestCase):
    def test_percent_is_literal(self):
        self._save("100% sure")
        self._save("100 percent sure")
        texts = [p["text"] for p in self.db.search_prompts(text="100%")]
        self.assertEqual(texts, ["100% sure"])

    def test_underscore_is_literal(self):
        self._save("snake_case name")
        self._save("snakeXcase name")
        texts = [p["text"] for p in self.db.search_prompts(text="snake_case")]
        self.assertEqual(texts, ["snake_case name"])

    def test_backslash_is_literal(self):
        self._save("path C:\\out\\img")
        self._save("path C:out img")
        texts = [p["text"] for p in self.db.search_prompts(text="C:\\out")]
        self.assertEqual(texts, ["path C:\\out\\img"])

    def test_partial_tag_filter_is_literal(self):
        self._save("tagged a", tags=["50%_off"])
        self._save("tagged b", tags=["50x_off"])
        texts = [
            p["text"] for p in self.db.search_prompts(tags=["50%"], tag_partial=True)
        ]
        self.assertEqual(texts, ["tagged a"])


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
        pid = self._save(
            "Special tags",
            tags=["tag-with-dash", "tag_with_underscore", "tag.with.dots"],
        )
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


class TestExport(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.out_dir = tempfile.mkdtemp()
        self.addCleanup(self._remove_out_dir)

    def _remove_out_dir(self):
        for name in os.listdir(self.out_dir):
            os.unlink(os.path.join(self.out_dir, name))
        os.rmdir(self.out_dir)

    def test_json_export_writes_every_prompt_with_tags(self):
        self._save("first", tags=["a", "b"], category="cat")
        self._save("second")
        path = os.path.join(self.out_dir, "prompts.json")

        self.assertTrue(self.db.export_prompts(path, "json"))

        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(sorted(p["text"] for p in data), ["first", "second"])
        self.assertEqual(
            next(p for p in data if p["text"] == "first")["tags"], ["a", "b"]
        )

    def test_csv_export_joins_tags(self):
        self._save("first", tags=["a", "b"])
        path = os.path.join(self.out_dir, "prompts.csv")

        self.assertTrue(self.db.export_prompts(path, "CSV"))

        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], "first")
        self.assertEqual(rows[0]["tags"], "a, b")

    def test_csv_export_of_empty_database_writes_nothing(self):
        path = os.path.join(self.out_dir, "empty.csv")
        self.assertTrue(self.db.export_prompts(path, "csv"))
        self.assertFalse(os.path.exists(path))

    def test_unsupported_format_returns_false(self):
        self._save("x")
        path = os.path.join(self.out_dir, "prompts.xml")
        self.assertFalse(self.db.export_prompts(path, "xml"))
        self.assertFalse(os.path.exists(path))

    def test_unwritable_destination_returns_false(self):
        self._save("x")
        path = os.path.join(self.out_dir, "missing", "dir", "prompts.json")
        self.assertFalse(self.db.export_prompts(path, "json"))


class TestDuplicateMerging(DatabaseTestCase):
    def _save_dup(self, text, created_at, **kwargs):
        pid = self.db.save_prompt(text=text, prompt_hash=None, **kwargs)
        with closing(sqlite3.connect(self.temp_db.name)) as conn, conn:
            conn.execute(
                "UPDATE prompts SET created_at = ? WHERE id = ?", (created_at, pid)
            )
        return pid

    def test_cleanup_keeps_oldest_merges_metadata_and_moves_images(self):
        keep = self._save_dup(
            "dup", "2026-01-01T00:00:00", tags=["a"], notes="first", rating=2
        )
        drop = self._save_dup(
            "DUP",
            "2026-02-01T00:00:00",
            tags=["b"],
            category="cat",
            notes="second",
            rating=4,
        )
        self.db.link_image_to_prompt(drop, "/out/moved.png")
        self.db.link_image_to_prompt(keep, "/out/kept.png")

        removed = self.db.cleanup_duplicates()

        self.assertEqual(removed, 1)
        self.assertIsNone(self.db.get_prompt_by_id(drop))
        merged = self.db.get_prompt_by_id(keep)
        self.assertEqual(merged["category"], "cat")
        self.assertEqual(merged["rating"], 4)
        self.assertEqual(merged["notes"], "first | second")
        self.assertEqual(sorted(merged["tags"]), ["a", "b"])
        self.assertEqual(len(self.db.get_prompt_images(keep)), 2)

    def test_cleanup_takes_notes_from_duplicate_when_primary_has_none(self):
        keep = self._save_dup("note dup", "2026-01-01T00:00:00")
        self._save_dup("note dup", "2026-02-01T00:00:00", notes="only here")
        self.db.cleanup_duplicates()
        self.assertEqual(self.db.get_prompt_by_id(keep)["notes"], "only here")

    def test_duplicate_scans_return_empty_on_database_error(self):
        with patch.object(
            self.db.model, "get_connection", side_effect=sqlite3.OperationalError
        ):
            self.assertEqual(self.db.cleanup_duplicates(), 0)

    def test_hash_duplicates_are_impossible_under_unique_hash(self):
        self._save("one")
        self.assertEqual(self.db.check_hash_duplicates(), [])

    def test_merge_helpers_survive_database_errors(self):
        pid = self._save("helper")
        broken = MagicMock()
        broken.execute.side_effect = sqlite3.OperationalError("disk I/O error")
        self.assertEqual(self.db._merge_duplicate_metadata(broken, pid, []), {})
        self.assertEqual(self.db._transfer_images_to_primary(broken, pid, [pid]), 0)
        self.db._update_primary_with_merged_metadata(broken, pid, {"tags": []})
        self.assertEqual(self.db.get_prompt_by_id(pid)["text"], "helper")


class TestImageQueries(DatabaseTestCase):
    METADATA = {
        "file_info": {"size": 123, "dimensions": [640, 480], "format": "PNG"},
        "workflow": {"nodes": [1, 2]},
        "prompt": {"3": {"inputs": {"cfg": float("nan")}}},
        "parameters": {"steps": 20},
    }

    def test_link_stores_metadata_and_cleans_nan(self):
        pid = self._save("with metadata")
        image_id = self.db.link_image_to_prompt(pid, "/out/meta.png", self.METADATA)

        image = self.db.get_image_by_id(image_id)
        self.assertEqual(image["file_size"], 123)
        self.assertEqual((image["width"], image["height"]), (640, 480))
        self.assertEqual(image["format"], "PNG")
        self.assertEqual(image["workflow_data"], {"nodes": [1, 2]})
        self.assertIsNone(image["prompt_metadata"]["3"]["inputs"]["cfg"])
        self.assertEqual(image["parameters"], {"steps": 20})

    def test_link_rejects_temporary_invalid_and_unknown_prompt_ids(self):
        self.assertEqual(self.db.link_image_to_prompt("temp_123", "/out/a.png"), 0)
        self.assertEqual(self.db.link_image_to_prompt("not-an-id", "/out/a.png"), 0)
        self.assertEqual(self.db.link_image_to_prompt(99999, "/out/a.png"), 0)
        self.assertEqual(self.db.link_image_to_prompt(None, "/out/a.png"), 0)

    def test_link_returns_zero_on_database_error(self):
        pid = self._save("err")
        with patch.object(
            self.db.model, "get_connection", side_effect=sqlite3.OperationalError
        ):
            self.assertEqual(self.db.link_image_to_prompt(pid, "/out/a.png"), 0)

    def test_recent_all_and_search_images_carry_prompt_text(self):
        a = self._save("alpha prompt", tags=["x"])
        b = self._save("beta prompt")
        self.db.link_image_to_prompt(a, "/out/a1.png")
        self.db.link_image_to_prompt(b, "/out/b1.png")
        with closing(sqlite3.connect(self.temp_db.name)) as conn, conn:
            conn.execute(
                "UPDATE generated_images SET generation_time = '2026-01-01 00:00:00'"
                " WHERE filename = 'a1.png'"
            )
            conn.execute(
                "UPDATE generated_images SET generation_time = '2026-02-01 00:00:00'"
                " WHERE filename = 'b1.png'"
            )

        recent = self.db.get_recent_images(limit=10)
        self.assertEqual(
            [i["prompt_text"] for i in recent], ["beta prompt", "alpha prompt"]
        )

        everything = self.db.get_all_images()
        self.assertEqual(len(everything), 2)
        by_text = {i["prompt_text"]: i for i in everything}
        self.assertEqual(by_text["alpha prompt"]["prompt_tags"], ["x"])
        self.assertEqual(by_text["beta prompt"]["prompt_tags"], [])

        page = self.db.get_all_images(limit=1, offset=1)
        self.assertEqual([i["prompt_text"] for i in page], ["alpha prompt"])

        found = self.db.search_images_by_prompt("alpha")
        self.assertEqual([i["filename"] for i in found], ["a1.png"])

    def test_image_search_treats_like_wildcards_literally(self):
        literal = self._save("50% off")
        other = self._save("50 percent off")
        self.db.link_image_to_prompt(literal, "/out/literal.png")
        self.db.link_image_to_prompt(other, "/out/other.png")
        found = self.db.search_images_by_prompt("50%")
        self.assertEqual([i["filename"] for i in found], ["literal.png"])

    def test_delete_image(self):
        pid = self._save("del")
        image_id = self.db.link_image_to_prompt(pid, "/out/del.png")
        self.assertTrue(self.db.delete_image(image_id))
        self.assertFalse(self.db.delete_image(image_id))
        self.assertIsNone(self.db.get_image_by_id(image_id))

    def test_cleanup_missing_images_keeps_files_that_exist(self):
        pid = self._save("files")
        fd, existing = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        self.addCleanup(os.unlink, existing)
        self.db.link_image_to_prompt(pid, existing)
        self.db.link_image_to_prompt(
            pid, os.path.join(tempfile.gettempdir(), "gone.png")
        )

        self.assertEqual(self.db.cleanup_missing_images(), 1)
        images = self.db.get_prompt_images(pid)
        self.assertEqual([i["image_path"] for i in images], [existing])

    def test_image_prompt_info_by_exact_normalised_or_basename_path(self):
        pid = self._save("lookup", tags=["t"], category="c", rating=3, notes="n")
        stored = os.path.join("out", "sub", "look.png")
        self.db.link_image_to_prompt(pid, stored, self.METADATA)

        exact = self.db.get_image_prompt_info(stored)
        self.assertEqual(exact["prompt_id"], pid)
        self.assertEqual(exact["tags"], ["t"])
        self.assertEqual(exact["workflow_data"], {"nodes": [1, 2]})

        normalised = self.db.get_image_prompt_info(
            os.path.join("out", "x", "..", "sub", "look.png")
        )
        self.assertEqual(normalised["prompt_id"], pid)
        basename = self.db.get_image_prompt_info(os.path.join("elsewhere", "look.png"))
        self.assertEqual(basename["prompt_id"], pid)
        self.assertIsNone(self.db.get_image_prompt_info("nothing.png"))

        self.assertEqual(self.db.get_prompt_id_for_image(stored), pid)
        self.assertEqual(
            self.db.get_prompt_id_for_image(
                os.path.join(".", "out", "sub", "look.png")
            ),
            pid,
        )
        self.assertIsNone(self.db.get_prompt_id_for_image("nothing.png"))

    def test_image_row_with_broken_json_yields_empty_dicts(self):
        pid = self._save("broken")
        image_id = self.db.link_image_to_prompt(pid, "/out/broken.png")
        with closing(sqlite3.connect(self.temp_db.name)) as conn, conn:
            conn.execute(
                "UPDATE generated_images SET workflow_data = '{not json', "
                "prompt_metadata = '{\"k\": 1}', parameters = NULL WHERE id = ?",
                (image_id,),
            )
        image = self.db.get_image_by_id(image_id)
        self.assertEqual(image["workflow_data"], {})
        self.assertEqual(image["prompt_metadata"], {"k": 1})
        self.assertEqual(image["parameters"], {})
        info = self.db.get_image_prompt_info("/out/broken.png")
        self.assertIsNone(info["workflow_data"])
        self.assertEqual(info["prompt_metadata"], {"k": 1})


class TestMaintenanceOperations(DatabaseTestCase):
    def test_statistics_count_everything(self):
        a = self._save("a", category="cat", tags=["t1", "t2"], rating=4)
        self._save("b", category=" cat ", rating=2)
        self._save("c")
        self.db.link_image_to_prompt(a, "/out/a.png")
        self.db.link_image_to_prompt(a, "/out/b.png")

        stats = self.db.get_statistics()

        self.assertEqual(stats["total_prompts"], 3)
        self.assertEqual(stats["total_categories"], 1)
        self.assertEqual(stats["average_rating"], 3.0)
        self.assertEqual(stats["total_tags"], 2)
        self.assertEqual(stats["total_images"], 2)
        self.assertEqual(stats["images_with_prompts"], 1)

    def test_update_text_and_rating(self):
        pid = self._save("old text", rating=1)
        self.assertTrue(self.db.update_prompt_text(pid, "new text"))
        self.assertTrue(self.db.update_prompt_rating(pid, 5))
        prompt = self.db.get_prompt_by_id(pid)
        self.assertEqual((prompt["text"], prompt["rating"]), ("new text", 5))
        self.assertFalse(self.db.update_prompt_text(99999, "x"))
        self.assertFalse(self.db.update_prompt_rating(99999, 3))

    def test_update_metadata_validation(self):
        pid = self._save("meta")
        self.assertFalse(self.db.update_prompt_metadata(pid))
        with self.assertRaises(ValueError):
            self.db.update_prompt_metadata(pid, rating=7)
        self.assertTrue(self.db.update_prompt_metadata(pid, tags=["only tags"]))
        self.assertEqual(self.db.get_prompt_by_id(pid)["tags"], ["only tags"])

    def test_save_prompt_validation(self):
        with self.assertRaises(ValueError):
            self.db.save_prompt(text="   ")
        with self.assertRaises(ValueError):
            self.db.save_prompt(text="rated", rating=0)

    def test_bulk_operations(self):
        a = self._save("a", tags=["keep"])
        b = self._save("b")
        c = self._save("c")
        self.db.link_image_to_prompt(c, "/out/c.png")

        self.assertEqual(self.db.bulk_add_tags([a, b, 99999], ["keep", "new"]), 2)
        self.assertEqual(self.db.bulk_add_tags([a], ["keep"]), 0)
        self.assertEqual(sorted(self.db.get_prompt_by_id(a)["tags"]), ["keep", "new"])

        self.assertEqual(self.db.bulk_set_category([a, b, 99999], "bulk"), 2)
        self.assertEqual(self.db.get_prompt_by_id(b)["category"], "bulk")

        self.assertEqual(self.db.bulk_delete_prompts([b, c, 99999]), 2)
        self.assertIsNone(self.db.get_prompt_by_id(c))
        self.assertEqual(self.db.get_prompt_images(c), [])

    def test_prune_orphaned_prompts_spares_protected_and_illustrated(self):
        orphan = self._save("orphan")
        protected = self._save("protected", tags=["__protected__"])
        illustrated = self._save("illustrated")
        self.db.link_image_to_prompt(illustrated, "/out/i.png")

        self.assertEqual(self.db.prune_orphaned_prompts(), 1)
        self.assertIsNone(self.db.get_prompt_by_id(orphan))
        self.assertIsNotNone(self.db.get_prompt_by_id(protected))
        self.assertIsNotNone(self.db.get_prompt_by_id(illustrated))
        self.assertEqual(self.db.prune_orphaned_prompts(), 0)

    def test_consistency_check_reports_orphaned_rows(self):
        self.assertEqual(self.db.check_consistency(), [])
        pid = self._save("to orphan", tags=["t"])
        self.db.link_image_to_prompt(pid, "/out/o.png")
        self.db.close()
        with closing(sqlite3.connect(self.temp_db.name)) as conn, conn:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("DELETE FROM prompts WHERE id = ?", (pid,))

        issues = self.db.check_consistency()

        self.assertEqual(len(issues), 2)
        self.assertTrue(any("prompt_tags" in i for i in issues))
        self.assertTrue(any(i.startswith("Image ") for i in issues))

    def test_subfolders_relative_to_roots_with_ancestors(self):
        pid = self._save("folders")
        root = os.path.join(os.sep, "gallery")
        self.db.link_image_to_prompt(
            pid, os.path.join(root, "2026", "08-Aug", "2026-08-06", "a.png")
        )
        self.db.link_image_to_prompt(pid, os.path.join(root, "b.png"))
        self.db.link_image_to_prompt(
            pid, os.path.join(os.sep, "elsewhere", "deep", "c.png")
        )
        self.db.link_image_to_prompt(pid, "loose.png")

        flat = self.db.get_prompt_subfolders(root_dirs=[root])
        self.assertIn(os.path.join("2026", "08-Aug", "2026-08-06"), flat)
        self.assertIn(os.path.join(os.sep, "elsewhere", "deep"), flat)
        self.assertNotIn(".", flat)

        nested = self.db.get_prompt_subfolders(root_dirs=[root], include_ancestors=True)
        self.assertIn("2026", nested)
        self.assertIn("2026/08-Aug", nested)

        without_roots = self.db.get_prompt_subfolders()
        self.assertIn(os.path.join(root, "2026", "08-Aug", "2026-08-06"), without_roots)

    def test_prompts_by_tags_and_or_modes(self):
        both = self._save("both", tags=["x", "y"])
        only_x = self._save("only x", tags=["x"])
        self._save("none")

        self.assertEqual(self.db.get_prompts_by_tags([])["total"], 0)
        either = self.db.get_prompts_by_tags(["x", "y"], mode="or")
        self.assertEqual(sorted(p["id"] for p in either["prompts"]), [both, only_x])
        self.assertEqual(either["total"], 2)
        all_of = self.db.get_prompts_by_tags(["x", "y"], mode="and", limit=1)
        self.assertEqual([p["id"] for p in all_of["prompts"]], [both])
        self.assertFalse(all_of["has_more"])

    def test_tag_counts_with_search_and_sorts(self):
        self._save("1", tags=["beta", "alpha"])
        self._save("2", tags=["beta"])

        by_count = self.db.get_tags_with_counts(sort="count_desc")
        self.assertEqual([t["name"] for t in by_count["tags"]], ["beta", "alpha"])
        ascending = self.db.get_tags_with_counts(sort="count_asc")
        self.assertEqual([t["name"] for t in ascending["tags"]], ["alpha", "beta"])
        reverse = self.db.get_tags_with_counts(sort="alpha_desc")
        self.assertEqual([t["name"] for t in reverse["tags"]], ["beta", "alpha"])
        searched = self.db.get_tags_with_counts(search="alp")
        self.assertEqual(searched["total"], 1)
        self.assertEqual(searched["tags"][0]["count"], 1)

    def test_tag_management_edge_cases(self):
        a = self._save("a", tags=["old", "target"])
        b = self._save("b", tags=["old"])

        merged = self.db.rename_tag_all_prompts("old", "target")
        self.assertEqual(merged["affected_count"], 2)
        self.assertEqual(self.db.get_prompt_by_id(a)["tags"], ["target"])
        self.assertEqual(self.db.get_prompt_by_id(b)["tags"], ["target"])
        self.assertEqual(
            self.db.rename_tag_all_prompts("missing", "x")["affected_count"], 0
        )
        self.assertEqual(self.db.delete_tag_all_prompts("missing")["affected_count"], 0)
        with self.assertRaises(ValueError):
            self.db.rename_tag_all_prompts(" ", "x")
        with self.assertRaises(ValueError):
            self.db.rename_tag_all_prompts("x", "")
        with self.assertRaises(ValueError):
            self.db.delete_tag_all_prompts("")
        with self.assertRaises(ValueError):
            self.db.merge_tags([], "t")
        with self.assertRaises(ValueError):
            self.db.merge_tags(["a"], " ")

        result = self.db.merge_tags(["missing", "target"], "final")
        self.assertEqual(result["tags_merged"], 1)
        self.assertEqual(result["affected_count"], 2)
        self.assertEqual(self.db.get_all_tags(), ["final"])

    def test_untagged_and_category_helpers(self):
        self._save("tagged", tags=["t"], category="  ")
        untagged = self._save("plain", category="shown")

        self.assertEqual(self.db.get_untagged_prompts_count(), 1)
        page = self.db.get_untagged_prompts(limit=5)
        self.assertEqual([p["id"] for p in page["prompts"]], [untagged])
        self.assertEqual(self.db.get_all_categories(), ["shown"])
        self.assertEqual(self.db.delete_prompts_by_category("nothing"), 0)


class TestRowConversion(DatabaseTestCase):
    def test_legacy_json_tags_column_is_parsed(self):
        convert = self.db._row_to_dict
        self.assertEqual(convert({"tags": '["a", "b"]'})["tags"], ["a", "b"])
        self.assertEqual(convert({"tags": '"a, b"'})["tags"], ["a", "b"])
        self.assertEqual(convert({"tags": "5"})["tags"], [])
        self.assertEqual(convert({"tags": "{not json"})["tags"], [])
        self.assertEqual(convert({"tags": None})["tags"], [])
        self.assertEqual(convert({"_tag_list": None})["tags"], [])

    def test_search_date_filters(self):
        pid = self._save("dated")
        with closing(sqlite3.connect(self.temp_db.name)) as conn, conn:
            conn.execute(
                "UPDATE prompts SET created_at = '2026-05-01T00:00:00' WHERE id = ?",
                (pid,),
            )
        self.assertEqual(
            len(self.db.search_prompts(date_from="2026-04-01", date_to="2026-06-01")), 1
        )
        self.assertEqual(len(self.db.search_prompts(date_from="2026-06-01")), 0)
        self.assertEqual(len(self.db.search_prompts(date_to="2026-04-01")), 0)
        self.assertEqual(len(self.db.search_prompts(rating_max=3)), 0)


class TestResolveDbPathFromConfig(unittest.TestCase):
    def test_default_path_comes_from_config_when_importable(self):
        tmpdir = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, tmpdir)
        fake = types.ModuleType("py.config")
        fake.PromptManagerConfig = types.SimpleNamespace(
            DEFAULT_DB_PATH=os.path.join(tmpdir, "configured.db")
        )
        with patch.dict(sys.modules, {"py.config": fake}):
            self.assertEqual(
                _resolve_db_path(None), os.path.join(tmpdir, "configured.db")
            )

    def test_falls_back_to_prompts_db_when_config_cannot_import(self):
        # Outside ComfyUI py.config fails to import (needs the server module)
        with patch.dict(sys.modules, {"py.config": None}):
            resolved = _resolve_db_path(None)
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertEqual(resolved, os.path.join(root, "prompts.db"))


class TestModelHousekeeping(DatabaseTestCase):
    def test_database_info_reports_counts_and_handles_errors(self):
        self._save("rated", rating=4, category="c")
        info = self.db.model.get_database_info()
        self.assertEqual(info["total_prompts"], 1)
        self.assertEqual(info["unique_categories"], 1)
        self.assertEqual(info["average_rating"], 4.0)
        self.assertGreater(info["database_size_bytes"], 0)
        with patch.object(
            self.db.model, "get_connection", side_effect=sqlite3.OperationalError
        ):
            self.assertEqual(self.db.model.get_database_info(), {})

    def test_vacuum_logs_errors_instead_of_raising(self):
        self._save("x")
        self.db.model.vacuum_database()
        with patch(
            "database.models.sqlite3.connect", side_effect=sqlite3.OperationalError
        ):
            self.db.model.vacuum_database()  # logged, not raised
        self.assertEqual(len(self.db.search_prompts()), 1)

    def test_close_tolerates_a_connection_that_fails_to_close(self):
        real = self.db.model.get_connection()
        real.close()
        failing = MagicMock()
        failing.close.side_effect = sqlite3.ProgrammingError("already closed")
        self.db.model._local.conn = failing
        self.db.close()  # logged, not raised
        self.assertTrue(failing.close.called)
        self.assertIsNot(self.db.model.get_connection(), failing)


if __name__ == "__main__":
    unittest.main()


class TestImagePaging(DatabaseTestCase):
    """Image listings page in the database, not by over-fetching in the API."""

    def _link_many(self, count):
        pid = self._save("paged prompt")
        image_ids = [
            self.db.link_image_to_prompt(pid, f"/out/img{i:03d}.png")
            for i in range(count)
        ]
        # Stamp distinct times afterwards through the model's own connection,
        # so no second connection holds a write lock while linking.
        with self.db.model.get_connection() as conn:
            for i, image_id in enumerate(image_ids):
                conn.execute(
                    "UPDATE generated_images SET generation_time = ? WHERE id = ?",
                    (f"2026-01-01T00:00:{i:02d}", image_id),
                )
        return pid

    def test_recent_images_offset_skips_newest_rows(self):
        self._link_many(5)
        newest_first = [img["filename"] for img in self.db.get_recent_images(limit=5)]
        page = self.db.get_recent_images(limit=2, offset=2)
        self.assertEqual([img["filename"] for img in page], newest_first[2:4])
        self.assertEqual(self.db.get_recent_images(limit=2, offset=10), [])

    def test_search_images_by_prompt_is_bounded_and_pages(self):
        self._link_many(5)
        first = self.db.search_images_by_prompt("paged", limit=2)
        second = self.db.search_images_by_prompt("paged", limit=2, offset=2)
        self.assertEqual(len(first), 2)
        self.assertEqual(len(second), 2)
        self.assertNotEqual({img["id"] for img in first}, {img["id"] for img in second})
        self.assertEqual(len(self.db.search_images_by_prompt("paged")), 5)
