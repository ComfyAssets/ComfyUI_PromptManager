"""Grouped library reads retain component roles and association semantics."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from database.library_commands import LibraryCommandError
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

    def test_library_items_expose_run_count_and_last_used(self):
        import datetime

        used = self.save("Used")
        never = self.save("Never")
        self.assertTrue(self.db.record_prompt_use(used))
        self.assertTrue(self.db.record_prompt_use(used, times=2))
        legacy = self.save("Legacy naive")
        offset = self.save("Offset")
        broken = self.save("Broken")
        zulu = self.save("Zulu")
        with self.db.model.get_connection() as connection:
            for prompt_id, value in (
                (legacy, "2026-10-07 18:39:06"),
                (zulu, "2026-10-07T18:39:06Z"),
                (offset, "2026-10-07T20:39:06.250+02:00"),
                (broken, "yesterday"),
            ):
                connection.execute(
                    "UPDATE prompts SET last_used_at=?,run_count=1 WHERE id=?",
                    (value, prompt_id),
                )
        items = {row["id"]: row for row in self.db.list_library()["items"]}
        self.assertEqual(items[used]["run_count"], 3)
        stamp = datetime.datetime.fromisoformat(items[used]["last_used_at"])
        self.assertEqual(stamp.utcoffset(), datetime.timedelta(0))
        self.assertLess(
            abs(datetime.datetime.now(datetime.timezone.utc) - stamp),
            datetime.timedelta(minutes=5),
        )
        self.assertEqual(items[never]["run_count"], 0)
        self.assertIsNone(items[never]["last_used_at"])
        self.assertEqual(items[legacy]["last_used_at"], "2026-10-07T18:39:06+00:00")
        self.assertEqual(
            items[offset]["last_used_at"], "2026-10-07T18:39:06.250000+00:00"
        )
        self.assertIsNone(items[broken]["last_used_at"])
        self.assertEqual(items[zulu]["last_used_at"], "2026-10-07T18:39:06+00:00")
        self.assertEqual(type(items[broken]["run_count"]), int)
        detail = self.db.get_library_positive(used)["positive"]
        self.assertEqual(detail["run_count"], 3)
        self.assertEqual(detail["last_used_at"], items[used]["last_used_at"])

    def test_variant_total_matches_filtered_cards(self):
        archived = self.db.save_prompt_pair("Cat", "Blur", category="A")
        self.db.save_prompt_pair("Cat", "Noise", category="A")
        self.db.save_prompt_pair("Cat", "Grain", category="B")
        self.db.archive_prompt_pair(archived)
        self.db.save_prompt_pair("Dog", "Blur", category="B")
        self.save("Legacy")
        everything = self.db.list_library()
        self.assertEqual(everything["variant_total"], 3)
        self.assertEqual(
            everything["variant_total"],
            sum(row["variant_count"] for row in everything["items"]),
        )
        cats = self.db.list_library(text="Cat")
        self.assertEqual(
            (cats["total"], cats["variant_total"]),
            (1, 2),
        )
        self.assertEqual(self.db.list_library(category="B")["variant_total"], 3)
        self.assertEqual(self.db.list_library(text="nothing")["variant_total"], 0)
        first = self.db.list_library(limit=1)
        self.assertEqual(first["variant_total"], 3)
        # A later parent is past the cursor's ceiling, like the total.
        self.db.save_prompt_pair("Bird", "Blur")
        second = self.db.list_library(limit=1, after=first["next_key"])
        self.assertEqual(
            (second["total"], second["variant_total"]),
            (first["total"], 3),
        )

    def test_edit_token_unchanged_by_usage_fields(self):
        positive = self.save("Cat", category="A", tags=["a"], rating=3)

        def parent():
            return self.db.get_library_positive(positive)["positive"]

        before = parent()
        listed = self.db.list_library()["items"][0]
        self.assertEqual(listed["edit_token"], before["edit_token"])
        self.db.record_prompt_use(positive, times=4)
        with self.db.model.get_connection() as connection:
            connection.execute(
                "UPDATE prompts SET last_used_at='2001-01-01T00:00:00+00:00' "
                "WHERE id=?",
                (positive,),
            )
        after = parent()
        self.assertNotEqual(
            (after["run_count"], after["last_used_at"]),
            (before["run_count"], before["last_used_at"]),
        )
        self.assertEqual(after["edit_token"], before["edit_token"])
        self.assertEqual(
            self.db.list_library()["items"][0]["edit_token"], before["edit_token"]
        )

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

    # -- native parent metadata commands -------------------------------------

    def current(self, positive):
        detail = self.db.get_library_positive(positive)
        return detail["library_epoch"], detail["positive"]["edit_token"]

    def update(self, positive, metadata, *, epoch=None, token=None):
        current_epoch, current_token = self.current(positive)
        return self.db.update_library_parent(
            positive,
            expected_epoch=current_epoch if epoch is None else epoch,
            if_match=current_token if token is None else token,
            metadata=metadata,
        )

    def owner_state(self, positive):
        connection = self.db.model.get_connection()
        prompt = dict(
            connection.execute(
                "SELECT text,category,rating,notes,created_at,updated_at,"
                "last_used_at,run_count FROM prompts WHERE id=?",
                (positive,),
            ).fetchone()
        )
        tags = [
            row[0]
            for row in connection.execute(
                "SELECT t.name FROM prompt_tags pt JOIN tags t ON t.id=pt.tag_id "
                "WHERE pt.prompt_id=? ORDER BY t.name",
                (positive,),
            )
        ]
        state = connection.execute(
            "SELECT prompt_id,title FROM prompt_library_state WHERE prompt_id=?",
            (positive,),
        ).fetchone()
        return prompt, tags, None if state is None else tuple(state)

    def test_metadata_clear_omission_conflicts_and_legacy_invalidation(self):
        from database.execution_history_schema import rotate_history_epoch
        from database.library_commands import LibraryCommandError

        positive = self.save("Cat", category="A", tags=["x", "y"], rating=3)
        with self.db.model.get_connection() as connection:
            connection.execute(
                "UPDATE prompts SET notes='n',updated_at='2001-01-01' WHERE id=?",
                (positive,),
            )
        first = self.db.save_prompt_pair(
            "Cat", "Blur", title="P1", category="PC", tags=["pt"], rating=2, notes="a"
        )
        second = self.db.save_prompt_pair(
            "Cat", "Noise", title="P2", category="QC", tags=["qt"], rating=5
        )
        self.db.record_prompt_pair_use(first, 3)
        pairs_before = [self.db.get_prompt_pair(pair) for pair in (first, second)]
        prompt_before, _tags, _state = self.owner_state(positive)
        epoch, token = self.current(positive)

        updated = self.update(
            positive, {"title": "Portrait", "rating": None, "tags": [], "notes": ""}
        )
        self.assertEqual(updated["library_epoch"], epoch)
        record = updated["positive"]
        self.assertIsNone(updated["positive"]["rating"])
        self.assertEqual(updated["positive"]["tags"], [])
        self.assertEqual(
            (record["title"], record["notes"], record["category"]),
            ("Portrait", "", "A"),
        )
        self.assertNotIn("previews", record)
        self.assertNotEqual(record["edit_token"], token)
        self.assertEqual(record["variant_count"], 2)
        fresh = self.db.get_library_positive(positive)["positive"]
        self.assertEqual(record, {key: fresh[key] for key in record})
        for pair_id, pair_before in zip((first, second), pairs_before):
            self.assertEqual(self.db.get_prompt_pair(pair_id), pair_before)
        prompt_after, tags, state = self.owner_state(positive)
        self.assertEqual(tags, [])
        self.assertEqual(state, (positive, "Portrait"))
        self.assertNotEqual(prompt_after["updated_at"], prompt_before["updated_at"])
        for key in ("text", "created_at", "last_used_at", "run_count"):
            self.assertEqual(prompt_after[key], prompt_before[key])

        cleared = self.update(positive, {"category": None, "title": ""})["positive"]
        self.assertEqual((cleared["category"], cleared["title"]), (None, ""))
        self.assertEqual(cleared["notes"], "")
        tagged = self.update(positive, {"tags": [" b ", "b", "a", "a "]})
        self.assertEqual(tagged["positive"]["tags"], ["a", "b"])
        titled = self.update(positive, {"title": None})["positive"]
        self.assertIsNone(titled["title"])

        # Two clients read one token: the first change wins, the second conflicts.
        epoch, shared = self.current(positive)
        winner = self.update(positive, {"title": "First"}, token=shared)
        before_conflict = self.owner_state(positive)
        with self.assertRaises(LibraryCommandError) as caught:
            self.update(positive, {"title": "Second"}, token=shared)
        self.assertEqual(caught.exception.code, "metadata-conflict")
        self.assertEqual(caught.exception.epoch, epoch)
        self.assertEqual(caught.exception.positive, winner["positive"])
        self.assertEqual(self.owner_state(positive), before_conflict)

        # Legacy metadata and standalone text writes invalidate native tokens.
        standalone = self.save("Dog")
        for target, legacy in (
            (positive, lambda: self.db.update_prompt_metadata(positive, category="L")),
            (positive, lambda: self.db.update_prompt_rating(positive, 4)),
            (standalone, lambda: self.db.update_prompt_text(standalone, "Dog 2")),
        ):
            with self.subTest(target=target):
                _epoch, stale = self.current(target)
                self.assertTrue(legacy())
                with self.assertRaises(LibraryCommandError) as caught:
                    self.update(target, {"notes": "late"}, token=stale)
                self.assertEqual(caught.exception.code, "metadata-conflict")
                self.assertNotEqual(caught.exception.positive["edit_token"], stale)

        # A restore with identical content still rejects old epochs and tokens.
        old_epoch, old_token = self.current(positive)
        before_restore = self.owner_state(positive)
        new_epoch = rotate_history_epoch(self.db.model.get_connection())
        for epoch_value, token_value, code in (
            (old_epoch, old_token, "storage-changed"),
            (new_epoch, old_token, "metadata-conflict"),
        ):
            with self.subTest(code=code):
                with self.assertRaises(LibraryCommandError) as caught:
                    self.update(
                        positive, {"title": "x"}, epoch=epoch_value, token=token_value
                    )
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.epoch, new_epoch)
        with self.assertRaises(LibraryCommandError) as caught:
            self.db.update_library_parent(
                9999,
                expected_epoch=old_epoch,
                if_match=old_token,
                metadata={"title": "x"},
            )
        self.assertEqual(caught.exception.code, "storage-changed")
        self.assertIsNone(caught.exception.positive)
        self.assertEqual(self.owner_state(positive), before_restore)

        # Missing and negative-only owners are absent and never registered.
        negative_only = self.db.get_prompt_pair(first)["negative_prompt_id"]
        for missing in (9999, negative_only):
            self.assertIsNone(
                self.db.update_library_parent(
                    missing,
                    expected_epoch=new_epoch,
                    if_match="0" * 64,
                    metadata={"title": "x"},
                )
            )
        self.assertIsNone(self.owner_state(negative_only)[2])

    def test_first_unchanged_native_metadata_save_registers_parent_without_changing_payload(  # noqa: E501
        self,
    ):
        kept = self.db.save_prompt_pair("Cat", "Blur", title="Pair")
        positive = self.db.get_prompt_pair(kept)["positive_prompt_id"]
        self.db.update_prompt_metadata(positive, category="A", tags=["t"])
        before = self.db.get_library_positive(positive)["positive"]
        owner_before = self.owner_state(positive)
        self.assertIsNone(owner_before[2])
        receipt = self.update(positive, {"category": "A", "title": None, "tags": ["t"]})
        self.assertEqual(
            receipt["positive"], {k: before[k] for k in receipt["positive"]}
        )
        prompt, tags, state = self.owner_state(positive)
        self.assertEqual((prompt, tags), owner_before[:2])
        self.assertEqual(state, (positive, None))

        connection = self.db.model.get_connection()
        statements = []
        before_repeat = connection.total_changes
        connection.set_trace_callback(statements.append)
        try:
            repeat = self.update(positive, {"category": "A", "rating": None})
        finally:
            connection.set_trace_callback(None)
        self.assertEqual(connection.total_changes - before_repeat, 0)
        writes = [
            query
            for query in statements
            if query.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}
        ]
        self.assertEqual(writes, [])
        self.assertEqual(repeat["positive"], receipt["positive"])
        self.assertEqual(self.owner_state(positive), (prompt, tags, state))

        # The registered parent survives the loss of its last positive pair.
        self.db.save_prompt_pair("Other", "Cat")
        self.assertTrue(self.db.delete_prompt_pair(kept))
        self.assertIn(positive, [row["id"] for row in self.db.list_library()["items"]])
        self.assertEqual(
            self.update(positive, {"title": "Kept"})["positive"]["title"], "Kept"
        )

    def test_pair_archive_and_restore_are_independent_of_parent_metadata(self):
        first = self.db.save_prompt_pair("Cat", "Blur", title="P1", tags=["p"])
        second = self.db.save_prompt_pair("Cat", "Noise")
        positive = self.db.get_prompt_pair(first)["positive_prompt_id"]
        _epoch, token = self.current(positive)
        for pair in (first, second):
            self.assertTrue(self.db.archive_prompt_pair(pair))
        self.assertEqual(self.current(positive)[1], token)
        pairs = [self.db.get_prompt_pair(pair) for pair in (first, second)]
        record = self.update(positive, {"title": "Parent", "rating": 4})["positive"]
        self.assertEqual((record["variant_count"], record["title"]), (0, "Parent"))
        self.assertEqual([self.db.get_prompt_pair(p) for p in (first, second)], pairs)
        self.assertTrue(all(pair["archived"] for pair in pairs))
        self.assertTrue(self.db.archive_prompt_pair(first, archived=False))
        detail = self.db.get_library_positive(positive)
        self.assertEqual(detail["positive"]["edit_token"], record["edit_token"])
        self.assertEqual(
            (detail["positive"]["title"], detail["positive"]["rating"]), ("Parent", 4)
        )
        self.assertEqual([v["id"] for v in detail["variants"]], [first])
        self.assertEqual(self.db.get_prompt_pair(first)["title"], "P1")

    def test_retention_registration_rolls_back_with_command(self):
        from contextlib import contextmanager
        from unittest.mock import patch

        from database.library_commands import LibraryCommandError

        pair = self.db.save_prompt_pair("Cat", "Blur")
        positive = self.db.get_prompt_pair(pair)["positive_prompt_id"]
        self.db.update_prompt_metadata(positive, category="A", tags=["t"])
        before = self.owner_state(positive)
        self.assertIsNone(before[2])
        epoch, token = self.current(positive)
        original_parents = self.db._library_parents
        original_transaction = self.db._pair_transaction
        reads = []

        def failing_receipt(*args, **kwargs):
            reads.append(1)
            if len(reads) > 1:
                raise sqlite3.OperationalError("receipt failed")
            return original_parents(*args, **kwargs)

        def failing_state(conn, prompt_id):
            conn.execute(
                "INSERT INTO prompt_library_state(prompt_id) VALUES(?)", (prompt_id,)
            )
            raise sqlite3.OperationalError("state failed")

        @contextmanager
        def failing_commit():
            with original_transaction() as conn:
                yield conn
                raise sqlite3.OperationalError("commit failed")

        failures = (
            (
                "_sync_prompt_tags",
                {"side_effect": sqlite3.OperationalError("tags failed")},
            ),
            ("_ensure_library_parent", {"side_effect": failing_state}),
            ("_library_parents", {"side_effect": failing_receipt}),
            ("_pair_transaction", {"new": failing_commit}),
        )
        for name, options in failures:
            with self.subTest(failure=name):
                reads.clear()
                with patch.object(self.db, name, **options):
                    with self.assertRaises(sqlite3.OperationalError):
                        self.update(
                            positive,
                            {"title": "T", "category": "B", "tags": ["u"]},
                            epoch=epoch,
                            token=token,
                        )
                self.assertFalse(self.db.model.get_connection().in_transaction)
                self.assertEqual(self.owner_state(positive), before)
        for options, code in (
            ({"token": "f" * 64}, "metadata-conflict"),
            ({"epoch": "00000000-0000-4000-8000-000000000000"}, "storage-changed"),
        ):
            with self.subTest(code=code):
                with self.assertRaises(LibraryCommandError) as caught:
                    self.update(positive, {"category": "A"}, **options)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(self.owner_state(positive), before)
        for metadata in ({}, {"text": "x"}, {"tags": None}, {"rating": True}):
            with self.subTest(metadata=metadata):
                with self.assertRaises(ValueError):
                    self.update(positive, metadata)
        self.assertEqual(self.owner_state(positive), before)
        self.assertEqual(
            self.update(positive, {"category": "B"})["positive"]["category"], "B"
        )
        self.assertEqual(self.owner_state(positive)[2], (positive, None))

    def test_concurrent_same_token_saves_admit_exactly_one_change(self):
        from concurrent.futures import ThreadPoolExecutor

        from database.library_commands import LibraryCommandError

        positive = self.save("Cat")
        epoch, token = self.current(positive)

        def save(index):
            try:
                self.db.update_library_parent(
                    positive,
                    expected_epoch=epoch,
                    if_match=token,
                    metadata={"title": f"Title {index}"},
                )
                return "ok"
            except LibraryCommandError as error:
                return error.code
            finally:
                self.db.close()

        with ThreadPoolExecutor(max_workers=6) as executor:
            outcomes = list(executor.map(save, range(12)))
        self.assertEqual(outcomes.count("ok"), 1)
        self.assertEqual(outcomes.count("metadata-conflict"), 11)
        title = self.db.get_library_positive(positive)["positive"]["title"]
        self.assertEqual(title, f"Title {outcomes.index('ok')}")

    # -- native create/revision and exact-parent Add negative -----------------

    PAIR_KEYS = {
        "id",
        "positive_prompt_id",
        "negative_prompt_id",
        "title",
        "category",
        "rating",
        "notes",
        "created_at",
        "updated_at",
        "last_used_at",
        "run_count",
        "archived",
        "positive_text",
        "negative_text",
        "image_count",
        "tags",
    }
    OUTCOMES = ("positive_created", "pair_created", "parent_metadata_applied")

    def create(self, positive, negative="", metadata=None, *, epoch=None):
        return self.db.create_library_recipe(
            positive,
            negative,
            expected_epoch=self.db.get_execution_epoch() if epoch is None else epoch,
            parent_metadata={"title": "Recipe"} if metadata is None else metadata,
        )

    def add(self, positive, negative, *, epoch=None, token=None):
        current_epoch, current_token = self.current(positive)
        return self.db.add_library_negative(
            positive,
            negative,
            expected_epoch=current_epoch if epoch is None else epoch,
            if_match=current_token if token is None else token,
        )

    def storage(self):
        connection = self.db.model.get_connection()
        return {
            table: [
                tuple(row)
                for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1,2")
            ]
            for table in (
                "prompts",
                "prompt_pairs",
                "prompt_library_state",
                "prompt_tags",
                "prompt_pair_tags",
                "tags",
                "image_prompt_pairs",
            )
        }

    def test_atomic_create_receipts_and_canonical_reuse(self):
        epoch = self.db.get_execution_epoch()
        metadata = {
            "title": "Portrait",
            "category": "Studio",
            "tags": [" soft light ", "soft light", "warm"],
            "rating": 4,
            "notes": "",
        }
        first = self.create("  First Spelling  ", "Blur", metadata)
        self.assertEqual(
            set(first),
            {"library_epoch", "positive", "pair", *self.OUTCOMES},
        )
        self.assertEqual(
            [
                first[k]
                for k in ("positive_created", "pair_created", "parent_metadata_applied")
            ],
            [True, True, True],
        )
        self.assertEqual(first["library_epoch"], epoch)
        positive = first["positive"]
        self.assertNotIn("previews", positive)
        self.assertEqual(
            {k: positive[k] for k in ("text", "title", "category", "rating", "notes")},
            {
                "text": "First Spelling",
                "title": "Portrait",
                "category": "Studio",
                "rating": 4,
                "notes": "",
            },
        )
        self.assertEqual(positive["tags"], ["soft light", "warm"])
        self.assertEqual((positive["variant_count"], positive["image_count"]), (1, 0))
        self.assertEqual(set(first["pair"]), self.PAIR_KEYS)
        self.assertEqual(first["pair"], self.db.get_prompt_pair(first["pair"]["id"]))
        # New pairs get the existing default empty pair metadata, not a copy.
        self.assertEqual(
            [first["pair"][k] for k in ("title", "category", "rating", "notes")],
            [None, None, None, None],
        )
        self.assertEqual(first["pair"]["tags"], [])
        fresh = self.db.get_library_positive(positive["id"])["positive"]
        self.assertEqual(positive, {key: fresh[key] for key in positive})

        # Outer trim and case reuse the first spelling; nothing is rewritten.
        reuse = self.create(" first spelling ", " BLUR ", {"title": "Other"})
        self.assertEqual(
            [
                reuse[k]
                for k in ("positive_created", "pair_created", "parent_metadata_applied")
            ],
            [False, False, False],
        )
        self.assertEqual(reuse["pair"]["positive_text"], "First Spelling")
        self.assertEqual(reuse["pair"]["negative_text"], "Blur")
        self.assertEqual(reuse["pair"]["id"], first["pair"]["id"])
        self.assertEqual(reuse["positive"], positive)

        # Internal whitespace is significant and creates a new parent.
        spaced = self.create("First  Spelling", "Blur", {"title": "Spaced"})
        self.assertEqual([spaced[k] for k in self.OUTCOMES], [True, True, True])
        self.assertNotEqual(spaced["positive"]["id"], positive["id"])

        # Existing positive with a new negative keeps the parent metadata.
        revised = self.create("FIRST SPELLING", "Noise", {"title": "Ignored"})
        self.assertEqual([revised[k] for k in self.OUTCOMES], [False, True, False])
        self.assertEqual(revised["positive"]["title"], "Portrait")
        self.assertEqual(revised["positive"]["variant_count"], 2)
        self.assertEqual(revised["positive"]["edit_token"], positive["edit_token"])

        # Missing-versus-empty: blank negatives share one explicit-empty pair.
        empty = self.create("First Spelling", "", {"title": "x"})
        blank = self.create("First Spelling", "   ", {"title": "x"})
        self.assertEqual([empty[k] for k in self.OUTCOMES], [False, True, False])
        self.assertEqual([blank[k] for k in self.OUTCOMES], [False, False, False])
        self.assertEqual(blank["pair"]["id"], empty["pair"]["id"])
        self.assertIsNone(empty["pair"]["negative_prompt_id"])
        self.assertEqual(empty["pair"]["negative_text"], "")

        # Archived pair reuse is reported and never restored implicitly.
        archived_pair = revised["pair"]["id"]
        self.assertTrue(self.db.archive_prompt_pair(archived_pair))
        archived = self.create("first spelling", "noise", {"title": "x"})
        self.assertEqual([archived[k] for k in self.OUTCOMES], [False, False, False])
        self.assertEqual(archived["pair"]["id"], archived_pair)
        self.assertIs(archived["pair"]["archived"], True)
        db_pair = self.db.get_prompt_pair(archived_pair)
        self.assertEqual(db_pair, archived["pair"])
        self.assertEqual(self.db.get_prompt_pair(archived_pair)["archived"], True)
        self.assertEqual(db_pair["negative_text"], "Noise")
        self.assertEqual(archived["positive"]["variant_count"], 2)

        # Reusing a legacy pair keeps its metadata, usage and image links.
        legacy = self.db.save_prompt_pair(
            "Legacy", "Grain", title="Pair", category="P", tags=["pt"], rating=2
        )
        legacy_positive = self.db.get_prompt_pair(legacy)["positive_prompt_id"]
        image = self.db.link_image_to_prompt(
            legacy_positive, str(Path(self.temp.name) / "legacy.png")
        )
        self.db.link_image_to_pair(legacy, image, {"source": "run"})
        self.db.record_prompt_pair_use(legacy, 3)
        self.db.update_prompt_metadata(legacy_positive, category="Owner")
        before = self.storage()
        pair_before = self.db.get_prompt_pair(legacy)
        prompt_before = self.owner_state(legacy_positive)
        again = self.create("legacy", "grain", {"title": "Ignored", "rating": 5})
        self.assertEqual([again[k] for k in self.OUTCOMES], [False, False, False])
        self.assertEqual(again["pair"], pair_before)
        self.assertEqual(again["positive"]["image_count"], 1)
        self.assertEqual(self.owner_state(legacy_positive)[:2], prompt_before[:2])
        after = self.storage()
        self.assertEqual(
            after["prompt_library_state"],
            sorted([*before["prompt_library_state"], (legacy_positive, None)]),
        )
        for table in set(after) - {"prompt_library_state"}:
            self.assertEqual(after[table], before[table], table)

    def test_native_create_revision_and_add_negative_retain_reused_positive_after_last_positive_pair_removal(  # noqa: E501
        self,
    ):
        # Legacy commands never register Library parent intent.
        self.db.save_prompt_pair("Other", "Portrait")
        self.db.save_prompt_pair("Another", "Plain")
        self.db.save_prompt_pair("Third", "")
        negative_only = self.db.get_prompt_pair(
            self.db.save_prompt_pair("Other", "Portrait")
        )["negative_prompt_id"]
        self.db.update_prompt_metadata(negative_only, category="Shared", tags=["n"])
        self.assertEqual(self.storage()["prompt_library_state"], [])
        self.assertIsNone(self.db.get_library_positive(negative_only))
        owner_before = self.owner_state(negative_only)

        # A formerly negative-only component is reused with its own metadata.
        created = self.create(" portrait ", "Noise", {"title": "Not applied"})
        self.assertEqual([created[k] for k in self.OUTCOMES], [False, True, False])
        self.assertEqual(created["positive"]["id"], negative_only)
        self.assertEqual(created["positive"]["text"], "Portrait")
        self.assertIsNone(created["positive"]["title"])
        self.assertEqual(created["positive"]["category"], "Shared")
        self.assertEqual(self.owner_state(negative_only)[:2], owner_before[:2])
        self.assertEqual(self.owner_state(negative_only)[2], (negative_only, None))
        token = created["positive"]["edit_token"]

        # Removing its last positive pair keeps the explicit parent.
        self.assertTrue(self.db.delete_prompt_pair(created["pair"]["id"]))
        detail = self.db.get_library_positive(negative_only)
        self.assertEqual(detail["positive"]["variant_count"], 0)
        self.assertEqual(detail["positive"]["edit_token"], token)

        # Add negative registers retention for an ordinary eligible parent too.
        plain = self.save("Standalone")
        added = self.add(plain, "Blur")
        self.assertEqual([added[k] for k in self.OUTCOMES], [False, True, False])
        self.assertEqual(self.owner_state(plain)[2], (plain, None))
        self.db.save_prompt_pair("Elsewhere", "standalone")
        self.assertTrue(self.db.delete_prompt_pair(added["pair"]["id"]))
        self.assertIn(plain, [row["id"] for row in self.db.list_library()["items"]])
        self.assertEqual(self.add(negative_only, "")["pair_created"], True)

        # An arbitrary negative-only component stays unavailable to Add.
        plain_negative = self.db.get_prompt_pair(
            self.db.save_prompt_pair("Another", "Plain")
        )["negative_prompt_id"]
        before = self.storage()
        self.assertIsNone(
            self.db.add_library_negative(
                plain_negative,
                "Blur",
                expected_epoch=self.db.get_execution_epoch(),
                if_match="0" * 64,
            )
        )
        self.assertEqual(self.storage(), before)

    def test_exact_parent_add_does_not_revalidate_or_replace_stored_positive(self):
        from database.execution_history_schema import rotate_history_epoch
        from database.library_commands import LibraryCommandError

        # Legacy rows may hold text a new submission could not: over-long and
        # outer whitespace. Add negative targets the ID and never re-saves it.
        long_text = "  " + "x" * 10050 + "  "
        with self.db.model.get_connection() as connection:
            connection.execute(
                "INSERT INTO prompts(text,hash,category,rating,notes) "
                "VALUES(?,?,?,?,?)",
                (long_text, generate_prompt_hash(long_text), "C", 3, "kept"),
            )
            positive = connection.execute(
                "SELECT id FROM prompts WHERE text=?", (long_text,)
            ).fetchone()[0]
        self.db.update_prompt_metadata(positive, tags=["keep"])
        existing_parent = self.db.get_library_positive(positive)["positive"]
        prompts_before = self.storage()["prompts"]

        added = self.add(positive, " Blur ")
        self.assertEqual([added[k] for k in self.OUTCOMES], [False, True, False])
        self.assertEqual(
            {
                k: added["positive"][k]
                for k in (
                    "text",
                    "title",
                    "category",
                    "tags",
                    "rating",
                    "notes",
                    "edit_token",
                )
            },
            {
                k: existing_parent[k]
                for k in (
                    "text",
                    "title",
                    "category",
                    "tags",
                    "rating",
                    "notes",
                    "edit_token",
                )
            },
        )
        self.assertEqual(added["pair"]["positive_prompt_id"], positive)
        self.assertEqual(added["pair"]["positive_text"], long_text)
        self.assertEqual(added["pair"]["negative_text"], "Blur")
        self.assertEqual(added["positive"]["variant_count"], 1)
        prompts_after = self.storage()["prompts"]
        self.assertEqual(prompts_after[: len(prompts_before)], prompts_before)
        self.assertEqual(len(prompts_after), len(prompts_before) + 1)

        # Reuse (including an archived pair and an explicit empty) is truthful.
        repeat = self.add(positive, "blur")
        self.assertEqual([repeat[k] for k in self.OUTCOMES], [False, False, False])
        self.assertEqual(repeat["pair"], added["pair"])
        self.assertTrue(self.db.archive_prompt_pair(added["pair"]["id"]))
        archived = self.add(positive, "BLUR")
        self.assertEqual(archived["pair_created"], False)
        self.assertIs(archived["pair"]["archived"], True)
        self.assertEqual(self.db.get_prompt_pair(added["pair"]["id"])["archived"], True)
        empty = self.add(positive, "")
        self.assertEqual(
            (empty["pair_created"], empty["pair"]["negative_prompt_id"]), (True, None)
        )
        self.assertEqual(self.add(positive, "  ")["pair"]["id"], empty["pair"]["id"])

        # Guards run before any component, pair or state insertion.
        other = self.save("Fresh parent")
        epoch, token = self.current(other)
        stale = token
        self.db.update_prompt_metadata(other, category="changed")
        before = self.storage()
        with self.assertRaises(LibraryCommandError) as caught:
            self.add(other, "Brand new negative", token=stale)
        self.assertEqual(caught.exception.code, "metadata-conflict")
        self.assertEqual(caught.exception.positive["category"], "changed")
        self.assertEqual(self.storage(), before)
        rotate_history_epoch(self.db.model.get_connection())
        with self.assertRaises(LibraryCommandError) as caught:
            self.add(other, "Brand new negative", epoch=epoch)
        self.assertEqual(caught.exception.code, "storage-changed")
        self.assertIsNone(caught.exception.positive)
        with self.assertRaises(LibraryCommandError) as caught:
            self.create("Brand new positive", "Brand new negative", epoch=epoch)
        self.assertEqual(caught.exception.code, "storage-changed")
        self.assertEqual(self.storage(), before)
        self.assertIsNone(
            self.db.add_library_negative(
                9999,
                "x",
                expected_epoch=self.db.get_execution_epoch(),
                if_match="0" * 64,
            )
        )
        for bad in (None, 5, "x" * 10001):
            with self.subTest(negative=bad):
                with self.assertRaises(ValueError):
                    self.add(other, bad)
        for options in ({"epoch": "nope"}, {"token": "F" * 64}):
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    self.add(other, "x", **options)
        for positive_text, negative_text, metadata in (
            ("", "x", {"title": "t"}),
            ("  ", "x", {"title": "t"}),
            (None, "x", {"title": "t"}),
            ("y" * 10001, "x", {"title": "t"}),
            ("ok", None, {"title": "t"}),
            ("ok", "y" * 10001, {"title": "t"}),
            ("ok", "x", {}),
            ("ok", "x", {"title": "  "}),
            ("ok", "x", {"title": None}),
            ("ok", "x", {"category": "c"}),
            ("ok", "x", {"title": "t", "text": "x"}),
            ("ok", "x", {"title": "t", "rating": 0}),
            ("ok", "x", None),
        ):
            with self.subTest(positive=positive_text, metadata=metadata):
                with self.assertRaises(ValueError):
                    self.db.create_library_recipe(
                        positive_text,
                        negative_text,
                        expected_epoch=self.db.get_execution_epoch(),
                        parent_metadata=metadata,
                    )
        self.assertEqual(self.storage(), before)

    def test_native_concurrent_winner_and_transaction_rollback(self):
        from concurrent.futures import ThreadPoolExecutor
        from contextlib import contextmanager
        from unittest.mock import patch

        epoch = self.db.get_execution_epoch()
        metadata = {"title": "Race", "tags": ["race"], "rating": 2}

        def create(index):
            try:
                return self.db.create_library_recipe(
                    " Racing Cat " if index % 2 else "racing cat",
                    "Racing Blur",
                    expected_epoch=epoch,
                    parent_metadata={**metadata, "notes": f"writer {index}"},
                )
            finally:
                self.db.close()

        with ThreadPoolExecutor(max_workers=6) as executor:
            receipts = list(executor.map(create, range(12)))
        self.assertEqual(sum(r["positive_created"] for r in receipts), 1)
        self.assertEqual(sum(r["pair_created"] for r in receipts), 1)
        self.assertEqual(sum(r["parent_metadata_applied"] for r in receipts), 1)
        winner = next(r for r in receipts if r["positive_created"])
        self.assertEqual(len({r["pair"]["id"] for r in receipts}), 1)
        stored = self.db.get_library_positive(winner["positive"]["id"])["positive"]
        self.assertEqual(stored["notes"], winner["positive"]["notes"])
        self.assertEqual(stored["tags"], ["race"])
        self.assertEqual(self.db.list_prompt_pairs()["total"], 1)

        positive = winner["positive"]["id"]
        _epoch, token = self.current(positive)

        def add(index):
            try:
                return self.db.add_library_negative(
                    positive, "Shared Negative", expected_epoch=epoch, if_match=token
                )
            finally:
                self.db.close()

        with ThreadPoolExecutor(max_workers=6) as executor:
            added = list(executor.map(add, range(12)))
        self.assertEqual(sum(r["pair_created"] for r in added), 1)
        self.assertEqual(len({r["pair"]["id"] for r in added}), 1)
        self.assertEqual(sum(r["positive_created"] for r in added), 0)

        # Any failure inside the command rolls the whole command back.
        unregistered = self.save("Unregistered legacy parent")
        unregistered_token = self.current(unregistered)[1]
        original_transaction = self.db._pair_transaction
        original_parents = self.db._library_parents

        @contextmanager
        def failing_commit():
            with original_transaction() as conn:
                yield conn
                raise sqlite3.OperationalError("commit failed")

        def failing_state(conn, prompt_id):
            conn.execute(
                "INSERT INTO prompt_library_state(prompt_id) VALUES(?)", (prompt_id,)
            )
            raise sqlite3.OperationalError("state failed")

        def failing_component(conn, text):
            conn.execute(
                "INSERT INTO prompts(text,hash) VALUES(?,?)",
                ("partial", generate_prompt_hash("partial")),
            )
            raise sqlite3.OperationalError("component failed")

        def failing_pair(*args, **kwargs):
            raise sqlite3.OperationalError("pair failed")

        def failing_result(*args, **kwargs):
            raise sqlite3.OperationalError("result failed")

        reads = []

        def failing_parent_read(*args, **kwargs):
            reads.append(1)
            if len(reads) > 1:
                raise sqlite3.OperationalError("parent read failed")
            return original_parents(*args, **kwargs)

        failures = (
            ("_sync_prompt_tags", {"side_effect": sqlite3.OperationalError("tags")}),
            ("_ensure_library_parent", {"side_effect": failing_state}),
            ("_pair_component_outcome", {"side_effect": failing_component}),
            ("_save_pair_components", {"side_effect": failing_pair}),
            ("_read_pair_rows", {"side_effect": failing_result}),
            ("_library_parents", {"side_effect": failing_parent_read}),
            ("_pair_transaction", {"new": failing_commit}),
        )
        commands = {
            "create": lambda: self.db.create_library_recipe(
                "Rolled back",
                "Rolled negative",
                expected_epoch=epoch,
                parent_metadata={"title": "T", "tags": ["rolled"]},
            ),
            "add": lambda: self.db.add_library_negative(
                unregistered,
                "Rolled negative",
                expected_epoch=epoch,
                if_match=unregistered_token,
            ),
        }
        before = self.storage()
        for command, run in commands.items():
            for name, options in failures:
                if command == "add" and name == "_sync_prompt_tags":
                    continue
                with self.subTest(command=command, failure=name):
                    reads.clear()
                    with patch.object(self.db, name, **options):
                        with self.assertRaises(sqlite3.OperationalError):
                            run()
                    self.assertFalse(self.db.model.get_connection().in_transaction)
                    self.assertEqual(self.storage(), before)

    def test_full_library_portable_export_preserves_independent_owners(self):
        from contextlib import contextmanager
        from unittest.mock import patch

        pair = self.db.save_prompt_pair(
            "Cat", "Blur", title="Pair", category="PC", tags=["z", "a"], rating=3
        )
        empty = self.db.save_prompt_pair("Cat", "", notes="note")
        self.db.archive_prompt_pair(empty)
        dual = self.db.save_prompt_pair("Blur", "Noise")
        positive = self.db.get_prompt_pair(pair)["positive_prompt_id"]
        self.db.update_prompt_metadata(
            positive, category="Owner", tags=["t"], rating=5, notes="owner note"
        )
        negative_only = self.db.get_prompt_pair(dual)["negative_prompt_id"]
        self.db.update_prompt_metadata(negative_only, category="Hidden")
        self.save("Solo", category="S")
        before = self.db.get_prompt_pair(pair)
        epoch = self.db.get_execution_epoch()
        original = self.db.library_snapshot

        @contextmanager
        def racing(*args, **kwargs):
            with original(*args, **kwargs) as snapshot:
                writer = sqlite3.connect(self.db.model.db_path)
                try:
                    writer.execute(
                        "UPDATE prompts SET category='Raced' WHERE id=?", (positive,)
                    )
                    writer.execute(
                        "UPDATE prompt_pairs SET title='Raced' WHERE id=?", (pair,)
                    )
                    writer.commit()
                finally:
                    writer.close()
                yield snapshot

        statements = []
        connection = self.db.model.get_connection()
        connection.set_trace_callback(statements.append)
        try:
            with patch.object(self.db, "library_snapshot", racing):
                doc = self.db.export_library()
        finally:
            connection.set_trace_callback(None)
        self.assertEqual(
            (doc["schema_version"], doc["kind"]), (1, "prompt-manager-library")
        )
        by_text = {owner["positive_text"]: owner for owner in doc["positives"]}
        self.assertEqual(set(by_text), {"Cat", "Blur", "Solo"})
        self.assertNotIn("Noise", by_text)
        for owner in doc["positives"]:
            self.assertEqual(
                set(owner),
                {
                    "positive_text",
                    "title",
                    "category",
                    "tags",
                    "rating",
                    "notes",
                    "variants",
                },
            )
            for variant in owner["variants"]:
                self.assertEqual(
                    set(variant),
                    {
                        "negative_text",
                        "title",
                        "category",
                        "tags",
                        "rating",
                        "notes",
                        "archived",
                    },
                )
        cat = by_text["Cat"]
        self.assertEqual(
            (cat["category"], cat["tags"], cat["rating"]), ("Owner", ["t"], 5)
        )
        first, second = cat["variants"]
        self.assertEqual(
            (first["negative_text"], first["title"], first["category"]),
            ("Blur", "Pair", "PC"),
        )
        self.assertEqual(
            (first["tags"], first["rating"], first["archived"]), (["a", "z"], 3, False)
        )
        self.assertEqual(second["negative_text"], "")
        self.assertTrue(second["archived"])
        self.assertEqual(second["notes"], "note")
        self.assertEqual(by_text["Solo"]["variants"], [])
        self.assertEqual(by_text["Solo"]["category"], "S")
        self.assertEqual(by_text["Blur"]["variants"][0]["negative_text"], "Noise")
        self.assertNotIn("Raced", json.dumps(doc))
        self.assertEqual(self.db.get_prompt_pair(pair)["title"], "Raced")
        self.assertNotEqual(before["title"], "Raced")
        self.assertEqual(self.db.get_execution_epoch(), epoch)
        writes = [
            q
            for q in statements
            if q.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
        ]
        self.assertEqual(writes, [])

    def test_export_uses_one_snapshot_and_never_writes(self):
        self.db.save_prompt_pair("Cat", "Blur")
        connection = self.db.model.get_connection()
        statements = []
        connection.set_trace_callback(statements.append)
        try:
            self.db.export_library()
        finally:
            connection.set_trace_callback(None)
        self.assertEqual(statements.count("BEGIN"), 1)
        self.assertEqual(statements.count("ROLLBACK"), 1)
        self.assertNotIn("COMMIT", statements)
        self.assertFalse(connection.in_transaction)
        self.assertEqual(
            self.db.export_library()["positives"][0]["variants"][0]["negative_text"],
            "Blur",
        )

    # -- final-review fixes ---------------------------------------------------

    def _category(self, positive):
        return self.owner_state(positive)[0]["category"]

    def test_native_patch_trims_category_and_blank_becomes_null(self):
        positive = self.db.save_prompt_pair("Cat", "Blur")
        positive = self.db.get_prompt_pair(positive)["positive_prompt_id"]
        self.state(positive)
        for raw, expected in (
            ("  Spaced  ", "Spaced"),
            ("", None),
            ("Again", "Again"),
            ("   ", None),
        ):
            with self.subTest(raw=raw):
                result = self.update(positive, {"category": raw})
                self.assertEqual(self._category(positive), expected)
                self.assertEqual(result["positive"]["category"], expected)

    def test_native_patch_padded_category_equal_to_stored_writes_nothing(self):
        positive = self.save("Cat", category="Studio")
        self.state(positive)
        before = self.owner_state(positive)
        statements = []
        connection = self.db.model.get_connection()
        epoch, token = self.current(positive)
        connection.set_trace_callback(statements.append)
        try:
            self.db.update_library_parent(
                positive,
                expected_epoch=epoch,
                if_match=token,
                metadata={"category": "  Studio "},
            )
        finally:
            connection.set_trace_callback(None)
        self.assertEqual(self.owner_state(positive), before)
        dml = [
            s
            for s in statements
            if s.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE"))
        ]
        self.assertEqual(dml, [])

    def test_native_patch_category_limit_applies_to_trimmed_value(self):
        positive = self.save("Cat")
        self.state(positive)
        self.update(positive, {"category": "  " + "x" * 100 + "  "})
        self.assertEqual(self._category(positive), "x" * 100)
        with self.assertRaises(ValueError):
            self.update(positive, {"category": "x" * 101})

    def test_native_create_trims_category_and_blank_becomes_null(self):
        for index, (raw, expected) in enumerate(
            (("  Spaced  ", "Spaced"), ("  ", None), ("", None))
        ):
            with self.subTest(raw=raw):
                result = self.db.create_library_recipe(
                    f"Cat {index}",
                    "",
                    expected_epoch=self.db.get_execution_epoch(),
                    parent_metadata={"title": "T", "category": raw},
                )
                positive = result["positive"]["id"]
                self.assertEqual(self._category(positive), expected)
                self.assertEqual(result["positive"]["category"], expected)

    def test_restore_of_identical_backup_fences_stale_patch_epoch(self):
        from unittest.mock import patch

        from database.models import PromptModel

        try:
            from tests.open_handles import assert_closed
        except ImportError:
            from open_handles import assert_closed

        positive = self.save("Cat")
        self.state(positive)
        self.update(positive, {"title": "Before"})
        epoch, token = self.current(positive)
        backup = str(Path(self.temp.name) / "backup.db")
        self.assertTrue(self.db.model.backup_database(backup))
        real = PromptModel.verify_database_file
        calls = []

        def spy(path, **kwargs):
            calls.append(kwargs)
            return real(path, **kwargs)

        with patch.object(PromptModel, "verify_database_file", staticmethod(spy)):
            self.db.model.restore_from_file(backup)
        self.assertIn({"require_library_state": True}, calls)
        with self.assertRaises(LibraryCommandError) as raised:
            self.db.update_library_parent(
                positive,
                expected_epoch=epoch,
                if_match=token,
                metadata={"title": "After"},
            )
        self.assertEqual(raised.exception.code, "storage-changed")
        self.assertEqual(self.owner_state(positive)[2], (positive, "Before"))
        self.db.close_all()
        assert_closed(self, self.temp.name)

    def test_archived_pair_image_scope_message_differs_from_foreign_pair(self):
        archived = self.db.save_prompt_pair("Cat", "Blur")
        positive = self.db.get_prompt_pair(archived)["positive_prompt_id"]
        self.db.archive_prompt_pair(archived)
        foreign = self.db.save_prompt_pair("Dog", "Blur")
        with self.assertRaisesRegex(ValueError, "not in the requested archive scope"):
            self.db.list_library_images(positive, pair_id=archived)
        self.assertEqual(
            self.db.list_library_images(positive, pair_id=archived, archive="all")[
                "total"
            ],
            0,
        )
        with self.assertRaisesRegex(ValueError, "does not belong to this positive"):
            self.db.list_library_images(positive, pair_id=foreign, archive="all")
