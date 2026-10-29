"""#442: the workspace Library's fenced bulk "Add tags" (DB command and route)."""

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

URL = "/prompt_manager/bulk/tags"


def save(db, text, **metadata):
    return db.save_prompt(text, prompt_hash=generate_prompt_hash(text), **metadata)


def tags_of(db, prompt_id):
    with db.model.get_connection() as conn:
        rows = conn.execute(
            "SELECT t.name FROM prompt_tags pt JOIN tags t ON t.id=pt.tag_id "
            "WHERE pt.prompt_id=? ORDER BY t.name",
            (prompt_id,),
        ).fetchall()
    return [row[0] for row in rows]


def updated_at(db, prompt_id):
    with db.model.get_connection() as conn:
        return conn.execute(
            "SELECT updated_at FROM prompts WHERE id=?", (prompt_id,)
        ).fetchone()[0]


def tag_names(db):
    with db.model.get_connection() as conn:
        return sorted(row[0] for row in conn.execute("SELECT name FROM tags"))


class TestBulkLibraryTags(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = PromptDatabase(str(Path(self.temp.name) / "tags.db"))
        self.epoch = self.db.get_library_options()["library_epoch"]

    def tearDown(self):
        self.db.close_all()
        self.temp.cleanup()

    def run_bulk(self, ids, tags, epoch=None):
        return self.db.bulk_add_library_tags(
            ids, tags, expected_epoch=epoch or self.epoch
        )

    def test_adds_only_missing_tags_and_counts_each_outcome(self):
        a = save(self.db, "Alpha", tags=["red"])
        b = save(self.db, "Beta", tags=["red", "blue"])
        c = save(self.db, "Gamma")
        before_b = updated_at(self.db, b)
        result = self.run_bulk([a, b, c, 99999], [" red ", "blue", "Blue"])
        self.assertEqual(
            result,
            {
                "library_epoch": self.epoch,
                "tags": ["red", "blue"],
                "updated": 2,
                "unchanged": 1,
                "not_found": 1,
                "over_limit": 0,
            },
        )
        self.assertEqual(tags_of(self.db, a), ["blue", "red"])
        self.assertEqual(tags_of(self.db, c), ["blue", "red"])
        self.assertEqual(updated_at(self.db, b), before_b)
        self.assertTrue(updated_at(self.db, a).endswith("+00:00"))

    def test_a_case_variant_of_a_held_tag_is_not_added_and_names_stay_exact(self):
        a = save(self.db, "Alpha", tags=["Portrait"])
        b = save(self.db, "Beta")
        result = self.run_bulk([a, b], ["portrait"])
        # Alpha already holds it (casefold); Beta gets the name exactly as typed.
        self.assertEqual((result["updated"], result["unchanged"]), (1, 1))
        self.assertEqual(tags_of(self.db, a), ["Portrait"])
        self.assertEqual(tags_of(self.db, b), ["portrait"])
        self.assertEqual(tag_names(self.db), ["Portrait", "portrait"])
        c = save(self.db, "Gamma", tags=["STRASSE"])
        self.assertEqual(self.run_bulk([c], ["straße"])["unchanged"], 1)

    def test_names_nothing_receives_are_never_inserted(self):
        full = save(self.db, "Full", tags=[f"t{i}" for i in range(20)])
        self.run_bulk([full, 99999], ["unused"])
        self.assertNotIn("unused", tag_names(self.db))

    def test_a_prompt_already_over_twenty_that_gains_nothing_is_unchanged(self):
        over = save(self.db, "Over", tags=[f"t{i}" for i in range(22)])
        result = self.run_bulk([over], ["t1"])
        self.assertEqual((result["unchanged"], result["over_limit"]), (1, 0))

    def test_image_and_pair_tags_are_untouched(self):
        pair = self.db.save_prompt_pair("Cat", "Blur", tags=["pairtag"])
        positive = self.db.get_prompt_pair(pair)["positive_prompt_id"]
        with self.db.model.get_connection() as conn:
            before = conn.execute("SELECT COUNT(*) FROM prompt_pair_tags").fetchone()[0]
        self.run_bulk([positive], ["pairtag", "own"])
        self.assertEqual(tags_of(self.db, positive), ["own", "pairtag"])
        with self.db.model.get_connection() as conn:
            after = conn.execute("SELECT COUNT(*) FROM prompt_pair_tags").fetchone()[0]
        self.assertEqual(after, before)

    def test_a_prompt_that_would_pass_twenty_tags_is_skipped_untouched(self):
        full = save(self.db, "Full", tags=[f"t{i}" for i in range(19)])
        other = save(self.db, "Other")
        before = updated_at(self.db, full)
        result = self.run_bulk([full, other], ["x", "y"])
        self.assertEqual((result["updated"], result["over_limit"]), (1, 1))
        self.assertEqual(len(tags_of(self.db, full)), 19)
        self.assertEqual(updated_at(self.db, full), before)
        self.assertEqual(tags_of(self.db, other), ["x", "y"])
        # Exactly twenty is allowed.
        self.assertEqual(self.run_bulk([full], ["x"])["updated"], 1)

    def test_changed_prompts_become_registered_library_parents(self):
        a = save(self.db, "Alpha")
        self.run_bulk([a], ["new"])
        with self.db.model.get_connection() as conn:
            self.assertIsNotNone(
                conn.execute(
                    "SELECT 1 FROM prompt_library_state WHERE prompt_id=?", (a,)
                ).fetchone()
            )

    def test_negative_only_prompts_are_not_found(self):
        pair = self.db.save_prompt_pair("Cat", "Blur")
        negative = self.db.get_prompt_pair(pair)["negative_prompt_id"]
        result = self.run_bulk([negative], ["x"])
        self.assertEqual((result["updated"], result["not_found"]), (0, 1))
        self.assertEqual(tags_of(self.db, negative), [])

    def test_restored_storage_writes_nothing_not_even_a_tag_name(self):
        a = save(self.db, "Alpha")
        rotate_history_epoch(self.db.model.get_connection())
        with self.assertRaises(LibraryCommandError) as caught:
            self.run_bulk([a], ["fresh"])
        self.assertEqual(caught.exception.code, "storage-changed")
        self.assertEqual(tags_of(self.db, a), [])
        self.assertNotIn("fresh", tag_names(self.db))

    def test_a_failure_mid_write_leaves_every_prompt_as_it_was(self):
        ids = [save(self.db, f"P{i}") for i in range(3)]
        with self.db.model.get_connection() as conn:
            conn.execute(
                "CREATE TRIGGER fail_third BEFORE INSERT ON prompt_tags "
                f"WHEN NEW.prompt_id={ids[2]} BEGIN SELECT RAISE(ABORT, 'boom'); END"
            )
        with self.assertRaises(sqlite3.DatabaseError):
            self.run_bulk(ids, ["x"])
        self.assertEqual([tags_of(self.db, pid) for pid in ids], [[], [], []])
        self.assertNotIn("x", tag_names(self.db))

    def test_entries_are_counted_after_dedupe_and_the_limit_names_prompts(self):
        a = save(self.db, "Alpha")
        self.assertEqual(self.run_bulk([a], ["dup"] * 30 + ["Dup"])["updated"], 1)
        with self.assertRaises(ValueError) as caught:
            self.run_bulk([a], [f"t{i}" for i in range(101)])
        self.assertEqual(str(caught.exception), "At most 20 tags can be added at once")

    def test_many_ids_are_read_in_chunks(self):
        ids = [save(self.db, f"Many {i}") for i in range(1000)]
        self.assertEqual(self.run_bulk(ids, ["bulk"])["updated"], 1000)

    def test_rejects_invalid_arguments(self):
        a = save(self.db, "Alpha")
        for ids, tags, epoch in (
            ([a], [], self.epoch),
            ([a], ["  "], self.epoch),
            ([a], "red", self.epoch),
            ([a], ["x" * 51], self.epoch),
            ([a], ["bad\x01"], self.epoch),
            ([a], ["a,b"], self.epoch),
            ([a], [f"t{i}" for i in range(21)], self.epoch),
            ([a], ["ok"], "not-a-uuid"),
            ([], ["ok"], self.epoch),
        ):
            with self.subTest(tags=tags, epoch=epoch, ids=ids):
                with self.assertRaises(ValueError):
                    self.run_bulk(ids, tags, epoch)


class TestBulkLibraryTagsRoute(RouteCoverageCase):
    def epoch(self):
        return self.api.db.get_library_options()["library_epoch"]

    async def test_fenced_form_adds_tags_and_reports_counts(self):
        ids = [self._save_prompt(f"route {i}") for i in range(2)]
        status, body = await self._json(
            "POST",
            URL,
            json={"prompt_ids": ids, "tags": ["Fresh"], "expected_epoch": self.epoch()},
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            body,
            {
                "success": True,
                "library_epoch": self.epoch(),
                "tags": ["Fresh"],
                "updated": 2,
                "unchanged": 0,
                "not_found": 0,
                "over_limit": 0,
            },
        )

    async def test_restored_storage_answers_409_storage_changed(self):
        pid = self._save_prompt("route restore")
        stale = self.epoch()
        new_epoch = rotate_history_epoch(self.api.db.model.get_connection())
        status, body = await self._json(
            "POST",
            URL,
            json={"prompt_ids": [pid], "tags": ["x"], "expected_epoch": stale},
        )
        self.assertEqual((status, body["code"]), (409, "storage-changed"))
        self.assertEqual(body["library_epoch"], new_epoch)

    async def test_fenced_form_validates_its_body(self):
        pid = self._save_prompt("route validate")
        epoch = self.epoch()
        for body in (
            {"prompt_ids": [pid], "tags": [], "expected_epoch": epoch},
            {"prompt_ids": [pid], "tags": "x", "expected_epoch": epoch},
            {"prompt_ids": [pid], "tags": ["x" * 51], "expected_epoch": epoch},
            {"prompt_ids": [pid], "tags": ["ok"], "expected_epoch": "nope"},
            {"prompt_ids": [True], "tags": ["ok"], "expected_epoch": epoch},
            {
                "prompt_ids": list(range(1, 1002)),
                "tags": ["ok"],
                "expected_epoch": epoch,
            },
            {"prompt_ids": [pid], "expected_epoch": epoch},
            {"prompt_ids": [pid], "tags": ["ok"], "expected_epoch": epoch, "x": 1},
        ):
            with self.subTest(body=body):
                status, answer = await self._json("POST", URL, json=body)
                self.assertEqual(status, 400)
                self.assertFalse(answer["success"])
        self.assertEqual(tags_of(self.api.db, pid), [])

    async def test_fenced_form_reports_an_unexpected_failure(self):
        pid = self._save_prompt("route failure")
        self._break("bulk_add_library_tags")
        status, body = await self._json(
            "POST",
            URL,
            json={"prompt_ids": [pid], "tags": ["ok"], "expected_epoch": self.epoch()},
        )
        self.assertEqual(status, 500)
        self.assertFalse(body["success"])

    async def test_legacy_form_refuses_a_body_that_is_not_an_object(self):
        for raw in ("[1, 2]", "not json"):
            with self.subTest(raw=raw):
                status, body = await self._json(
                    "POST", URL, data=raw, headers={"Content-Type": "application/json"}
                )
                self.assertEqual(status, 400)
                self.assertFalse(body["success"])

    async def test_legacy_form_is_unchanged(self):
        pid = self._save_prompt("route legacy")
        status, body = await self._json(
            "POST", URL, json={"prompt_ids": [pid], "tags": ["Legacy"]}
        )
        self.assertEqual((status, body["updated_count"]), (200, 1))
        self.assertNotIn("library_epoch", body)


if __name__ == "__main__":
    unittest.main()
