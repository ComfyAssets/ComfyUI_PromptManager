"""Grouped library reads retain component roles and association semantics."""

import tempfile
import unittest
from pathlib import Path

from database.operations import PromptDatabase
from utils.hashing import generate_prompt_hash


class TestLibrary(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = PromptDatabase(str(Path(self.temp.name) / "library.db"))

    def tearDown(self):
        self.db.close_all()
        self.temp.cleanup()

    def save(self, text, **metadata):
        return self.db.save_prompt(
            text, prompt_hash=generate_prompt_hash(text), **metadata
        )

    def test_groups_positive_parents_excludes_negative_only_and_keeps_dual_once(self):
        first = self.db.save_prompt_pair("Cat", "Blur")
        self.db.save_prompt_pair("Cat", "Noise")
        self.db.save_prompt_pair("Blur", "Watermark")
        legacy = self.save("Legacy")
        rows = self.db.list_library()["items"]
        self.assertEqual({row["text"] for row in rows}, {"Cat", "Blur", "Legacy"})
        parent = next(row for row in rows if row["text"] == "Cat")
        self.assertEqual(parent["variant_count"], 2)
        self.assertEqual(len([row for row in rows if row["id"] == legacy]), 1)
        self.db.archive_prompt_pair(first)
        detail = self.db.get_library_positive(parent["id"])
        self.assertEqual(len(detail["variants"]), 1)
        self.assertEqual(detail["positive"]["variant_count"], 1)

    def test_filters_match_all_constraints_on_same_parent_or_same_active_pair(self):
        a = self.db.save_prompt_pair("Cat", "Blur", category="A", tags=["red"])
        self.db.save_prompt_pair("Cat", "Noise", category="B", tags=["blue"])
        self.assertEqual(self.db.list_library(category="A", tags=["blue"])["items"], [])
        self.assertEqual(
            self.db.list_library(text="Blur", category="A", tags=["red"])["total"], 1
        )
        self.db.archive_prompt_pair(a)
        self.assertEqual(self.db.list_library(text="Blur")["total"], 0)
        self.save("Legacy cat", category="A", tags=["red", "blue"])
        self.assertEqual(
            self.db.list_library(category="A", tags=["red", "blue"])["total"], 1
        )
        self.assertEqual(self.db.list_library(text="%")["items"], [])

    def test_keyset_handles_insertion_deletion_ties_and_invalid_parameters(self):
        for text in ("a", "b", "c", "d"):
            self.save(text)
        first = self.db.list_library(limit=2)
        self.save("new")
        second = self.db.list_library(limit=2, after=first["next_key"])
        self.assertEqual([row["text"] for row in second["items"]], ["b", "a"])
        self.assertFalse(second["has_more"])
        for options in ({"limit": 0}, {"sort": "sql"}, {"tags": [2]}, {"text": 2}):
            with self.assertRaises(ValueError):
                self.db.list_library(**options)

    def test_detail_distinguishes_explicit_empty_and_deduplicates_provenance_rows(self):
        pair = self.db.save_prompt_pair("Cat", "")
        positive = self.db.get_prompt_pair(pair)["positive_prompt_id"]
        image = self.db.link_image_to_prompt(
            positive, str(Path(self.temp.name) / "image.png")
        )
        self.db.link_image_to_pair(pair, image, {"source": "one"})
        self.db.link_image_to_pair(pair, image, {"source": "two"})
        self.db.link_image_to_prompt(
            positive, str(Path(self.temp.name) / "unassigned.png")
        )
        detail = self.db.get_library_positive(positive)
        self.assertEqual(detail["positive"]["image_count"], 2)
        self.assertEqual(detail["unassigned_image_count"], 1)
        self.assertEqual(detail["variants"][0]["negative_text"], "")
        self.assertTrue(detail["variants"][0]["explicit_empty"])
        self.assertEqual(detail["variants"][0]["image_count"], 1)
        page = self.db.list_library_images(positive, pair_id=pair)
        self.assertEqual(len(page["images"]), 1)
        self.assertEqual(page["total"], 1)
        unrelated = self.db.save_prompt_pair("Dog", "")
        with self.assertRaises(ValueError):
            self.db.list_library_images(positive, pair_id=unrelated)
        self.assertIsNone(self.db.get_library_positive(9999))

    def test_options_include_active_pair_categories_not_negative_only_or_archived(self):
        self.save("Legacy", category="Legacy category")
        pair = self.db.save_prompt_pair("Cat", "Noise", category="Pair category")
        negative = self.db.get_prompt_pair(pair)["negative_prompt_id"]
        with self.db.model.get_connection() as connection:
            connection.execute(
                "UPDATE prompts SET category='Negative only' WHERE id=?", (negative,)
            )
        archived = self.db.save_prompt_pair("Dog", "Blur", category="Archived category")
        self.db.archive_prompt_pair(archived)
        self.assertEqual(
            self.db.get_library_options(),
            {"categories": ["Legacy category", "Pair category"]},
        )

    def test_variant_detail_is_bounded_and_cursor_continues_without_duplicates(self):
        for index in range(85):
            self.db.save_prompt_pair("Cat", f"Negative {index}", tags=[f"tag{index}"])
        positive = self.db.list_library()["items"][0]["id"]
        first = self.db.get_library_positive(positive, variant_limit=40)
        self.assertEqual(len(first["variants"]), 40)
        self.assertEqual(first["variant_total"], 85)
        self.assertTrue(first["variants_has_more"])
        second = self.db.get_library_positive(
            positive, variant_limit=40, variant_after=first["variants_next_key"]
        )
        third = self.db.get_library_positive(
            positive, variant_after=second["variants_next_key"]
        )
        all_variants = first["variants"] + second["variants"] + third["variants"]
        self.assertEqual(len({item["id"] for item in all_variants}), 85)
        self.assertFalse(third["variants_has_more"])
        self.assertTrue(all(len(item["tags"]) == 1 for item in all_variants))
        with self.assertRaises(ValueError):
            self.db.get_library_positive(positive, variant_limit=201)

    def test_sorts_keysets_survive_anchor_deletion_and_insertions_without_writes(self):
        import sqlite3

        a = self.save("Alpha", rating=2, tags=["tag"])
        b = self.save("beta", rating=5)
        c = self.save("charlie")
        for sort, expected in (
            ("newest", [c, b, a]),
            ("oldest", [a, b, c]),
            ("name", [a, b, c]),
            ("rating", [b, a, c]),
        ):
            with self.subTest(sort=sort):
                first = self.db.list_library(sort=sort, limit=1)
                second = self.db.list_library(
                    sort=sort, limit=1, after=first["next_key"]
                )
                third = self.db.list_library(
                    sort=sort, limit=1, after=second["next_key"]
                )
                self.assertEqual(
                    [part["items"][0]["id"] for part in (first, second, third)],
                    expected,
                )
        first = self.db.list_library(sort="name", limit=1)
        with self.db.model.get_connection() as connection:
            connection.execute("DELETE FROM prompts WHERE id=?", (a,))
        self.save("between")
        second = self.db.list_library(sort="name", after=first["next_key"])
        self.assertEqual([item["id"] for item in second["items"]], [b, c])
        connection = self.db.model.get_connection()
        before = connection.total_changes
        denied = {
            sqlite3.SQLITE_INSERT,
            sqlite3.SQLITE_UPDATE,
            sqlite3.SQLITE_DELETE,
            sqlite3.SQLITE_CREATE_TABLE,
            sqlite3.SQLITE_ALTER_TABLE,
        }
        connection.set_authorizer(
            lambda operation, *_: (
                sqlite3.SQLITE_DENY if operation in denied else sqlite3.SQLITE_OK
            )
        )
        try:
            self.db.list_library()
            self.db.get_library_positive(b)
            self.db.get_library_options()
            self.db.get_library_previews([b])
            self.db.list_library_images(b)
        finally:
            connection.set_authorizer(None)
        self.assertEqual(connection.total_changes, before)

    def test_archived_associations_and_legacy_positive_roles_remain_distinct(self):
        pair = self.db.save_prompt_pair("Cat", "Noise")
        pair_data = self.db.get_prompt_pair(pair)
        negative = pair_data["negative_prompt_id"]
        self.db.link_image_to_prompt(negative, str(Path(self.temp.name) / "legacy.png"))
        rows = self.db.list_library()["items"]
        self.assertIn(negative, [row["id"] for row in rows])
        positive = pair_data["positive_prompt_id"]
        image = self.db.link_image_to_prompt(
            positive, str(Path(self.temp.name) / "pair.png")
        )
        self.db.link_image_to_pair(pair, image, {"source": "test"})
        self.db.archive_prompt_pair(pair)
        detail = self.db.get_library_positive(positive)
        self.assertEqual(detail["variants"], [])
        self.assertEqual(detail["positive"]["image_count"], 1)
        self.assertEqual(detail["unassigned_image_count"], 0)
        self.assertEqual(self.db.list_library_images(positive)["total"], 1)
        with self.assertRaises(ValueError):
            self.db.list_library_images(positive, pair_id=pair)

    def test_invalid_database_inputs_fail_before_queries(self):
        for options in (
            {"category": 2},
            {"tags": [""]},
            {"text": "x" * 501},
            {"after": {}},
            {"after": {"ceiling": "x", "id": 1, "value": 1}},
        ):
            with self.assertRaises(ValueError):
                self.db.list_library(**options)
        for operation in (
            lambda: self.db.get_library_positive(1, variant_after=0),
            lambda: self.db.list_library_images(1, after=0),
        ):
            with self.assertRaises(ValueError):
                operation()

    def test_cursor_key_comes_from_read_page_even_if_anchor_is_deleted_during_read(
        self,
    ):
        from unittest.mock import patch

        self.save("a")
        self.save("b")
        original = self.db._library_parents

        def remove_anchor(*args, **kwargs):
            rows = original(*args, **kwargs)
            with self.db.model.get_connection() as connection:
                connection.execute("DELETE FROM prompts WHERE id=?", (rows[0]["id"],))
            return rows

        with patch.object(self.db, "_library_parents", side_effect=remove_anchor):
            first = self.db.list_library(limit=1)
        second = self.db.list_library(after=first["next_key"])
        self.assertEqual([row["text"] for row in second["items"]], ["a"])

    def test_options_preserve_exact_category_values_that_filters_accept(self):
        self.save("Legacy", category="  Category  ")
        self.db.save_prompt_pair("Cat", "Noise", category="  Variant  ")
        options = self.db.get_library_options()["categories"]
        self.assertEqual(options, ["  Category  ", "  Variant  "])
        self.assertTrue(
            all(self.db.list_library(category=value)["total"] == 1 for value in options)
        )

    def test_name_sort_uses_full_long_text_and_keeps_deleted_anchor_key(self):
        prefix = "shared prefix " * 30
        self.save(prefix + "Zulu")
        alpha = self.save(prefix + "Alpha")
        self.save(prefix + "Middle")
        first = self.db.list_library(sort="name", limit=1)
        self.assertEqual(first["items"][0]["id"], alpha)
        self.assertEqual(first["next_key"]["value"], (prefix + "Alpha").lower())
        with self.db.model.get_connection() as connection:
            connection.execute("DELETE FROM prompts WHERE id=?", (alpha,))
        rest = self.db.list_library(sort="name", after=first["next_key"])
        self.assertEqual(
            [item["text"] for item in rest["items"]],
            [prefix + "Middle", prefix + "Zulu"],
        )

    def test_batch_previews_return_two_newest_links_per_parent_in_one_select(self):
        parents = [self.save(f"Parent {index}") for index in range(200)]
        expected = {}
        for parent in parents[:3]:
            ids = [
                self.db.link_image_to_prompt(
                    parent, str(Path(self.temp.name) / f"{parent}-{index}.png")
                )
                for index in range(3)
            ]
            expected[parent] = ids[-1:0:-1]
        connection = self.db.model.get_connection()
        statements = []
        connection.set_trace_callback(statements.append)
        try:
            previews = self.db.get_library_previews(parents)
        finally:
            connection.set_trace_callback(None)
        selects = [
            query
            for query in statements
            if query.lstrip().upper().startswith(("SELECT", "WITH"))
        ]
        self.assertEqual(len(selects), 1)
        self.assertEqual(
            {
                parent: [image["id"] for image in images]
                for parent, images in previews.items()
                if images
            },
            expected,
        )
        self.assertEqual(self.db.get_library_previews([]), {})
        with self.assertRaises(ValueError):
            self.db.get_library_previews(parents + [9999])
