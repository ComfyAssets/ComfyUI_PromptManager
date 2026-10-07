"""Tests for prompt usage tracking: run_count, last_used_at and server-side sorting."""

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.operations import PromptDatabase
from prompt_manager_base import PromptManagerBase
from utils.hashing import generate_prompt_hash


class UsageTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = PromptDatabase(self.path)

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(self.path + suffix):
                os.unlink(self.path + suffix)

    def _save(self, text, **kwargs):
        return self.db.save_prompt(
            text=text, prompt_hash=generate_prompt_hash(text), **kwargs
        )

    def _set(self, prompt_id, **columns):
        with sqlite3.connect(self.path) as conn:
            for column, value in columns.items():
                conn.execute(
                    f"UPDATE prompts SET {column} = ? WHERE id = ?", (value, prompt_id)
                )

    def _ids(self, prompts):
        return [p["id"] for p in prompts]


class TestRecordPromptUse(UsageTestCase):
    def test_new_prompt_starts_unused_but_timestamped(self):
        prompt = self.db.get_prompt_by_id(self._save("fresh"))
        self.assertEqual(prompt["run_count"], 0)
        self.assertTrue(prompt["last_used_at"])

    def test_record_use_increments_and_touches_last_used(self):
        pid = self._save("used")
        self._set(pid, last_used_at="2020-01-01T00:00:00.000+00:00")

        self.assertTrue(self.db.record_prompt_use(pid))
        self.assertTrue(self.db.record_prompt_use(pid))

        prompt = self.db.get_prompt_by_id(pid)
        self.assertEqual(prompt["run_count"], 2)
        self.assertGreater(prompt["last_used_at"], "2020-01-01T00:00:00.000+00:00")

    def test_rerun_right_after_another_save_still_sorts_first(self):
        # No hand-set timestamps: creation and re-run happen within the same millisecond
        old = self._save("old")
        self._save("new")
        self.db.record_prompt_use(old)
        result = self.db.get_recent_prompts(limit=1, sort="last_used_desc")
        self.assertEqual(self._ids(result["prompts"]), [old])

    def test_microsecond_timestamps_order_correctly_against_backfilled_ones(self):
        backfilled = self._save("backfilled")
        self._set(backfilled, last_used_at="2026-01-01T00:00:00.000+00:00")
        recent = self._save("recent")
        self.db.record_prompt_use(recent)
        self.assertRegex(
            self.db.get_prompt_by_id(recent)["last_used_at"],
            r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}\+00:00$",
        )
        result = self.db.get_recent_prompts(limit=2, sort="last_used_desc")
        self.assertEqual(self._ids(result["prompts"]), [recent, backfilled])

    def test_record_use_can_add_several_runs_at_once(self):
        pid = self._save("several")
        self.db.record_prompt_use(pid, times=3)
        self.assertEqual(self.db.get_prompt_by_id(pid)["run_count"], 3)

    def test_record_use_on_missing_prompt_returns_false(self):
        self.assertFalse(self.db.record_prompt_use(999999))


class TestServerSideSort(UsageTestCase):
    def setUp(self):
        super().setUp()
        self.old = self._save("old prompt", rating=5)
        self.mid = self._save("middle prompt", rating=1)
        self.new = self._save("new prompt")
        self._set(self.old, last_used_at="2026-01-01T00:00:00.000+00:00", run_count=9)
        self._set(self.mid, last_used_at="2026-01-02T00:00:00.000+00:00", run_count=1)
        self._set(self.new, last_used_at="2026-01-03T00:00:00.000+00:00", run_count=3)

    def test_rerun_bubbles_old_prompt_to_top_across_pages(self):
        self.db.record_prompt_use(self.old)
        page1 = self.db.get_recent_prompts(limit=1, offset=0, sort="last_used_desc")
        self.assertEqual(self._ids(page1["prompts"]), [self.old])

    def test_most_used(self):
        result = self.db.get_recent_prompts(limit=10, sort="run_count_desc")
        self.assertEqual(self._ids(result["prompts"]), [self.old, self.new, self.mid])

    def test_rating_sort_puts_unrated_last_and_spans_pages(self):
        page1 = self.db.get_recent_prompts(limit=1, offset=0, sort="rating_desc")
        self.assertEqual(self._ids(page1["prompts"]), [self.old])
        result = self.db.get_recent_prompts(limit=10, sort="rating_asc")
        self.assertEqual(self._ids(result["prompts"]), [self.mid, self.old, self.new])

    def test_text_sort(self):
        result = self.db.get_recent_prompts(limit=10, sort="text_asc")
        self.assertEqual(self._ids(result["prompts"]), [self.mid, self.new, self.old])

    def test_default_and_unknown_sort_keep_newest_first(self):
        expected = [self.new, self.mid, self.old]
        self.assertEqual(
            self._ids(self.db.get_recent_prompts(limit=10)["prompts"]), expected
        )
        injected = self.db.get_recent_prompts(limit=10, sort="id; DROP TABLE prompts")
        self.assertEqual(self._ids(injected["prompts"]), expected)

    def test_search_accepts_sort(self):
        self.db.record_prompt_use(self.mid)
        results = self.db.search_prompts(text="prompt", sort="last_used_desc")
        self.assertEqual(self._ids(results)[0], self.mid)


@unittest.skipIf(
    sqlite3.sqlite_version_info < (3, 35, 0),
    "simulating the old schema needs ALTER TABLE DROP COLUMN (SQLite 3.35+)",
)
class TestUsageMigration(unittest.TestCase):
    """Databases from before 3.2.4 get the columns and an estimated backfill."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db = PromptDatabase(self.path)
        self.with_images = db.save_prompt(text="has images", prompt_hash="h1")
        self.no_images = db.save_prompt(text="no images", prompt_hash="h2")
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "UPDATE prompts SET created_at = '2026-01-01T10:00:00.000000+00:00'"
            )
            # generated_images uses SQLite's space-separated CURRENT_TIMESTAMP format
            for i, ts in enumerate(("2026-03-05 08:00:00", "2026-02-01 09:00:00")):
                conn.execute(
                    "INSERT INTO generated_images (prompt_id, image_path, filename, generation_time)"
                    " VALUES (?, ?, ?, ?)",
                    (self.with_images, f"/out/{i}.png", f"{i}.png", ts),
                )
            # Simulate a pre-3.2.4 schema
            for index in ("idx_prompts_last_used", "idx_prompts_run_count"):
                conn.execute(f"DROP INDEX IF EXISTS {index}")
            conn.execute("ALTER TABLE prompts DROP COLUMN last_used_at")
            conn.execute("ALTER TABLE prompts DROP COLUMN run_count")

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(self.path + suffix):
                os.unlink(self.path + suffix)

    def test_backfill_estimates_usage_from_linked_images(self):
        db = PromptDatabase(self.path)
        used = db.get_prompt_by_id(self.with_images)
        unused = db.get_prompt_by_id(self.no_images)

        self.assertEqual(used["run_count"], 2)
        self.assertTrue(used["last_used_at"].startswith("2026-03-05T08:00:00"))
        self.assertEqual(unused["run_count"], 1)
        self.assertTrue(unused["last_used_at"].startswith("2026-01-01T10:00:00"))

    def test_rows_written_while_downgraded_are_healed_on_next_start(self):
        db = PromptDatabase(self.path)
        # 3.2.3 does not know the columns: its inserts leave last_used_at NULL
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT INTO prompts (text, hash, created_at)"
                " VALUES ('from 3.2.3', 'h3', '2026-04-01T00:00:00.000000+00:00')"
            )
            legacy_id = conn.execute(
                "SELECT id FROM prompts WHERE hash = 'h3'"
            ).fetchone()[0]
        self.assertIsNone(db.get_prompt_by_id(legacy_id)["last_used_at"])

        healed = PromptDatabase(self.path).get_prompt_by_id(legacy_id)
        self.assertTrue(healed["last_used_at"].startswith("2026-04-01T00:00:00"))
        self.assertEqual(healed["run_count"], 1)

    def test_migration_runs_once(self):
        db = PromptDatabase(self.path)
        db.record_prompt_use(self.no_images)
        reopened = PromptDatabase(self.path)
        self.assertEqual(reopened.get_prompt_by_id(self.no_images)["run_count"], 2)


class TestNodeRecordsUse(UsageTestCase):
    """Node saves count a run only when asked; re-runs are counted by the queue hook.

    ComfyUI skips unchanged nodes, so counting at execution misses re-runs; see
    tests/test_usage_tracking.py for the hook that counts them.
    """

    def _node(self):
        node = PromptManagerBase.__new__(PromptManagerBase)
        node.db = self.db
        node.logger = __import__("logging").getLogger("test.prompt_usage")
        return node

    def test_save_without_count_run_leaves_counting_to_the_queue_hook(self):
        node = self._node()
        first = node._save_prompt_to_database("a lighthouse at dusk")
        second = node._save_prompt_to_database("a lighthouse at dusk")

        self.assertEqual(first, second)
        self.assertEqual(self.db.get_prompt_by_id(first)["run_count"], 0)

    def test_count_run_counts_new_and_existing_prompts(self):
        node = self._node()
        pid = node._save_prompt_to_database("batch item", count_run=True)
        node._save_prompt_to_database("batch item", count_run=True)
        self.assertEqual(self.db.get_prompt_by_id(pid)["run_count"], 2)


if __name__ == "__main__":
    unittest.main()
