"""The read-only health check (System › Overview and Maintenance).

``run_health_checks`` reads one snapshot on its own ``mode=ro`` connection:
SQLite's integrity and foreign-key checks, stored prompt hashes against
``utils.hashing``, duplicate pair rows, the legacy consistency queries, image
links whose file is gone (only under readable sources) and the Needs-review
count when one is supplied. It never writes, and it never turns a check it
could not run into a count.
"""

import hashlib
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import health  # noqa: E402
from database.operations import PromptDatabase  # noqa: E402
from utils.hashing import generate_prompt_hash  # noqa: E402

try:
    from tests.open_handles import assert_closed  # noqa: E402
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed  # noqa: E402

CHECK_IDS = [
    "integrity",
    "foreign_keys",
    "canonical_hashes",
    "pair_uniqueness",
    "missing_files",
    "ambiguous_metadata",
    "consistency",
]


def _digest(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class HealthTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.temp.name))
        self.output = self.root / "output"
        self.output.mkdir()
        self.db_path = str(self.root / "prompts.db")
        self.db = PromptDatabase(self.db_path)

    def tearDown(self):
        self.db.close_all()
        assert_closed(self, self.root)
        self.temp.cleanup()

    def save(self, text, **metadata):
        return self.db.save_prompt(
            text, prompt_hash=generate_prompt_hash(text), **metadata
        )

    def image(self, prompt_id, name, *, exists=True, folder=None):
        folder = Path(folder or self.output)
        path = folder / name
        if exists:
            path.write_bytes(b"png")
        return self.db.link_image_to_prompt(prompt_id, str(path))

    def run_checks(self, roots=None, **kwargs):
        roots = [str(self.output)] if roots is None else roots
        return self.db.run_health_checks(roots, **kwargs)

    @staticmethod
    def check(report, check_id):
        return next(c for c in report["checks"] if c["id"] == check_id)

    def raw(self):
        """A plain connection for planting damage the app itself never writes."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys=OFF")
        return conn


class TestHealthReport(HealthTestCase):
    def test_clean_library_passes_except_unavailable_review(self):
        prompt = self.save("A clean prompt")
        self.db.save_prompt_pair("Cat", "Blur")
        self.image(prompt, "a.png")
        report = self.run_checks(review_count=0)
        self.assertTrue(report["success"])
        self.assertEqual([c["id"] for c in report["checks"]], CHECK_IDS)
        self.assertEqual(
            [c["label"] for c in report["checks"]],
            [
                "Integrity check",
                "Foreign keys & pair references",
                "Canonical hashes",
                "Pair uniqueness",
                "Missing files",
                "Ambiguous metadata",
                "Consistency",
            ],
        )
        for check in report["checks"]:
            self.assertEqual(check["status"], "passed", check)
            self.assertEqual(check["count"], 0, check)
        self.assertEqual(report["status"], "passed")
        self.assertRegex(report["checked_at"], r"^\d{4}-\d\d-\d\dT.*\+00:00$")

    def test_health_check_never_writes(self):
        prompt = self.save("Untouched")
        self.image(prompt, "kept.png")
        self.image(prompt, "gone.png", exists=False)
        with self.raw() as conn:
            conn.execute("UPDATE prompts SET hash='stale' WHERE id=?", (prompt,))
        conn.close()
        # The app's own connection stays open, as in the running server.
        self.db.get_statistics()
        files = [self.db_path, self.db_path + "-wal"]
        before = [_digest(path) for path in files]
        shm_size = os.path.getsize(self.db_path + "-shm")
        media = sorted(os.listdir(self.output))
        opened = []
        real_connect = sqlite3.connect

        class Recording(sqlite3.Connection):
            changes_at_close = None

            def close(self):
                Recording.changes_at_close = self.total_changes
                super().close()

        def connect(*args, **kwargs):
            conn = real_connect(*args, factory=Recording, **kwargs)
            opened.append((args, kwargs, conn))
            return conn

        with patch.object(health.sqlite3, "connect", side_effect=connect):
            report = self.run_checks()
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(len(opened), 1)
        args, kwargs, conn = opened[0]
        self.assertTrue(str(args[0]).endswith("?mode=ro"), args)
        self.assertTrue(kwargs.get("uri"))
        self.assertEqual(Recording.changes_at_close, 0)
        with self.assertRaises(sqlite3.ProgrammingError):  # closed in finally
            conn.execute("SELECT 1")
        self.assertEqual([_digest(path) for path in files], before)
        # The -shm holds WAL read marks every reader updates; it never grows.
        self.assertEqual(os.path.getsize(self.db_path + "-shm"), shm_size)
        self.assertEqual(sorted(os.listdir(self.output)), media)
        with self.db.model.get_connection() as live:
            stored = live.execute(
                "SELECT hash FROM prompts WHERE id=?", (prompt,)
            ).fetchone()[0]
        self.assertEqual(stored, "stale")

    def test_connection_closed_when_a_check_raises(self):
        self.save("Any")
        with (
            patch.object(
                health,
                "_canonical_hashes",
                side_effect=sqlite3.OperationalError("boom"),
            ),
            self.assertLogs("prompt_manager.database.health", "ERROR"),
        ):
            report = self.run_checks(review_count=0)
        canonical = self.check(report, "canonical_hashes")
        self.assertEqual(canonical["status"], "failed")
        self.assertIsNone(canonical["count"])
        self.assertEqual(canonical["detail"], ["The check could not finish."])
        self.assertEqual(self.check(report, "integrity")["status"], "passed")
        self.assertEqual(report["status"], "incomplete")
        self.db.close_all()
        assert_closed(self, self.root)

    def test_unopenable_database_fails_every_database_check(self):
        missing = PromptDatabase.__new__(PromptDatabase)
        missing.model = type("M", (), {"db_path": str(self.root / "nope.db")})()
        with self.assertLogs("prompt_manager.database.health", "ERROR"):
            report = missing.run_health_checks([str(self.output)], review_count=0)
        statuses = {c["id"]: c["status"] for c in report["checks"]}
        self.assertEqual(statuses["integrity"], "failed")
        self.assertEqual(statuses["missing_files"], "failed")
        self.assertEqual(statuses["ambiguous_metadata"], "passed")
        self.assertEqual(report["status"], "incomplete")
        self.assertFalse(os.path.exists(self.root / "nope.db"))

    def test_integrity_and_foreign_key_issues_reported(self):
        prompt = self.save("Linked")
        with self.raw() as conn:
            conn.execute("CREATE TABLE probe(a)")
            conn.execute("CREATE INDEX probe_a ON probe(a)")
            conn.executemany("INSERT INTO probe VALUES(?)", [(i,) for i in range(16)])
            conn.execute(
                "INSERT INTO generated_images(prompt_id,image_path,filename,file_path)"
                " VALUES(?,?,?,?)",
                (9999, str(self.output / "orphan.png"), "orphan.png", "orphan"),
            )
            conn.execute(
                "INSERT INTO prompt_tags(prompt_id,tag_id) VALUES(?,?)", (prompt, 777)
            )
        conn.close()
        with self.raw() as conn:
            conn.execute("PRAGMA writable_schema=ON")
            conn.execute(
                "UPDATE sqlite_master SET sql='CREATE INDEX probe_a ON probe(a DESC)'"
                " WHERE name='probe_a'"
            )
        conn.close()
        self.db.close_all()  # the next read sees the edited schema
        (self.output / "orphan.png").write_bytes(b"png")
        with self.raw() as conn:
            expected = [r[0] for r in conn.execute("PRAGMA integrity_check(100)")]
        conn.close()
        self.assertGreater(len(expected), 10)
        report = self.run_checks(review_count=0)
        integrity = self.check(report, "integrity")
        self.assertEqual(integrity["status"], "issues")
        self.assertEqual(integrity["count"], len(expected))
        self.assertEqual(integrity["detail"], expected[:10])
        self.assertIn("missing from index probe_a", integrity["detail"][0])
        foreign = self.check(report, "foreign_keys")
        self.assertEqual(foreign["status"], "issues")
        self.assertEqual(foreign["count"], 2)
        self.assertEqual(
            sorted(foreign["detail"]),
            [
                "generated_images row 1 references a missing prompts row",
                "prompt_tags row 1 references a missing tags row",
            ],
        )
        consistency = self.check(report, "consistency")
        self.assertEqual(consistency["status"], "issues")
        self.assertEqual(consistency["count"], 1)
        self.assertEqual(
            consistency["detail"], ["Image 1 references non-existent prompt 9999"]
        )
        self.assertEqual(report["status"], "issues")

    def test_canonical_hash_mismatch_counts_rows(self):
        ids = [self.save(f"Prompt {n}") for n in range(14)]
        with self.raw() as conn:
            for n, prompt_id in enumerate(ids[:12]):
                conn.execute(
                    "UPDATE prompts SET hash=? WHERE id=?", (f"bad{n}", prompt_id)
                )
            conn.execute("UPDATE prompts SET hash=NULL WHERE id=?", (ids[12],))
        conn.close()
        # Normalised spellings hash alike: this one is not a mismatch.
        self.save("  MIXED Case  ")
        report = self.run_checks(review_count=0)
        canonical = self.check(report, "canonical_hashes")
        self.assertEqual(canonical["status"], "issues")
        self.assertEqual(canonical["count"], 13)
        self.assertEqual(len(canonical["detail"]), 10)
        self.assertEqual(
            canonical["detail"][0],
            f"Prompt {ids[0]}: the stored hash does not match its text",
        )

    def test_duplicate_pair_rows_reported(self):
        pair = self.db.save_prompt_pair("Cat", "")
        positive = self.db.get_prompt_pair(pair)["positive_prompt_id"]
        self.db.save_prompt_pair("Dog", "Blur")
        with self.raw() as conn:
            # Only the partial index keeps positive-only pairs unique; a restored
            # or hand-edited database may have lost it.
            conn.execute("DROP INDEX idx_prompt_pairs_positive_only")
            conn.executemany(
                "INSERT INTO prompt_pairs(positive_prompt_id,negative_prompt_id)"
                " VALUES(?,NULL)",
                [(positive,), (positive,)],
            )
        conn.close()
        report = self.run_checks(review_count=0)
        pairs = self.check(report, "pair_uniqueness")
        self.assertEqual(pairs["status"], "issues")
        self.assertEqual(pairs["count"], 2)
        self.assertEqual(
            pairs["detail"],
            [f"Positive prompt {positive} with no negative: 3 pair rows"],
        )


class TestMissingFiles(HealthTestCase):
    def test_missing_files_counted_under_readable_sources_only(self):
        prompt = self.save("Images")
        self.image(prompt, "here.png")
        for n in range(12):
            self.image(prompt, f"gone-{n}.png", exists=False)
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        self.image(prompt, "outside.png", exists=False, folder=elsewhere)
        report = self.run_checks(review_count=0)
        missing = self.check(report, "missing_files")
        self.assertEqual(missing["status"], "issues")
        self.assertEqual(missing["count"], 12)
        self.assertEqual(len(missing["detail"]), 10)
        self.assertRegex(missing["detail"][0], r"^Image \d+: gone-0\.png is missing$")
        self.assertNotIn(str(self.root), " ".join(missing["detail"]))
        with self.db.model.get_connection() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM generated_images").fetchone()[0],
                14,
            )

    def test_links_outside_every_source_are_named_not_counted(self):
        prompt = self.save("Images")
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        self.image(prompt, "outside.png", exists=False, folder=elsewhere)
        missing = self.check(self.run_checks(review_count=0), "missing_files")
        self.assertEqual(missing["status"], "passed")
        self.assertEqual(missing["count"], 0)
        self.assertEqual(
            missing["detail"],
            ["1 link outside the image sources was not checked."],
        )

    def test_missing_file_vs_unavailable_root(self):
        prompt = self.save("Images")
        absent = self.root / "unmounted"
        absent.mkdir()
        self.image(prompt, "on-nas.png", folder=absent)
        self.image(prompt, "gone.png", exists=False)
        os.unlink(absent / "on-nas.png")
        absent.rmdir()
        report = self.run_checks([str(self.output), str(absent)], review_count=0)
        missing = self.check(report, "missing_files")
        self.assertEqual(missing["status"], "unavailable")
        self.assertIsNone(missing["count"])
        self.assertEqual(
            missing["detail"],
            ["1 image source is unavailable, so missing files were not counted."],
        )
        self.assertEqual(report["status"], "incomplete")

    def test_no_sources_is_unavailable(self):
        self.save("Images")
        missing = self.check(self.run_checks([], review_count=0), "missing_files")
        self.assertEqual(missing["status"], "unavailable")
        self.assertIsNone(missing["count"])
        self.assertEqual(missing["detail"], ["No image source is available."])


class TestAmbiguousMetadata(HealthTestCase):
    def test_ambiguous_metadata_unavailable_without_review_queue(self):
        self.save("Any")
        report = self.run_checks()
        ambiguous = self.check(report, "ambiguous_metadata")
        self.assertEqual(ambiguous["status"], "unavailable")
        self.assertIsNone(ambiguous["count"])
        self.assertEqual(ambiguous["detail"], ["Needs review is not available yet."])
        self.assertEqual(report["status"], "incomplete")

    def test_review_count_reports_images_needing_review(self):
        report = self.run_checks(review_count=3)
        ambiguous = self.check(report, "ambiguous_metadata")
        self.assertEqual(ambiguous["status"], "issues")
        self.assertEqual(ambiguous["count"], 3)
        self.assertEqual(ambiguous["detail"], [])
        self.assertEqual(report["status"], "issues")

    def test_invalid_review_count_is_rejected(self):
        for value in (-1, True, "3", 1.5):
            with self.assertRaises(ValueError):
                self.run_checks(review_count=value)


class TestOverallStatus(unittest.TestCase):
    def test_overall_status_precedence(self):
        def checks(*statuses):
            return [{"status": status} for status in statuses]

        self.assertEqual(health.overall_status(checks("passed", "passed")), "passed")
        self.assertEqual(health.overall_status(checks("passed", "issues")), "issues")
        self.assertEqual(
            health.overall_status(checks("issues", "unavailable")), "incomplete"
        )
        self.assertEqual(health.overall_status(checks("failed")), "incomplete")


if __name__ == "__main__":
    unittest.main()
