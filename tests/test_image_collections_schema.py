"""Collections schema (#331): install, validation and restore safety."""

import re
import sqlite3
import tempfile
import unittest
import unicodedata
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

try:
    from tests.open_handles import assert_closed
    from tests.test_annotation_schema import LEGACY_TABLES, table_rows
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed
    from test_annotation_schema import LEGACY_TABLES, table_rows

from database.models import PromptModel

try:  # guarded so a missing module fails assertions, not test loading
    from database import image_collections
    from database.image_collections import (
        image_collections_schema_error,
        migrate_image_collections,
    )
except ImportError:
    image_collections = None
    image_collections_schema_error = None
    migrate_image_collections = None

COLLECTIONS = "image_collections"
ITEMS = "image_collection_items"
INDEX = "idx_image_collection_items_file"
TABLES = (COLLECTIONS, ITEMS)
DDL = {
    COLLECTIONS: (
        f"CREATE TABLE {COLLECTIONS} (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE, "
        "created_at TEXT NOT NULL)"
    ),
    ITEMS: (
        f"CREATE TABLE {ITEMS} (collection_id INTEGER NOT NULL REFERENCES "
        f"{COLLECTIONS}(id) ON DELETE CASCADE, root_key TEXT NOT NULL, "
        "relative_path TEXT NOT NULL, added_at TEXT NOT NULL, "
        "PRIMARY KEY (collection_id, root_key, relative_path))"
    ),
}
INDEX_DDL = f"CREATE INDEX {INDEX} ON {ITEMS}(root_key, relative_path)"
NOW = "2026-10-27T00:00:00.000000+00:00"
IMAGES = 23_000


def _compact(sql):
    return re.sub(r"\s+", "", sql or "").lower()


def expected_catalog():
    return {
        ("table", COLLECTIONS, _compact(DDL[COLLECTIONS])),
        ("table", ITEMS, _compact(DDL[ITEMS])),
        ("index", INDEX, _compact(INDEX_DDL)),
        ("index", f"sqlite_autoindex_{COLLECTIONS}_1", ""),
        ("index", f"sqlite_autoindex_{ITEMS}_1", ""),
    }


def catalog(conn):
    return {
        (kind, name, _compact(sql))
        for kind, name, sql in conn.execute(
            "SELECT type,name,sql FROM sqlite_master "
            "WHERE name IN (?,?,?) OR tbl_name IN (?,?)",
            (*TABLES, INDEX, *TABLES),
        )
    }


def key(name):
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", name).casefold())


def schema(conn, exclude=()):
    return sorted(
        tuple(row)
        for row in conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name"
        )
        if row[1] not in exclude and row[2] not in exclude
    )


def row_counts(conn):
    tables = [
        name
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    ]
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # nosec
        for table in tables
    }


class ImageCollectionsSchemaTests(unittest.TestCase):
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
            image_collections_schema_error,
            "database.image_collections is not implemented",
        )

    def closed_current(self, name="current.db", *statements):
        """A closed current database, altered afterwards by *statements*."""
        path = str(Path(self.temp.name) / name)
        model = self.model(path)
        model.close_all()
        PromptModel.reset_schema_cache()
        with closing(sqlite3.connect(path)) as raw, raw:
            raw.execute("PRAGMA foreign_keys=OFF")
            for statement in statements:
                if isinstance(statement, tuple):
                    raw.execute(*statement)
                else:
                    raw.execute(statement)
        return path

    @staticmethod
    def drop_group(path):
        with closing(sqlite3.connect(path)) as raw, raw:
            raw.execute(f"DROP TABLE {ITEMS}")
            raw.execute(f"DROP TABLE {COLLECTIONS}")

    def test_migration_creates_tables_and_index(self):
        self.require_module()
        model = self.model()
        with model.get_connection() as conn:
            self.assertEqual(catalog(conn), expected_catalog())
            self.assertIsNone(image_collections_schema_error(conn, allow_missing=False))
            self.assertEqual(
                conn.execute(f"SELECT COUNT(*) FROM {COLLECTIONS}").fetchone()[0], 0
            )
            self.assertEqual(
                conn.execute(f"SELECT COUNT(*) FROM {ITEMS}").fetchone()[0], 0
            )
        self.assertEqual(
            PromptModel.verify_database_file(self.path, require_library_state=True),
            (True, "ok"),
        )

    def test_migration_is_idempotent(self):
        self.require_module()
        model = self.model()
        with model.get_connection() as conn:
            conn.execute(
                f"INSERT INTO {COLLECTIONS} (name, name_key, created_at) "
                "VALUES ('Kept', 'kept', ?)",
                (NOW,),
            )
            conn.execute(f"INSERT INTO {ITEMS} VALUES (1, 'root', 'a.png', ?)", (NOW,))
            before = schema(conn)
        model.close_all()
        for _ in range(2):
            PromptModel.reset_schema_cache()
            model = self.model()
            with model.get_connection() as conn:
                migrate_image_collections(conn)
                self.assertEqual(schema(conn), before)
                self.assertEqual(
                    [tuple(r) for r in conn.execute(f"SELECT * FROM {ITEMS}")],
                    [(1, "root", "a.png", NOW)],
                )
            model.close_all()

    def test_migration_reads_only_its_own_tables(self):
        self.require_module()
        model = self.model()
        with model.get_connection() as conn:
            conn.execute("INSERT INTO prompts(id,text,hash) VALUES(1,'positive','p')")
            conn.executemany(
                "INSERT INTO generated_images(prompt_id,image_path,filename,"
                "file_path) VALUES(1,?,?,?)",
                ((f"/out/{n}.png", f"{n}.png", f"/out/{n}.png") for n in range(IMAGES)),
            )
            conn.execute("INSERT INTO image_favorites VALUES('root','a.png',?)", (NOW,))
        model.close_all()
        self.drop_group(self.path)
        with closing(sqlite3.connect(self.path)) as raw:
            raw.execute("PRAGMA foreign_keys=ON")
            counts_before = row_counts(raw)
            schema_before = schema(raw)
            legacy_before = table_rows(raw, LEGACY_TABLES)
            self.assertEqual(counts_before["generated_images"], IMAGES)
            touched = []

            def record(action, arg1, arg2, _db, _trigger):
                touched.append((action, arg1, arg2))
                return sqlite3.SQLITE_OK

            raw.set_authorizer(record)
            try:
                migrate_image_collections(raw)
            finally:
                raw.set_authorizer(None)
            raw.commit()
            reads = {
                arg1 for action, arg1, _arg2 in touched if action == sqlite3.SQLITE_READ
            }
            self.assertTrue(reads)
            self.assertLessEqual(reads, {"sqlite_master", COLLECTIONS, ITEMS})
            writes = {
                arg1
                for action, arg1, _arg2 in touched
                if action
                in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE)
            }
            self.assertLessEqual(writes, {"sqlite_master"})
            self.assertEqual(catalog(raw), expected_catalog())
            counts_after = row_counts(raw)
            self.assertEqual(counts_after.pop(COLLECTIONS), 0)
            self.assertEqual(counts_after.pop(ITEMS), 0)
            self.assertEqual(counts_after, counts_before)
            self.assertEqual(schema(raw, exclude=TABLES), schema_before)
            self.assertEqual(table_rows(raw, LEGACY_TABLES), legacy_before)
            # A second run is a no-op.
            after_first = schema(raw)
            changes = raw.total_changes
            migrate_image_collections(raw)
            raw.commit()
            self.assertEqual(schema(raw), after_first)
            self.assertEqual(raw.total_changes, changes)

    def test_migration_refuses_a_foreign_object_of_the_same_name(self):
        self.require_module()
        for statements in (
            (f"CREATE TABLE {COLLECTIONS} (x)",),
            (f"CREATE VIEW {COLLECTIONS} AS SELECT 1",),
            (f"CREATE INDEX {INDEX} ON prompts(text)",),
        ):
            with self.subTest(statements=statements):
                path = str(Path(self.temp.name) / f"foreign{len(self.models)}.db")
                self.model(path).close_all()
                PromptModel.reset_schema_cache()
                self.drop_group(path)
                with closing(sqlite3.connect(path)) as raw, raw:
                    for statement in statements:
                        raw.execute(statement)
                with closing(sqlite3.connect(path)) as raw:
                    before = schema(raw)
                    self.assertIsNotNone(image_collections_schema_error(raw))
                with self.assertRaises(sqlite3.DatabaseError):
                    self.model(path).get_connection()
                PromptModel.close_all_instances()
                PromptModel.reset_schema_cache()
                with closing(sqlite3.connect(path)) as raw:
                    self.assertEqual(schema(raw), before)
                    self.assertIsNone(
                        raw.execute(
                            "SELECT 1 FROM sqlite_master WHERE name=?", (ITEMS,)
                        ).fetchone()
                    )
                self.assertFalse(PromptModel.verify_database_file(path)[0])

    def test_migration_rolls_back_on_error(self):
        self.require_module()
        self.model().close_all()
        PromptModel.reset_schema_cache()
        self.drop_group(self.path)
        with closing(sqlite3.connect(self.path)) as raw:
            with patch.object(
                image_collections,
                "image_collections_schema_error",
                side_effect=[None, "Image collections went wrong"],
            ):
                with self.assertRaisesRegex(sqlite3.DatabaseError, "went wrong"):
                    migrate_image_collections(raw)
            raw.commit()
            self.assertEqual(catalog(raw), set())

    def test_startup_accepts_a_value_saved_under_older_rules(self):
        self.require_module()
        path = self.closed_current(
            "older.db",
            (
                f"INSERT INTO {COLLECTIONS} (name, name_key, created_at) "
                "VALUES (?, 'not the key', ?)",
                (" " + "x" * 150 + " ", NOW),
            ),
        )
        model = self.model(path)
        with model.get_connection() as conn:
            self.assertIsNone(image_collections_schema_error(conn, check_values=False))
            self.assertIsNotNone(image_collections_schema_error(conn))
            self.assertEqual(
                conn.execute(f"SELECT COUNT(*) FROM {COLLECTIONS}").fetchone()[0], 1
            )
        model.close_all()
        self.assertFalse(
            PromptModel.verify_database_file(path, require_library_state=True)[0]
        )

    def test_restore_accepts_an_old_backup_without_collections(self):
        self.require_module()
        path = self.closed_current("old.db")
        self.drop_group(path)
        with closing(sqlite3.connect(path)) as raw:
            self.assertIsNone(image_collections_schema_error(raw))
            self.assertEqual(
                image_collections_schema_error(raw, allow_missing=False),
                "Missing image collections schema",
            )
        self.assertEqual(PromptModel.verify_database_file(path), (True, "ok"))
        model = self.model(path)
        with model.get_connection() as conn:
            self.assertEqual(catalog(conn), expected_catalog())
        model.close_all()
        self.assertEqual(
            PromptModel.verify_database_file(path, require_library_state=True),
            (True, "ok"),
        )

    def test_restore_requires_collections_on_a_current_stage(self):
        self.require_module()
        path = self.closed_current("stage.db")
        self.drop_group(path)
        self.assertEqual(
            PromptModel.verify_database_file(path, require_library_state=True),
            (False, "Missing image collections schema"),
        )
        # One table without the other is never acceptable.
        partial = self.closed_current("partial.db", f"DROP TABLE {ITEMS}")
        self.assertFalse(PromptModel.verify_database_file(partial)[0])

    def assert_refused(self, name, *statements):
        self.require_module()
        path = self.closed_current(
            f"{name}.db",
            (
                f"INSERT INTO {COLLECTIONS} (id, name, name_key, created_at) "
                "VALUES (1, 'Good', 'good', ?)",
                (NOW,),
            ),
            (f"INSERT INTO {ITEMS} VALUES (1, 'root', 'a.png', ?)", (NOW,)),
            *statements,
        )
        with closing(sqlite3.connect(path)) as raw:
            self.assertIsNone(image_collections_schema_error(raw, check_values=False))
            error = image_collections_schema_error(raw)
        self.assertIsNotNone(error)
        self.assertIn("collections", error.lower())
        ok, reason = PromptModel.verify_database_file(path)
        self.assertFalse(ok)
        self.assertEqual(reason, error)
        self.assertFalse(
            PromptModel.verify_database_file(path, require_library_state=True)[0]
        )

    def test_a_valid_current_database_is_accepted(self):
        self.require_module()
        path = self.closed_current(
            "valid.db",
            (
                f"INSERT INTO {COLLECTIONS} (id, name, name_key, created_at) "
                "VALUES (1, ?, ?, ?)",
                ("LoRa testing", key("LoRa testing"), NOW),
            ),
            (f"INSERT INTO {ITEMS} VALUES (1, 'root', ?, ?)", ("é" * 4096, NOW)),
        )
        self.assertEqual(
            PromptModel.verify_database_file(path, require_library_state=True),
            (True, "ok"),
        )

    def test_restore_refuses_a_non_text_name(self):
        self.assert_refused("int-name", f"UPDATE {COLLECTIONS} SET name = 5")

    def test_restore_refuses_an_empty_name(self):
        self.assert_refused("empty", f"UPDATE {COLLECTIONS} SET name = '', name_key=''")

    def test_restore_refuses_a_name_over_100_characters(self):
        name = "é" * 101
        self.assert_refused(
            "long",
            (f"UPDATE {COLLECTIONS} SET name = ?, name_key = ?", (name, key(name))),
        )

    def test_restore_refuses_a_control_character(self):
        name = "a\x07b"
        self.assert_refused(
            "control",
            (f"UPDATE {COLLECTIONS} SET name = ?, name_key = ?", (name, key(name))),
        )

    def test_restore_refuses_a_leading_or_trailing_space(self):
        for name in (" Good", "Good "):
            with self.subTest(name=name):
                self.assert_refused(
                    f"padded{len(name)}{name[0] == ' '}",
                    (
                        f"UPDATE {COLLECTIONS} SET name = ?, name_key = ?",
                        (name, key(name)),
                    ),
                )

    def test_restore_refuses_a_wrong_name_key(self):
        self.assert_refused("key", f"UPDATE {COLLECTIONS} SET name_key = 'Good'")

    def test_restore_refuses_a_non_text_key(self):
        # Column affinity turns a number into text; a blob stays a blob.
        self.assert_refused("blob-key", f"UPDATE {ITEMS} SET relative_path = x'61'")

    def test_restore_refuses_a_key_over_4096_characters(self):
        self.assert_refused(
            "long-key", (f"UPDATE {ITEMS} SET root_key = ?", ("é" * 4097,))
        )

    def test_restore_refuses_a_timestamp_over_64_bytes(self):
        self.assert_refused(
            "stamp", (f"UPDATE {COLLECTIONS} SET created_at = ?", ("t" * 65,))
        )
        self.assert_refused(
            "item-stamp", (f"UPDATE {ITEMS} SET added_at = ?", ("t" * 65,))
        )

    def test_restore_refuses_an_item_of_a_missing_collection(self):
        self.assert_refused("orphan", f"INSERT INTO {ITEMS} VALUES (9, 'r', 'b', 'n')")

    def test_restore_refuses_a_non_integer_collection_id(self):
        self.assert_refused("text-id", f"UPDATE {ITEMS} SET collection_id = 'one'")

    def test_restore_refuses_a_trigger_or_extra_index(self):
        self.require_module()
        for name, statement in (
            (
                "trigger",
                f"CREATE TRIGGER t AFTER INSERT ON {ITEMS} BEGIN SELECT 1; END",
            ),
            ("extra", f"CREATE INDEX extra ON {COLLECTIONS}(name)"),
        ):
            with self.subTest(name=name):
                path = self.closed_current(f"{name}.db", statement)
                with closing(sqlite3.connect(path)) as raw:
                    self.assertIsNotNone(image_collections_schema_error(raw))
                self.assertFalse(PromptModel.verify_database_file(path)[0])

    def test_deleting_a_collection_deletes_its_members(self):
        self.require_module()
        model = self.model()
        with model.get_connection() as conn:
            conn.execute(
                f"INSERT INTO {COLLECTIONS} (id, name, name_key, created_at) "
                "VALUES (1, 'A', 'a', ?), (2, 'B', 'b', ?)",
                (NOW, NOW),
            )
            conn.executemany(
                f"INSERT INTO {ITEMS} VALUES (?, 'root', ?, ?)",
                ((1, "a.png", NOW), (1, "b.png", NOW), (2, "a.png", NOW)),
            )
            conn.execute(f"DELETE FROM {COLLECTIONS} WHERE id = 1")
            self.assertEqual(
                [tuple(r) for r in conn.execute(f"SELECT collection_id FROM {ITEMS}")],
                [(2,)],
            )


if __name__ == "__main__":
    unittest.main()
