"""Versioned migration and hostile context restore boundaries."""

import json
import sqlite3
import unittest
from unittest.mock import patch

from database import execution_history_schema as history
from utils.execution_contexts import content_digest

EPOCH = "11111111-1111-4111-8111-111111111111"
RUN = "22222222-2222-4222-8222-222222222222"


def legacy(conn):
    for table, columns in history._TABLE_COLUMNS.items():
        definitions = [
            f"{name} {kind}{' NOT NULL' if required else ''}"
            f"{' PRIMARY KEY' if primary else ''}"
            for name, kind, required, primary in columns
        ]
        checks = (
            (
                "singleton_id = 1",
                "schema_version = 1",
                history._uuid_check("storage_epoch"),
            )
            if table == "execution_history_metadata"
            else history._RUN_CHECKS
        )
        definitions.extend(f"CHECK({check})" for check in checks)
        conn.execute(f"CREATE TABLE {table} ({', '.join(definitions)})")
    for name, (unique, columns) in history._INDEXES.items():
        conn.execute(
            f"CREATE {'UNIQUE ' if unique else ''}INDEX {name} "
            f"ON execution_runs({', '.join(columns)})"
        )
    conn.execute("INSERT INTO execution_history_metadata VALUES(1,1,?)", (EPOCH,))
    conn.execute(
        "INSERT INTO execution_runs "
        "VALUES(?,?,?,?,?,1,100,NULL,NULL,NULL,'observed',NULL,0,NULL,NULL"
        ",NULL)",
        (RUN, EPOCH, 0, RUN, EPOCH),
    )
    conn.execute(
        "CREATE TABLE prompts(id INTEGER PRIMARY KEY, text TEXT, run_count INTEGER)"
    )
    conn.execute("INSERT INTO prompts VALUES(7,'kept',19)")
    conn.commit()


class ContextSchemaTests(unittest.TestCase):
    def connection(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        return conn

    def test_v1_upgrade_preserves_epoch_and_records(self):
        conn = self.connection()
        legacy(conn)
        before = conn.execute("SELECT * FROM execution_runs").fetchall()
        self.assertIsNone(history.history_schema_error(conn))
        history.migrate_execution_history(conn)
        self.assertEqual(
            conn.execute(
                "SELECT schema_version,storage_epoch FROM execution_history_metadata"
            ).fetchone(),
            (history.SCHEMA_VERSION, EPOCH),
        )
        self.assertEqual(
            conn.execute("SELECT * FROM execution_runs").fetchall(), before
        )
        self.assertEqual(
            conn.execute("SELECT * FROM prompts").fetchall(), [(7, "kept", 19)]
        )
        for table in (
            "execution_context_texts",
            "execution_prompt_snapshots",
            "execution_contexts",
            "execution_run_contexts",
            "execution_context_capture",
        ):
            self.assertEqual(
                conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0
            )

    def test_migration_idempotent(self):
        conn = self.connection()
        history.migrate_execution_history(conn)
        first = "\n".join(conn.iterdump())
        history.migrate_execution_history(conn)
        self.assertEqual("\n".join(conn.iterdump()), first)
        self.assertEqual(
            conn.execute(
                "SELECT schema_version FROM execution_history_metadata"
            ).fetchone()[0],
            history.SCHEMA_VERSION,
        )

    def test_migration_failure_rolls_back_metadata_and_all_tables(self):
        conn = self.connection()
        legacy(conn)
        before = "\n".join(conn.iterdump())

        def fail(connection):
            connection.execute("CREATE TABLE execution_context_texts(x TEXT)")
            raise sqlite3.OperationalError("injected context DDL failure")

        with patch(
            "database.execution_history_schema.install_context_schema",
            fail,
            create=True,
        ):
            with self.assertRaisesRegex(sqlite3.OperationalError, "injected"):
                history.migrate_execution_history(conn)
        self.assertEqual("\n".join(conn.iterdump()), before)

    def test_partial_context_schema_rejected_even_for_v1_or_absent(self):
        for old in (False, True):
            conn = self.connection()
            if old:
                legacy(conn)
            conn.execute("CREATE TABLE execution_context_texts(text_id TEXT)")
            self.assertIsNotNone(history.history_schema_error(conn))
            with self.assertRaises(sqlite3.DatabaseError):
                history.migrate_execution_history(conn)

    def populate(self, conn):
        legacy(conn)
        history.migrate_execution_history(conn)
        text = content_digest("text", ("hello",))
        snapshot = content_digest("snapshot", ("hello", None, None, None))
        context = content_digest("context", (snapshot, None))
        conn.execute("INSERT INTO execution_context_texts VALUES(?,?)", (text, "hello"))
        conn.execute(
            "INSERT INTO execution_prompt_snapshots VALUES(?,?,NULL,NULL,NULL)",
            (snapshot, text),
        )
        conn.execute(
            "INSERT INTO execution_contexts VALUES(?,?,NULL)", (context, snapshot)
        )
        unknown = dict(provenance="unknown", source="unknown", reason="missing")
        evidence = dict(
            format_version=1,
            role_provenance="inferred",
            positive=dict(
                raw=dict(provenance="recorded", source="runtime_return", reason=None),
                effective=unknown,
                prepend=unknown,
                append=unknown,
            ),
            negative=None,
        )
        conn.execute(
            "INSERT INTO execution_run_contexts VALUES(?,?,?,?,NULL,NULL,NULL,NULL,1)",
            (RUN, "node", context, json.dumps(evidence)),
        )
        conn.execute(
            "INSERT INTO execution_context_capture VALUES(?,'partial','missing',1)",
            (RUN,),
        )
        return evidence

    def test_owned_identifier_constraints_reject_nul_suffixed_bytes(self):
        columns = {
            "execution_context_texts": ("text_id",),
            "execution_prompt_snapshots": (
                "snapshot_id",
                "raw_text_id",
                "effective_text_id",
                "prepend_text_id",
                "append_text_id",
            ),
            "execution_contexts": (
                "context_id",
                "positive_snapshot_id",
                "negative_snapshot_id",
            ),
            "execution_run_contexts": (
                "run_id",
                "anchor_id",
                "context_id",
                "reference_epoch",
            ),
            "execution_context_capture": ("run_id",),
        }
        for table, names in columns.items():
            for column in names:
                with self.subTest(table=table, column=column):
                    conn = self.connection()
                    self.populate(conn)
                    prefix = (
                        RUN
                        if column in ("run_id", "reference_epoch")
                        else "node" if column == "anchor_id" else "a" * 64
                    )
                    with self.assertRaises(sqlite3.IntegrityError):
                        conn.execute(
                            f"UPDATE {table} SET {column}=?",
                            (prefix + "\0" + "x" * 1024,),
                        )

    def test_context_validation_rejects_oversized_keys_before_fetch(self):
        for table, column, prefix in (
            ("execution_context_texts", "text_id", "a" * 64),
            ("execution_run_contexts", "anchor_id", "node"),
        ):
            with self.subTest(table=table, column=column):
                conn = self.connection()
                self.populate(conn)
                malicious = prefix + "\0" + "x" * 1024
                if table == "execution_context_texts":
                    for dependent in (
                        "execution_context_capture",
                        "execution_run_contexts",
                        "execution_contexts",
                        "execution_prompt_snapshots",
                    ):
                        conn.execute(f"DELETE FROM {dependent}")
                conn.execute("PRAGMA ignore_check_constraints=ON")
                conn.execute(f"UPDATE {table} SET {column}=?", (malicious,))

                def bounded_text(value):
                    self.assertNotEqual(value, malicious.encode())
                    return value.decode()

                conn.text_factory = bounded_text
                self.assertIsNotNone(history.history_schema_error(conn))

    def test_unicode_anchor_boundary_and_prompt_nuls_preserve_exact_text(self):
        conn = self.connection()
        self.populate(conn)
        text = "hello\0world"
        text_id = content_digest("text", (text,))
        snapshot_id = content_digest("snapshot", (text, None, None, None))
        context_id = content_digest("context", (snapshot_id, None))
        conn.execute(
            "UPDATE execution_context_texts SET text_id=?,text_value=?",
            (text_id, text),
        )
        conn.execute(
            "UPDATE execution_prompt_snapshots SET snapshot_id=?,raw_text_id=?",
            (snapshot_id, text_id),
        )
        conn.execute(
            "UPDATE execution_contexts SET context_id=?,positive_snapshot_id=?",
            (context_id, snapshot_id),
        )
        conn.execute(
            "UPDATE execution_run_contexts SET context_id=?,anchor_id=?",
            (context_id, "😀" * 128),
        )
        self.assertIsNone(history.history_schema_error(conn))
        self.assertEqual(
            conn.execute("SELECT text_value FROM execution_context_texts").fetchone(),
            (text,),
        )

    def test_link_without_capture_header_is_malformed_v2(self):
        from database.execution_context_schema import context_schema_error

        conn = self.connection()
        self.populate(conn)
        conn.execute("DELETE FROM execution_context_capture")
        before = "\n".join(conn.iterdump())
        self.assertIsNotNone(context_schema_error(conn))
        self.assertIsNotNone(history.history_schema_error(conn, allow_missing=False))
        self.assertEqual("\n".join(conn.iterdump()), before)

    def test_valid_context_and_fingerprints(self):
        conn = self.connection()
        self.populate(conn)
        self.assertIsNone(history.history_schema_error(conn, allow_missing=False))

    def test_v2_rejects_partial_schema_extra_uniqueness_bad_fingerprint_or_orphan(self):
        mutations = (
            "DROP TABLE execution_context_capture",
            "CREATE UNIQUE INDEX extra_text ON execution_context_texts(text_value)",
            "UPDATE execution_context_texts SET text_value='changed'",
            "UPDATE execution_prompt_snapshots SET raw_text_id='" + "0" * 64 + "'",
            "UPDATE execution_contexts SET context_id='" + "0" * 64 + "'",
            "UPDATE execution_run_contexts SET "
            "run_id='33333333-3333-4333-8333-333333333333'",
            "UPDATE execution_run_contexts SET evidence_json='{}'",
            "UPDATE execution_context_capture SET "
            "run_id='33333333-3333-4333-8333-333333333333'",
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                conn = self.connection()
                self.populate(conn)
                conn.execute(mutation)
                self.assertIsNotNone(history.history_schema_error(conn))

    def test_evidence_rejects_extra_metadata_and_false_unknown(self):
        for change in ("extra", "known", "role", "version"):
            conn = self.connection()
            evidence = self.populate(conn)
            if change == "extra":
                evidence["graph"] = {}
            elif change == "known":
                evidence["positive"]["effective"] = dict(
                    provenance="inferred", source="graph_literal", reason=None
                )
            elif change == "role":
                evidence["negative"] = evidence["positive"]
            else:
                evidence["format_version"] = True
            conn.execute(
                "UPDATE execution_run_contexts SET evidence_json=?",
                (json.dumps(evidence),),
            )
            self.assertIsNotNone(history.history_schema_error(conn))

    def test_more_than_sixteen_context_links_rejected(self):
        conn = self.connection()
        self.populate(conn)
        for i in range(16):
            conn.execute(
                "INSERT INTO execution_run_contexts SELECT "
                "run_id,?,context_id,evidence_json,positive_prompt_id,negative_pro"
                "mpt_id,pair_id,reference_epoch,capture_version FROM "
                "execution_run_contexts LIMIT 1",
                (str(i),),
            )
        self.assertIsNotNone(history.history_schema_error(conn))

    def test_owned_schema_rejects_triggers_views_and_generated_columns(self):
        for mutation in (
            "CREATE TRIGGER hidden_write AFTER INSERT ON "
            "execution_context_texts BEGIN DELETE FROM "
            "execution_context_texts; END",
            "CREATE VIEW context_view AS SELECT * FROM execution_context_texts",
            "ALTER TABLE execution_context_texts ADD COLUMN hidden TEXT "
            "GENERATED ALWAYS AS (text_value) VIRTUAL",
        ):
            with self.subTest(mutation=mutation):
                conn = self.connection()
                self.populate(conn)
                conn.execute(mutation)
                self.assertIsNotNone(history.history_schema_error(conn))

    def test_duplicate_json_keys_and_unbounded_or_false_complete_rows_rejected(self):
        for change in (
            "duplicate",
            "oversized",
            "reason",
            "complete",
            "empty_complete",
        ):
            with self.subTest(change=change):
                conn = self.connection()
                self.populate(conn)
                conn.execute("PRAGMA ignore_check_constraints=ON")
                if change == "duplicate":
                    conn.execute(
                        "UPDATE execution_run_contexts SET "
                        "evidence_json=replace(evidence_json, '{', "
                        "'{\"format_version\":1,')"
                    )
                elif change == "oversized":
                    conn.execute(
                        "UPDATE execution_context_texts SET text_value=?",
                        ("é" * 32769,),
                    )
                elif change == "reason":
                    conn.execute(
                        "UPDATE execution_context_capture SET reason='arbitrary' "
                    )
                else:
                    conn.execute(
                        "UPDATE execution_context_capture SET state='complete'"
                    )
                    if change == "empty_complete":
                        conn.execute("DELETE FROM execution_run_contexts")
                self.assertIsNotNone(history.history_schema_error(conn))

    def test_restore_enforces_context_and_run_aggregate_payload_bounds(self):
        for size, links, valid in (
            (40000, 0, False),
            (40000, 1, False),
            (25000, 5, True),
            (25000, 6, False),
        ):
            with self.subTest(size=size, links=links):
                conn = self.connection()
                evidence = self.populate(conn)
                text = "x" * size
                text_id = content_digest("text", (text,))
                snapshot_id = content_digest("snapshot", (text,) * 4)
                context_id = content_digest("context", (snapshot_id, snapshot_id))
                conn.execute(
                    "UPDATE execution_context_texts SET text_id=?,text_value=?",
                    (text_id, text),
                )
                conn.execute(
                    "UPDATE execution_prompt_snapshots SET "
                    "snapshot_id=?,raw_text_id=?,effective_text_id=?,prepend_text_id=?"
                    ",append_text_id=?",
                    (snapshot_id,) + (text_id,) * 4,
                )
                conn.execute(
                    "UPDATE execution_contexts SET "
                    "context_id=?,positive_snapshot_id=?,negative_snapshot_id=?",
                    (context_id, snapshot_id, snapshot_id),
                )
                known = dict(
                    provenance="recorded", source="runtime_return", reason=None
                )
                evidence["positive"] = dict.fromkeys(
                    ("raw", "effective", "prepend", "append"), known
                )
                evidence["negative"] = evidence["positive"]
                conn.execute(
                    "UPDATE execution_run_contexts SET context_id=?,evidence_json=?",
                    (context_id, json.dumps(evidence)),
                )
                for i in range(links - 1):
                    conn.execute(
                        "INSERT INTO execution_run_contexts SELECT "
                        "run_id,?,context_id,evidence_json,positive_prompt_id,"
                        "negative_prompt_id,pair_id,reference_epoch,"
                        "capture_version FROM "
                        "execution_run_contexts LIMIT 1",
                        (str(i),),
                    )
                if not links:
                    conn.execute("DELETE FROM execution_run_contexts")
                error = history.history_schema_error(conn)
                if valid:
                    self.assertIsNone(error)
                else:
                    self.assertIsNotNone(error)
