"""Image categories (#332): schema, migration, restore check, store and file key."""

import hashlib
import ntpath
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

try:
    from open_handles import assert_closed
except ImportError:
    from tests.open_handles import assert_closed

from database.image_categories import (
    COPY_SQL,
    INDEX,
    STATEMENTS,
    TABLE,
    copy_image_category,
    image_categories_schema_error,
    migrate_image_categories,
)
from database.models import PromptModel
from database.operations import PromptDatabase
from py.api.images import image_category_key

_DDL = (
    f"CREATE TABLE {TABLE} (root_key TEXT NOT NULL, relative_path TEXT NOT NULL, "
    "category TEXT NOT NULL, updated_at TEXT NOT NULL, "
    "PRIMARY KEY (root_key, relative_path))"
)
_INDEX_DDL = f"CREATE INDEX {INDEX} ON {TABLE}(category)"


def category_checksum(conn) -> tuple:
    """sha256 of prompts and prompt_pairs ``(id, category, updated_at)``."""
    sums = []
    for table in ("prompts", "prompt_pairs"):
        digest = hashlib.sha256()
        for row in conn.execute(  # nosec B608 - fixed table names
            f"SELECT id, category, updated_at FROM {table} ORDER BY id"
        ):
            digest.update(repr(tuple(row)).encode())
        sums.append(digest.hexdigest())
    return tuple(sums)


def table_checksums(conn) -> dict:
    """``{name: (schema sql, sha256 of rows)}`` of every table but ours."""
    found = {}
    for name, sql in conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' AND name != ?",
        (TABLE,),
    ).fetchall():
        digest = hashlib.sha256()
        rows = conn.execute(f'SELECT * FROM "{name}"')  # nosec B608
        for text in sorted(repr(tuple(row)) for row in rows):
            digest.update(text.encode())
        found[name] = (sql, digest.hexdigest())
    return found


class _TempDir(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.path = os.path.join(self.tmpdir, "categories.db")

    def tearDown(self):
        PromptModel.close_all_instances()
        assert_closed(self, self.tmpdir)
        for name in os.listdir(self.tmpdir):
            os.unlink(os.path.join(self.tmpdir, name))
        os.rmdir(self.tmpdir)


class TestImageCategoriesSchema(_TempDir):
    def test_migration_creates_table_and_index(self):
        db = PromptDatabase(self.path)
        db.close_all()
        PromptModel.close_all_instances()
        with closing(sqlite3.connect(self.path)) as conn:
            names = {
                (kind, name)
                for kind, name in conn.execute(
                    "SELECT type, name FROM sqlite_master WHERE tbl_name=?", (TABLE,)
                )
            }
            self.assertEqual(
                names,
                {
                    ("table", TABLE),
                    ("index", INDEX),
                    ("index", f"sqlite_autoindex_{TABLE}_1"),
                },
            )
            self.assertEqual(
                conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0], 0
            )
            self.assertIsNone(image_categories_schema_error(conn, allow_missing=False))

    def test_migration_leaves_prompt_categories_unchanged(self):
        """23,000 links; the migration reads and writes only its own objects."""
        db = PromptDatabase(self.path)
        db.close_all()
        PromptModel.close_all_instances()
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute(f"DROP TABLE {TABLE}")  # a library from before #332
            categories = ["Landscapes", "Portraits", None, "  ", "Scans"]
            conn.executemany(
                "INSERT INTO prompts (text, category, hash, updated_at) "
                "VALUES (?, ?, ?, ?)",
                [
                    (f"p{n}", categories[n % 5], f"h{n}", f"2026-01-01 00:00:{n % 60}")
                    for n in range(200)
                ],
            )
            conn.executemany(
                "INSERT INTO prompt_pairs (positive_prompt_id, negative_prompt_id, "
                "category) VALUES (?, ?, ?)",
                [(n, n + 100, categories[(n + 1) % 5]) for n in range(1, 50)],
            )
            conn.executemany(
                "INSERT INTO generated_images (prompt_id, image_path, filename, "
                "file_path) VALUES (?, ?, ?, ?)",
                [
                    (n % 200 + 1, f"/out/{n}.png", f"{n}.png", f"/out/{n}.png")
                    for n in range(23000)
                ],
            )
            conn.commit()
            before = table_checksums(conn)
            prompts_before = category_checksum(conn)
            seen = []

            def authorize(action, arg1, arg2, _db, _trigger):
                seen.append((action, arg1, arg2))
                return sqlite3.SQLITE_OK

            conn.set_authorizer(authorize)
            migrate_image_categories(conn)
            conn.commit()
            conn.set_authorizer(None)
            own = {TABLE, "sqlite_master", "sqlite_schema", INDEX}
            touched = set()
            for action, arg1, arg2 in seen:
                if action in (sqlite3.SQLITE_READ, sqlite3.SQLITE_INSERT):
                    touched.add(arg1)
                if action == sqlite3.SQLITE_READ:
                    self.assertIn(arg1, own, (action, arg1, arg2))
                elif action in (
                    sqlite3.SQLITE_INSERT,
                    sqlite3.SQLITE_UPDATE,
                    sqlite3.SQLITE_DELETE,
                ):
                    self.assertIn(arg1, own, (action, arg1, arg2))
                elif action == sqlite3.SQLITE_CREATE_TABLE:
                    self.assertEqual(arg1, TABLE)
                elif action == sqlite3.SQLITE_CREATE_INDEX:
                    self.assertIn(arg1, (INDEX, f"sqlite_autoindex_{TABLE}_1"))
                    self.assertEqual(arg2, TABLE)
                elif action == sqlite3.SQLITE_PRAGMA:
                    self.assertEqual((arg1, arg2), ("table_info", TABLE))
            self.assertIn(TABLE, touched)  # the authorizer saw the work
            self.assertEqual(table_checksums(conn), before)
            self.assertEqual(category_checksum(conn), prompts_before)
            schema = conn.execute(
                "SELECT sql FROM sqlite_master ORDER BY name"
            ).fetchall()
            migrate_image_categories(conn)  # a second run changes nothing
            conn.commit()
            self.assertEqual(
                conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall(),
                schema,
            )
        # The whole startup sequence keeps them too.
        PromptDatabase(self.path).close_all()
        PromptModel.close_all_instances()
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(category_checksum(conn), prompts_before)
            self.assertEqual(
                conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0], 0
            )

    def test_structure_accepted_and_refused(self):
        variants = {
            "missing": None,
            "extra column": (
                f"CREATE TABLE {TABLE} (root_key TEXT NOT NULL, relative_path TEXT "
                "NOT NULL, category TEXT NOT NULL, updated_at TEXT NOT NULL, x TEXT, "
                "PRIMARY KEY (root_key, relative_path))",
                _INDEX_DDL,
            ),
            "wrong index": (_DDL, f"CREATE INDEX {INDEX} ON {TABLE}(updated_at)"),
            "no index": (_DDL,),
            "extra index": (
                _DDL,
                _INDEX_DDL,
                f"CREATE INDEX extra ON {TABLE}(root_key)",
            ),
            "trigger": (
                _DDL,
                _INDEX_DDL,
                f"CREATE TRIGGER t AFTER INSERT ON {TABLE} BEGIN SELECT 1; END",
            ),
            "wrong ddl": (
                f"CREATE TABLE {TABLE} (root_key TEXT, relative_path TEXT, "
                "category TEXT, updated_at TEXT, "
                "PRIMARY KEY (root_key, relative_path))",
                _INDEX_DDL,
            ),
            "view": (f"CREATE VIEW {TABLE} AS SELECT 1",),
        }
        for label, statements in variants.items():
            with self.subTest(label), closing(sqlite3.connect(":memory:")) as conn:
                for sql in statements or ():
                    conn.execute(sql)
                error = image_categories_schema_error(conn)
                if statements is None:
                    self.assertIsNone(error)
                    self.assertIsNotNone(
                        image_categories_schema_error(conn, allow_missing=False)
                    )
                    continue
                self.assertIsNotNone(error)
                self.assertIsNotNone(
                    image_categories_schema_error(conn, check_values=False)
                )
                with self.assertRaises(sqlite3.DatabaseError):
                    migrate_image_categories(conn)
        with closing(sqlite3.connect(":memory:")) as conn:
            migrate_image_categories(conn)
            migrate_image_categories(conn)  # idempotent
            self.assertIsNone(image_categories_schema_error(conn, allow_missing=False))

    def test_values_refused_but_startup_checks_structure_only(self):
        bad = {
            "blank": "",
            "101 characters": "x" * 101,
            "control character": "a\tb",
            "nul": "a\x00b",
            "leading space": " Landscapes",
            "trailing space": "Landscapes ",
        }
        for label, value in bad.items():
            with self.subTest(label), closing(sqlite3.connect(":memory:")) as conn:
                migrate_image_categories(conn)
                conn.execute(
                    f"INSERT INTO {TABLE} VALUES ('r', 'a.png', ?, 'now')", (value,)
                )
                self.assertIsNotNone(image_categories_schema_error(conn))
                self.assertIsNone(
                    image_categories_schema_error(conn, check_values=False)
                )
                migrate_image_categories(conn)  # startup still opens it
        with closing(sqlite3.connect(":memory:")) as conn:
            migrate_image_categories(conn)
            conn.execute(
                f"INSERT INTO {TABLE} VALUES ('r', ?, 'Scans', 'now')", ("x" * 5000,)
            )
            self.assertIsNotNone(image_categories_schema_error(conn))
            conn.execute(f"DELETE FROM {TABLE}")
            conn.execute(f"INSERT INTO {TABLE} VALUES ('r', x'00ff', 'Scans', 'now')")
            self.assertIsNotNone(image_categories_schema_error(conn))
            conn.execute(f"DELETE FROM {TABLE}")
            for value in ("x" * 100, "Scans", "Café · 風景", "a b"):
                conn.execute(
                    f"INSERT OR REPLACE INTO {TABLE} VALUES ('r', 'a.png', ?, 'now')",
                    (value,),
                )
                self.assertIsNone(image_categories_schema_error(conn), value)

    def test_restore_check(self):
        PromptDatabase(self.path).close_all()
        PromptModel.close_all_instances()
        ok, reason = PromptModel.verify_database_file(
            self.path, require_library_state=True
        )
        self.assertTrue(ok, reason)
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute(f"DROP TABLE {TABLE}")
            conn.commit()
        # A backup from before #332 is accepted ...
        ok, reason = PromptModel.verify_database_file(self.path)
        self.assertTrue(ok, reason)
        # ... but a current database must have the table.
        ok, reason = PromptModel.verify_database_file(
            self.path, require_library_state=True
        )
        self.assertFalse(ok)
        self.assertIn("image categories", reason.lower())
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute(_DDL)
            conn.execute(_INDEX_DDL)
            conn.execute(f"INSERT INTO {TABLE} VALUES ('r', 'a.png', ' x', 'now')")
            conn.commit()
        ok, reason = PromptModel.verify_database_file(self.path)
        self.assertFalse(ok)
        self.assertIn("image categories", reason.lower())


class TestImageCategoryStore(_TempDir):
    def setUp(self):
        super().setUp()
        self.db = PromptDatabase(self.path)

    def tearDown(self):
        self.db.close_all()
        super().tearDown()

    def rows(self):
        return (
            self.db.model.get_connection()
            .execute(
                f"SELECT root_key, relative_path, category, updated_at FROM {TABLE} "
                "ORDER BY root_key, relative_path"
            )
            .fetchall()
        )

    def test_set_counts_updated_and_unchanged(self):
        keys = [("/out", "a.png"), ("/other", "a.png"), ("/out", "a.png")]
        self.assertEqual(
            self.db.set_image_categories(keys, "Portraits"),
            {"updated": 2, "unchanged": 0},
        )
        stamps = {(r[0], r[1]): r[3] for r in self.rows()}
        self.assertEqual(
            self.db.set_image_categories(
                [("/out", "a.png"), ("/out", "b.png")], "Portraits"
            ),
            {"updated": 1, "unchanged": 1},
        )
        # An unchanged file keeps its updated_at.
        self.assertEqual(
            {(r[0], r[1]): r[3] for r in self.rows()}[("/out", "a.png")],
            stamps[("/out", "a.png")],
        )
        self.assertEqual(
            self.db.set_image_categories([("/out", "a.png")], "Scans"),
            {"updated": 1, "unchanged": 0},
        )
        self.assertEqual(self.db.image_category("/out", "a.png"), "Scans")
        self.assertEqual(self.db.image_category("/other", "a.png"), "Portraits")
        self.assertIsNone(self.db.image_category("/out", "missing.png"))
        self.assertEqual(
            self.db.own_categories_of(["/out", "/out", "/none"]),
            {("/out", "a.png"): "Scans", ("/out", "b.png"): "Portraits"},
        )

    def test_clear_counts_removed_rows(self):
        self.db.set_image_categories([("/out", "a.png"), ("/b", "c.png")], "Scans")
        self.assertEqual(
            self.db.clear_image_categories(
                [("/out", "a.png"), ("/b", "c.png"), ("/out", "gone.png")]
            ),
            2,
        )
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.db.clear_image_categories([("/out", "a.png")]), 0)

    def test_a_failed_write_writes_nothing(self):
        self.db.set_image_categories([("/out", "a.png")], "Scans")
        keys = [("/out", "b.png"), ("/out", None)]  # NOT NULL fails the second
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.set_image_categories(keys, "Portraits")
        self.assertEqual([r[:3] for r in self.rows()], [("/out", "a.png", "Scans")])

    def test_store_never_touches_prompt_categories(self):
        conn = self.db.model.get_connection()
        with conn:
            conn.execute(
                "INSERT INTO prompts (text, category, hash) VALUES ('p', 'L', 'h')"
            )
        before = category_checksum(conn)
        seen = set()
        conn.set_authorizer(
            lambda action, arg1, *_: seen.add(arg1) or sqlite3.SQLITE_OK
        )
        try:
            self.db.set_image_categories([("/out", "a.png")], "Scans")
            self.db.own_categories_of(["/out"])
            self.db.image_category("/out", "a.png")
            self.db.clear_image_categories([("/out", "a.png")])
        finally:
            conn.set_authorizer(None)
        self.assertFalse(seen & {"prompts", "prompt_pairs", "generated_images"}, seen)
        self.assertEqual(category_checksum(conn), before)

    def test_copy_keeps_kept_copy_category(self):
        self.db.set_image_categories([("/out", "removed.png")], "Scans")
        self.db.set_image_categories([("/out", "kept.png")], "Portraits")
        with self.db._pair_transaction() as conn:
            inserted = copy_image_category(
                conn, ("/out", "removed.png"), ("/out", "kept.png")
            )
        self.assertEqual(inserted, 0)
        self.assertEqual(self.db.image_category("/out", "kept.png"), "Portraits")
        with self.db._pair_transaction() as conn:
            inserted = copy_image_category(
                conn, ("/out", "removed.png"), ("/out", "new.png")
            )
        self.assertEqual(inserted, 1)
        self.assertEqual(self.db.image_category("/out", "new.png"), "Scans")
        # The removed copy's row is never deleted.
        self.assertEqual(self.db.image_category("/out", "removed.png"), "Scans")
        with self.db._pair_transaction() as conn:
            self.assertEqual(
                copy_image_category(conn, ("/out", "none.png"), ("/out", "x.png")), 0
            )
            self.assertEqual(
                copy_image_category(
                    conn, ("/out", "removed.png"), ("/out", "removed.png")
                ),
                0,
            )

    def test_statements_never_scan_the_table(self):
        conn = self.db.model.get_connection()
        for sql in STATEMENTS:
            with self.subTest(sql):
                params = (None,) * sql.count("?")
                plan = " ".join(
                    row[-1] for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}", params)
                )
                self.assertNotRegex(plan, rf"SCAN {TABLE}\b", plan)
        plan = " ".join(
            row[-1]
            for row in conn.execute(
                f"EXPLAIN QUERY PLAN SELECT rowid FROM {TABLE} WHERE category=? "
                "ORDER BY rowid",
                ("x",),
            )
        )
        self.assertIn(INDEX, plan)
        self.assertIn(COPY_SQL, STATEMENTS)


class TestImageCategoryKey(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(os.path.realpath(self._tmp.name))
        self.outer = self.base / "output"
        self.inner = self.outer / "nested"
        (self.inner / "sub").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_equals_the_favorite_key_without_nesting(self):
        from py.api.images import _favorite_relative_key, _favorite_root_key

        key = image_category_key([self.outer], self.outer / "sub" / "a.png")
        self.assertEqual(
            key, (_favorite_root_key(self.outer), _favorite_relative_key("sub/a.png"))
        )

    def test_deepest_source_wins_in_any_order(self):
        from py.api.images import _favorite_root_key

        path = self.inner / "sub" / "a.png"
        for sources in ([self.outer, self.inner], [self.inner, self.outer]):
            with self.subTest(sources=sources):
                self.assertEqual(
                    image_category_key(sources, path),
                    (_favorite_root_key(self.inner), "sub/a.png"),
                )
        # A file of the outer source only is keyed by it.
        self.assertEqual(
            image_category_key([self.outer, self.inner], self.outer / "b.png"),
            (_favorite_root_key(self.outer), "b.png"),
        )

    def test_outside_every_source_has_no_key(self):
        self.assertIsNone(image_category_key([self.inner], self.outer / "a.png"))
        self.assertIsNone(image_category_key([], self.outer / "a.png"))
        # A sibling sharing a name prefix is not inside.
        self.assertIsNone(
            image_category_key([self.outer], self.base / "output2" / "a.png")
        )
        self.assertIsNone(image_category_key([self.outer], self.outer))

    def test_symlinked_subfolder_is_not_resolved(self):
        target = self.base / "elsewhere"
        target.mkdir()
        link = self.outer / "linked"
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are not available")
        key = image_category_key([self.outer], link / "a.png")
        self.assertEqual(key[1], "linked/a.png")

    def test_windows_folds_case_of_the_relative_key(self):
        with patch("py.api.images._path_flavor", ntpath):
            upper = image_category_key([self.outer], self.outer / "Sub" / "A.PNG")
            lower = image_category_key([self.outer], self.outer / "sub" / "a.png")
        self.assertEqual(upper[1], "sub/a.png")
        self.assertEqual(upper, lower)

    def test_posix_keeps_case_distinct(self):
        if os.path.normcase("A") == "a":
            self.skipTest("this OS folds case")
        upper = image_category_key([self.outer], self.outer / "A.png")
        lower = image_category_key([self.outer], self.outer / "a.png")
        self.assertNotEqual(upper, lower)


if __name__ == "__main__":
    unittest.main()
