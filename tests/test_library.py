"""Grouped library reads retain component roles and association semantics."""

import sqlite3
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
            {
                "library_epoch": self.db.get_execution_epoch(),
                "categories": ["Legacy category", "Pair category"],
            },
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

    def state(self, prompt_id, title=None):
        with self.db.model.get_connection() as connection:
            connection.execute(
                "INSERT INTO prompt_library_state(prompt_id,title) VALUES(?,?)",
                (prompt_id, title),
            )

    def test_parent_projection_title_sort_search_and_archive_scope(self):
        pair = self.db.save_prompt_pair("Alpha text", "Blur", tags=["pair-only"])
        positive = self.db.get_prompt_pair(pair)["positive_prompt_id"]
        with self.db.model.get_connection() as connection:
            connection.execute(
                "UPDATE prompts SET category='A',notes=? WHERE id=?",
                ("keep\nnotes", positive),
            )
        self.state(positive, "Portrait")
        blank = self.save("Middle text", category="Active")
        self.state(blank, " \t\u00a0\u3000")
        self.save("beta text")
        epoch = self.db.get_execution_epoch()
        page = self.db.list_library(sort="name")
        self.assertEqual(page["library_epoch"], epoch)
        self.assertEqual(
            [item["text"] for item in page["items"]],
            ["beta text", "Middle text", "Alpha text"],
        )
        parent = page["items"][2]
        self.assertEqual(
            (parent["title"], parent["notes"]), ("Portrait", "keep\nnotes")
        )
        self.assertNotIn("archived", parent)
        self.assertRegex(parent["edit_token"], r"^[0-9a-f]{64}$")
        self.assertEqual(page["items"][1]["title"], " \t\u00a0\u3000")
        self.assertEqual(self.db.list_library(text="Portrait")["total"], 1)
        self.assertEqual(self.db.list_library(text="keep")["total"], 0)
        self.assertEqual(
            self.db.list_library(category="A", tags=["pair-only"])["items"], []
        )
        self.assertEqual(self.db.get_library_options()["categories"], ["A", "Active"])
        renamed = self.db.list_library(sort="name", limit=1)
        with self.db.model.get_connection() as connection:
            connection.execute(
                "UPDATE prompt_library_state SET title='Aardvark' WHERE prompt_id=?",
                (positive,),
            )
        self.assertEqual(
            self.db.list_library(sort="name", limit=1)["items"][0]["id"], positive
        )
        self.assertEqual(renamed["next_key"]["value"], "beta text")
        archived = self.db.save_prompt_pair("Alpha text", "Noise")
        self.db.archive_prompt_pair(archived)
        detail = self.db.get_library_positive(positive)
        self.assertEqual(detail["library_epoch"], epoch)
        self.assertEqual(
            (
                detail["positive"]["variant_count"],
                detail["positive"]["archived_variant_count"],
            ),
            (1, 1),
        )
        self.assertEqual(
            [(v["id"], v["archived"]) for v in detail["variants"]], [(pair, False)]
        )
        only = self.db.get_library_positive(positive, archive="archived")
        self.assertEqual(
            [(v["id"], v["archived"]) for v in only["variants"]], [(archived, True)]
        )
        self.assertEqual(only["variant_total"], 1)
        every = self.db.get_library_positive(positive, archive="all", variant_limit=1)
        self.assertEqual(
            (every["variant_total"], every["variants"][0]["id"]), (2, archived)
        )
        rest = self.db.get_library_positive(
            positive, archive="all", variant_after=every["variants_next_key"]
        )
        self.assertEqual([v["id"] for v in rest["variants"]], [pair])
        for operation in (
            lambda: self.db.get_library_positive(positive, archive="hidden"),
            lambda: self.db.list_library_images(positive, archive="hidden"),
        ):
            with self.assertRaises(ValueError):
                operation()

    def test_all_archived_pairs_keep_parent_and_unfiltered_gallery(self):
        first = self.db.save_prompt_pair("Cat", "Blur")
        second = self.db.save_prompt_pair("Cat", "Noise")
        positive = self.db.get_prompt_pair(first)["positive_prompt_id"]
        images = []
        for pair in (first, second):
            image = self.db.link_image_to_prompt(
                positive, str(Path(self.temp.name) / f"{pair}.png")
            )
            self.db.link_image_to_pair(pair, image, {"source": "test"})
            images.append(image)
            self.db.archive_prompt_pair(pair)
        self.assertIn(positive, [row["id"] for row in self.db.list_library()["items"]])
        detail = self.db.get_library_positive(positive)
        self.assertEqual(detail["positive"]["archived_variant_count"], 2)
        gallery = self.db.list_library_images(positive)
        self.assertEqual([image["id"] for image in gallery["images"]], images[::-1])
        self.assertEqual(gallery["library_epoch"], self.db.get_execution_epoch())
        with self.assertRaises(ValueError):
            self.db.list_library_images(positive, pair_id=first)
        for archive in ("archived", "all"):
            page = self.db.list_library_images(positive, pair_id=first, archive=archive)
            self.assertEqual([image["id"] for image in page["images"]], [images[0]])
        self.db.archive_prompt_pair(second, archived=False)
        with self.assertRaises(ValueError):
            self.db.list_library_images(positive, pair_id=second, archive="archived")

    def test_explicit_parent_survives_last_positive_pair_removal(self):
        kept = self.db.save_prompt_pair("Portrait", "Blur")
        plain = self.db.save_prompt_pair("Plain", "Blur")
        positive = self.db.get_prompt_pair(kept)["positive_prompt_id"]
        plain_positive = self.db.get_prompt_pair(plain)["positive_prompt_id"]
        self.db.save_prompt_pair("Other", "Portrait")
        self.db.save_prompt_pair("Another", "Plain")
        self.state(positive)
        self.assertTrue(self.db.delete_prompt_pair(kept))
        self.assertTrue(self.db.delete_prompt_pair(plain))
        ids = [row["id"] for row in self.db.list_library()["items"]]
        self.assertIn(positive, ids)
        self.assertNotIn(plain_positive, ids)
        detail = self.db.get_library_positive(positive)
        self.assertEqual(detail["positive"]["variant_count"], 0)
        self.assertIsNone(detail["positive"]["title"])
        self.assertIsNone(self.db.get_library_positive(plain_positive))

    def test_token_changes_only_for_owner_projection(self):
        from database.execution_history_schema import rotate_history_epoch
        from database.library_tokens import library_edit_token

        positive = self.save("Cat", category="A", tags=["b", "a"], rating=3)

        def token():
            return self.db.get_library_positive(positive)["positive"]["edit_token"]

        original = token()
        record = self.db.get_library_positive(positive)["positive"]
        self.assertEqual(
            library_edit_token(self.db.get_execution_epoch(), record), original
        )
        self.state(positive)
        self.assertEqual(token(), original)
        pair = self.db.save_prompt_pair("Cat", "Blur", title="Pair", tags=["c"])
        self.db.link_image_to_pair(
            pair,
            self.db.link_image_to_prompt(positive, str(Path(self.temp.name) / "i.png")),
            {"source": "test"},
        )
        self.db.archive_prompt_pair(pair)
        with self.db.model.get_connection() as connection:
            connection.execute(
                "UPDATE prompts SET updated_at='2001-01-01',created_at='2000-01-01' "
                "WHERE id=?",
                (positive,),
            )
        self.assertEqual(token(), original)
        seen = {original}
        for statement, values in (
            ("UPDATE prompt_library_state SET title='T' WHERE prompt_id=?", ()),
            ("UPDATE prompts SET category='B' WHERE id=?", ()),
            ("UPDATE prompts SET notes='n' WHERE id=?", ()),
            ("UPDATE prompts SET rating=4 WHERE id=?", ()),
            ("UPDATE prompts SET text='Cat ' WHERE id=?", ()),
            (
                "DELETE FROM prompt_tags WHERE prompt_id=? AND tag_id=?",
                (
                    self.db.model.get_connection()
                    .execute("SELECT id FROM tags WHERE name='a'")
                    .fetchone()[0],
                ),
            ),
        ):
            with self.subTest(statement=statement):
                with self.db.model.get_connection() as connection:
                    connection.execute(statement, (positive, *values))
                self.assertNotIn(token(), seen)
                seen.add(token())
        before = token()
        rotate_history_epoch(self.db.model.get_connection())
        self.assertNotEqual(token(), before)

    def test_library_snapshot_reads_epoch_and_ceiling_in_one_select_and_ends(self):
        self.save("Cat")
        connection = self.db.model.get_connection()
        statements = []
        connection.set_trace_callback(statements.append)
        try:
            with self.db.library_snapshot(include_ceiling=True) as snapshot:
                self.assertTrue(connection.in_transaction)
            with self.assertRaises(RuntimeError):
                with self.db.library_snapshot():
                    raise RuntimeError("read failed")
        finally:
            connection.set_trace_callback(None)
        self.assertFalse(connection.in_transaction)
        self.assertEqual(snapshot[1:], (self.db.get_execution_epoch(), 1))
        selects = [
            query for query in statements if query.lstrip().upper().startswith("SELECT")
        ]
        self.assertEqual(len(selects), 2)
        self.assertIn("storage_epoch", selects[0])
        self.assertIn("MAX", selects[0].upper())
        self.assertEqual(statements.count("ROLLBACK"), 2)
        connection.execute("BEGIN")
        try:
            with self.assertRaises(sqlite3.ProgrammingError):
                with self.db.library_snapshot():
                    pass
        finally:
            connection.rollback()
        with self.db.model.get_connection() as writer:
            writer.execute("DELETE FROM execution_history_metadata")
        with self.assertRaises(sqlite3.DatabaseError):
            self.db.get_library_options()
        self.assertFalse(connection.in_transaction)
