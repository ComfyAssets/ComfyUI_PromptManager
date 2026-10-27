"""Auto-tagged marks schema group (#379): install, validation and restore."""

import hashlib
import re
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

try:
    from tests.open_handles import assert_closed
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed

from database.models import PromptModel

try:  # guarded so a missing module fails assertions, not test loading
    from database.autotag_schema import (
        AUTOTAG_TABLE,
        autotag_schema_error,
        autotag_without_annotations_error,
        migrate_autotag_state,
    )
except ImportError:
    AUTOTAG_TABLE = None
    autotag_schema_error = None
    autotag_without_annotations_error = None
    migrate_autotag_state = None

TABLE = "media_image_autotags"
DDL = (
    f"CREATE TABLE {TABLE} (byte_id TEXT PRIMARY KEY "
    "REFERENCES media_annotation_bytes(id) ON DELETE CASCADE) WITHOUT ROWID"
)
ANNOTATION_TABLES = (
    "media_annotation_metadata",
    "media_annotation_files",
    "media_annotation_bytes",
    "media_image_tags",
)
REVIEW_TABLES = (
    "media_review_metadata",
    "media_review_items",
    "media_review_decisions",
)
FILE_ID = "0f8e4b2a-1c3d-4e5f-8a9b-0c1d2e3f4a5b"
BYTE_ID = "6a1b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d"
OTHER_ID = "7b2c3d4e-5f6a-4b7c-9d0e-1f2a3b4c5d6e"
DIGEST = hashlib.sha256(b"img").hexdigest()
NOW = "2026-10-27T00:00:00.000000+00:00"
NEEDS_ANNOTATIONS = "Image auto-tag marks need the image annotation tables"
INVALID_VALUES = "Image auto-tag marks contain invalid values"
INVALID_SCHEMA = "Image auto-tag marks have an invalid schema"


def _compact(sql):
    return re.sub(r"\s+", "", sql or "").lower()


def catalog(conn):
    return {
        (kind, name, _compact(sql))
        for kind, name, sql in conn.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE name=? OR tbl_name=?",
            (TABLE, TABLE),
        )
    }


def marks(conn):
    return [tuple(row) for row in conn.execute(f"SELECT byte_id FROM {TABLE}")]


def insert_byte(conn, file_id=FILE_ID, byte_id=BYTE_ID, locator="/out/img.png"):
    conn.execute(
        "INSERT INTO media_annotation_files VALUES(?,?,?)", (file_id, locator, NOW)
    )
    conn.execute(
        "INSERT INTO media_annotation_bytes VALUES(?,?,?,?,?,?,?,0)",
        (byte_id, file_id, DIGEST, 3, 1_700_000_000_000_000_000, NOW, NOW),
    )


class AutotagSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(assert_closed, self, self.temp.name)
        self.addCleanup(PromptModel.reset_schema_cache)
        self.models = []
        self.addCleanup(self.close_models)
        self.path = str(Path(self.temp.name) / "live.db")

    def close_models(self):
        for model in self.models:
            model.close_all()

    def model(self, path=None):
        model = PromptModel(path or self.path)
        self.models.append(model)
        return model

    def require_module(self):
        self.assertIsNotNone(
            autotag_schema_error, "database.autotag_schema is not implemented"
        )

    def raw(self, path, *statements, foreign_keys=False):
        with closing(sqlite3.connect(path)) as raw, raw:
            raw.execute(f"PRAGMA foreign_keys={'ON' if foreign_keys else 'OFF'}")
            for statement in statements:
                if isinstance(statement, tuple):
                    raw.execute(*statement)
                else:
                    raw.execute(statement)

    def closed_current(self, name="current.db", *, with_mark=True):
        """A closed, current database with one byte record (and its mark)."""
        path = str(Path(self.temp.name) / name)
        model = self.model(path)
        with model.get_connection() as conn:
            insert_byte(conn)
            if with_mark:
                conn.execute(f"INSERT INTO {TABLE} VALUES(?)", (BYTE_ID,))
        model.close_all()
        return path

    def test_fresh_install_has_exactly_the_pinned_table(self):
        self.require_module()
        self.assertEqual(AUTOTAG_TABLE, TABLE)
        model = self.model()
        with model.get_connection() as conn:
            # WITHOUT ROWID with a primary key: no sqlite_autoindex row.
            self.assertEqual(catalog(conn), {("table", TABLE, _compact(DDL))})
            self.assertEqual(
                [tuple(row) for row in conn.execute(f"PRAGMA table_info({TABLE})")],
                [(0, "byte_id", "TEXT", 1, None, 1)],
            )
            self.assertIsNone(autotag_schema_error(conn, allow_missing=False))
            self.assertEqual(marks(conn), [])
        self.assertEqual(
            PromptModel.verify_database_file(self.path, require_library_state=True),
            (True, "ok"),
        )

    def test_reopen_and_rerun_are_idempotent(self):
        self.require_module()
        path = self.closed_current()
        for _ in range(2):
            PromptModel.reset_schema_cache()
            model = self.model(path)
            with model.get_connection() as conn:
                migrate_autotag_state(conn)
                self.assertEqual(marks(conn), [(BYTE_ID,)])
                self.assertEqual(catalog(conn), {("table", TABLE, _compact(DDL))})
            model.close_all()

    def test_old_database_without_the_table_gains_it_and_keeps_its_rows(self):
        self.require_module()
        path = self.closed_current(with_mark=False)
        self.raw(path, f"DROP TABLE {TABLE}")
        with closing(sqlite3.connect(path)) as raw:
            before = raw.execute("SELECT * FROM media_annotation_bytes").fetchall()
            self.assertIsNone(autotag_schema_error(raw))
            self.assertEqual(
                autotag_schema_error(raw, allow_missing=False),
                "Missing image auto-tag marks schema",
            )
        self.assertTrue(PromptModel.verify_database_file(path)[0])
        self.assertEqual(
            PromptModel.verify_database_file(path, require_library_state=True),
            (False, "Missing image auto-tag marks schema"),
        )
        PromptModel.reset_schema_cache()
        model = self.model(path)
        with model.get_connection() as conn:
            self.assertEqual(catalog(conn), {("table", TABLE, _compact(DDL))})
            self.assertEqual(
                [
                    tuple(row)
                    for row in conn.execute("SELECT * FROM media_annotation_bytes")
                ],
                before,
            )
        model.close_all()

    def test_table_without_annotation_group_is_refused(self):
        self.require_module()
        path = self.closed_current(with_mark=False)
        self.raw(
            path,
            *(
                f"DROP TABLE {t}"
                for t in REVIEW_TABLES + tuple(reversed(ANNOTATION_TABLES))
            ),
        )
        with closing(sqlite3.connect(path)) as raw:
            self.assertEqual(autotag_without_annotations_error(raw), NEEDS_ANNOTATIONS)
            for allow_missing in (True, False):
                self.assertEqual(
                    autotag_schema_error(raw, allow_missing=allow_missing),
                    NEEDS_ANNOTATIONS,
                )
        self.assertEqual(
            PromptModel.verify_database_file(path), (False, NEEDS_ANNOTATIONS)
        )
        PromptModel.reset_schema_cache()
        with self.assertRaises(sqlite3.DatabaseError) as raised:
            self.model(path)
        self.assertIn(NEEDS_ANNOTATIONS, str(raised.exception))

    def test_invalid_values_are_refused_without_repair(self):
        self.require_module()
        cases = {
            "not-a-uuid": (f"INSERT INTO {TABLE} VALUES('not-a-uuid')",),
            "upper-uuid": ((f"INSERT INTO {TABLE} VALUES(?)", (OTHER_ID.upper(),)),),
            "dangling": ((f"INSERT INTO {TABLE} VALUES(?)", (OTHER_ID,)),),
        }
        for name, statements in cases.items():
            with self.subTest(name):
                path = self.closed_current(f"{name}.db", with_mark=False)
                self.raw(path, *statements)
                with closing(sqlite3.connect(path)) as raw:
                    self.assertEqual(autotag_schema_error(raw), INVALID_VALUES)
                    before = marks(raw)
                self.assertEqual(
                    PromptModel.verify_database_file(path), (False, INVALID_VALUES)
                )
                PromptModel.reset_schema_cache()
                with self.assertRaises(sqlite3.DatabaseError):
                    self.model(path)
                with closing(sqlite3.connect(path)) as raw:
                    self.assertEqual(marks(raw), before)

    def test_altered_definitions_are_refused(self):
        self.require_module()
        cases = {
            "rowid": f"CREATE TABLE {TABLE} (byte_id TEXT PRIMARY KEY "
            "REFERENCES media_annotation_bytes(id) ON DELETE CASCADE)",
            "restrict": f"CREATE TABLE {TABLE} (byte_id TEXT PRIMARY KEY "
            "REFERENCES media_annotation_bytes(id) ON DELETE RESTRICT) WITHOUT ROWID",
            "extra column": f"CREATE TABLE {TABLE} (byte_id TEXT PRIMARY KEY "
            "REFERENCES media_annotation_bytes(id) ON DELETE CASCADE, engine TEXT) "
            "WITHOUT ROWID",
        }
        for name, ddl in cases.items():
            with self.subTest(name):
                path = self.closed_current(f"{name}.db", with_mark=False)
                self.raw(path, f"DROP TABLE {TABLE}", ddl)
                with closing(sqlite3.connect(path)) as raw:
                    self.assertEqual(autotag_schema_error(raw), INVALID_SCHEMA)
                self.assertFalse(PromptModel.verify_database_file(path)[0])
        path = self.closed_current("index.db", with_mark=False)
        self.raw(path, f"CREATE INDEX idx_extra ON {TABLE}(byte_id)")
        with closing(sqlite3.connect(path)) as raw:
            self.assertEqual(autotag_schema_error(raw), INVALID_SCHEMA)

    def test_deleting_the_byte_record_cascades_to_its_mark(self):
        self.require_module()
        model = self.model()
        with model.get_connection() as conn:
            insert_byte(conn)
            conn.execute(f"INSERT INTO {TABLE} VALUES(?)", (BYTE_ID,))
            conn.execute(f"INSERT OR IGNORE INTO {TABLE} VALUES(?)", (BYTE_ID,))
            self.assertEqual(marks(conn), [(BYTE_ID,)])
            conn.execute("DELETE FROM media_annotation_files")
            self.assertEqual(marks(conn), [])

    def test_restore_of_a_pre_change_backup_migrates(self):
        self.require_module()
        src = self.closed_current("backup.db", with_mark=False)
        self.raw(src, f"DROP TABLE {TABLE}")
        live = self.model()
        live.restore_from_file(src)
        with closing(sqlite3.connect(self.path)) as raw:
            self.assertEqual(catalog(raw), {("table", TABLE, _compact(DDL))})
            self.assertEqual(
                raw.execute("SELECT id FROM media_annotation_bytes").fetchall(),
                [(BYTE_ID,)],
            )
        self.assertEqual(
            PromptModel.verify_database_file(self.path, require_library_state=True),
            (True, "ok"),
        )
        with closing(sqlite3.connect(src)) as raw:  # the source is untouched
            self.assertEqual(catalog(raw), set())

    def test_install_failure_rolls_back(self):
        self.require_module()
        src = self.closed_current("copy.db", with_mark=False)
        self.raw(src, f"DROP TABLE {TABLE}")
        path = str(Path(self.temp.name) / "rollback.db")
        shutil.copyfile(src, path)
        with closing(sqlite3.connect(path)) as raw:
            raw.execute("BEGIN")
            raw.execute("CREATE TABLE marker(x)")
            # An invalid group already present: nothing created, outer work kept.
            raw.execute(f"CREATE TABLE {TABLE}(byte_id TEXT)")
            with self.assertRaises(sqlite3.DatabaseError):
                migrate_autotag_state(raw)
            self.assertEqual(
                raw.execute(
                    f"SELECT sql FROM sqlite_master WHERE name='{TABLE}'"
                ).fetchone()[0],
                f"CREATE TABLE {TABLE}(byte_id TEXT)",
            )
            self.assertIsNotNone(
                raw.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='marker'"
                ).fetchone()
            )
            raw.rollback()


if __name__ == "__main__":
    unittest.main()
