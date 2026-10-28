"""Collections (#331): the database operations."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from tests.open_handles import assert_closed
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed

from database.models import PromptModel
from database.operations import PromptDatabase

try:  # guarded so a missing module fails assertions, not test loading
    from database.image_collections import (
        CollectionFilesMissing,
        CollectionInvalid,
        CollectionMissing,
        CollectionNameTaken,
        READ_SQL,
    )
except ImportError:
    CollectionFilesMissing = CollectionMissing = CollectionNameTaken = None
    CollectionInvalid = ValueError
    READ_SQL = None

ROOT = "/real/output"
OTHER = "/real/other"


class ImageCollectionOperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(assert_closed, self, self.temp.name)
        self.addCleanup(PromptModel.reset_schema_cache)
        self.db = PromptDatabase(str(Path(self.temp.name) / "prompts.db"))
        self.addCleanup(self.db.close_all)

    def require(self):
        self.assertTrue(
            hasattr(self.db, "add_to_collection"),
            "ImageCollectionOperations is not implemented",
        )

    def add(self, items, **target):
        return self.db.add_to_collection(items=items, **target)

    def test_add_creates_a_collection_by_name(self):
        self.require()
        result = self.add(
            [(ROOT, "a.png"), (ROOT, "b.png"), (OTHER, "a.png")], name="LoRa testing"
        )
        collection = result["collection"]
        self.assertEqual(collection["name"], "LoRa testing")
        self.assertEqual(
            result,
            {"collection": collection, "created": True, "added": 3, "already": 0},
        )
        self.assertEqual(self.db.list_collections(), [collection])
        self.assertTrue(self.db.collection_exists(collection["id"]))
        self.assertEqual(self.db.get_collection(collection["id"]), collection)
        self.assertEqual(
            self.db.collection_members(collection["id"], ROOT), {"a.png", "b.png"}
        )
        self.assertEqual(
            self.db.root_collection_members(OTHER), {collection["id"]: {"a.png"}}
        )
        self.assertEqual(self.db.collection_counts(), {collection["id"]: 3})
        self.assertEqual(self.db.file_collections(ROOT, "a.png"), [collection["id"]])
        self.assertEqual(self.db.file_collections(ROOT, "zzz.png"), [])

    def test_add_by_name_ignores_case_and_unicode_form(self):
        self.require()
        first = self.add([(ROOT, "a.png")], name="Café grids")["collection"]
        # NFD spelling (macOS) in another case joins the same collection.
        second = self.add([(ROOT, "b.png")], name="CAFÉ  GRIDS")
        self.assertEqual(second["collection"], first)
        self.assertFalse(second["created"])
        self.assertEqual(len(self.db.list_collections()), 1)
        self.assertEqual(
            self.db.collection_members(first["id"], ROOT), {"a.png", "b.png"}
        )

    def test_add_counts_already_present(self):
        self.require()
        made = self.add([(ROOT, "a.png"), (ROOT, "b.png")], name="Set")
        result = self.add(
            [(ROOT, "b.png"), (ROOT, "c.png"), (ROOT, "c.png")],
            collection_id=made["collection"]["id"],
        )
        self.assertEqual(
            (result["created"], result["added"], result["already"]), (False, 1, 1)
        )
        self.assertEqual(self.db.collection_counts(), {made["collection"]["id"]: 3})

    def test_add_with_a_new_name_and_nothing_inserted_creates_nothing(self):
        self.require()
        with self.assertRaises(CollectionFilesMissing):
            self.add([], name="Empty")
        self.assertEqual(self.db.list_collections(), [])
        # An existing name with nothing to add is not an error.
        made = self.add([(ROOT, "a.png")], name="Kept")["collection"]
        self.assertEqual(
            self.add([], name="kept"),
            {"collection": made, "created": False, "added": 0, "already": 0},
        )

    def test_add_to_a_deleted_collection_raises_missing(self):
        self.require()
        made = self.add([(ROOT, "a.png")], name="Gone")["collection"]
        self.db.delete_collection(made["id"])
        with self.assertRaises(CollectionMissing):
            self.add([(ROOT, "b.png")], collection_id=made["id"])
        with self.assertRaises(CollectionMissing):
            self.db.remove_from_collection(made["id"], [(ROOT, "a.png")])
        with self.assertRaises(CollectionMissing):
            self.db.rename_collection(made["id"], "Back")
        self.assertFalse(self.db.collection_exists(made["id"]))
        self.assertIsNone(self.db.get_collection(made["id"]))

    def test_remove_returns_the_count(self):
        self.require()
        made = self.add([(ROOT, "a.png"), (ROOT, "b.png")], name="Set")["collection"]
        result = self.db.remove_from_collection(
            made["id"], [(ROOT, "a.png"), (ROOT, "a.png"), (ROOT, "never.png")]
        )
        self.assertEqual(result, {"collection": made, "removed": 1})
        self.assertEqual(self.db.collection_members(made["id"], ROOT), {"b.png"})
        # Removing the last member keeps the (now empty) collection.
        self.db.remove_from_collection(made["id"], [(ROOT, "b.png")])
        self.assertEqual(self.db.list_collections(), [made])
        self.assertEqual(self.db.collection_counts(), {made["id"]: 0})

    def test_rename_refuses_a_taken_name(self):
        self.require()
        first = self.add([(ROOT, "a.png")], name="First")["collection"]
        second = self.add([(ROOT, "a.png")], name="Second")["collection"]
        with self.assertRaises(CollectionNameTaken) as caught:
            self.db.rename_collection(second["id"], " first ")
        self.assertEqual(caught.exception.collection_id, first["id"])
        self.assertEqual(self.db.get_collection(second["id"]), second)

    def test_rename_accepts_a_case_variant_of_its_own_name(self):
        self.require()
        made = self.add([(ROOT, "a.png")], name="lora grids")["collection"]
        renamed = self.db.rename_collection(made["id"], "LoRA   grids ")
        self.assertEqual(renamed, {"id": made["id"], "name": "LoRA grids"})
        self.assertEqual(self.db.list_collections(), [renamed])
        other = self.db.rename_collection(made["id"], "Portraits")
        self.assertEqual(other["name"], "Portraits")

    def test_delete_unknown_raises_missing(self):
        self.require()
        made = self.add([(ROOT, "a.png")], name="Doomed")["collection"]
        self.assertTrue(self.db.delete_collection(made["id"]))
        self.assertEqual(self.db.root_collection_members(ROOT), {})
        with self.assertRaises(CollectionMissing):
            self.db.delete_collection(made["id"])
        with self.assertRaises(CollectionMissing):
            self.db.delete_collection(999)

    def test_list_is_in_name_key_order(self):
        self.require()
        for name in ("beta", "Alpha", "été", "Zeta", "alpha two"):
            self.add([(ROOT, "a.png")], name=name)
        self.assertEqual(
            [row["name"] for row in self.db.list_collections()],
            ["Alpha", "alpha two", "beta", "Zeta", "été"],
        )

    def test_names_are_trimmed_and_collapsed(self):
        self.require()
        made = self.add([(ROOT, "a.png")], name="  LoRa \t testing\n ")["collection"]
        self.assertEqual(made["name"], "LoRa testing")
        for bad in ("", "   ", "x" * 101, "bell\x07", "c1\x81", "del\x7f", 5, None):
            with self.subTest(name=bad):
                with self.assertRaises(CollectionInvalid):
                    self.add([(ROOT, "a.png")], name=bad)
        self.assertEqual(len(self.db.list_collections()), 1)

    def test_targets_and_items_are_validated(self):
        self.require()
        made = self.add([(ROOT, "a.png")], name="Set")["collection"]
        for target in (
            {},
            {"name": "x", "collection_id": made["id"]},
            {"collection_id": True},
            {"collection_id": 0},
            {"collection_id": "1"},
        ):
            with self.subTest(target=target):
                with self.assertRaises(CollectionInvalid):
                    self.add([(ROOT, "a.png")], **target)
        for items in ([("root",)], [(ROOT, 5)], "a.png", [(ROOT, "x" * 4097)]):
            with self.subTest(items=items):
                with self.assertRaises(CollectionInvalid):
                    self.add(items, collection_id=made["id"])

    def test_a_failure_after_the_membership_move_rolls_it_back(self):
        self.require()
        made = self.add([(ROOT, "b.png")], name="Grids")["collection"]
        move = {
            "removed": [],
            "kept": [],
            "targets": {},
            "kept_path": "/real/output/a.png",
            "favorites": [(ROOT, "b.png", ROOT, "a.png")],
            "removed_locator": "/real/output/b.png",
            "kept_locator": "/real/output/a.png",
            "sha256": "0" * 64,
            "kept_size": 1,
            "kept_mtime_ns": 1,
        }
        with patch.object(
            type(self.db), "_move_copy_tags", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                self.db.move_copy_records(move)
        self.assertEqual(self.db.file_collections(ROOT, "b.png"), [made["id"]])
        self.assertEqual(self.db.file_collections(ROOT, "a.png"), [])
        # Without the failure the same move succeeds.
        self.assertEqual(self.db.move_copy_records(move)["collections"], 1)
        self.assertEqual(self.db.file_collections(ROOT, "a.png"), [made["id"]])

    def test_count_snapshot_reads_in_one_transaction(self):
        self.require()
        made = self.add([(ROOT, "a.png"), (OTHER, "b.png")], name="Set")["collection"]
        statements = []
        conn = self.db.model.get_connection()
        conn.set_trace_callback(statements.append)
        try:
            members, stored = self.db.collection_count_snapshot([ROOT, OTHER, ROOT])
        finally:
            conn.set_trace_callback(None)
        self.assertEqual(
            members, {ROOT: {made["id"]: {"a.png"}}, OTHER: {made["id"]: {"b.png"}}}
        )
        self.assertEqual(stored, {made["id"]: 2})
        self.assertEqual(statements[0], "BEGIN")
        self.assertEqual(statements[-1], "ROLLBACK")
        self.assertFalse(conn.in_transaction)

    def test_reads_use_an_index(self):
        self.require()
        self.add([(ROOT, "a.png")], name="Set")
        conn = sqlite3.connect(self.db.model.db_path)
        try:
            for sql in READ_SQL:
                plan = " ".join(
                    row[3]
                    for row in conn.execute(
                        "EXPLAIN QUERY PLAN " + sql, [None] * sql.count("?")
                    )
                )
                with self.subTest(sql=sql):
                    self.assertNotIn("SCAN image_collection_items", plan)
                    if "?" in sql:  # a lookup, not the whole list
                        self.assertNotIn("SCAN image_collections", plan)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
