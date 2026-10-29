"""Immutable scalar capture, deduplication, and conservative navigation hints."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import uuid4
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import textwrap
from unittest.mock import patch
import unittest

from database import execution_contexts
from database.execution_history import HistoryEpochChanged
from database.execution_context_schema import context_schema_error
from database.execution_contexts import LIVE_HINT_PROMPT_IDS_SQL
from database.execution_history_schema import rotate_history_epoch
from utils.execution_contexts import (
    ContextBundle,
    PromptSnapshot,
    RecipeContext,
    TextEvidence,
)

try:
    from tests import test_execution_history_database as history_tests
except ImportError:
    import test_execution_history_database as history_tests


def known(value):
    return TextEvidence(value, "recorded", "runtime_return", None)


def snapshot(raw):
    return PromptSnapshot(known(raw), known(raw), known(""), known(""))


def bundle(negative="negative", positive="positive", anchor="4"):
    return ContextBundle(
        (RecipeContext(anchor, snapshot(positive), snapshot(negative), "inferred"),),
        "complete",
        "captured",
    )


class ContextDatabaseTests(unittest.TestCase):
    setUp = history_tests.HistoryDatabaseTests.setUp
    close_databases = history_tests.HistoryDatabaseTests.close_databases
    database = history_tests.HistoryDatabaseTests.database
    start = history_tests.HistoryDatabaseTests.start
    finish = history_tests.HistoryDatabaseTests.finish

    def capture(self, db, item=0, capture=None):
        start = self.start(db, item)
        db.write_execution_start(start)
        db.write_execution_finish(self.finish(start))
        self.assertTrue(
            db.write_execution_contexts(
                start.run_id, start.storage_epoch, capture or bundle()
            )
        )
        return start

    def counts(self, db):
        conn = db.model.get_connection()
        return tuple(
            conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "execution_runs",
                "execution_contexts",
                "execution_prompt_snapshots",
                "execution_context_capture",
            )
        )

    def test_finish_capture_uses_stored_parent_identity(self):
        db = self.database()
        start = replace(self.start(db), run_id=str(uuid4()))
        db.write_execution_start(start)
        finish = self.finish(start)
        self.assertTrue(db.write_execution_contexts_for_finish(finish, bundle()))
        self.assertEqual(db.get_execution_contexts(start.run_id)["state"], "complete")
        missing = replace(finish, queue_item_id=999)
        self.assertFalse(db.write_execution_contexts_for_finish(missing, bundle()))
        self.assertEqual(self.counts(db), (1, 1, 2, 1))

    def test_finish_generation_guard_encloses_commit_and_rollback(self):
        db = self.database()
        start = self.start(db)
        db.write_execution_start(start)
        finish = self.finish(start)
        observed = []

        @contextmanager
        def guard():
            observed.append(db.model.get_connection().in_transaction)
            try:
                yield True
            finally:
                observed.append(db.model.get_connection().in_transaction)

        self.assertTrue(
            db.write_execution_contexts_for_finish(
                finish, bundle(), generation_guard=guard
            )
        )
        self.assertEqual(observed, [True, False])
        observed.clear()
        other = replace(self.start(db, 1), run_id=str(uuid4()))
        db.write_execution_start(other)
        with patch(
            "database.execution_contexts._insert_exact",
            side_effect=ValueError("collision"),
        ):
            with self.assertRaises(ValueError):
                db.write_execution_contexts_for_finish(
                    self.finish(other), bundle(), generation_guard=guard
                )
        self.assertEqual(observed, [True, False])
        self.assertEqual(
            db.get_execution_contexts(other.run_id)["state"], "not_captured"
        )

    def test_finish_generation_guard_rejects_without_capture(self):
        db = self.database()
        start = self.start(db)
        db.write_execution_start(start)

        @contextmanager
        def stale():
            yield False

        self.assertFalse(
            db.write_execution_contexts_for_finish(
                self.finish(start), bundle(), generation_guard=stale
            )
        )
        self.assertEqual(self.counts(db), (1, 0, 0, 0))

    def test_a_a_b_cardinality(self):
        db = self.database()
        self.assertTrue(
            callable(getattr(db, "write_execution_contexts", None)),
            "PromptDatabase must expose immutable context persistence",
        )
        starts = [self.capture(db, i, bundle(n)) for i, n in enumerate(("A", "A", "B"))]
        self.assertEqual(self.counts(db), (3, 2, 3, 3))
        reads = [db.get_execution_contexts(s.run_id) for s in starts]
        self.assertEqual(
            reads[0]["contexts"][0]["context_id"], reads[1]["contexts"][0]["context_id"]
        )
        self.assertNotEqual(
            reads[0]["contexts"][0]["context_id"], reads[2]["contexts"][0]["context_id"]
        )
        self.assertIsNone(context_schema_error(db.model.get_connection()))

    def test_parallel_identical_inserts_deduplicate(self):
        db = self.database()
        starts = [self.start(db, i) for i in range(8)]
        for start in starts:
            db.write_execution_start(start)

        hold = history_tests.FirstWriterHold()
        write_capture = execution_contexts._write_capture

        def held_open(*args, **kwargs):
            hold()
            return write_capture(*args, **kwargs)

        def write(start):
            return db.write_execution_contexts(
                start.run_id, start.storage_epoch, bundle()
            )

        with (
            patch.object(execution_contexts, "_write_capture", side_effect=held_open),
            history_tests.queued_writer_budget(),
            ThreadPoolExecutor(max_workers=4) as pool,
        ):
            self.assertEqual(list(pool.map(write, starts)), [True] * 8)
        self.assertEqual(self.counts(db), (8, 1, 2, 8))

    def test_replay_freezes_first_capture(self):
        db = self.database()
        start = self.capture(db)
        original = db.get_execution_contexts(start.run_id)
        counts = self.counts(db)
        self.assertTrue(
            db.write_execution_contexts(start.run_id, start.storage_epoch, bundle())
        )
        db.write_execution_finish(self.finish(start))
        self.assertEqual(db.get_execution_contexts(start.run_id), original)
        self.assertTrue(
            db.write_execution_contexts(
                start.run_id, start.storage_epoch, bundle("changed")
            )
        )
        read = db.get_execution_contexts(start.run_id)
        self.assertEqual(
            (read["state"], read["reason"]), ("conflicting", "conflicting")
        )
        self.assertEqual(read["contexts"], original["contexts"])
        self.assertEqual(self.counts(db), counts)
        db.write_execution_contexts(start.run_id, start.storage_epoch, bundle())
        self.assertEqual(db.get_execution_contexts(start.run_id), read)

    def test_evidence_replay_and_anchor_changes_conflict_without_inserting(self):
        for change in ("evidence", "anchor", "status"):
            with self.subTest(change=change):
                db = self.database(str(Path(self.temp.name) / f"{change}.db"))
                start = self.capture(db)
                original = db.get_execution_contexts(start.run_id)["contexts"]
                candidate = bundle()
                if change == "evidence":
                    ctx = candidate.contexts[0]
                    candidate = replace(
                        candidate,
                        contexts=(
                            replace(
                                ctx,
                                positive=replace(
                                    ctx.positive,
                                    raw=TextEvidence(
                                        "positive", "inferred", "graph_literal", None
                                    ),
                                ),
                            ),
                        ),
                    )
                elif change == "anchor":
                    candidate = bundle(anchor="5")
                else:
                    candidate = replace(candidate, state="partial", reason="partial")
                db.write_execution_contexts(
                    start.run_id, start.storage_epoch, candidate
                )
                read = db.get_execution_contexts(start.run_id)
                self.assertEqual(read["state"], "conflicting")
                self.assertEqual(read["contexts"], original)
                self.assertEqual(self.counts(db), (1, 1, 2, 1))

    def test_hash_collision_rejected_without_partial_insert(self):
        db = self.database()
        start = self.capture(db)
        original = db.get_execution_contexts(start.run_id)
        second = self.start(db, 1)
        db.write_execution_start(second)
        # A collision must compare stored scalar content, then roll back all rows.
        from utils.execution_contexts import content_digest

        collided = content_digest("text", ("negative",))

        def digest(kind, fields):
            return (
                collided
                if kind == "text" and fields == ("changed",)
                else content_digest(kind, fields)
            )

        before = tuple(db.model.get_connection().iterdump())
        with patch("database.execution_contexts.content_digest", side_effect=digest):
            with self.assertRaises(ValueError):
                db.write_execution_contexts(
                    second.run_id,
                    second.storage_epoch,
                    bundle("changed", "new-positive"),
                )
        self.assertEqual(tuple(db.model.get_connection().iterdump()), before)
        self.assertEqual(db.get_execution_contexts(start.run_id), original)

    def test_missing_parent_and_epoch_mismatch_insert_nothing(self):
        db = self.database()
        start = self.start(db)
        self.assertEqual(
            db.get_execution_contexts(start.run_id)["state"], "not_captured"
        )
        self.assertFalse(
            db.write_execution_contexts(start.run_id, start.storage_epoch, bundle())
        )
        self.assertEqual(self.counts(db), (0, 0, 0, 0))
        db.write_execution_start(start)
        with self.assertRaises(HistoryEpochChanged):
            db.write_execution_contexts(
                start.run_id, "11111111-1111-4111-8111-111111111111", bundle()
            )
        with db.model.get_connection() as conn:
            conn.execute(
                "UPDATE execution_runs SET storage_epoch=? WHERE run_id=?",
                ("11111111-1111-4111-8111-111111111111", start.run_id),
            )
        self.assertFalse(
            db.write_execution_contexts(start.run_id, start.storage_epoch, bundle())
        )
        self.assertEqual(self.counts(db), (1, 0, 0, 0))

    def test_invalid_bundle_has_no_partial_rows(self):
        db = self.database()
        start = self.start(db)
        db.write_execution_start(start)
        for candidate in (
            replace(bundle(), reason="arbitrary"),
            replace(bundle(), contexts=()),
            replace(bundle(), contexts=(RecipeContext("4", None, None, "inferred"),)),
        ):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                db.write_execution_contexts(
                    start.run_id, start.storage_epoch, candidate
                )
        self.assertEqual(self.counts(db), (1, 0, 0, 0))

    def test_unknown_empty_unicode_and_evidence_round_trip(self):
        db = self.database()
        unknown = TextEvidence(None, "unknown", "unknown", "missing")
        ctx = RecipeContext(
            "3",
            PromptSnapshot(known(" e\u0301 \n"), unknown, known(""), known("é")),
            None,
            "inferred",
        )
        start = self.capture(db, capture=ContextBundle((ctx,), "partial", "missing"))
        read = db.get_execution_contexts(start.run_id)["contexts"][0]
        self.assertEqual(
            read["positive"],
            {"raw": " e\u0301 \n", "effective": None, "prepend": "", "append": "é"},
        )
        self.assertIsNone(read["negative"])
        self.assertEqual(
            read["evidence"]["positive"]["effective"],
            {"provenance": "unknown", "source": "unknown", "reason": "missing"},
        )
        self.assertEqual(
            set(read["evidence"]),
            {"format_version", "role_provenance", "positive", "negative"},
        )
        self.assertIsNone(context_schema_error(db.model.get_connection()))

    def test_library_mutations_and_id_reuse_do_not_rewrite_history(self):
        db = self.database()
        conn = db.model.get_connection()
        with conn:
            conn.execute(
                "INSERT INTO prompts(id,text) VALUES(1,'positive'),(2,'negative')"
            )
            conn.execute(
                "INSERT INTO prompt_pairs(id,positive_prompt_id,negative_prompt_id) "
                "VALUES(7,1,2)"
            )
        start = self.capture(db)
        original = db.get_execution_contexts(start.run_id)["contexts"][0]
        self.assertEqual(
            (
                original["positive_prompt_id"],
                original["negative_prompt_id"],
                original["pair_id"],
            ),
            (1, 2, 7),
        )
        db.archive_prompt_pair(7)
        self.assertEqual(
            db.get_execution_contexts(start.run_id)["contexts"][0], original
        )
        db.delete_prompt_pair(7)
        db.update_prompt_text(1, "edited")
        read = db.get_execution_contexts(start.run_id)["contexts"][0]
        self.assertIsNone(read["positive_prompt_id"])
        self.assertIsNone(read["pair_id"])
        db.delete_prompt(2)
        with conn:
            conn.execute("INSERT INTO prompts(id,text) VALUES(2,'reused')")
        self.assertIsNone(
            db.get_execution_contexts(start.run_id)["contexts"][0]["negative_prompt_id"]
        )
        self.assertEqual(db.prune_orphaned_prompts(), 2)
        after = db.get_execution_contexts(start.run_id)["contexts"][0]
        for key in ("positive", "negative", "evidence", "context_id"):
            self.assertEqual(after[key], original[key])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM prompts").fetchone()[0], 0)
        self.assertIsNone(context_schema_error(conn))

    def test_prune_keeps_prompts_referenced_by_recorded_runs(self):
        db = self.database()
        conn = db.model.get_connection()
        with conn:
            conn.execute(
                "INSERT INTO prompts(id,text) VALUES(1,'positive'),(2,'negative'),"
                "(3,'unreferenced')"
            )
        start = self.capture(db)
        original = db.get_execution_contexts(start.run_id)["contexts"][0]
        self.assertEqual(
            (original["positive_prompt_id"], original["negative_prompt_id"]), (1, 2)
        )
        self.assertEqual(db.prune_orphaned_prompts(), 1)
        self.assertEqual(
            [row[0] for row in conn.execute("SELECT id FROM prompts ORDER BY id")],
            [1, 2],
        )
        self.assertEqual(
            db.get_execution_contexts(start.run_id)["contexts"][0], original
        )
        self.assertEqual(db.prune_orphaned_prompts(), 0)
        db.update_prompt_text(2, "edited")
        self.assertEqual(db.prune_orphaned_prompts(), 1)
        self.assertEqual(
            [row[0] for row in conn.execute("SELECT id FROM prompts")], [1]
        )
        self.assertIsNone(context_schema_error(conn))

    def test_prune_ignores_hints_made_ambiguous_by_identical_text(self):
        db = self.database()
        conn = db.model.get_connection()
        with conn:
            conn.execute(
                "INSERT INTO prompts(id,text) VALUES(1,'positive'),(2,'negative')"
            )
        start = self.capture(db)
        context = db.get_execution_contexts(start.run_id)["contexts"][0]
        self.assertEqual(context["negative_prompt_id"], 2)
        with conn:
            conn.execute("INSERT INTO prompts(id,text) VALUES(3,'negative')")
        db.link_image_to_prompt(3, "/out/three.png")
        context = db.get_execution_contexts(start.run_id)["contexts"][0]
        self.assertEqual(
            (context["positive_prompt_id"], context["negative_prompt_id"]), (1, None)
        )
        self.assertEqual(db.prune_orphaned_prompts(), 1)
        self.assertEqual(
            [row[0] for row in conn.execute("SELECT id FROM prompts ORDER BY id")],
            [1, 3],
        )
        after = db.get_execution_contexts(start.run_id)["contexts"][0]
        self.assertEqual(
            (after["positive_prompt_id"], after["negative_prompt_id"]), (1, None)
        )
        self.assertEqual(db.prune_orphaned_prompts(), 0)

    def test_live_hint_set_matches_reader_hints(self):
        db = self.database()
        conn = db.model.get_connection()
        with conn:
            conn.execute(
                "INSERT INTO prompts(id,text) VALUES(1,'positive'),(2,'negative'),"
                "(4,'other')"
            )
        runs = [self.capture(db, 0)]
        runs.append(self.capture(db, 1, bundle(positive="other", negative="none")))

        def assert_same():
            reader = {
                context[key]
                for run in runs
                for context in db.get_execution_contexts(run.run_id)["contexts"]
                for key in ("positive_prompt_id", "negative_prompt_id")
                if context[key] is not None
            }
            live = {row[0] for row in conn.execute(LIVE_HINT_PROMPT_IDS_SQL)}
            self.assertEqual(live, reader)
            return live

        self.assertEqual(assert_same(), {1, 2, 4})
        with conn:
            conn.execute("INSERT INTO prompts(id,text) VALUES(3,'negative')")
        self.assertEqual(assert_same(), {1, 4})
        with conn:
            conn.execute("DELETE FROM prompts WHERE id=3")
        self.assertEqual(assert_same(), {1, 2, 4})
        db.update_prompt_text(4, "Other")
        self.assertEqual(assert_same(), {1, 2})
        with conn:
            conn.execute("INSERT INTO prompts(id,text) VALUES(5,'none')")
        self.assertEqual(assert_same(), {1, 2})
        rotate_history_epoch(conn)
        self.assertEqual(assert_same(), set())

    def test_hints_resolve_the_stored_form_of_outer_whitespace_raw_text(self):
        # #438: the Library stores text.strip(); the raw run text keeps its spaces.
        db = self.database()
        conn = db.model.get_connection()
        with conn:
            conn.execute(
                "INSERT INTO prompts(id,text) VALUES(1,'positive'),(2,'negative')"
            )
            conn.execute(
                "INSERT INTO prompt_pairs(id,positive_prompt_id,negative_prompt_id) "
                "VALUES(7,1,2)"
            )
        start = self.capture(db, 0, bundle(" negative\n", positive="positive\n\t"))
        context = db.get_execution_contexts(start.run_id)["contexts"][0]
        self.assertEqual(context["positive"]["raw"], "positive\n\t")
        self.assertEqual(
            (
                context["positive_prompt_id"],
                context["negative_prompt_id"],
                context["pair_id"],
            ),
            (1, 2, 7),
        )
        live = {row[0] for row in conn.execute(LIVE_HINT_PROMPT_IDS_SQL)}
        self.assertEqual(live, {1, 2})
        stored = conn.execute(
            "SELECT positive_prompt_id FROM execution_run_contexts WHERE run_id=?",
            (start.run_id,),
        ).fetchone()[0]
        self.assertEqual(stored, 1)
        # A legacy row that kept outer whitespace keeps its exact-text hint and
        # with it the recorded-run protection from pruning.
        with conn:
            conn.execute("INSERT INTO prompts(id,text) VALUES(3,' legacy ')")
        legacy = self.capture(db, 1, bundle("negative", positive=" legacy "))
        hinted = db.get_execution_contexts(legacy.run_id)["contexts"][0]
        self.assertEqual(hinted["positive_prompt_id"], 3)
        live = {row[0] for row in conn.execute(LIVE_HINT_PROMPT_IDS_SQL)}
        self.assertEqual(live, {1, 2, 3})
        self.assertEqual(db.prune_orphaned_prompts(), 0)
        # Both forms present make the hint ambiguous, in both rule forms.
        with conn:
            conn.execute("INSERT INTO prompts(id,text) VALUES(4,'legacy')")
        hinted = db.get_execution_contexts(legacy.run_id)["contexts"][0]
        self.assertIsNone(hinted["positive_prompt_id"])
        live = {row[0] for row in conn.execute(LIVE_HINT_PROMPT_IDS_SQL)}
        self.assertEqual(live, {1, 2})

    def test_hints_require_exact_unique_raw_text_and_never_refresh(self):
        db = self.database()
        conn = db.model.get_connection()
        with conn:
            conn.execute(
                "INSERT INTO prompts(id,text) VALUES(1,' positive "
                "'),(2,'negative'),(3,'negative')"
            )
        start = self.capture(db)
        read = db.get_execution_contexts(start.run_id)["contexts"][0]
        self.assertIsNone(read["positive_prompt_id"])
        self.assertIsNone(read["negative_prompt_id"])
        with conn:
            conn.execute("DELETE FROM prompts WHERE id=3")
            conn.execute("UPDATE prompts SET text='positive' WHERE id=1")
        db.write_execution_contexts(start.run_id, start.storage_epoch, bundle())
        self.assertEqual(db.get_execution_contexts(start.run_id)["contexts"], [read])
        fresh = self.capture(db, 1)
        self.assertEqual(
            db.get_execution_contexts(fresh.run_id)["contexts"][0][
                "positive_prompt_id"
            ],
            1,
        )
        with conn:
            conn.execute("INSERT INTO prompts(id,text) VALUES(3,'positive')")
        self.assertIsNone(
            db.get_execution_contexts(fresh.run_id)["contexts"][0]["positive_prompt_id"]
        )

    def test_null_negative_pair_only_matches_a_known_stored_empty(self):
        db = self.database()
        with db.model.get_connection() as conn:
            conn.execute("INSERT INTO prompts(id,text) VALUES(1,'positive')")
            conn.execute("INSERT INTO prompt_pairs(id,positive_prompt_id) VALUES(7,1)")
        # A whitespace-only negative is the stored empty one (#438)
        for item, raw, expected in ((0, "", 7), (1, " \n", 7), (2, None, None)):
            candidate = bundle(negative=raw or "")
            if raw is None:
                ctx = candidate.contexts[0]
                candidate = replace(
                    candidate,
                    contexts=(
                        replace(
                            ctx,
                            negative=replace(
                                ctx.negative,
                                raw=TextEvidence(None, "unknown", "unknown", "missing"),
                            ),
                        ),
                    ),
                    state="partial",
                    reason="partial",
                )
            start = self.capture(db, item, candidate)
            self.assertEqual(
                db.get_execution_contexts(start.run_id)["contexts"][0]["pair_id"],
                expected,
            )

    def test_snapshot_and_context_collisions_roll_back_all_new_rows(self):
        from utils.execution_contexts import content_digest

        for kind in ("snapshot", "context"):
            with self.subTest(kind=kind):
                db = self.database(str(Path(self.temp.name) / f"{kind}.db"))
                first = self.capture(db)
                old_context = db.get_execution_contexts(first.run_id)["contexts"][0][
                    "context_id"
                ]
                old_snapshot = (
                    db.model.get_connection()
                    .execute(
                        "SELECT negative_snapshot_id FROM execution_contexts WHERE "
                        "context_id=?",
                        (old_context,),
                    )
                    .fetchone()[0]
                )
                second = self.start(db, 1)
                db.write_execution_start(second)
                before = tuple(db.model.get_connection().iterdump())

                def digest(current_kind, fields):
                    if current_kind == kind:
                        return old_snapshot if kind == "snapshot" else old_context
                    return content_digest(current_kind, fields)

                with (
                    patch(
                        "database.execution_contexts.content_digest", side_effect=digest
                    ),
                    self.assertRaises(ValueError),
                ):
                    db.write_execution_contexts(
                        second.run_id,
                        second.storage_epoch,
                        bundle("changed", "new-positive"),
                    )
                self.assertEqual(tuple(db.model.get_connection().iterdump()), before)

    def test_parallel_contradictory_replay_keeps_one_first_capture(self):
        db = self.database()
        start = self.start(db)
        db.write_execution_start(start)

        def write(negative):
            return db.write_execution_contexts(
                start.run_id, start.storage_epoch, bundle(negative)
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(write, ("A", "B"))), [True, True])
        read = db.get_execution_contexts(start.run_id)
        self.assertEqual(read["state"], "conflicting")
        self.assertIn(read["contexts"][0]["negative"]["raw"], ("A", "B"))
        self.assertEqual(self.counts(db), (1, 1, 2, 1))

    def test_merge_never_rebinds_a_hint_to_normalized_equal_primary(self):
        db = self.database()
        with db.model.get_connection() as conn:
            conn.execute(
                "INSERT INTO prompts(id,text,created_at) VALUES(1,' POSITIVE "
                "','2020-01-01'),(2,'positive','2021-01-01')"
            )
        start = self.capture(db)
        before = db.get_execution_contexts(start.run_id)["contexts"][0]
        self.assertEqual(before["positive_prompt_id"], 2)
        self.assertEqual(db.cleanup_duplicates(), 1)
        after = db.get_execution_contexts(start.run_id)["contexts"][0]
        self.assertIsNone(after["positive_prompt_id"])
        self.assertEqual(after["positive"], before["positive"])
        self.assertEqual(after["evidence"], before["evidence"])
        self.assertEqual(
            db.model.get_connection().execute("SELECT text FROM prompts").fetchone()[0],
            " POSITIVE ",
        )

    def test_ambiguous_empty_negative_omits_pair_hint(self):
        db = self.database()
        with db.model.get_connection() as conn:
            conn.execute(
                "INSERT INTO prompts(id,text) VALUES(1,'positive'),(2,''),(3,'')"
            )
            conn.execute("INSERT INTO prompt_pairs(id,positive_prompt_id) VALUES(7,1)")
        start = self.capture(db, capture=bundle(negative=""))
        read = db.get_execution_contexts(start.run_id)["contexts"][0]
        self.assertIsNone(read["negative_prompt_id"])
        self.assertIsNone(read["pair_id"])

    def test_replay_checks_scalar_content_even_if_digests_collide(self):
        db = self.database()
        start = self.capture(db)
        before = tuple(db.model.get_connection().iterdump())
        from utils.execution_contexts import content_digest

        original_snapshot = content_digest("snapshot", ("negative", "negative", "", ""))

        def digest(kind, fields):
            if kind == "snapshot" and fields == ("changed", "changed", "", ""):
                return original_snapshot
            return content_digest(kind, fields)

        with (
            patch("database.execution_contexts.content_digest", side_effect=digest),
            self.assertRaises(ValueError),
        ):
            db.write_execution_contexts(
                start.run_id, start.storage_epoch, bundle("changed")
            )
        self.assertEqual(tuple(db.model.get_connection().iterdump()), before)

    def test_package_qualified_capture_with_and_without_repository_on_sys_path(self):
        # A host imports node packs under a package name, not as top-level utils.
        script = textwrap.dedent("""\
            import importlib
            import sys
            import tempfile
            import types
            from pathlib import Path
            root, expose_root = sys.argv[1:]
            if expose_root == "yes":
                sys.path.insert(0, root)
            else:
                assert all(Path(p).resolve() != Path(root) for p in sys.path)
            package = types.ModuleType("context_pack")
            package.__path__ = [root]
            sys.modules[package.__name__] = package
            records = importlib.import_module("context_pack.utils.execution_contexts")
            operations = importlib.import_module("context_pack.database.operations")
            events = importlib.import_module(
                "context_pack.utils.execution_history_events"
            )
            schema = importlib.import_module(
                "context_pack.database.execution_context_schema"
            )
            with tempfile.TemporaryDirectory() as directory:
                db = operations.PromptDatabase(str(Path(directory) / "capture.db"))
                try:
                    start = events.make_start(
                        "11111111-1111-4111-8111-111111111111",
                        ((0, "22222222-2222-4222-8222-222222222222", {}, {}), 0),
                        db.get_execution_epoch(), 100,
                    )
                    db.write_execution_start(start)
                    def known(value):
                        return records.TextEvidence(
                            value, "recorded", "runtime_return", None
                        )
                    snapshot = records.PromptSnapshot(
                        known("raw"), known("effective"), known(""), known("")
                    )
                    context = records.RecipeContext(
                        "4", snapshot, snapshot, "inferred"
                    )
                    bundle = records.ContextBundle(
                        (context,), "complete", "captured"
                    )
                    assert db.write_execution_contexts(
                        start.run_id, start.storage_epoch, bundle
                    )
                    read = db.get_execution_contexts(start.run_id)
                    assert read["state"] == "complete", read
                    assert read["contexts"][0]["positive"]["raw"] == "raw", read
                    role = read["contexts"][0]["positive"]
                    assert role["effective"] == "effective", read
                    error = schema.context_schema_error(db.model.get_connection())
                    assert error is None, error
                    verified = db.model.verify_database_file(db.model.db_path)
                    assert verified == (True, "ok"), verified
                    if expose_root == "no":
                        assert "utils.execution_contexts" not in sys.modules
                        assert "database.execution_context_schema" not in sys.modules
                finally:
                    db.close_all()
            """)
        root = str(Path(__file__).resolve().parents[1])
        for expose_root in ("yes", "no"):
            with self.subTest(repository_on_sys_path=expose_root):
                result = subprocess.run(
                    [sys.executable, "-I", "-c", script, root, expose_root],
                    cwd=self.temp.name,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
