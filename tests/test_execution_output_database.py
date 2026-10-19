"""Frozen output occurrences belong to an exact stored execution attempt."""

import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from threading import Barrier
from unittest.mock import patch

from database.execution_history import HistoryEpochChanged
from utils.execution_outputs import OutputBundle, OutputReference

try:
    from tests import test_execution_history_database as history_tests
except ImportError:
    import test_execution_history_database as history_tests


def bundle(
    filename="first.png", *, root="a" * 64, node="9", ordinal=0, path_style="posix"
):
    return OutputBundle(
        (OutputReference(node, ordinal, "output", "", filename, path_style, root),),
        "complete" if root else "partial",
        "reported" if root else "root_unavailable",
    )


class OutputDatabaseTests(unittest.TestCase):
    setUp = history_tests.HistoryDatabaseTests.setUp
    close_databases = history_tests.HistoryDatabaseTests.close_databases
    database = history_tests.HistoryDatabaseTests.database
    start = history_tests.HistoryDatabaseTests.start
    finish = history_tests.HistoryDatabaseTests.finish

    def capture(self, db, value=None, item=0):
        start = self.start(db, item)
        db.write_execution_start(start)
        self.assertTrue(
            db.write_execution_outputs_for_finish(self.finish(start), value or bundle())
        )
        return start

    def rows(self, db):
        return [
            tuple(row)
            for row in db.model.get_connection().execute(
                "SELECT * FROM execution_run_outputs ORDER BY run_id,"
                "output_node_id,image_ordinal"
            )
        ]

    def test_exact_attempt_parent_not_recomputed_uuid(self):
        db = self.database()
        stored_id = "55555555-5555-4555-8555-555555555555"
        start = replace(self.start(db), run_id=stored_id)
        db.write_execution_start(start)
        self.assertTrue(
            db.write_execution_outputs_for_finish(self.finish(start), bundle())
        )
        self.assertEqual(self.rows(db)[0][0], stored_id)
        self.assertEqual(db.get_execution_outputs(stored_id)["state"], "complete")
        other = self.capture(db, item=1)
        self.assertEqual(len(self.rows(db)), 2)
        self.assertEqual(
            db.get_execution_outputs(other.run_id)["references"][0]["filename"],
            "first.png",
        )
        self.assertEqual(db.get_execution_run(stored_id)["outcome"], "observed")

    def test_exact_replay_noop_and_conflict_sticky(self):
        db = self.database()
        first = bundle()
        refs = (replace(first.references[0], output_node_id="2"),) + (
            replace(first.references[0], image_ordinal=0),
            replace(first.references[0], image_ordinal=1),
        )
        first = replace(first, references=refs)
        start = self.capture(db, first)
        finish = self.finish(start)
        original = self.rows(db)
        # Construct identical DTOs from dictionaries with different insertion orders.
        reordered = tuple(
            OutputReference(**dict(reversed(list(vars(ref).items())))) for ref in refs
        )
        self.assertTrue(
            db.write_execution_outputs_for_finish(
                finish, replace(first, references=reordered)
            )
        )
        self.assertEqual(db.get_execution_outputs(start.run_id)["state"], "complete")
        self.assertEqual(self.rows(db), original)
        self.assertTrue(
            db.write_execution_outputs_for_finish(finish, bundle("other.png"))
        )
        capture = db.get_execution_outputs(start.run_id)
        self.assertEqual(
            (capture["state"], capture["reason"]), ("conflicting", "conflicting")
        )
        self.assertEqual(
            [r["filename"] for r in capture["references"]], ["first.png"] * 3
        )
        self.assertEqual(capture["references"][0]["context_membership"], "unknown")
        for replay in (first, bundle("third.png")):
            db.write_execution_outputs_for_finish(finish, replay)
            self.assertEqual(db.get_execution_outputs(start.run_id), capture)
            self.assertEqual(self.rows(db), original)

    def test_header_and_every_reference_scalar_participate_in_replay(self):
        variants = (
            replace(bundle(), state="partial", reason="unsupported"),
            bundle(root="b" * 64),
            bundle(node="8"),
            bundle(ordinal=1),
            replace(
                bundle(),
                references=(replace(bundle().references[0], folder_type="temp"),),
            ),
            replace(
                bundle(),
                references=(replace(bundle().references[0], subfolder="nested"),),
            ),
            replace(
                bundle(),
                references=(replace(bundle().references[0], path_style="windows"),),
            ),
            OutputBundle((), "complete", "none_reported"),
        )
        db = self.database()
        for item, variant in enumerate(variants):
            with self.subTest(item=item):
                start = self.capture(db, item=item)
                db.write_execution_outputs_for_finish(self.finish(start), variant)
                capture = db.get_execution_outputs(start.run_id)
                self.assertEqual(capture["state"], "conflicting")
                self.assertEqual(capture["references"][0]["root_key"], "a" * 64)
                self.assertEqual(capture["references"][0]["output_node_id"], "9")

    def test_concurrent_replay_keeps_first_rows(self):
        db = self.database()
        start = self.start(db)
        db.write_execution_start(start)
        finish = self.finish(start)
        peers = [self.database() for _ in range(4)]
        barrier = Barrier(len(peers))
        hold = history_tests.FirstWriterHold()

        @contextmanager
        def held_open():
            hold()
            yield True

        def write(index):
            barrier.wait(timeout=60)
            return peers[index].write_execution_outputs_for_finish(
                finish,
                bundle("first.png" if index % 2 else "second.png"),
                generation_guard=held_open,
            )

        with (
            history_tests.queued_writer_budget(),
            ThreadPoolExecutor(max_workers=4) as executor,
        ):
            self.assertEqual(list(executor.map(write, range(4))), [True] * 4)
        capture = db.get_execution_outputs(start.run_id)
        self.assertEqual(
            (capture["state"], capture["reason"]), ("conflicting", "conflicting")
        )
        self.assertEqual(len(capture["references"]), 1)
        frozen = self.rows(db)
        for value in (bundle(), bundle("second.png")):
            db.write_execution_outputs_for_finish(finish, value)
            self.assertEqual(self.rows(db), frozen)

    def test_epoch_and_generation_guard_cover_commit(self):
        db = self.database()
        start = self.start(db)
        db.write_execution_start(start)
        observations = []
        conn = db.model.get_connection()
        active = False

        @contextmanager
        def guard():
            nonlocal active
            self.assertTrue(conn.in_transaction)
            active = True
            try:
                yield True
            finally:
                observations.append(("exit", conn.in_transaction))
                active = False

        def trace(sql):
            if sql == "COMMIT":
                observations.append(("commit", active))

        conn.set_trace_callback(trace)
        self.addCleanup(conn.set_trace_callback, None)
        self.assertTrue(
            db.write_execution_outputs_for_finish(
                self.finish(start), bundle(), generation_guard=guard
            )
        )
        self.assertEqual(observations, [("commit", True), ("exit", False)])
        self.assertFalse(active)

        @contextmanager
        def stale():
            yield False

        other = self.start(db, item=1)
        db.write_execution_start(other)
        self.assertFalse(
            db.write_execution_outputs_for_finish(
                self.finish(other), bundle(), generation_guard=stale
            )
        )
        self.assertEqual(
            db.get_execution_outputs(other.run_id)["state"], "not_captured"
        )
        old_epoch = start.storage_epoch
        with conn:
            conn.execute(
                "UPDATE execution_history_metadata SET storage_epoch=?",
                ("66666666-6666-4666-8666-666666666666",),
            )
        with self.assertRaises(HistoryEpochChanged):
            db.write_execution_outputs_for_finish(
                self.finish(start), bundle("late.png")
            )
        self.assertEqual(db.get_execution_run(start.run_id)["storage_epoch"], old_epoch)
        self.assertEqual(
            db.get_execution_outputs(start.run_id)["references"][0]["filename"],
            "first.png",
        )

    def test_failure_rolls_back_header_and_keeps_guard_until_rollback(self):
        db = self.database()
        start = self.start(db)
        db.write_execution_start(start)
        conn = db.model.get_connection()
        with conn:
            conn.execute(
                "CREATE TRIGGER reject_output BEFORE INSERT ON execution_run_outputs "
                "BEGIN SELECT RAISE(ABORT,'injected'); END"
            )
        states = []

        @contextmanager
        def guard():
            states.append(conn.in_transaction)
            try:
                yield True
            finally:
                states.append(conn.in_transaction)

        with self.assertRaises(sqlite3.IntegrityError):
            db.write_execution_outputs_for_finish(
                self.finish(start), bundle(), generation_guard=guard
            )
        self.assertEqual(states, [True, False])
        self.assertEqual(
            db.get_execution_outputs(start.run_id)["state"], "not_captured"
        )
        self.assertEqual(self.rows(db), [])

    def test_missing_header_or_parent_rejected(self):
        db = self.database()
        start = self.start(db)
        self.assertFalse(
            db.write_execution_outputs_for_finish(self.finish(start), bundle())
        )
        self.assertEqual(
            db.get_execution_outputs(start.run_id),
            {
                "state": "not_captured",
                "reason": None,
                "format_version": None,
                "source": None,
                "references": [],
            },
        )
        db.write_execution_start(start)
        conn = db.model.get_connection()
        conn.execute("PRAGMA foreign_keys=OFF")
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 0)
        with conn:
            conn.execute(
                "INSERT INTO execution_run_outputs "
                "VALUES(?, '9',0,'output','','orphan.png','posix',?)",
                (start.run_id, "a" * 64),
            )
        with self.assertRaises(sqlite3.DatabaseError):
            db.get_execution_outputs(start.run_id)
        with self.assertRaises(sqlite3.DatabaseError):
            db.write_execution_outputs_for_finish(self.finish(start), bundle())
        self.assertEqual(
            conn.execute("SELECT count(*) FROM execution_output_capture").fetchone()[0],
            0,
        )
        with conn:
            conn.execute("DELETE FROM execution_run_outputs")
        db.write_execution_outputs_for_finish(self.finish(start), bundle())
        with conn:
            conn.execute("DELETE FROM execution_runs")
        with self.assertRaises(sqlite3.DatabaseError):
            db.get_execution_outputs(start.run_id)

    def test_reads_never_access_files_or_rebind(self):
        db = self.database()
        value = bundle(root=None)
        value = replace(
            value,
            references=(
                replace(
                    value.references[0],
                    folder_type="temp",
                    subfolder=" Exact Ω ",
                    filename="Keep Case.png",
                ),
            ),
        )
        start = self.capture(db, value)
        conn = db.model.get_connection()
        before = list(conn.iterdump())
        with (
            patch("builtins.open", side_effect=AssertionError("file read")),
            patch("os.stat", side_effect=AssertionError("stat")),
            patch("os.path.realpath", side_effect=AssertionError("resolve")),
        ):
            capture = db.get_execution_outputs(start.run_id)
        self.assertEqual(
            capture,
            {
                "state": "partial",
                "reason": "root_unavailable",
                "format_version": 1,
                "source": "host_final_history",
                "references": [
                    {
                        "output_node_id": "9",
                        "image_ordinal": 0,
                        "folder_type": "temp",
                        "subfolder": " Exact Ω ",
                        "filename": "Keep Case.png",
                        "path_style": "posix",
                        "root_key": None,
                        "file_identity": "unverified",
                        "newly_generated": "unknown",
                        "context_membership": "unknown",
                        "transient": True,
                    }
                ],
            },
        )
        self.assertEqual(list(conn.iterdump()), before)
        for table in (
            "prompts",
            "prompt_pairs",
            "generated_images",
            "execution_run_contexts",
        ):
            self.assertEqual(
                conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0
            )

    def test_read_uses_consistent_snapshot_and_preserves_callers_transaction(self):
        db = self.database()
        start = self.capture(db)
        peer = self.database()
        conn = db.model.get_connection()
        changed = False

        def trace(sql):
            nonlocal changed
            if "FROM execution_run_outputs" in sql and not changed:
                changed = True
                with peer.model.get_connection() as writer:
                    writer.execute(
                        "UPDATE execution_run_outputs SET filename='later.png'"
                    )

        conn.set_trace_callback(trace)
        self.addCleanup(conn.set_trace_callback, None)
        self.assertEqual(
            db.get_execution_outputs(start.run_id)["references"][0]["filename"],
            "first.png",
        )
        conn.set_trace_callback(None)
        self.assertTrue(changed)
        self.assertFalse(conn.in_transaction)
        self.assertEqual(
            db.get_execution_outputs(start.run_id)["references"][0]["filename"],
            "later.png",
        )
        conn.execute("BEGIN")
        db.get_execution_outputs(start.run_id)
        self.assertTrue(conn.in_transaction)
        conn.rollback()

    def test_empty_unavailable_and_partial_headers_remain_distinct(self):
        db = self.database()
        for item, (state, reason) in enumerate(
            (
                ("complete", "none_reported"),
                ("unavailable", "unavailable"),
                ("partial", "unsupported"),
            )
        ):
            start = self.capture(db, OutputBundle((), state, reason), item)
            capture = db.get_execution_outputs(start.run_id)
            self.assertEqual(
                (capture["state"], capture["reason"], capture["references"]),
                (state, reason, []),
            )

    def test_invalid_scalars_rejected_before_transaction(self):
        db = self.database()
        start = self.start(db)
        db.write_execution_start(start)
        trace = []
        conn = db.model.get_connection()
        conn.set_trace_callback(trace.append)
        self.addCleanup(conn.set_trace_callback, None)
        for invalid in (
            replace(
                bundle(),
                references=(replace(bundle().references[0], filename="../unsafe.png"),),
            ),
            replace(bundle(), source="filesystem_scan"),
        ):
            with self.assertRaises(ValueError):
                db.write_execution_outputs_for_finish(self.finish(start), invalid)
        with self.assertRaises(ValueError):
            db.write_execution_outputs_for_finish(start, bundle())
        self.assertEqual(trace, [])
