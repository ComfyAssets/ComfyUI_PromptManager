"""Dependency checks exclude competing pair writers until mutation finishes."""

import os
import sqlite3
import tempfile
import unittest.mock

from database.operations import PromptDatabase
from utils.hashing import generate_prompt_hash


class TestPairDependencyConcurrency(unittest.TestCase):
    def test_legacy_mutators_lock_out_pair_creation_before_dependency_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "concurrent.db")
            db = PromptDatabase(path)
            try:
                original = db._ensure_prompts_unpaired
                checks = []

                def check_with_competing_writer(conn, ids):
                    original(conn, ids)
                    competitor = sqlite3.connect(path, timeout=0)
                    try:
                        with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                            competitor.execute("BEGIN IMMEDIATE")
                    finally:
                        competitor.close()
                    checks.append(list(ids))

                actions = (
                    lambda pid: db.update_prompt_text(pid, "changed"),
                    db.delete_prompt,
                    lambda pid: db.bulk_delete_prompts([pid]),
                    lambda _pid: db.delete_prompts_by_category("temporary"),
                )
                with unittest.mock.patch.object(
                    db, "_ensure_prompts_unpaired", check_with_competing_writer
                ):
                    for index, action in enumerate(actions):
                        with self.subTest(action=index):
                            text = f"unpaired {index}"
                            pid = db.save_prompt(
                                text,
                                category="temporary",
                                prompt_hash=generate_prompt_hash(text),
                            )
                            self.assertTrue(action(pid))
                self.assertEqual(len(checks), len(actions))
            finally:
                db.close_all()


if __name__ == "__main__":
    unittest.main()
