"""#437: the workspace Library's fenced bulk "Set category" (DB command and route)."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from database.execution_history_schema import rotate_history_epoch
from database.library_commands import LibraryCommandError
from database.operations import PromptDatabase
from utils.hashing import generate_prompt_hash

try:
    from test_api_prompts import RouteCoverageCase
except ImportError:
    from tests.test_api_prompts import RouteCoverageCase

URL = "/prompt_manager/bulk/category"


def save(db, text, **metadata):
    return db.save_prompt(text, prompt_hash=generate_prompt_hash(text), **metadata)


def row(db, prompt_id):
    with db.model.get_connection() as conn:
        found = conn.execute(
            "SELECT category, updated_at FROM prompts WHERE id=?", (prompt_id,)
        ).fetchone()
    return tuple(found) if found else None


class TestBulkLibraryCategory(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = PromptDatabase(str(Path(self.temp.name) / "bulk.db"))
        self.epoch = self.db.get_library_options()["library_epoch"]

    def tearDown(self):
        self.db.close_all()
        self.temp.cleanup()

    def run_bulk(self, ids, category, epoch=None):
        return self.db.bulk_set_library_category(
            ids, category, expected_epoch=epoch or self.epoch
        )

    def test_sets_changed_rows_and_counts_unchanged_and_missing(self):
        a = save(self.db, "Alpha", category="Old")
        b = save(self.db, "Beta", category="New")
        c = save(self.db, "Gamma")
        before_b = row(self.db, b)
        result = self.run_bulk([a, b, c, 99999], "  New ")
        self.assertEqual(
            result,
            {
                "library_epoch": self.epoch,
                "category": "New",
                "updated": 2,
                "unchanged": 1,
                "not_found": 1,
            },
        )
        self.assertEqual(row(self.db, a)[0], "New")
        self.assertEqual(row(self.db, c)[0], "New")
        # An unchanged row keeps its timestamp (and so its edit token).
        self.assertEqual(row(self.db, b), before_b)

    def test_changed_category_rotates_the_edit_token_only_for_changed_rows(self):
        a = save(self.db, "Alpha", category="Old")
        b = save(self.db, "Beta", category="New")
        tokens = {
            pid: self.db.get_library_positive(pid)["positive"]["edit_token"]
            for pid in (a, b)
        }
        self.run_bulk([a, b], "New")
        self.assertNotEqual(
            self.db.get_library_positive(a)["positive"]["edit_token"], tokens[a]
        )
        self.assertEqual(
            self.db.get_library_positive(b)["positive"]["edit_token"], tokens[b]
        )

    def test_changed_rows_become_registered_library_parents_like_the_editor(self):
        a = save(self.db, "Alpha", category="Old")
        b = save(self.db, "Beta", category="Same")

        def registered(pid):
            with self.db.model.get_connection() as conn:
                return bool(
                    conn.execute(
                        "SELECT 1 FROM prompt_library_state WHERE prompt_id=?", (pid,)
                    ).fetchone()
                )

        self.assertEqual((registered(a), registered(b)), (False, False))
        self.run_bulk([a, b], "Same")
        self.assertEqual((registered(a), registered(b)), (True, False))

    def test_updated_at_is_utc_iso_text(self):
        a = save(self.db, "Alpha")
        self.run_bulk([a], "Utc")
        self.assertTrue(row(self.db, a)[1].endswith("+00:00"))

    def test_negative_only_prompts_are_not_library_parents(self):
        pair = self.db.save_prompt_pair("Cat", "Blur")
        negative = self.db.get_prompt_pair(pair)["negative_prompt_id"]
        positive = self.db.get_prompt_pair(pair)["positive_prompt_id"]
        result = self.run_bulk([positive, negative], "Animals")
        self.assertEqual((result["updated"], result["not_found"]), (1, 1))
        self.assertIsNone(row(self.db, negative)[0])

    def test_restored_storage_writes_nothing(self):
        a = save(self.db, "Alpha", category="Old")
        new_epoch = rotate_history_epoch(self.db.model.get_connection())
        with self.assertRaises(LibraryCommandError) as caught:
            self.run_bulk([a], "New")
        self.assertEqual(caught.exception.code, "storage-changed")
        self.assertEqual(caught.exception.epoch, new_epoch)
        self.assertEqual(row(self.db, a)[0], "Old")

    def test_a_failure_mid_write_leaves_every_row_as_it_was(self):
        ids = [save(self.db, f"P{i}", category="Old") for i in range(3)]
        with self.db.model.get_connection() as conn:
            conn.execute(
                "CREATE TRIGGER fail_third BEFORE UPDATE OF category ON prompts "
                f"WHEN OLD.id={ids[2]} BEGIN SELECT RAISE(ABORT, 'boom'); END"
            )
        with self.assertRaises(sqlite3.DatabaseError):
            self.run_bulk(ids, "New")
        self.assertEqual([row(self.db, pid)[0] for pid in ids], ["Old"] * 3)

    def test_many_ids_are_read_in_chunks(self):
        ids = [save(self.db, f"Many {i}") for i in range(1000)]
        result = self.run_bulk(ids, "Bulk")
        self.assertEqual(result["updated"], 1000)

    def test_rejects_invalid_arguments(self):
        a = save(self.db, "Alpha")
        for ids, category, epoch in (
            ([a], "", self.epoch),
            ([a], "   ", self.epoch),
            ([a], None, self.epoch),
            ([a], "x" * 101, self.epoch),
            ([a], "bad\x01", self.epoch),
            ([a], "ok", "not-a-uuid"),
            ([], "ok", self.epoch),
        ):
            with self.subTest(category=category, epoch=epoch, ids=ids):
                with self.assertRaises(ValueError):
                    self.run_bulk(ids, category, epoch)


class TestBulkLibraryCategoryRoute(RouteCoverageCase):
    def epoch(self):
        return self.api.db.get_library_options()["library_epoch"]

    async def test_fenced_form_sets_categories_and_reports_counts(self):
        ids = [self._save_prompt(f"route {i}", category="Old") for i in range(2)]
        status, body = await self._json(
            "POST",
            URL,
            json={
                "prompt_ids": ids,
                "category": "Fresh",
                "expected_epoch": self.epoch(),
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            body,
            {
                "success": True,
                "library_epoch": self.epoch(),
                "category": "Fresh",
                "updated": 2,
                "unchanged": 0,
                "not_found": 0,
            },
        )
        self.assertEqual(self.api.db.get_prompt_by_id(ids[1])["category"], "Fresh")

    async def test_restored_storage_answers_409_storage_changed(self):
        pid = self._save_prompt("route restore", category="Old")
        stale = self.epoch()
        new_epoch = rotate_history_epoch(self.api.db.model.get_connection())
        status, body = await self._json(
            "POST",
            URL,
            json={"prompt_ids": [pid], "category": "Fresh", "expected_epoch": stale},
        )
        self.assertEqual((status, body["code"]), (409, "storage-changed"))
        self.assertFalse(body["success"])
        self.assertEqual(body["library_epoch"], new_epoch)
        self.assertIsInstance(body["error"], str)
        self.assertEqual(self.api.db.get_prompt_by_id(pid)["category"], "Old")

    async def test_fenced_form_validates_its_body(self):
        pid = self._save_prompt("route validate")
        epoch = self.epoch()
        for body in (
            {"prompt_ids": [pid], "category": "", "expected_epoch": epoch},
            {"prompt_ids": [pid], "category": "  ", "expected_epoch": epoch},
            {"prompt_ids": [pid], "category": 5, "expected_epoch": epoch},
            {"prompt_ids": [pid], "category": "x" * 101, "expected_epoch": epoch},
            {"prompt_ids": [pid], "category": "ok", "expected_epoch": "nope"},
            {"prompt_ids": [pid], "category": "ok", "expected_epoch": 7},
            {"prompt_ids": [], "category": "ok", "expected_epoch": epoch},
            {"prompt_ids": [True], "category": "ok", "expected_epoch": epoch},
            {
                "prompt_ids": list(range(1, 1002)),
                "category": "ok",
                "expected_epoch": epoch,
            },
            {"prompt_ids": [pid], "expected_epoch": epoch},
            {
                "prompt_ids": [pid],
                "category": "ok",
                "expected_epoch": epoch,
                "extra": 1,
            },
        ):
            with self.subTest(body=body):
                status, answer = await self._json("POST", URL, json=body)
                self.assertEqual(status, 400)
                self.assertFalse(answer["success"])
        self.assertIsNone(self.api.db.get_prompt_by_id(pid)["category"])

    async def test_fenced_form_reports_an_unexpected_failure(self):
        pid = self._save_prompt("route failure")
        self._break("bulk_set_library_category")
        status, body = await self._json(
            "POST",
            URL,
            json={
                "prompt_ids": [pid],
                "category": "ok",
                "expected_epoch": self.epoch(),
            },
        )
        self.assertEqual(status, 500)
        self.assertFalse(body["success"])

    async def test_legacy_form_is_unchanged(self):
        pid = self._save_prompt("route legacy", category="Old")
        status, body = await self._json(
            "POST", URL, json={"prompt_ids": [pid], "category": "Legacy"}
        )
        self.assertEqual((status, body["updated_count"]), (200, 1))
        self.assertNotIn("library_epoch", body)
        self.assertEqual(self.api.db.get_prompt_by_id(pid)["category"], "Legacy")


if __name__ == "__main__":
    unittest.main()
