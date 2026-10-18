"""Bounded chronological projections preserve frozen evidence and exact coordinates."""

import hashlib
import json
import sqlite3
import unittest
from dataclasses import replace
from unittest.mock import patch

from database.execution_history import HistoryEpochChanged
from database.execution_history_schema import history_schema_error
from utils.execution_contexts import ContextBundle, RecipeContext, TextEvidence
from utils.execution_outputs import OutputBundle
from tests.test_execution_context_database import bundle, snapshot
from tests.test_execution_output_database import bundle as outputs
from tests import test_execution_history_database as history_tests


class HistoryReadTests(unittest.TestCase):
    setUp = history_tests.HistoryDatabaseTests.setUp
    close_databases = history_tests.HistoryDatabaseTests.close_databases
    database = history_tests.HistoryDatabaseTests.database
    start = history_tests.HistoryDatabaseTests.start
    finish = history_tests.HistoryDatabaseTests.finish

    def saved(self, db, text="positive", identity=1):
        with db.model.get_connection() as conn:
            conn.execute("INSERT INTO prompts(id,text) VALUES(?,?)", (identity, text))
        return identity

    def capture(self, db, item=0, context=None, output=None, time=100):
        start = self.start(db, item, time)
        db.write_execution_start(start)
        db.write_execution_finish(self.finish(start))
        if context is not False:
            db.write_execution_contexts(
                start.run_id, start.storage_epoch, context or bundle()
            )
        if output is not None:
            db.write_execution_outputs_for_finish(self.finish(start), output)
        return start

    def require_reads(self, db):
        self.assertTrue(
            callable(getattr(db, "recorded_history_snapshot", None)),
            "PromptDatabase must expose bounded history reads",
        )

    def test_aab_distinct_runs_exact_raw_scope(self):
        db = self.database()
        self.saved(db)
        self.saved(db, identity=2)
        with db.model.get_connection() as conn:
            conn.execute("UPDATE prompts SET run_count=31")
            conn.execute(
                "INSERT INTO generated_images(prompt_id,image_path,filename) "
                "VALUES(1,'/first','first'),(2,'/second','second')"
            )
        a = bundle("A").contexts[0]
        b = bundle("B").contexts[0]
        starts = [
            self.capture(
                db, 0, replace(bundle("A"), contexts=(a, replace(a, anchor_id="5")))
            ),
            self.capture(
                db, 1, replace(bundle("A"), contexts=(a, replace(b, anchor_id="6")))
            ),
            self.capture(db, 2, bundle("B"), outputs()),
        ]
        conn = db.model.get_connection()
        tables = ("prompts", "generated_images", "prompt_pairs", "image_prompt_pairs")
        before = {t: list(conn.execute(f"SELECT * FROM {t}")) for t in tables}
        self.require_reads(db)
        with db.recorded_history_snapshot():
            page = db.list_history_window(1)
            duplicate = db.list_history_window(2)
        self.assertEqual(page["recorded_run_count"], 3)
        self.assertEqual(
            [r["run_id"] for r in page["items"]], [s.run_id for s in starts]
        )
        self.assertEqual(len(page["context_summaries"]), 2)
        self.assertEqual(
            page["group"]["raw_text_id"], duplicate["group"]["raw_text_id"]
        )
        self.assertEqual(len(page["items"][0]["matching_anchors"]), 2)
        self.assertEqual(
            len(page["context_summaries"][0]["negative_raw"]["evidence"]), 3
        )
        self.assertEqual(
            page["items"][0]["output_summary"]["list_status"], "not_captured"
        )
        self.assertEqual(
            page["items"][2]["output_summary"]["retained_occurrence_count"], 1
        )
        self.assertEqual(
            before, {t: list(conn.execute(f"SELECT * FROM {t}")) for t in tables}
        )

    def test_raw_scope_distinctions_and_eligibility(self):
        db = self.database()
        values = (
            "positive",
            "Positive",
            " positive",
            "positive ",
            "é",
            "e\u0301",
            "",
            " ",
        )
        for i, raw in enumerate(values, 1):
            self.saved(db, raw, i)
            self.capture(db, i, bundle(positive=raw))
        modified = replace(
            snapshot("positive"),
            effective=snapshot("other").raw,
            prepend=snapshot("prefix").raw,
        )
        self.capture(
            db,
            50,
            replace(
                bundle(),
                contexts=(RecipeContext("4", modified, snapshot(""), "inferred"),),
            ),
        )
        unknown = replace(
            snapshot("positive"),
            raw=TextEvidence(None, "unknown", "unknown", "missing"),
        )
        self.capture(
            db,
            51,
            ContextBundle(
                (RecipeContext("4", unknown, snapshot(""), "inferred"),),
                "partial",
                "captured",
            ),
        )
        self.saved(db, "x" * 65537, 99)
        self.saved(db, "negative-only", 100)
        with db.model.get_connection() as conn:
            conn.execute(
                "INSERT INTO prompt_pairs(positive_prompt_id,negative_prompt_id) "
                "VALUES(1,100)"
            )
        self.require_reads(db)
        with db.recorded_history_snapshot():
            for i in range(1, len(values) + 1):
                self.assertEqual(
                    db.list_history_window(i)["recorded_run_count"], 2 if i == 1 else 1
                )
            self.assertIsNone(db.list_history_window(100))
            self.assertIsNone(db.list_history_window(101))
            page = db.list_history_window(99)
            self.assertEqual(page["group"]["scope_status"], "uncapturable_text")
            self.assertIsNone(page["group"]["raw_text_id"])
            self.assertEqual(page["recorded_run_count"], 0)

    def test_prefix_is_sql_bounded_and_nul_safe(self):
        db = self.database()
        self.saved(db)
        negative = "A\0猫😀" + "z" * 20000
        self.capture(db, context=bundle(negative))
        self.capture(db, 1, bundle("A" + "😀" * 128))
        self.require_reads(db)
        traced = []
        conn = db.model.get_connection()
        conn.set_trace_callback(traced.append)
        self.addCleanup(conn.set_trace_callback, None)
        original_factory = conn.row_factory
        prefix_results = []

        def bounded_rows(cursor, values):
            names = [column[0] for column in cursor.description]
            if "prefix" in names:
                prefix = values[names.index("prefix")]
                prefix_results.append(prefix)
                self.assertLessEqual(len(prefix), 512)
            return sqlite3.Row(cursor, values)

        conn.row_factory = bounded_rows
        self.addCleanup(setattr, conn, "row_factory", original_factory)
        with patch.object(
            db, "get_execution_contexts", side_effect=AssertionError("full reader")
        ):
            with db.recorded_history_snapshot():
                page = db.list_history_window(1)
        raw = page["context_summaries"][0]["negative_raw"]
        self.assertEqual(len(prefix_results), 2)
        self.assertEqual(
            page["context_summaries"][1]["negative_raw"]["preview"],
            ("A" + "😀" * 128)[:100],
        )
        self.assertEqual(raw["preview"], negative[:100])
        self.assertLessEqual(len(raw["preview"].encode()), 512)
        self.assertTrue(raw["truncated"])
        text_queries = [s.lower().replace(" ", "") for s in traced if "text_value" in s]
        self.assertTrue(
            any("substr(cast(" in s and "asblob),1,512)" in s for s in text_queries)
        )
        self.assertFalse(
            any(
                "selectr.text_value" in s or "select*fromexecution_context_texts" in s
                for s in text_queries
            )
        )

    def test_empty_unknown_and_per_anchor_evidence(self):
        db = self.database()
        self.saved(db)
        a = bundle("").contexts[0]
        cached = replace(
            a,
            negative=replace(
                a.negative, raw=replace(a.negative.raw, source="host_cache_output")
            ),
        )
        self.capture(db, 0, replace(bundle(""), contexts=(a,)))
        self.capture(db, 1, replace(bundle(""), contexts=(cached,)))
        self.capture(
            db, 2, ContextBundle((replace(a, negative=None),), "partial", "captured")
        )
        self.require_reads(db)
        with db.recorded_history_snapshot():
            summaries = db.list_history_window(1)["context_summaries"]
        raw = summaries[0]["negative_raw"]
        self.assertEqual(
            (raw["state"], raw["preview"], raw["truncated"]),
            ("explicit_empty", "", False),
        )
        self.assertEqual(
            {e["source"] for e in raw["evidence"]},
            {"runtime_return", "host_cache_output"},
        )
        unknown = summaries[1]["negative_raw"]
        self.assertEqual(
            (unknown["state"], unknown["preview"], unknown["truncated"]),
            ("unknown", None, False),
        )

    def test_four_key_paging_upper_bound_and_vacuum(self):
        db = self.database()
        self.saved(db)
        starts = [self.capture(db, i, time=2**53 + 7) for i in range(7)]
        self.require_reads(db)
        with db.recorded_history_snapshot():
            page = db.list_history_window(1, limit=2)
        self.assertEqual([r["queue_item_id"] for r in page["items"]], [5, 6])
        self.assertTrue(page["has_more"])
        self.capture(db, 8, time=2**53 + 8)
        db.model.get_connection().execute("VACUUM")
        ids = [r["run_id"] for r in page["items"]]
        while page["has_more"]:
            with db.recorded_history_snapshot():
                page = db.list_history_window(1, limit=2, after=page["next_position"])
            self.assertEqual(page["recorded_run_count"], 7)
            ids = [r["run_id"] for r in page["items"]] + ids
        self.assertEqual(ids, [s.run_id for s in starts])
        with db.model.get_connection() as conn:
            conn.execute("UPDATE prompts SET text='changed' WHERE id=1")
        from database.history_reads import WindowPosition

        with db.recorded_history_snapshot():
            with self.assertRaises(ValueError):
                db.list_history_window(
                    1,
                    after=WindowPosition(
                        "0" * 64,
                        (1, starts[0].session_id, 0, starts[0].run_id),
                        (1, starts[0].session_id, 0, starts[0].run_id),
                    ),
                )

    def test_snapshot_epoch_first_ownership_and_rollback(self):
        db = self.database()
        self.require_reads(db)
        conn = db.model.get_connection()
        for read in (
            lambda: db.list_history_window(1),
            lambda: db.get_history_run("missing"),
            lambda: db.list_history_outputs("missing"),
            lambda: db.get_history_occurrence("missing", "9", 0),
        ):
            with self.assertRaises(sqlite3.ProgrammingError):
                read()
        conn.execute("BEGIN")
        with self.assertRaises(sqlite3.ProgrammingError):
            with db.recorded_history_snapshot():
                pass
        self.assertTrue(conn.in_transaction)
        conn.rollback()
        trace = []
        conn.set_trace_callback(trace.append)
        with self.assertRaisesRegex(RuntimeError, "abort"):
            with db.recorded_history_snapshot() as epoch:
                self.assertEqual(epoch, db.get_execution_epoch())
                db.get_execution_contexts("missing")
                db.get_execution_outputs("missing")
                self.assertTrue(conn.in_transaction)
                raise RuntimeError("abort")
        conn.set_trace_callback(None)
        self.assertIn("storage_epoch", next(q for q in trace if q.startswith("SELECT")))
        self.assertFalse(conn.in_transaction)
        with self.assertRaises(HistoryEpochChanged):
            with db.recorded_history_snapshot("wrong"):
                pass
        self.assertFalse(conn.in_transaction)

    def test_detail_preserves_full_text_snapshots_hints_and_archive(self):
        db = self.database()
        self.saved(db)
        self.saved(db, "negative", 2)
        with db.model.get_connection() as conn:
            conn.execute(
                "INSERT INTO prompt_pairs(id,positive_prompt_id,negative_prompt_id) "
                "VALUES(7,1,2)"
            )
        start = self.capture(db, output=outputs())
        legacy = db.get_execution_contexts(start.run_id)
        self.require_reads(db)
        with db.recorded_history_snapshot():
            detail = db.get_history_run(start.run_id)
            self.assertIsNone(db.get_history_run("missing"))
        c = detail["contexts"][0]
        self.assertTrue(c["positive_snapshot_id"])
        self.assertTrue(c["negative_snapshot_id"])
        self.assertEqual(c["pair_id"], 7)
        self.assertEqual(
            c["navigation"],
            dict(positive_available=True, negative_available=True, pair_available=True),
        )
        self.assertEqual(c["positive"], legacy["contexts"][0]["positive"])
        db.archive_prompt_pair(7)
        with db.recorded_history_snapshot():
            c = db.get_history_run(start.run_id)["contexts"][0]
        self.assertEqual(c["pair_id"], 7)
        self.assertFalse(c["navigation"]["pair_available"])
        with db.model.get_connection() as conn:
            conn.execute(
                "UPDATE execution_history_metadata SET storage_epoch=?",
                ("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",),
            )
        with db.recorded_history_snapshot():
            detail = db.get_history_run(start.run_id)
        self.assertEqual(detail["run"]["storage_epoch"], start.storage_epoch)
        self.assertEqual(detail["contexts"][0]["reference_epoch"], start.storage_epoch)
        self.assertIsNone(detail["contexts"][0]["positive_prompt_id"])

    def test_output_coordinates_fingerprint_and_capture_states(self):
        db = self.database()
        ref = outputs().references[0]
        refs = tuple(
            replace(ref, output_node_id=n, image_ordinal=i)
            for n, i in (("2", 0), ("9", 0), ("9", 1), ("😀", 0))
        )
        start = self.capture(db, output=replace(outputs(), references=refs))
        self.require_reads(db)
        with db.recorded_history_snapshot():
            page = db.list_history_outputs(start.run_id, limit=2)
            last = db.list_history_outputs(
                start.run_id, limit=2, after=page["next_position"]
            )
            one = db.get_history_occurrence(start.run_id, "9", 0)
            self.assertIsNone(db.get_history_occurrence(start.run_id, "9", 8))
            self.assertIsNone(db.list_history_outputs("missing"))
        items = page["items"] + last["items"]
        self.assertEqual(
            [
                (i["occurrence"]["output_node_id"], i["occurrence"]["image_ordinal"])
                for i in items
            ],
            [(r.output_node_id, r.image_ordinal) for r in refs],
        )
        self.assertEqual(one["receipt"], items[1])
        self.assertEqual(one["capture"], page["capture"])
        self.assertEqual(page["retained_occurrence_count"], 4)
        self.assertEqual(
            page["capture_fingerprint"],
            hashlib.sha256(
                json.dumps(
                    [1, "complete", "reported", 1, "host_final_history", 4],
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
        )
        self.assertEqual(items[0]["file_identity"], "unverified")
        self.assertEqual(items[0]["context_membership"], "unknown")
        db.write_execution_outputs_for_finish(
            self.finish(start), outputs("different.png")
        )
        with db.recorded_history_snapshot():
            from database import history_reads

            changed = getattr(history_reads, "HistoryCaptureChanged", None)
            self.assertTrue(
                isinstance(changed, type), "capture drift needs a distinct exception"
            )
            self.assertTrue(issubclass(changed, ValueError))
            with self.assertRaises(changed):
                db.list_history_outputs(start.run_id, after=page["next_position"])
            self.assertEqual(
                db.list_history_outputs(start.run_id)["list_status"], "incomplete"
            )
        for index, capture in enumerate(
            (
                None,
                OutputBundle((), "complete", "none_reported"),
                OutputBundle((), "unavailable", "unavailable"),
                replace(outputs(), state="partial", reason="unsupported"),
            ),
            1,
        ):
            s = self.capture(db, index, output=capture)
            with db.recorded_history_snapshot():
                p = db.list_history_outputs(s.run_id)
            self.assertEqual(
                p["retained_occurrence_count"],
                None if capture is None else len(capture.references),
            )
            self.assertEqual(
                p["list_status"],
                (
                    "not_captured"
                    if capture is None
                    else "complete" if capture.state == "complete" else "incomplete"
                ),
            )
            self.assertFalse(p["has_more"])

    def test_limits_empty_group_and_orphan_validation(self):
        db = self.database()
        self.saved(db)
        s = self.capture(db, context=False)
        self.require_reads(db)
        with db.recorded_history_snapshot():
            p = db.list_history_window(1)
            self.assertEqual(p["group"]["scope_status"], "no_matches")
            self.assertEqual(p["items"], [])
            self.assertEqual(
                db.get_history_run(s.run_id)["context_capture"]["state"], "not_captured"
            )
            for limit in (0, 101, True, 1.5):
                with self.assertRaises(ValueError):
                    db.list_history_window(1, limit=limit)
                with self.assertRaises(ValueError):
                    db.list_history_outputs(s.run_id, limit=limit)
        self.capture(db, 1, output=outputs())
        conn = db.model.get_connection()
        conn.execute("PRAGMA foreign_keys=OFF")
        self.addCleanup(conn.execute, "PRAGMA foreign_keys=ON")
        with conn:
            conn.execute("DELETE FROM execution_output_capture")
        with db.recorded_history_snapshot():
            with self.assertRaises(sqlite3.DatabaseError):
                db.list_history_outputs(self.start(db, 1).run_id)

    def test_query_indexes_additive_and_reinitializable(self):
        db = self.database()
        conn = db.model.get_connection()
        self.require_reads(db)
        expected = {
            "execution_prompt_snapshots": ("raw_text_id", "snapshot_id"),
            "execution_contexts": ("positive_snapshot_id", "context_id"),
            "execution_run_contexts": ("context_id", "run_id", "anchor_id"),
            "execution_runs": (
                "observed_dequeue_at",
                "session_id",
                "queue_item_id",
                "run_id",
            ),
        }
        for table, columns in expected.items():
            indexes = [
                r for r in conn.execute(f"PRAGMA index_list({table})") if not r[2]
            ]
            self.assertTrue(
                any(
                    tuple(r[2] for r in conn.execute(f"PRAGMA index_info({i[1]})"))
                    == columns
                    for i in indexes
                ),
                table,
            )
        self.assertIsNone(history_schema_error(conn, allow_missing=False))
        from database.execution_history_schema import migrate_execution_history

        for table in expected:
            conn.execute(f"DROP INDEX idx_history_read_{table}")
        # Optional read indexes are not an owned-schema validity requirement.
        self.assertIsNone(history_schema_error(conn, allow_missing=False))
        migrate_execution_history(conn)
        migrate_execution_history(conn)
        self.assertTrue(
            all(
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name=?",
                    (f"idx_history_read_{t}",),
                ).fetchone()
                for t in expected
            )
        )

    def test_epoch_first_snapshot_excludes_concurrent_finish(self):
        db = self.database()
        self.saved(db)
        start = self.start(db)
        db.write_execution_start(start)
        db.write_execution_contexts(start.run_id, start.storage_epoch, bundle())
        peer = self.database()
        before = db.model.get_connection().total_changes
        with db.recorded_history_snapshot():
            peer.write_execution_finish(self.finish(start))
            self.assertEqual(
                db.get_history_run(start.run_id)["run"]["outcome"], "observed"
            )
            page = db.list_history_window(1)
            self.assertEqual(page["items"][0]["outcome"], "observed")
            self.assertIsNone(page["items"][0]["observed_callback_at"])
        with db.recorded_history_snapshot():
            self.assertEqual(
                db.get_history_run(start.run_id)["run"]["outcome"], "succeeded"
            )
        self.assertEqual(db.model.get_connection().total_changes, before)

    def test_compact_diagnostics_full_detail_and_no_filesystem(self):
        db = self.database()
        self.saved(db)
        start = self.start(db)
        db.write_execution_start(start)
        db.write_execution_finish(
            self.finish(
                start,
                "execution_error",
                node_id="4",
                node_type="Example",
                exception_type="ValueError",
            )
        )
        db.write_execution_contexts(
            start.run_id, start.storage_epoch, bundle("猫" * 20000)
        )
        conn = db.model.get_connection()
        before = conn.total_changes
        with (
            patch("builtins.open", side_effect=AssertionError("filesystem")),
            patch("os.stat", side_effect=AssertionError("filesystem")),
        ):
            with db.recorded_history_snapshot():
                page = db.list_history_window(1)
                detail = db.get_history_run(start.run_id)
                db.list_history_outputs(start.run_id)
                db.get_history_occurrence(start.run_id, "9", 0)
        compact = page["items"][0]
        self.assertTrue(compact["diagnostics_available"])
        self.assertNotIn("node_id", compact)
        self.assertEqual(detail["run"]["node_id"], "4")
        self.assertEqual(detail["contexts"][0]["negative"]["raw"], "猫" * 20000)
        self.assertEqual(conn.total_changes, before)

    def test_all_four_order_keys_across_sessions_and_run_ids(self):
        db = self.database()
        self.saved(db)
        conn = db.model.get_connection()
        # Equal local dequeue coordinates in separate sessions are legitimate.
        starts = []
        for i, session in enumerate(
            (
                "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            )
        ):
            start = replace(
                self.start(db, 2**53 + 1, 2**53 + 2),
                session_id=session,
                run_id=f"{i+1:08x}-1111-4111-8111-111111111111",
            )
            db.write_execution_start(start)
            db.write_execution_contexts(start.run_id, start.storage_epoch, bundle())
            starts.append(start)
        with db.recorded_history_snapshot():
            p = db.list_history_window(1, limit=1)
            q = db.list_history_window(1, limit=1, after=p["next_position"])
        self.assertEqual(p["items"][0]["session_id"], starts[0].session_id)
        self.assertEqual(q["items"][0]["session_id"], starts[1].session_id)
        self.assertEqual(q["items"][0]["queue_item_id"], 2**53 + 1)
        self.assertFalse(q["has_more"])
        self.assertNotIn(
            "rowid",
            " ".join(
                r[3]
                for r in conn.execute(
                    "EXPLAIN QUERY PLAN SELECT run_id FROM execution_runs "
                    "ORDER BY observed_dequeue_at,session_id,queue_item_id,run_id"
                )
            ),
        )

    def test_exact_scalar_collision_check_and_original_hint_invalidation(self):
        db = self.database()
        self.saved(db)
        start = self.capture(db)
        # A fake digest collision must never group different full scalars.
        from utils.execution_contexts import content_digest

        self.saved(db, "different", 2)
        with patch(
            "database.history_reads.content_digest",
            return_value=content_digest("text", ("positive",)),
        ):
            with db.recorded_history_snapshot():
                self.assertEqual(db.list_history_window(2)["recorded_run_count"], 0)
        self.saved(db, "positive", 3)
        with db.recorded_history_snapshot():
            context = db.get_history_run(start.run_id)["contexts"][0]
        self.assertIsNone(context["positive_prompt_id"])
        self.assertFalse(context["navigation"]["positive_available"])
        with db.model.get_connection() as conn:
            conn.execute("DELETE FROM prompts WHERE id IN (1,3)")
        self.saved(db, "positive", 4)
        with db.recorded_history_snapshot():
            context = db.get_history_run(start.run_id)["contexts"][0]
        self.assertIsNone(context["positive_prompt_id"])
        self.assertEqual(context["reference_epoch"], start.storage_epoch)

    def test_original_positive_hint_remains_but_negative_only_navigation_is_unavailable(
        self,
    ):
        db = self.database()
        self.saved(db)
        start = self.capture(db)
        with db.recorded_history_snapshot():
            original = db.get_history_run(start.run_id)["contexts"][0]
        self.assertEqual(original["positive_prompt_id"], 1)
        self.assertTrue(original["navigation"]["positive_available"])
        self.saved(db, "other positive", 3)
        with db.model.get_connection() as conn:
            conn.execute(
                "INSERT INTO prompt_pairs(positive_prompt_id,negative_prompt_id) "
                "VALUES(3,1)"
            )
        with db.recorded_history_snapshot():
            current = db.get_history_run(start.run_id)["contexts"][0]
            self.assertIsNone(db.list_history_window(1))
        self.assertEqual(current["positive_prompt_id"], 1)
        self.assertEqual(current["reference_epoch"], original["reference_epoch"])
        self.assertFalse(current["navigation"]["positive_available"])
        self.assertEqual(current["positive"], original["positive"])
        self.assertEqual(current["evidence"], original["evidence"])

    def test_snapshot_ownership_cleanup_survives_connection_close(self):
        from database.history_reads import _snapshots

        db = self.database()
        previous = _snapshots.get()
        with self.assertRaises(sqlite3.ProgrammingError):
            with db.recorded_history_snapshot():
                db.close()
        self.assertEqual(_snapshots.get(), previous)
        with db.recorded_history_snapshot():
            self.assertIsNone(db.get_history_run("missing"))
