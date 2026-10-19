"""Library state schema: install, validation, restore and rebuild safety."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

try:
    from tests.open_handles import assert_closed
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed

from database.models import PromptModel

try:  # guarded so a missing module fails assertions, not test loading
    from database.library_schema import library_schema_error
except ImportError:
    library_schema_error = None

TABLE = "prompt_library_state"
GOOD_COLUMNS = [("prompt_id", "INTEGER"), ("title", "TEXT")]
FK = "FOREIGN KEY(prompt_id) REFERENCES prompts(id) ON DELETE CASCADE"
INVALID_SCHEMAS = {
    "reserved view": "VIEW",
    "partial columns": f"CREATE TABLE {TABLE}(prompt_id INTEGER PRIMARY KEY, {FK})",
    "extra column": (
        f"CREATE TABLE {TABLE}(prompt_id INTEGER PRIMARY KEY, title TEXT, "
        f"extra TEXT, {FK})"
    ),
    "wrong primary key": f"CREATE TABLE {TABLE}(prompt_id INTEGER, title TEXT, {FK})",
    "wrong type": (
        f"CREATE TABLE {TABLE}(prompt_id TEXT PRIMARY KEY, title TEXT, {FK})"
    ),
    "not null title": (
        f"CREATE TABLE {TABLE}(prompt_id INTEGER PRIMARY KEY, "
        f"title TEXT NOT NULL, {FK})"
    ),
    "default title": (
        f"CREATE TABLE {TABLE}(prompt_id INTEGER PRIMARY KEY, "
        f"title TEXT DEFAULT 'x', {FK})"
    ),
    "check": (
        f"CREATE TABLE {TABLE}(prompt_id INTEGER PRIMARY KEY, title TEXT "
        f"CHECK(length(title) < 5), {FK})"
    ),
    "missing foreign key": (
        f"CREATE TABLE {TABLE}(prompt_id INTEGER PRIMARY KEY, title TEXT)"
    ),
    "restrict foreign key": (
        f"CREATE TABLE {TABLE}(prompt_id INTEGER PRIMARY KEY, title TEXT, "
        "FOREIGN KEY(prompt_id) REFERENCES prompts(id) ON DELETE RESTRICT)"
    ),
}


class LibrarySchemaTests(unittest.TestCase):
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
            library_schema_error, "database.library_schema is not implemented"
        )

    @staticmethod
    def columns(conn):
        return [r[1:3] for r in conn.execute(f"PRAGMA table_info({TABLE})")]

    def test_fresh_old_reopen_and_legacy_parent_rebuild(self):
        self.require_module()
        model = self.model()
        with model.get_connection() as conn:
            self.assertEqual(self.columns(conn), GOOD_COLUMNS)
            self.assertIsNone(library_schema_error(conn, allow_missing=False))
            conn.execute("INSERT INTO prompts(id,text,hash) VALUES(7,'p','p')")
            conn.execute(f"INSERT INTO {TABLE} VALUES(7,'Title')")
            conn.execute("DELETE FROM prompts WHERE id=7")
            self.assertEqual(conn.execute(f"SELECT * FROM {TABLE}").fetchall(), [])
        # An old database without the table gains it, repeatedly and idempotently.
        with model.get_connection() as conn:
            conn.execute(f"DROP TABLE {TABLE}")
            self.assertIsNone(library_schema_error(conn))
            self.assertIsNotNone(library_schema_error(conn, allow_missing=False))
        model.close_all()
        for _ in range(2):
            PromptModel.reset_schema_cache()
            model = self.model()
            with model.get_connection() as conn:
                self.assertEqual(self.columns(conn), GOOD_COLUMNS)
                self.assertIsNone(library_schema_error(conn, allow_missing=False))
            model.close_all()
        # A legacy parent rebuild (FK-disabled copy and swap) keeps state rows.
        legacy = str(Path(self.temp.name) / "legacy.db")
        with closing(sqlite3.connect(legacy)) as raw, raw:
            raw.execute(
                "CREATE TABLE prompts(id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "text TEXT NOT NULL,created_at TIMESTAMP,updated_at TIMESTAMP,"
                "category TEXT,tags TEXT,rating INTEGER,notes TEXT,hash TEXT UNIQUE,"
                "workflow_name TEXT)"
            )
            raw.execute("INSERT INTO prompts(id,text,hash) VALUES(7,'legacy','l')")
            raw.execute(
                f"CREATE TABLE {TABLE}(prompt_id INTEGER PRIMARY KEY, title TEXT, {FK})"
            )
            raw.execute(f"INSERT INTO {TABLE} VALUES(7,'Kept')")
        rebuilt = self.model(legacy)
        with rebuilt.get_connection() as conn:
            self.assertEqual(
                [tuple(r) for r in conn.execute(f"SELECT * FROM {TABLE}")],
                [(7, "Kept")],
            )
            self.assertIsNone(library_schema_error(conn, allow_missing=False))
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def build_invalid(self, name, definition, rows=()):
        path = str(Path(self.temp.name) / f"{name.replace(' ', '-')}.db")
        model = self.model(path)
        with model.get_connection() as conn:
            conn.execute("INSERT INTO prompts(id,text,hash) VALUES(7,'p','p')")
        model.close_all()
        with closing(sqlite3.connect(path)) as raw, raw:
            raw.execute("PRAGMA foreign_keys=OFF")
            raw.execute(f"DROP TABLE {TABLE}")
            if definition == "VIEW":
                raw.execute(
                    f"CREATE VIEW {TABLE} AS SELECT id AS prompt_id FROM prompts"
                )
            else:
                raw.execute(definition)
            for sql, args in rows:
                raw.execute(sql, args)
        return path

    def assert_rejected_unrepaired(self, path):
        with closing(sqlite3.connect(path)) as raw:
            before = raw.execute(
                "SELECT type,name,sql FROM sqlite_master ORDER BY name"
            ).fetchall()
            self.assertIsNotNone(library_schema_error(raw, allow_missing=False))
            self.assertIsNotNone(library_schema_error(raw))
        self.assertFalse(PromptModel.verify_database_file(path)[0])
        PromptModel.reset_schema_cache()
        with self.assertRaises(sqlite3.DatabaseError):
            self.model(path)
        PromptModel.reset_schema_cache()
        with closing(sqlite3.connect(path)) as raw:
            self.assertEqual(
                raw.execute(
                    "SELECT type,name,sql FROM sqlite_master ORDER BY name"
                ).fetchall(),
                before,
            )

    def test_invalid_schema_is_rejected_without_repair(self):
        self.require_module()
        cases = dict(INVALID_SCHEMAS)
        for name, definition in cases.items():
            with self.subTest(name):
                self.assert_rejected_unrepaired(self.build_invalid(name, definition))
        valid = INVALID_SCHEMAS["check"].replace(" CHECK(length(title) < 5)", "")
        values = {
            "orphan": ("INSERT INTO {t} VALUES(404,'x')", ()),
            "blob title": ("INSERT INTO {t} VALUES(7,?)", (b"blob",)),
            "long title": ("INSERT INTO {t} VALUES(7,?)", ("x" * 10_001,)),
        }
        for name, (sql, args) in values.items():
            with self.subTest(name):
                path = self.build_invalid(name, valid, [(sql.format(t=TABLE), args)])
                self.assert_rejected_unrepaired(path)
        with self.subTest("missing"):
            model = self.model(str(Path(self.temp.name) / "missing.db"))
            with model.get_connection() as conn:
                conn.execute(f"DROP TABLE {TABLE}")
                self.assertIsNone(library_schema_error(conn))
                self.assertEqual(
                    library_schema_error(conn, allow_missing=False),
                    "Missing Library state schema",
                )
        with self.subTest("boundary"):
            path = self.build_invalid(
                "boundary",
                valid,
                [
                    (f"INSERT INTO {TABLE} VALUES(7,?)", ("x" * 10_000,)),
                ],
            )
            self.assertEqual(PromptModel.verify_database_file(path)[0], True)

    def test_install_failure_rolls_back_and_retries(self):
        self.require_module()
        with patch(
            "database.library_schema.library_schema_error",
            side_effect=[None, "injected final validation failure"],
        ):
            with self.assertRaisesRegex(sqlite3.DatabaseError, "injected"):
                self.model()
        with closing(sqlite3.connect(self.path)) as raw:
            self.assertIsNone(
                raw.execute(
                    "SELECT name FROM sqlite_master WHERE name=?", (TABLE,)
                ).fetchone()
            )
        PromptModel.reset_schema_cache()
        model = self.model()
        self.assertEqual(
            PromptModel.verify_database_file(model.db_path, require_library_state=True),
            (True, "ok"),
        )

    def test_wal_restore_preserves_library_and_frozen_history(self):
        self.require_module()
        from database.execution_history_schema import history_schema_error

        source_path = str(Path(self.temp.name) / "source.db")
        source = self.model(source_path)
        with source.get_connection() as conn:
            conn.execute("PRAGMA wal_autocheckpoint=0")
            conn.execute("INSERT INTO prompts(id,text,hash) VALUES(7,'p','p')")
            conn.execute(f"INSERT INTO {TABLE} VALUES(7,'Restored title')")
            conn.commit()
            frozen_rows_before = conn.execute("SELECT * FROM execution_runs").fetchall()
            epoch_before = conn.execute(
                "SELECT storage_epoch FROM execution_history_metadata"
            ).fetchone()[0]
        self.assertGreater(Path(source_path + "-wal").stat().st_size, 0)
        live = self.model()
        with live.get_connection() as conn:
            conn.execute("INSERT INTO prompts(id,text,hash) VALUES(1,'live','live')")
            previous_epoch = conn.execute(
                "SELECT storage_epoch FROM execution_history_metadata"
            ).fetchone()[0]
        source_before = Path(source_path).read_bytes()
        self.assertEqual(
            PromptModel.verify_database_file(source_path, require_library_state=True),
            (True, "ok"),
        )
        backup = live.restore_from_file(source_path)
        self.assertTrue(Path(backup).is_file())
        self.assertEqual(Path(source_path).read_bytes(), source_before)
        with live.get_connection() as conn:
            restored_state = [tuple(r) for r in conn.execute(f"SELECT * FROM {TABLE}")]
            frozen_rows_after = conn.execute("SELECT * FROM execution_runs").fetchall()
            epoch_after = conn.execute(
                "SELECT storage_epoch FROM execution_history_metadata"
            ).fetchone()[0]
            self.assertIsNone(library_schema_error(conn, allow_missing=False))
            self.assertIsNone(history_schema_error(conn, allow_missing=False))
        self.assertEqual(restored_state, [(7, "Restored title")])
        self.assertEqual(frozen_rows_after, frozen_rows_before)
        self.assertNotEqual(epoch_after, previous_epoch)
        self.assertNotEqual(epoch_after, epoch_before)
        with closing(sqlite3.connect(backup)) as raw:
            self.assertEqual(raw.execute("SELECT id FROM prompts").fetchall(), [(1,)])

    def test_staging_failures_preserve_source_live_safety_and_handles(self):
        self.require_module()
        live = self.model()
        with live.get_connection() as conn:
            conn.execute("INSERT INTO prompts(id,text,hash) VALUES(1,'live','live')")
            conn.execute(f"INSERT INTO {TABLE} VALUES(1,'Live title')")
        # A source with an invalid Library table is refused before any change.
        bad = self.build_invalid("restore bad", INVALID_SCHEMAS["check"])
        # Same defect in the staged copy must be refused too.
        old = str(Path(self.temp.name) / "old.db")
        old_model = self.model(old)
        old_model.close_all()
        with closing(sqlite3.connect(old)) as raw, raw:
            raw.execute(f"DROP TABLE {TABLE}")
        for source, patched in ((bad, False), (old, True)):
            with self.subTest(source=Path(source).name):
                before = Path(source).read_bytes()
                safety = sorted(Path(self.temp.name).glob("live.db.backup_*"))
                if patched:
                    context = patch(
                        "database.models.migrate_library_state",
                        side_effect=sqlite3.OperationalError("staging failed"),
                    )
                    expected = sqlite3.OperationalError
                else:
                    context = patch.object(PromptModel, "backup_database")
                    expected = ValueError
                with context as hook, self.assertRaises(expected):
                    live.restore_from_file(source)
                if not patched:
                    hook.assert_not_called()
                self.assertEqual(Path(source).read_bytes(), before)
                self.assertEqual(
                    sorted(Path(self.temp.name).glob("live.db.backup_*")), safety
                )
                with live.get_connection() as conn:
                    self.assertEqual(
                        [tuple(r) for r in conn.execute(f"SELECT title FROM {TABLE}")],
                        [("Live title",)],
                    )
        self.assertEqual(
            PromptModel.verify_database_file(old, require_library_state=True)[0], False
        )
        self.assertEqual(PromptModel.verify_database_file(old)[0], True)


if __name__ == "__main__":
    unittest.main()
