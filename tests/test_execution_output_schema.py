"""Atomic output schema upgrades and bounded validation of untrusted evidence."""

import sqlite3
import unittest
from unittest.mock import patch

from database import execution_history_schema as history
from database.execution_context_schema import install_context_schema

try:
    from tests.test_execution_context_schema import EPOCH, RUN, legacy
except ImportError:
    from test_execution_context_schema import EPOCH, RUN, legacy

OUTPUT_TABLES = ("execution_output_capture", "execution_run_outputs")
ROOT = "a" * 64


def v2(conn):
    """Manufacture the supported pre-output metadata CHECK and context schema."""
    legacy(conn)
    sql = (
        conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='execution_history_metadata'"
        )
        .fetchone()[0]
        .replace("schema_version = 1", "schema_version = 2")
    )
    conn.execute("DROP TABLE execution_history_metadata")
    conn.execute(sql)
    conn.execute("INSERT INTO execution_history_metadata VALUES(1,2,?)", (EPOCH,))
    install_context_schema(conn)
    conn.commit()


def capture(conn, run=RUN, state="complete", reason="reported", rows=True):
    conn.execute(
        "INSERT INTO execution_output_capture VALUES(?,?,?,1,'host_final_history')",
        (run, state, reason),
    )
    if rows:
        conn.execute(
            "INSERT INTO execution_run_outputs VALUES(?, '保存😀', 0, 'output', "
            "'Batch Å', ' image 😀.png ', 'posix', ?)",
            (run, ROOT),
        )


class OutputSchemaTests(unittest.TestCase):
    def connection(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        return conn

    def populated(self):
        conn = self.connection()
        legacy(conn)
        history.migrate_execution_history(conn)
        capture(conn)
        conn.commit()
        return conn

    def test_legacy_v1_v2_upgrade_preserves_rows_ddl_epoch_and_counters(self):
        for fixture in (None, legacy, v2):
            with self.subTest(fixture=fixture):
                conn = self.connection()
                if fixture:
                    fixture(conn)
                before = list(conn.iterdump())
                ddl = dict(conn.execute("SELECT name,sql FROM sqlite_master"))
                self.assertIsNone(history.history_schema_error(conn))
                self.assertIsNotNone(
                    history.history_schema_error(conn, allow_missing=False)
                )
                history.migrate_execution_history(conn)
                version, epoch = conn.execute(
                    "SELECT schema_version,storage_epoch FROM "
                    "execution_history_metadata"
                ).fetchone()
                self.assertEqual(version, 3)
                if fixture:
                    self.assertEqual(epoch, EPOCH)
                    self.assertEqual(
                        conn.execute("SELECT * FROM prompts").fetchall(),
                        [(7, "kept", 19)],
                    )
                    self.assertEqual(
                        conn.execute(
                            "SELECT run_id,storage_epoch FROM execution_runs"
                        ).fetchall(),
                        [(RUN, EPOCH)],
                    )
                    after = list(conn.iterdump())
                    self.assertTrue(
                        all(line in after for line in before if "metadata" not in line)
                    )
                    for name, sql in ddl.items():
                        if name != "execution_history_metadata":
                            self.assertEqual(
                                conn.execute(
                                    "SELECT sql FROM sqlite_master WHERE name=?",
                                    (name,),
                                ).fetchone()[0],
                                sql,
                            )
                for table in OUTPUT_TABLES:
                    self.assertEqual(
                        conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0
                    )
                self.assertIsNone(
                    history.history_schema_error(conn, allow_missing=False)
                )
                snapshot = list(conn.iterdump())
                history.migrate_execution_history(conn)
                self.assertEqual(list(conn.iterdump()), snapshot)

    def test_v2_context_ids_and_content_survive_upgrade(self):
        conn = self.connection()
        try:
            from tests.test_execution_context_schema import ContextSchemaTests
        except ImportError:
            from test_execution_context_schema import ContextSchemaTests
        ContextSchemaTests().populate(conn)
        # Explicit supported v2: remove every output-owned table and rebuild metadata.
        for table in reversed(OUTPUT_TABLES):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        sql = (
            conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='execution_history_metadata'"
            )
            .fetchone()[0]
            .replace("schema_version = 3", "schema_version = 2")
        )
        conn.execute("DROP TABLE execution_history_metadata")
        conn.execute(sql)
        conn.execute("INSERT INTO execution_history_metadata VALUES(1,2,?)", (EPOCH,))
        from database.execution_context_schema import TABLE_COLUMNS

        before = {
            table: conn.execute(f"SELECT * FROM {table}").fetchall()
            for table in TABLE_COLUMNS
        }
        history.migrate_execution_history(conn)
        for table, rows in before.items():
            self.assertEqual(conn.execute(f"SELECT * FROM {table}").fetchall(), rows)
        self.assertIsNone(history.history_schema_error(conn, allow_missing=False))

    def test_creation_and_final_validation_failures_roll_back_and_retry(self):
        for fixture in (None, legacy, v2):
            for failure in ("creation", "validation"):
                with self.subTest(fixture=fixture, failure=failure):
                    conn = self.connection()
                    if fixture:
                        fixture(conn)
                    before = list(conn.iterdump())
                    if failure == "creation":

                        def fail(connection):
                            connection.execute(
                                "CREATE TABLE execution_output_capture(x)"
                            )
                            raise sqlite3.OperationalError(
                                "injected output DDL failure"
                            )

                        replacement = patch.object(
                            history, "install_output_schema", fail
                        )
                    else:
                        original = history.history_schema_error

                        def fail(connection, *, allow_missing=True):
                            return (
                                original(connection)
                                if allow_missing
                                else "injected final output validation failure"
                            )

                        replacement = patch.object(
                            history, "history_schema_error", fail
                        )
                    with (
                        replacement,
                        self.assertRaisesRegex(sqlite3.DatabaseError, "injected"),
                    ):
                        history.migrate_execution_history(conn)
                    self.assertEqual(list(conn.iterdump()), before)
                    history.migrate_execution_history(conn)
                    self.assertIsNone(
                        history.history_schema_error(conn, allow_missing=False)
                    )

    def test_outputs_without_history_or_under_old_versions_are_rejected(self):
        for fixture in (None, legacy, v2):
            for table in OUTPUT_TABLES:
                with self.subTest(fixture=fixture, table=table):
                    conn = self.connection()
                    if fixture:
                        fixture(conn)
                    conn.execute(f"CREATE TABLE {table}(x TEXT)")
                    before = list(conn.iterdump())
                    self.assertIsNotNone(history.history_schema_error(conn))
                    with self.assertRaises(sqlite3.DatabaseError):
                        history.migrate_execution_history(conn)
                    self.assertEqual(list(conn.iterdump()), before)

    def test_missing_output_table_rejected_in_both_validation_modes(self):
        for table in OUTPUT_TABLES:
            conn = self.populated()
            conn.execute(f"DROP TABLE {table}")
            for allow in (True, False):
                self.assertIsNotNone(
                    history.history_schema_error(conn, allow_missing=allow)
                )

    def test_exact_owned_indexes_checks_columns_foreign_keys_and_objects(self):
        mutations = (
            "CREATE UNIQUE INDEX extra ON execution_run_outputs(filename)",
            "CREATE INDEX extra ON execution_run_outputs(filename)",
            "CREATE TRIGGER extra AFTER INSERT ON execution_run_outputs "
            "BEGIN SELECT 1; END",
            "CREATE VIEW extra AS SELECT * FROM execution_run_outputs",
        )
        for sql in mutations:
            with self.subTest(sql=sql):
                conn = self.populated()
                conn.execute(sql)
                self.assertIsNotNone(history.history_schema_error(conn))
        replacements = (
            (
                "execution_run_outputs",
                "filename TEXT NOT NULL",
                "filename TEXT NOT NULL UNIQUE",
            ),
            (
                "execution_run_outputs",
                "filename TEXT NOT NULL",
                "filename TEXT NOT NULL COLLATE NOCASE",
            ),
            (
                "execution_run_outputs",
                "PRIMARY KEY (run_id, output_node_id, image_ordinal)",
                "PRIMARY KEY (run_id, image_ordinal, output_node_id)",
            ),
            (
                "execution_output_capture",
                "format_version = 1",
                "format_version IN (1, 2)",
            ),
            (
                "execution_output_capture",
                "REFERENCES execution_runs(run_id)",
                "REFERENCES execution_runs(job_id)",
            ),
            ("execution_run_outputs", "ON DELETE NO ACTION", "ON DELETE CASCADE"),
            ("execution_run_outputs", "ON UPDATE NO ACTION", "ON UPDATE CASCADE"),
            (
                "execution_run_outputs",
                "REFERENCES execution_output_capture(run_id)",
                "REFERENCES execution_runs(run_id)",
            ),
            ("execution_run_outputs", "root_key TEXT", "root_key BLOB"),
        )
        for table, old, new in replacements:
            with self.subTest(table=table, replacement=new):
                conn = self.populated()
                sql = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE name=?", (table,)
                ).fetchone()[0]
                self.assertIn(old, sql)
                conn.execute(f"DROP TABLE {table}")
                conn.execute(sql.replace(old, new))
                self.assertIsNotNone(history.history_schema_error(conn))

    def test_orphan_run_header_and_rows_rejected_without_fk_enforcement(self):
        for mutation in (
            "DELETE FROM execution_runs",
            "DELETE FROM execution_output_capture",
            "UPDATE execution_run_outputs "
            "SET run_id='33333333-3333-4333-8333-333333333333'",
        ):
            with self.subTest(mutation=mutation):
                conn = self.populated()
                conn.execute(mutation)
                self.assertIsNotNone(history.history_schema_error(conn))

    def test_malformed_scalars_rejected_before_text_or_row_fetch(self):
        cases = (
            ("execution_output_capture", "run_id", RUN + "\0"),
            ("execution_output_capture", "state", "complete\0"),
            ("execution_output_capture", "reason", "reported\0"),
            ("execution_output_capture", "source", "host_final_history\0"),
            ("execution_output_capture", "format_version", "x" * 4096),
            ("execution_run_outputs", "run_id", RUN + "\0"),
            ("execution_run_outputs", "output_node_id", "x" * 129),
            ("execution_run_outputs", "output_node_id", "😀" * 129),
            ("execution_run_outputs", "output_node_id", "n\0"),
            ("execution_run_outputs", "image_ordinal", "x" * 4096),
            ("execution_run_outputs", "folder_type", "output\0"),
            ("execution_run_outputs", "subfolder", "x" * 2049),
            ("execution_run_outputs", "subfolder", "a\0b"),
            ("execution_run_outputs", "filename", "x" * 1025),
            ("execution_run_outputs", "filename", "a\0b"),
            ("execution_run_outputs", "path_style", "posix\0"),
            ("execution_run_outputs", "root_key", ROOT + "\0"),
            ("execution_run_outputs", "filename", b"\xff"),
        )
        for table, column, malicious in cases:
            with self.subTest(table=table, column=column, value=repr(malicious)[:30]):
                conn = self.populated()
                conn.execute("PRAGMA ignore_check_constraints=ON")
                conn.execute(f"UPDATE {table} SET {column}=?", (malicious,))

                def bounded_text(value):
                    self.assertNotEqual(
                        value,
                        malicious.encode() if type(malicious) is str else malicious,
                    )
                    return value.decode()

                def bounded_row(cursor, row):
                    self.assertFalse(any(value == malicious for value in row))
                    return row

                conn.text_factory = bounded_text
                conn.row_factory = bounded_row
                self.assertIsNotNone(history.history_schema_error(conn))

    def test_state_version_source_and_locator_corruption_rejected(self):
        cases = (
            ("execution_output_capture", "source", "scan"),
            ("execution_output_capture", "format_version", 2),
            ("execution_output_capture", "state", "not_captured"),
            ("execution_output_capture", "reason", "none_reported"),
            ("execution_run_outputs", "filename", "../image.png"),
            ("execution_run_outputs", "filename", ""),
            ("execution_run_outputs", "subfolder", "a/../b"),
            ("execution_run_outputs", "folder_type", "input"),
            ("execution_run_outputs", "path_style", "native"),
            ("execution_run_outputs", "image_ordinal", 256),
            ("execution_run_outputs", "image_ordinal", -1),
            ("execution_run_outputs", "root_key", None),
        )
        for table, column, value in cases:
            with self.subTest(table=table, column=column, value=value):
                conn = self.populated()
                conn.execute("PRAGMA ignore_check_constraints=ON")
                conn.execute(f"UPDATE {table} SET {column}=?", (value,))
                self.assertIsNotNone(history.history_schema_error(conn))

    def test_state_row_consistency_and_conflicting_first_rows(self):
        cases = (
            ("complete", "reported", False, False),
            ("complete", "none_reported", False, True),
            ("complete", "none_reported", True, False),
            ("partial", "invalid_evidence", False, True),
            ("partial", "root_unavailable", False, False),
            ("partial", "root_unavailable", True, True),
            ("unavailable", "unavailable", True, False),
            ("unavailable", "invalid_evidence", False, True),
            ("partial", "reported", True, False),
            ("conflicting", "conflicting", True, True),
        )
        for state, reason, rows, valid in cases:
            with self.subTest(state=state, reason=reason, rows=rows):
                conn = self.connection()
                legacy(conn)
                history.migrate_execution_history(conn)
                conn.execute("PRAGMA ignore_check_constraints=ON")
                capture(conn, state=state, reason=reason, rows=rows)
                self.assertEqual(history.history_schema_error(conn) is None, valid)

    def test_bundle_count_and_exact_payload_bounds(self):
        for count, filename, valid in (
            (256, "a.png", True),
            (257, "a.png", False),
            (128, "x" * 1024, False),
        ):
            with self.subTest(count=count, filename_bytes=len(filename)):
                conn = self.populated()
                conn.execute("DELETE FROM execution_run_outputs")
                conn.executemany(
                    "INSERT INTO execution_run_outputs "
                    "VALUES(?,?,0,'temp','',?,'posix',?)",
                    ((RUN, str(index), filename, ROOT) for index in range(count)),
                )
                self.assertEqual(history.history_schema_error(conn) is None, valid)

    def test_exact_128_kib_charge_is_valid_but_one_extra_byte_is_rejected(self):
        conn = self.populated()
        conn.execute("DELETE FROM execution_run_outputs")
        # 128-byte bundle header plus 128 references charged 1023 bytes each.
        # Each row: 128 overhead + 1 node + 4 folder + 821 name + 5 style + 64 root.
        conn.executemany(
            "INSERT INTO execution_run_outputs VALUES(?,'n',?,'temp','',?,'posix',?)",
            ((RUN, ordinal, "x" * 821, ROOT) for ordinal in range(128)),
        )
        self.assertIsNone(history.history_schema_error(conn))
        conn.execute(
            "UPDATE execution_run_outputs SET filename=? WHERE image_ordinal=0",
            ("x" * 822,),
        )
        self.assertIsNotNone(history.history_schema_error(conn))

    def test_oversized_bundle_is_rejected_before_fetching_its_scalar_payload(self):
        conn = self.populated()
        conn.execute("DELETE FROM execution_run_outputs")
        filename = "x" * 1024
        conn.executemany(
            "INSERT INTO execution_run_outputs VALUES(?,?,0,'temp','',?,'posix',?)",
            ((RUN, str(index), filename, ROOT) for index in range(128)),
        )

        def bounded_text(value):
            self.assertNotEqual(value, filename.encode())
            return value.decode()

        conn.text_factory = bounded_text
        self.assertIsNotNone(history.history_schema_error(conn))

    def test_duplicate_locators_and_unicode_survive_validation_unchanged(self):
        conn = self.populated()
        conn.execute(
            "INSERT INTO execution_run_outputs SELECT run_id,output_node_id,"
            "1,folder_type,subfolder,filename,path_style,root_key "
            "FROM execution_run_outputs"
        )
        conn.execute(
            "INSERT INTO execution_run_outputs SELECT run_id,'other',"
            "image_ordinal,folder_type,subfolder,filename,path_style,root_key "
            "FROM execution_run_outputs"
        )
        before = conn.execute("SELECT * FROM execution_run_outputs").fetchall()
        self.assertEqual(len(before), 4)
        self.assertIsNone(history.history_schema_error(conn, allow_missing=False))
        history.migrate_execution_history(conn)
        self.assertEqual(
            conn.execute("SELECT * FROM execution_run_outputs").fetchall(), before
        )
