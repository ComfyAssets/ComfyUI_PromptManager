"""Bounded vocabulary reads count exactly what Library Browse shows."""

import tempfile
import unittest
from pathlib import Path

from database.operations import PromptDatabase
from utils.hashing import generate_prompt_hash


class TestVocabularyReads(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = PromptDatabase(str(Path(self.temp.name) / "vocabulary.db"))

    def tearDown(self):
        try:
            # Windows refuses to remove a database that still has open handles.
            self.db.close_all()
        finally:
            self.temp.cleanup()

    def save(self, text, **metadata):
        return self.db.save_prompt(
            text, prompt_hash=generate_prompt_hash(text), **metadata
        )

    def read(self, name, **options):
        method = getattr(self.db, name, None)
        self.assertTrue(callable(method), f"PromptDatabase.{name} is missing")
        return method(**options)

    def add_unused(self, *names):
        with self.db.model.get_connection() as conn:
            for name in names:
                conn.execute("INSERT INTO tags(name) VALUES(?)", (name,))

    def all_tags(self):
        rows, after = [], None
        while True:
            page = self.read("list_vocabulary_tags", limit=200, after=after)
            rows.extend(page["tags"])
            if not page["has_more"]:
                return rows
            after = page["next_key"]

    def test_tag_rows_are_registry_rows_in_creation_order_with_unused_at_zero(self):
        self.save("First", tags=["c"])
        self.save("Second", tags=["a"])
        self.save("Third", tags=["b", "a"])
        self.add_unused("z")
        page = self.read("list_vocabulary_tags")
        self.assertEqual([row["name"] for row in page["tags"]], ["c", "a", "b", "z"])
        self.assertEqual(page["total"], 4)
        self.assertFalse(page["has_more"])
        self.assertIsNone(page["next_key"])
        by_name = {row["name"]: row for row in page["tags"]}
        self.assertEqual(by_name["z"]["prompt_count"], 0)
        self.assertEqual(by_name["a"]["prompt_count"], 2)
        self.assertEqual(set(by_name["c"]), {"id", "name", "prompt_count"})

    def test_prompt_count_equals_library_browse_total_for_every_tag(self):
        self.save("Parent only", tags=["parent-only"])
        self.db.save_prompt_pair("Pair cat", "Pair noise", tags=["pair-only"])
        archived = self.db.save_prompt_pair(
            "Archived cat", "Archived noise", tags=["archived-only"]
        )
        self.db.archive_prompt_pair(archived)
        self.save("Legacy blur", tags=["negative-only"])
        self.db.save_prompt_pair("Negative owner", "Legacy blur")
        self.save("Dual dog", tags=["dual"])
        self.db.save_prompt_pair("Dual dog", "Dual noise", tags=["dual"])
        retained = self.save("Retained negative", tags=["retained"])
        self.db.save_prompt_pair("Retained owner", "Retained negative")
        with self.db.model.get_connection() as conn:
            conn.execute(
                "INSERT INTO prompt_library_state(prompt_id,title) VALUES(?,?)",
                (retained, "Kept"),
            )
        self.add_unused("unused")
        rows = self.all_tags()
        self.assertEqual(len(rows), 7)
        for row in rows:
            self.assertEqual(
                row["prompt_count"],
                self.db.list_library(tags=[row["name"]])["total"],
                row["name"],
            )
        counts = {row["name"]: row["prompt_count"] for row in rows}
        self.assertEqual(
            counts,
            {
                "parent-only": 1,
                "pair-only": 1,
                "archived-only": 0,
                "negative-only": 0,
                "dual": 1,
                "retained": 1,
                "unused": 0,
            },
        )

    def test_category_rows_match_library_options_and_browse_totals(self):
        self.save("Zeta first", category="Zeta")
        self.db.save_prompt_pair("Alpha owner", "Alpha noise", category="Alpha")
        self.save("Padded", category=" Landscape ")
        self.save("Plain", category="Landscape")
        self.save("Blank", category="   ")
        self.save("Null")
        self.save("Zeta again", category="Zeta")
        archived = self.db.save_prompt_pair(
            "Archived owner", "Archived noise", category="Archived-only"
        )
        self.db.archive_prompt_pair(archived)
        self.save("Legacy negative", category="Negative-only")
        self.db.save_prompt_pair("Negative owner", "Legacy negative")
        page = self.read("list_vocabulary_categories")
        values = [row["value"] for row in page["categories"]]
        self.assertEqual(values, ["Zeta", "Alpha", " Landscape ", "Landscape"])
        self.assertEqual(set(values), set(self.db.get_library_options()["categories"]))
        self.assertEqual(page["total"], 4)
        self.assertFalse(page["has_more"])
        for row in page["categories"]:
            self.assertEqual(set(row), {"value", "prompt_count"})
            self.assertEqual(
                row["prompt_count"],
                self.db.list_library(category=row["value"])["total"],
                row["value"],
            )
        self.assertEqual(page["categories"][0]["prompt_count"], 2)

    def test_category_pages_continue_by_first_use_then_value(self):
        for index in range(5):
            self.save(f"Owner {index}", category=f"C{4 - index}")
        first = self.read("list_vocabulary_categories", limit=2)
        second = self.read(
            "list_vocabulary_categories", limit=2, after=first["next_key"]
        )
        third = self.read(
            "list_vocabulary_categories", limit=2, after=second["next_key"]
        )
        values = [
            row["value"]
            for page in (first, second, third)
            for row in page["categories"]
        ]
        self.assertEqual(values, ["C4", "C3", "C2", "C1", "C0"])
        self.assertEqual(
            [page["has_more"] for page in (first, second, third)], [True, True, False]
        )
        for after in ([1], ["1", "C4"], {"id": 1}, [True, "C4"]):
            with self.assertRaises(ValueError):
                self.read("list_vocabulary_categories", after=after)

    def test_pages_are_bounded_and_continue_without_gaps(self):
        self.add_unused(*[f"tag-{index:03d}" for index in range(250)])
        pages, after = [], None
        for _ in range(3):
            page = self.read("list_vocabulary_tags", limit=100, after=after)
            pages.append(page)
            after = page["next_key"]
        self.assertEqual([len(page["tags"]) for page in pages], [100, 100, 50])
        self.assertEqual([page["has_more"] for page in pages], [True, True, False])
        self.assertEqual({page["total"] for page in pages}, {250})
        names = [row["name"] for page in pages for row in page["tags"]]
        self.assertEqual(names, [f"tag-{index:03d}" for index in range(250)])
        for options in ({"limit": 0}, {"limit": 201}, {"after": "1"}, {"after": 0}):
            with self.assertRaises(ValueError):
                self.read("list_vocabulary_tags", **options)

    def test_reads_share_one_snapshot_and_report_epoch(self):
        self.save("Cat", category="Animal", tags=["cat"])
        epoch = self.db.get_execution_epoch()
        tags = self.read("list_vocabulary_tags")
        categories = self.read("list_vocabulary_categories")
        self.assertEqual(tags["library_epoch"], epoch)
        self.assertEqual(categories["library_epoch"], epoch)
        helper = getattr(self.db, "_vocabulary_tags", None)
        self.assertTrue(callable(helper), "PromptDatabase._vocabulary_tags is missing")
        with self.db.library_snapshot() as (conn, snapshot_epoch, _ceiling):
            inner = helper(conn, snapshot_epoch, limit=10, after=None)
            self.assertEqual(inner["library_epoch"], epoch)
            self.assertTrue(conn.in_transaction)


if __name__ == "__main__":
    unittest.main()
