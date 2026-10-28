"""Storing a file's prompt from its text: one transaction, one prompt each (#247)."""

import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

try:
    from tests.open_handles import assert_closed
    from tests import test_execution_history_database as history_tests
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed
    import test_execution_history_database as history_tests

from database.operations import PromptDatabase
from database.pair_ingestion import (
    RUN_MARKS,
    SCAN_MARKS,
    Marks,
    ingest_resolved_pair,
)
from database.scanned_prompts import store_scanned_prompt, store_scanned_prompt_or_raise
from utils.hashing import generate_prompt_hash

META = {"prompt": {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "x"}}}}


def resolved(positive="cat", negative="blur"):
    return {
        "state": "resolved",
        "positive": {"text": positive, "provenance": {"source": "api"}},
        "negative": {"text": negative, "provenance": {"source": "api"}},
    }


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = PromptDatabase(str(Path(self.temp.name) / "test.db"))
        self.path = str(Path(self.temp.name) / "gen.png")

    def tearDown(self):
        self.db.close_all()
        assert_closed(self, self.temp.name)
        self.temp.cleanup()

    def prompts(self):
        rows = self.db.model.get_connection().execute(
            "SELECT id, text FROM prompts ORDER BY id"
        )
        return [tuple(row) for row in rows]

    def links(self):
        rows = self.db.model.get_connection().execute(
            "SELECT prompt_id, image_path FROM generated_images ORDER BY id"
        )
        return [tuple(row) for row in rows]

    def save(self, text, **fields):
        return self.db.save_prompt(
            text, prompt_hash=generate_prompt_hash(text), **fields
        )


class TestStoreScannedPrompt(StoreCase):
    def test_a_new_text_is_created_with_the_scan_marks_and_linked(self):
        status, prompt_id = store_scanned_prompt_or_raise(
            self.db, self.path, META, " a cat ", SCAN_MARKS
        )
        self.assertEqual(status, "added")
        prompt = self.db.get_prompt_by_id(prompt_id)
        self.assertEqual(prompt["text"], "a cat")
        self.assertEqual(prompt["category"], "scanned")
        self.assertEqual(prompt["tags"], ["auto-scanned"])
        self.assertEqual(prompt["notes"], "Auto-scanned from gen.png")
        self.assertEqual(prompt["run_count"], 0)
        self.assertEqual(self.links(), [(prompt_id, self.path)])

    def test_a_run_created_prompt_names_the_workflow_run(self):
        _status, prompt_id = store_scanned_prompt_or_raise(
            self.db, self.path, META, "a cat", RUN_MARKS
        )
        prompt = self.db.get_prompt_by_id(prompt_id)
        self.assertEqual(prompt["notes"], "Saved from a workflow run: gen.png")
        self.assertEqual(prompt["tags"], ["auto-scanned"])

    def test_a_stored_text_is_reused_by_hash_and_keeps_its_fields(self):
        existing = self.save("A Cat", category="mine", notes="kept")
        status, prompt_id = store_scanned_prompt_or_raise(
            self.db, self.path, META, "a cat ", SCAN_MARKS
        )
        self.assertEqual((status, prompt_id), ("linked", existing))
        prompt = self.db.get_prompt_by_id(existing)
        self.assertEqual((prompt["category"], prompt["notes"]), ("mine", "kept"))
        self.assertEqual(prompt["tags"], [])
        self.assertEqual(len(self.prompts()), 1)

    def test_a_repeat_is_unchanged(self):
        first = store_scanned_prompt_or_raise(
            self.db, self.path, META, "a cat", SCAN_MARKS
        )
        again = store_scanned_prompt_or_raise(
            self.db, self.path, META, "a cat", SCAN_MARKS
        )
        self.assertEqual(again, ("unchanged", first[1]))
        self.assertEqual(len(self.links()), 1)

    def test_a_file_linked_to_another_prompt_is_a_conflict(self):
        other = self.save("a dog")
        # Linked under another spelling of the same file.
        spelled = os.path.join(self.temp.name, ".", "gen.png")
        self.db.link_image_to_prompt(other, spelled)
        result = store_scanned_prompt_or_raise(
            self.db, self.path, META, "a cat", SCAN_MARKS
        )
        self.assertEqual(result, ("conflict", None))
        self.assertEqual(self.prompts(), [(other, "a dog")])
        self.assertEqual(self.links(), [(other, spelled)])

    def test_a_failed_link_raises_and_leaves_no_prompt_behind(self):
        with mock.patch.object(
            self.db, "_insert_image_link", side_effect=sqlite3.OperationalError("x")
        ):
            with self.assertRaises(sqlite3.OperationalError):
                store_scanned_prompt_or_raise(
                    self.db, self.path, META, "a cat", SCAN_MARKS
                )
            with self.assertLogs("prompt_manager", "ERROR"):
                result = store_scanned_prompt(
                    self.db, self.path, META, "a cat", SCAN_MARKS
                )
        self.assertEqual(result, ("found", None))
        self.assertEqual(self.prompts(), [])
        self.assertEqual(self.links(), [])

    def test_two_concurrent_writers_store_one_prompt_and_one_link(self):
        hold = history_tests.FirstWriterHold()
        outcome = self.db._pair_component_outcome
        started = threading.Barrier(2)

        def held_open(*args, **kwargs):
            hold()
            return outcome(*args, **kwargs)

        def write(_index):
            started.wait(timeout=30)
            try:
                return store_scanned_prompt_or_raise(
                    self.db, self.path, META, "a cat", SCAN_MARKS
                )[0]
            finally:
                self.db.model.close()

        with (
            mock.patch.object(
                self.db, "_pair_component_outcome", side_effect=held_open
            ),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            statuses = sorted(pool.map(write, range(2)))
        self.assertEqual(statuses, ["added", "unchanged"])
        self.assertEqual(len(self.prompts()), 1)
        self.assertEqual(len(self.links()), 1)


class TestPairMarks(StoreCase):
    def test_marks_reach_only_created_components(self):
        kept = self.save("cat", category="mine")
        result = ingest_resolved_pair(
            self.db, self.path, META, resolved(), marks=RUN_MARKS
        )
        self.assertEqual(result["status"], "added")
        positive = self.db.get_prompt_by_id(kept)
        self.assertEqual((positive["category"], positive["tags"]), ("mine", []))
        negative_id = next(pid for pid, text in self.prompts() if text == "blur")
        negative = self.db.get_prompt_by_id(negative_id)
        self.assertEqual(negative["category"], "scanned")
        self.assertEqual(negative["tags"], ["auto-scanned"])
        self.assertEqual(negative["notes"], "Saved from a workflow run: gen.png")

    def test_without_marks_created_components_stay_bare(self):
        """Regression guard (passes on the base): the node path is unchanged."""
        ingest_resolved_pair(self.db, self.path, META, resolved())
        for prompt_id, _text in self.prompts():
            prompt = self.db.get_prompt_by_id(prompt_id)
            self.assertIsNone(prompt["category"])
            self.assertEqual(prompt["tags"], [])

    def test_a_blank_negative_creates_only_the_positive(self):
        result = ingest_resolved_pair(
            self.db, self.path, META, resolved(negative=""), marks=SCAN_MARKS
        )
        self.assertEqual(result["status"], "added")
        self.assertEqual([text for _id, text in self.prompts()], ["cat"])

    def test_marks_are_a_frozen_value(self):
        self.assertEqual(
            SCAN_MARKS, Marks("scanned", ("auto-scanned",), SCAN_MARKS.note)
        )
        with self.assertRaises(AttributeError):
            SCAN_MARKS.category = "x"


class TestPublicRunAttribution(unittest.TestCase):
    def test_recorded_runs_and_named_prompt_are_public(self):
        from database import run_attribution

        self.assertTrue(callable(run_attribution.recorded_runs))
        self.assertTrue(callable(run_attribution.named_prompt))
        self.assertFalse(hasattr(run_attribution, "_recorded_runs"))


if __name__ == "__main__":
    unittest.main()
