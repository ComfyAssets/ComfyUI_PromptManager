"""Atomic, repeatable metadata enrichment without changing usage counts."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database.operations import PromptDatabase
from database.pair_ingestion import ingest_resolved_pair
from utils.hashing import generate_prompt_hash


def resolved(positive="cat", negative="blur"):
    return {
        "state": "resolved",
        "positive": {"text": positive, "provenance": {"source": "api"}},
        "negative": {"text": negative, "provenance": {"source": "api"}},
    }


class TestPairIngestion(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = PromptDatabase(str(Path(self.temp.name) / "test.db"))
        self.path = str(Path(self.temp.name) / "image.png")

    def tearDown(self):
        self.db.close_all()
        self.temp.cleanup()

    def test_first_scan_repeat_and_negative_variants_reuse_components(self):
        first = ingest_resolved_pair(self.db, self.path, {}, resolved())
        self.assertEqual(first["status"], "added")
        self.assertEqual(first["pairs_added"], 1)
        second = ingest_resolved_pair(self.db, self.path, {}, resolved())
        self.assertEqual(second["status"], "unchanged")
        self.assertEqual(second["pairs_added"], 0)
        pairs = self.db.list_prompt_pairs()["pairs"]
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["image_count"], 1)
        self.assertEqual(pairs[0]["run_count"], 0)
        self.assertEqual(
            self.db.get_prompt_by_hash(generate_prompt_hash("cat"))["run_count"], 0
        )
        ingest_resolved_pair(self.db, self.path + "2", {}, resolved(negative="noise"))
        self.assertEqual(
            self.db.model.get_connection()
            .execute("SELECT count(*) FROM prompts")
            .fetchone()[0],
            3,
        )

    def test_existing_image_enriched_in_place_and_metadata_preserved(self):
        pid = self.db.save_prompt(
            "cat", category="Personal", prompt_hash=generate_prompt_hash("cat")
        )
        image_id = self.db.link_image_to_prompt(pid, self.path)
        result = ingest_resolved_pair(
            self.db, self.path, {"prompt": {"a": 1}}, resolved()
        )
        self.assertEqual(result["status"], "enriched")
        self.assertEqual(result["image_id"], image_id)
        self.assertEqual(self.db.get_prompt_by_id(pid)["category"], "Personal")
        row = self.db.get_image_by_id(image_id)
        self.assertIn("a", row["prompt_metadata"])

    def test_conflicting_image_is_not_reassigned_or_duplicated(self):
        pid = self.db.save_prompt("dog", prompt_hash=generate_prompt_hash("dog"))
        self.db.link_image_to_prompt(pid, self.path)
        result = ingest_resolved_pair(self.db, self.path, {}, resolved())
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(self.db.list_prompt_pairs()["total"], 0)
        self.assertEqual(
            self.db.model.get_connection()
            .execute("SELECT count(*) FROM generated_images")
            .fetchone()[0],
            1,
        )

    def test_unknown_and_missing_negative_never_become_empty_pairs(self):
        for state in ("absent", "ambiguous", "unresolved"):
            value = resolved()
            value["state"] = state
            result = ingest_resolved_pair(self.db, self.path, {}, value)
            self.assertEqual(result["status"], state)
        self.assertEqual(self.db.list_prompt_pairs()["total"], 0)
        result = ingest_resolved_pair(self.db, self.path, {}, resolved(negative=""))
        self.assertEqual(result["status"], "added")

    def test_failure_after_inserts_rolls_back_all_rows(self):
        with patch.object(
            self.db, "_link_image_to_pair", side_effect=RuntimeError("injected")
        ):
            with self.assertRaises(RuntimeError):
                ingest_resolved_pair(self.db, self.path, {}, resolved())
        conn = self.db.model.get_connection()
        for table in (
            "prompts",
            "prompt_pairs",
            "generated_images",
            "image_prompt_pairs",
        ):
            self.assertEqual(
                conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0
            )

    def test_distinct_paths_are_not_collapsed_and_effective_text_stays_in_provenance(
        self,
    ):
        value = resolved("prefix cat suffix")
        value["positive"]["storage_text"] = "cat"
        a = ingest_resolved_pair(self.db, self.path, {}, value)
        b = ingest_resolved_pair(
            self.db, self.path.replace("image.png", "other/image.png"), {}, value
        )
        self.assertEqual(a["pair_id"], b["pair_id"])
        pair = self.db.get_prompt_pair(a["pair_id"])
        self.assertEqual(pair["positive_text"], "cat")
        self.assertEqual(pair["image_count"], 2)
        association = self.db.get_image_prompt_pairs(a["image_id"])[0]
        self.assertEqual(
            association["provenance"]["positive"]["text"], "prefix cat suffix"
        )

    def test_image_lookup_exposes_its_recorded_pair_not_latest_negative(self):
        value = resolved("trigger cat", "blur")
        value["positive"]["storage_text"] = "cat"
        first = ingest_resolved_pair(self.db, self.path, {}, value)
        ingest_resolved_pair(self.db, self.path + "2", {}, resolved("cat", "noise"))
        info = self.db.get_image_prompt_info(self.path)
        self.assertEqual(info["pair"]["id"], first["pair_id"])
        self.assertEqual(info["pair"]["negative_text"], "blur")
        self.assertEqual(info["pair_context"]["positive_text"], "trigger cat")
        self.assertEqual(info["pair_context"]["negative_text"], "blur")

    def test_overwritten_file_with_different_negative_reports_conflict(self):
        ingest_resolved_pair(self.db, self.path, {}, resolved())
        result = ingest_resolved_pair(
            self.db, self.path, {}, resolved(negative="noise")
        )
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(self.db.list_prompt_pairs()["total"], 1)
