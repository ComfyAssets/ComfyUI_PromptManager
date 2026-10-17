"""Restores preserve pair ownership and bounded, readable JSON provenance."""

import json
import os
import tempfile
import unittest.mock

try:
    from tests.open_handles import assert_closed
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed

from database.models import PromptModel
from database.operations import PromptDatabase


class TestPairRestoreSemantics(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(assert_closed, self, directory.name)
        self.path = os.path.join(directory.name, "restore.db")
        self.db = PromptDatabase(self.path)
        self.addCleanup(self.db.close_all)
        self.pair_id = self.db.save_prompt_pair("portrait", "blurry")
        pair = self.db.get_prompt_pair(self.pair_id)
        self.image_id = self.db.link_image_to_prompt(
            pair["positive_prompt_id"], os.path.join(directory.name, "image.png")
        )

    def raw_link(self, provenance, pair_id=None):
        with self.db.model.get_connection() as conn:
            conn.execute("DELETE FROM image_prompt_pairs")
            conn.execute(
                "INSERT INTO image_prompt_pairs(image_id,pair_id,provenance) "
                "VALUES(?,?,?)",
                (self.image_id, pair_id or self.pair_id, provenance),
            )
            conn.commit()

    def test_restore_rejects_pair_with_different_image_positive_owner(self):
        other = self.db.save_prompt_pair("landscape", "blurry")
        self.raw_link("{}", other)
        ok, reason = PromptModel.verify_database_file(self.path)
        self.assertFalse(ok)
        self.assertIn("positive", reason.lower())

    def test_restore_rejects_unreadable_nonobject_and_nonfinite_provenance(self):
        for value in (
            "not JSON",
            "[]",
            "null",
            "false",
            "42",
            '"text"',
            '{"value":NaN}',
            '{"value":Infinity}',
            '{"value":-Infinity}',
            '{"value":1e999}',
            b'{"blob":true}',
        ):
            with self.subTest(value=value):
                self.raw_link(value)
                ok, reason = PromptModel.verify_database_file(self.path)
                self.assertFalse(ok)
                self.assertIn("provenance", reason.lower())

    def test_restore_retains_valid_nested_object_provenance(self):
        provenance = {"format": "future", "nested": [{"empty": "", "value": 2.5}]}
        self.raw_link(json.dumps(provenance))
        self.assertEqual(PromptModel.verify_database_file(self.path), (True, "ok"))
        self.assertEqual(
            self.db.get_image_prompt_pairs(self.image_id)[0]["provenance"], provenance
        )

    def test_restore_rejects_excessive_json_nesting_without_raising(self):
        self.raw_link('{"nested":' * 1500 + "0" + "}" * 1500)
        ok, reason = PromptModel.verify_database_file(self.path)
        self.assertFalse(ok)
        self.assertIn("provenance", reason.lower())

    def test_restore_and_runtime_share_the_provenance_size_limit(self):
        provenance = {"large": "x" * (4_000_000 + 1)}
        with self.assertRaises(ValueError):
            self.db.link_image_to_pair(self.pair_id, self.image_id, provenance)
        self.raw_link(json.dumps(provenance))
        ok, reason = PromptModel.verify_database_file(self.path)
        self.assertFalse(ok)
        self.assertIn("provenance", reason.lower())

    def test_runtime_normalizes_deep_json_encoding_error(self):
        provenance = {}
        child = provenance
        for _ in range(1500):
            child["nested"] = {}
            child = child["nested"]
        with self.assertRaises(ValueError):
            self.db.link_image_to_pair(self.pair_id, self.image_id, provenance)
        self.assertEqual(self.db.get_image_prompt_pairs(self.image_id), [])

    def test_runtime_converts_encoder_recursion_failure_to_validation_error(self):
        with unittest.mock.patch(
            "database.pairs.json.dumps", side_effect=RecursionError("depth")
        ):
            with self.assertRaisesRegex(ValueError, "JSON"):
                self.db.link_image_to_pair(self.pair_id, self.image_id, {})

    def test_restore_converts_parser_recursion_failure_to_rejection(self):
        self.raw_link("{}")
        with unittest.mock.patch(
            "database.models.json.loads", side_effect=RecursionError("depth")
        ):
            ok, reason = PromptModel.verify_database_file(self.path)
        self.assertFalse(ok)
        self.assertIn("provenance", reason.lower())


if __name__ == "__main__":
    unittest.main()
