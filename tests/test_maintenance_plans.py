"""Maintenance plans (#249 System S6): the plan store and the read-only previews.

``PlanStore`` holds each reviewed plan server-side under a single-use token
for ``ttl`` seconds, within a count and byte budget it never truncates to fit.
``PromptDatabase.preview_maintenance`` builds a plan on its own ``mode=ro``
connection: it writes nothing (the TESTING.md read-only standard) and reuses
the legacy maintenance queries, so a preview counts what the legacy cleanup
would change, except that a missing-file link is only counted under a source
that can be read now.
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

from database import maintenance  # noqa: E402
from database.operations import PromptDatabase  # noqa: E402
from py.api.maintenance_plans import (  # noqa: E402
    PlanExpired,
    PlanStore,
    PlanUsed,
    ScopeTooLarge,
)
from utils.hashing import generate_prompt_hash  # noqa: E402

try:
    from tests import test_execution_history_database as history_tests  # noqa: E402
    from tests.open_handles import assert_closed  # noqa: E402
    from tests.test_execution_context_database import bundle  # noqa: E402
except ImportError:  # discovered with tests/ as the top-level directory
    import test_execution_history_database as history_tests  # noqa: E402
    from open_handles import assert_closed  # noqa: E402
    from test_execution_context_database import bundle  # noqa: E402

OPERATIONS = ("duplicate_prompts", "optimize", "unused_components", "missing_links")


def _digest(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class TestPlanStore(unittest.TestCase):
    def test_plan_expiry_budget_and_single_use(self):
        clock = FakeClock()
        store = PlanStore(ttl=900, max_plans=2, max_bytes=200, clock=clock)
        first = store.issue("optimize", "sha256:a", {"targets": []}, ("db", 0))
        self.assertGreaterEqual(len(first), 40)  # token_urlsafe(32)
        plan = store.peek(first, "optimize")
        self.assertEqual(
            (plan.operation, plan.fingerprint, plan.db_identity),
            ("optimize", "sha256:a", ("db", 0)),
        )
        # Single use: the second consume names the job the first one started.
        self.assertIs(store.consume(first, "optimize", job_id="job-1"), plan)
        with self.assertRaises(PlanUsed) as used:
            store.consume(first, "optimize", job_id="job-2")
        self.assertEqual(used.exception.job_id, "job-1")
        with self.assertRaises(PlanUsed):
            store.peek(first, "optimize")
        # Another operation's token, an unknown token and a non-string are expired.
        second = store.issue("missing_links", "sha256:b", {"targets": [1]}, ("db", 0))
        for token, operation in (
            (second, "optimize"),
            ("nope", "missing_links"),
            (None, "missing_links"),
        ):
            with self.subTest(token=token), self.assertRaises(PlanExpired):
                store.peek(token, operation)
        # Expiry: valid up to ttl, gone after it.
        clock.now += 900
        self.assertEqual(store.peek(second, "missing_links").fingerprint, "sha256:b")
        clock.now += 0.001
        with self.assertRaises(PlanExpired):
            store.consume(second, "missing_links", job_id="job-3")
        # Budget: a plan over max_bytes is refused whole, never truncated.
        with self.assertRaises(ScopeTooLarge):
            store.issue("unused_components", "x", {"targets": list(range(100))}, ())
        # Count budget: the oldest plan makes room for a new one and is expired.
        a = store.issue("optimize", "a", {}, ())
        b = store.issue("optimize", "b", {}, ())
        c = store.issue("optimize", "c", {}, ())
        with self.assertRaises(PlanExpired):
            store.peek(a, "optimize")
        self.assertEqual(
            [store.peek(t, "optimize").fingerprint for t in (b, c)], ["b", "c"]
        )
        # Byte budget across plans: older plans are dropped until the new one fits.
        sized = PlanStore(max_plans=8, max_bytes=100, clock=clock)
        pad = {"pad": "x" * 40}  # 50 bytes as compact JSON
        p1, p2, p3 = (sized.issue("optimize", str(n), pad, ()) for n in range(3))
        with self.assertRaises(PlanExpired):
            sized.peek(p1, "optimize")
        self.assertEqual([sized.peek(t).fingerprint for t in (p2, p3)], ["1", "2"])

    def test_expires_at_is_wall_clock(self):
        store = PlanStore(ttl=60, clock=FakeClock(), wall_clock=lambda: 5000.0)
        token = store.issue("optimize", "f", {}, ())
        self.assertEqual(store.peek(token, "optimize").expires_at, 5060.0)

    def test_discard_forgets_a_plan(self):
        store = PlanStore()
        token = store.issue("optimize", "f", {}, ())
        store.discard(token)
        store.discard("unknown")
        with self.assertRaises(PlanExpired):
            store.peek(token, "optimize")

    def test_used_tokens_are_remembered_within_a_bound(self):
        store = PlanStore(max_plans=2)
        tokens = []
        for index in range(store.MAX_USED + 1):
            token = store.issue("optimize", str(index), {}, ())
            store.consume(token, "optimize", job_id=f"job-{index}")
            tokens.append(token)
        with self.assertRaises(PlanExpired):  # the oldest record was dropped
            store.peek(tokens[0], "optimize")
        with self.assertRaises(PlanUsed):
            store.peek(tokens[-1], "optimize")


class MaintenanceDatabaseCase(unittest.TestCase):
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
        path = Path(folder or self.output) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if exists:
            path.write_bytes(b"png")
        return self.db.link_image_to_prompt(prompt_id, str(path))

    def duplicate(self, text):
        """A second prompt row whose text differs only in case (legacy duplicate)."""
        with self.db.model.get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO prompts(text, created_at) VALUES(?, '2999-01-01')",
                (text,),
            )
        return cursor.lastrowid

    def preview(self, operation, roots=None):
        roots = [str(self.output)] if roots is None else roots
        return self.db.preview_maintenance(operation, roots)

    def ids(self, sql, *args):
        with self.db.model.get_connection() as conn:
            return [row[0] for row in conn.execute(sql, args)]


class TestPreviews(MaintenanceDatabaseCase):
    def populate(self):
        kept = self.save("Kept prompt")
        self.image(kept, "kept.png")
        self.image(kept, "gone.png", exists=False)
        self.save("Unused prompt")
        self.duplicate("KEPT PROMPT")
        return kept

    def test_preview_writes_nothing(self):
        self.populate()
        # The app's own connection stays open, as in the running server.
        self.db.get_statistics()
        files = [self.db_path, self.db_path + "-wal"]
        real_connect = sqlite3.connect

        class Recording(sqlite3.Connection):
            changes_at_close = []

            def close(self):
                Recording.changes_at_close.append(self.total_changes)
                super().close()

        opened = []

        def connect(*args, **kwargs):
            conn = real_connect(*args, factory=Recording, **kwargs)
            opened.append((args, kwargs, conn))
            return conn

        for operation in OPERATIONS:
            with self.subTest(operation=operation):
                before = [_digest(path) for path in files]
                shm_size = os.path.getsize(self.db_path + "-shm")
                media = {
                    name: _digest(self.output / name)
                    for name in os.listdir(self.output)
                }
                prompts = self.ids("SELECT id FROM prompts ORDER BY id")
                images = self.ids("SELECT id FROM generated_images ORDER BY id")
                opened.clear()
                Recording.changes_at_close.clear()
                with patch.object(maintenance.sqlite3, "connect", side_effect=connect):
                    draft = self.preview(operation)
                self.assertTrue(draft.fingerprint.startswith("sha256:"))
                self.assertEqual(len(opened), 1)
                args, kwargs, conn = opened[0]
                self.assertTrue(str(args[0]).endswith("?mode=ro"), args)
                self.assertTrue(kwargs.get("uri"))
                self.assertEqual(Recording.changes_at_close, [0])
                with self.assertRaises(sqlite3.ProgrammingError):  # closed
                    conn.execute("SELECT 1")
                self.assertEqual([_digest(path) for path in files], before)
                self.assertEqual(os.path.getsize(self.db_path + "-shm"), shm_size)
                self.assertEqual(
                    {
                        name: _digest(self.output / name)
                        for name in os.listdir(self.output)
                    },
                    media,
                )
                self.assertEqual(
                    self.ids("SELECT id FROM prompts ORDER BY id"), prompts
                )
                self.assertEqual(
                    self.ids("SELECT id FROM generated_images ORDER BY id"), images
                )

    def test_unknown_operation_raises(self):
        with self.assertRaises(ValueError):
            self.db.preview_maintenance("reboot", [])

    def test_same_library_gives_the_same_fingerprint(self):
        self.populate()
        for operation in OPERATIONS:
            with self.subTest(operation=operation):
                self.assertEqual(
                    self.preview(operation).fingerprint,
                    self.preview(operation).fingerprint,
                )

    def test_preview_details_are_capped_and_carry_no_server_path(self):
        prompt = self.save("Many")
        for index in range(60):
            self.image(prompt, f"sub/gone-{index}.png", exists=False)
        draft = self.preview("missing_links")
        self.assertEqual(draft.scope["count"], 60)
        self.assertEqual(len(draft.details), maintenance.MAX_DETAILS)
        self.assertEqual(draft.details[0]["label"], "sub/gone-0.png")
        self.assertEqual(draft.details[0]["detail"], "output · the file is missing")
        self.assertNotIn(str(self.root), str(draft.public()))
        self.assertEqual(len(draft.targets), 60)

    def test_duplicate_prompts_plan_counts_the_legacy_groups(self):
        kept = self.save("A cat")
        self.duplicate("A CAT")
        self.duplicate("a cat")
        paired = self.db.get_prompt_pair(self.db.save_prompt_pair("a dog", "blur"))
        self.duplicate("A DOG")
        draft = self.preview("duplicate_prompts")
        self.assertEqual(draft.scope["count"], 2)
        self.assertEqual(draft.scope["label"], "2 duplicate prompts in 1 group")
        self.assertEqual(draft.protected["count"], 2)
        self.assertEqual(
            draft.protected["reasons"], ["1 group belongs to a saved pair"]
        )
        self.assertEqual(draft.targets[0][0], kept)
        self.assertEqual(len(draft.targets), 1)
        self.assertEqual(
            [row["detail"] for row in draft.details],
            ["Keeps the oldest of 3 copies", "Kept: belongs to a saved pair"],
        )
        self.assertEqual(draft.details[0]["label"], "A cat")
        self.assertIsNotNone(paired)

    def test_unused_components_plan_protects_pairs_history_and_titled_parents(self):
        orphan = self.save("Orphan")
        pair = self.db.get_prompt_pair(self.db.save_prompt_pair("paired", "neg"))
        titled = self.save("Titled parent")
        untitled = self.save("Explicit parent")
        protected = self.save("Protected", tags=["__protected__"])
        with self.db.model.get_connection() as conn:
            conn.execute(
                "INSERT INTO prompt_library_state(prompt_id,title) "
                "VALUES(?,?),(?,NULL)",
                (titled, "My recipe", untitled),
            )
        hinted = self.save("Recorded run")
        draft_before_hint = self.preview("unused_components")
        self.assertEqual(sorted(draft_before_hint.targets), sorted([orphan, hinted]))
        self.record_run_hint(hinted)
        draft = self.preview("unused_components")
        self.assertEqual(draft.targets, [orphan])
        self.assertEqual(draft.scope["count"], 1)
        self.assertEqual(draft.scope["label"], "1 unused prompt component")
        self.assertEqual(draft.protected["count"], 6)
        self.assertEqual(
            draft.protected["reasons"],
            [
                "2 in saved pairs",
                "1 tagged __protected__",
                "2 with Library state (a title or an explicit parent)",
                "1 used by a recorded run",
            ],
        )
        self.assertEqual(
            draft.details,
            [
                {
                    "label": "Orphan",
                    "detail": maintenance.UNUSED_DETAIL,
                }
            ],
        )
        # The plan counts exactly what the legacy prune removes.
        self.assertIsNotNone(pair)
        self.assertEqual(protected > 0, True)
        self.assertEqual(self.db.prune_orphaned_prompts(), 1)
        self.assertEqual(self.preview("unused_components").targets, [])

    def record_run_hint(self, prompt_id, run=0):
        """Make ``prompt_id`` the live positive hint of a recorded run."""
        text = self.db.get_prompt_by_id(prompt_id)["text"]
        history = history_tests.HistoryDatabaseTests
        start = history.start(self, self.db, run)
        self.db.write_execution_start(start)
        self.db.write_execution_finish(history.finish(self, start))
        self.assertTrue(
            self.db.write_execution_contexts(
                start.run_id, start.storage_epoch, bundle(positive=text, negative="")
            )
        )

    def test_missing_links_plan_skips_unavailable_roots(self):
        prompt = self.save("Links")
        self.image(prompt, "here.png")
        gone = self.image(prompt, "gone.png", exists=False)
        unmounted = self.root / "unmounted"
        self.image(prompt, "away.png", folder=unmounted)
        os.remove(unmounted / "away.png")
        os.rmdir(unmounted)  # the drive went away: its links look missing
        self.image(
            prompt, "elsewhere.png", folder=self.root / "elsewhere", exists=False
        )
        draft = self.preview("missing_links", [str(self.output), str(unmounted)])
        self.assertEqual(draft.targets, [gone])
        self.assertEqual(draft.scope["count"], 1)
        self.assertEqual(draft.scope["label"], "1 link to a missing file")
        self.assertEqual(draft.protected["count"], 2)
        self.assertEqual(
            draft.protected["reasons"],
            [
                "1 link under an unavailable source",
                "1 link outside the image sources",
            ],
        )
        self.assertFalse(draft.media["deletes_files"])
        # No readable source: nothing is counted, every link is protected.
        none = self.preview("missing_links", [str(unmounted)])
        self.assertEqual((none.targets, none.scope["count"]), ([], 0))
        self.assertEqual(none.protected["count"], 4)

    def test_missing_links_plan_matches_the_health_check(self):
        prompt = self.save("Links")
        self.image(prompt, "a.png", exists=False)
        self.image(prompt, "b.png", exists=False)
        self.image(prompt, "c.png")
        report = self.db.run_health_checks([str(self.output)])
        missing = next(c for c in report["checks"] if c["id"] == "missing_files")
        self.assertEqual(self.preview("missing_links").scope["count"], missing["count"])

    def test_optimize_plan_reports_sizes_without_paths(self):
        self.populate()
        draft = self.preview("optimize")
        self.assertEqual(draft.targets, [])
        self.assertEqual(draft.scope, {"count": 1, "label": "The whole database"})
        self.assertEqual(
            [row["label"] for row in draft.details],
            ["Database file", "Free pages", "Free space needed"],
        )
        self.assertNotIn(str(self.root), str(draft.public()))

    def test_legacy_cleanup_missing_images_skips_unavailable_roots(self):
        prompt = self.save("Links")
        gone = self.image(prompt, "gone.png", exists=False)
        unmounted = self.root / "unmounted"
        away = self.image(prompt, "away.png", folder=unmounted, exists=False)
        outside = self.image(prompt, "x.png", folder=self.root / "other", exists=False)
        unmounted.rmdir()
        self.assertEqual(
            self.db.cleanup_missing_images(roots=[str(self.output), str(unmounted)]), 1
        )
        remaining = self.ids("SELECT id FROM generated_images ORDER BY id")
        self.assertEqual(remaining, sorted([away, outside]))
        self.assertNotIn(gone, remaining)
        # Without roots it keeps the legacy rule: every link whose file is gone.
        self.assertEqual(self.db.cleanup_missing_images(), 2)

    def test_reader_closes_its_connection_when_a_read_raises(self):
        self.populate()
        with patch.object(
            maintenance, "_orphaned_ids", side_effect=sqlite3.OperationalError("boom")
        ):
            with self.assertRaises(sqlite3.OperationalError):
                self.preview("unused_components")
        # The teardown's assert_closed proves the read-only handle was closed.


class TestApply(MaintenanceDatabaseCase):
    """``apply_maintenance`` rechecks the fingerprint inside BEGIN IMMEDIATE."""

    def apply(self, draft, roots=None):
        roots = [str(self.output)] if roots is None else roots
        return self.db.apply_maintenance(
            draft.operation, draft.fingerprint, draft.targets, roots
        )

    def test_unused_components_apply_removes_exactly_the_reviewed_prompts(self):
        first, second = self.save("One"), self.save("Two")
        draft = self.preview("unused_components")
        with patch.object(maintenance, "ID_BATCH", 1):  # one id per statement
            result = self.apply(draft)
        self.assertEqual(result["changed"], 2)
        self.assertEqual(
            [row["prompt_id"] for row in result["rows"]], sorted([first, second])
        )
        self.assertIsNone(self.db.get_prompt_by_id(first))
        self.assertIsNone(self.db.get_prompt_by_id(second))

    def test_changed_targets_raise_before_anything_is_written(self):
        orphan, protected = self.save("Orphan"), self.save("Soon protected")
        prompt = self.save("Links")
        gone = self.image(prompt, "gone.png", exists=False)
        kept = self.save("Kept")
        self.duplicate("KEPT")
        cases = {
            "unused_components": lambda: self.db.model.get_connection().execute(
                "INSERT INTO prompt_library_state(prompt_id,title) VALUES(?,?)",
                (protected, "Now a recipe"),
            ),
            "missing_links": lambda: (self.output / "gone.png").write_bytes(b"back"),
            "duplicate_prompts": lambda: self.db.update_prompt_metadata(kept, rating=4),
        }
        for operation, change in cases.items():
            with self.subTest(operation=operation):
                draft = self.preview(operation)
                self.assertTrue(draft.targets)
                change()
                self.db.model.get_connection().commit()
                with self.assertRaises(maintenance.PlanChanged):
                    self.apply(draft)
        self.assertIsNotNone(self.db.get_prompt_by_id(orphan))
        self.assertIsNotNone(self.db.get_image_by_id(gone))
        self.assertEqual(len(self.db.search_prompts(text="kept")), 2)

    def test_a_link_whose_source_went_away_is_never_removed(self):
        folder = self.root / "drive"
        prompt = self.save("Links")
        link = self.image(prompt, "gone.png", folder=folder, exists=False)
        roots = [str(folder)]
        draft = self.preview("missing_links", roots)
        self.assertEqual(draft.targets, [link])
        folder.rmdir()  # unmounted between the review and the apply
        with self.assertRaises(maintenance.PlanChanged):
            self.apply(draft, roots)
        self.assertIsNotNone(self.db.get_image_by_id(link))

    def test_missing_links_apply_removes_rows_only(self):
        prompt = self.save("Links")
        kept = self.image(prompt, "kept.png")
        gone = [self.image(prompt, f"gone-{n}.png", exists=False) for n in range(3)]
        media = {p.name: _digest(p) for p in self.output.iterdir()}
        draft = self.preview("missing_links")
        with patch.object(maintenance, "ID_BATCH", 2):
            result = self.apply(draft)
        self.assertEqual(result["changed"], 3)
        self.assertEqual(
            [os.path.basename(row["image_path"]) for row in result["rows"]],
            [f"gone-{n}.png" for n in range(3)],
        )
        self.assertEqual({p.name: _digest(p) for p in self.output.iterdir()}, media)
        self.assertIsNotNone(self.db.get_image_by_id(kept))
        for image_id in gone:
            self.assertIsNone(self.db.get_image_by_id(image_id))

    def test_duplicate_prompts_apply_merges_into_the_oldest(self):
        kept = self.save("A cat")
        copy = self.duplicate("A CAT")
        image = self.image(copy, "cat.png")
        draft = self.preview("duplicate_prompts")
        result = self.apply(draft)
        self.assertEqual(result["changed"], 1)
        self.assertEqual(result["rows"], [{"prompt_id": copy, "kept_id": kept}])
        self.assertIsNone(self.db.get_prompt_by_id(copy))
        self.assertEqual(self.db.get_image_by_id(image)["prompt_id"], kept)

    def test_optimize_apply_vacuums(self):
        self.save("Some")
        draft = self.preview("optimize")
        with patch.object(self.db.model, "vacuum_database") as vacuum:
            self.assertEqual(self.apply(draft), {"changed": 1, "rows": []})
        vacuum.assert_called_once_with()
        self.assertEqual(self.apply(self.preview("optimize"))["changed"], 1)
        with self.assertRaises(maintenance.PlanChanged):
            self.db.apply_maintenance("optimize", "sha256:other", [], [])

    def test_apply_of_an_unknown_operation_raises(self):
        with self.assertRaises(ValueError):
            self.db.apply_maintenance("reboot", "x", [], [])


class TestVacuum(MaintenanceDatabaseCase):
    def test_vacuum_failure_raises(self):
        with patch(
            "database.models.sqlite3.connect",
            side_effect=sqlite3.OperationalError("disk I/O error"),
        ):
            with self.assertLogs("prompt_manager", "ERROR"):
                with self.assertRaises(sqlite3.OperationalError):
                    self.db.model.vacuum_database()


if __name__ == "__main__":
    unittest.main()
