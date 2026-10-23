"""Image annotation schema group (#249): install, validation and restore safety."""

import hashlib
import re
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
    from database.annotation_schema import (
        annotation_schema_error,
        migrate_annotation_state,
    )
except ImportError:
    annotation_schema_error = None
    migrate_annotation_state = None

METADATA = "media_annotation_metadata"
FILES = "media_annotation_files"
BYTES = "media_annotation_bytes"
IMAGE_TAGS = "media_image_tags"
TABLES = (METADATA, FILES, BYTES, IMAGE_TAGS)
REVIEW_TABLES = (
    "media_review_decisions",
    "media_review_items",
    "media_review_metadata",
)
# The exact DDL the plan pins; the module must produce precisely these objects.
DDL = {
    METADATA: (
        f"CREATE TABLE {METADATA}(singleton_id INTEGER PRIMARY KEY "
        "CHECK(singleton_id=1), schema_version INTEGER NOT NULL "
        "CHECK(schema_version=1), image_index_revision INTEGER NOT NULL DEFAULT 0 "
        "CHECK(image_index_revision>=0))"
    ),
    FILES: (
        f"CREATE TABLE {FILES}(id TEXT PRIMARY KEY CHECK(length(id)=36), "
        "locator TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)"
    ),
    BYTES: (
        f"CREATE TABLE {BYTES}(id TEXT PRIMARY KEY CHECK(length(id)=36), "
        f"file_id TEXT NOT NULL REFERENCES {FILES}(id) ON DELETE CASCADE, "
        "sha256 TEXT NOT NULL CHECK(length(sha256)=64 AND sha256 NOT GLOB "
        "'*[^0-9a-f]*'), size INTEGER NOT NULL CHECK(size>=0), "
        "mtime_ns INTEGER NOT NULL, verified_at TEXT NOT NULL, "
        "first_seen_at TEXT NOT NULL, tag_revision INTEGER NOT NULL DEFAULT 0 "
        "CHECK(tag_revision>=0), UNIQUE(file_id, sha256))"
    ),
    IMAGE_TAGS: (
        f"CREATE TABLE {IMAGE_TAGS}(byte_id TEXT NOT NULL REFERENCES {BYTES}(id) "
        "ON DELETE CASCADE, tag_id INTEGER NOT NULL REFERENCES tags(id) "
        "ON DELETE CASCADE, position INTEGER NOT NULL CHECK(position>=0), "
        "PRIMARY KEY(byte_id, tag_id))"
    ),
}
INDEXES = {
    "idx_media_annotation_bytes_fingerprint": (
        f"CREATE INDEX idx_media_annotation_bytes_fingerprint ON {BYTES}"
        "(file_id, size, mtime_ns)"
    ),
    "idx_media_image_tags_tag": (
        f"CREATE INDEX idx_media_image_tags_tag ON {IMAGE_TAGS}(tag_id)"
    ),
    "idx_media_image_tags_position": (
        f"CREATE UNIQUE INDEX idx_media_image_tags_position ON {IMAGE_TAGS}"
        "(byte_id, position)"
    ),
}
AUTOINDEXES = {FILES: 2, BYTES: 2, IMAGE_TAGS: 1}
LEGACY_TABLES = (
    "prompts",
    "generated_images",
    "tags",
    "prompt_tags",
    "prompt_pair_tags",
    "image_favorites",
)
FILE_ID = "0f8e4b2a-1c3d-4e5f-8a9b-0c1d2e3f4a5b"
BYTE_ID = "6a1b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d"
OTHER_ID = "7b2c3d4e-5f6a-4b7c-9d0e-1f2a3b4c5d6e"
DIGEST = hashlib.sha256(b"img").hexdigest()
NOW = "2026-10-22T00:00:00.000000+00:00"
MAX_LOCATOR = 4096


def _compact(sql):
    return re.sub(r"\s+", "", sql or "").lower()


def expected_catalog():
    objects = {("table", name, _compact(sql)) for name, sql in DDL.items()}
    objects |= {("index", name, _compact(sql)) for name, sql in INDEXES.items()}
    objects |= {
        ("index", f"sqlite_autoindex_{table}_{n}", "")
        for table, count in AUTOINDEXES.items()
        for n in range(1, count + 1)
    }
    return objects


def catalog(conn):
    names = tuple(TABLES) + tuple(INDEXES)
    marks = ",".join("?" * len(names))
    table_marks = ",".join("?" * len(TABLES))
    return {
        (kind, name, _compact(sql))
        for kind, name, sql in conn.execute(
            "SELECT type,name,sql FROM sqlite_master "
            f"WHERE name IN ({marks}) OR tbl_name IN ({table_marks})",  # nosec B608
            names + tuple(TABLES),
        )
    }


def insert_group_rows(conn, tag_name="img-only", locator="/out/img.png"):
    """One file, one byte record and one image tag carrying *tag_name*."""
    tag_id = conn.execute("INSERT INTO tags(name) VALUES(?)", (tag_name,)).lastrowid
    conn.execute(f"INSERT INTO {FILES} VALUES(?,?,?)", (FILE_ID, locator, NOW))
    conn.execute(
        f"INSERT INTO {BYTES} VALUES(?,?,?,?,?,?,?,?)",
        (BYTE_ID, FILE_ID, DIGEST, 3, 1_700_000_000_000_000_000, NOW, NOW, 1),
    )
    conn.execute(f"INSERT INTO {IMAGE_TAGS} VALUES(?,?,0)", (BYTE_ID, tag_id))
    return tag_id


def rows(conn, sql, *params):
    """Plain tuples whatever row factory *conn* uses."""
    return [tuple(row) for row in conn.execute(sql, params)]


def image_tags(conn):
    return rows(
        conn,
        f"SELECT t.name, it.position FROM {IMAGE_TAGS} it "
        "JOIN tags t ON t.id = it.tag_id ORDER BY it.position",
    )


def metadata(conn):
    return rows(conn, f"SELECT schema_version,image_index_revision FROM {METADATA}")


def table_rows(conn, tables):
    found = {}
    for table in tables:
        present = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if present:
            found[table] = rows(conn, f"SELECT * FROM {table}")  # nosec B608
    return found


def populate_legacy(conn):
    conn.execute(
        "INSERT INTO prompts(id,text,hash,category,notes,run_count) VALUES"
        "(7,'positive','p','keep','notes',12)"
    )
    conn.execute("INSERT INTO prompts(id,text,hash) VALUES(9,'negative','n')")
    conn.execute(
        "INSERT INTO generated_images(id,prompt_id,image_path,filename,file_path) "
        "VALUES(4,7,'/tmp/image.png','image.png','/tmp/image.png')"
    )
    conn.execute("INSERT INTO tags(id,name) VALUES(3,'keep-tag')")
    conn.execute("INSERT INTO prompt_tags VALUES(7,3)")
    conn.execute(
        "INSERT INTO prompt_pairs(id,positive_prompt_id,negative_prompt_id) "
        "VALUES(6,7,9)"
    )
    conn.execute("INSERT INTO prompt_pair_tags VALUES(6,3)")
    conn.execute("INSERT INTO image_favorites VALUES('root','a.png',?)", (NOW,))


class AnnotationSchemaTests(unittest.TestCase):
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
            annotation_schema_error, "database.annotation_schema is not implemented"
        )

    def drop_group(self, path):
        """Remove the group from a closed database file (an old backup).

        The review queue group cites annotation bytes and predates nothing, so
        an old backup lacks it as well.
        """
        with closing(sqlite3.connect(path)) as raw, raw:
            raw.execute("PRAGMA foreign_keys=OFF")
            for table in REVIEW_TABLES + tuple(reversed(TABLES)):
                raw.execute(f"DROP TABLE {table}")

    def test_fresh_install_and_reopen_is_idempotent(self):
        self.require_module()
        model = self.model()
        with model.get_connection() as conn:
            self.assertEqual(catalog(conn), expected_catalog())
            self.assertIsNone(annotation_schema_error(conn, allow_missing=False))
            self.assertEqual(metadata(conn), [(1, 0)])
            insert_group_rows(conn)
        self.assertEqual(
            PromptModel.verify_database_file(self.path, require_library_state=True),
            (True, "ok"),
        )
        model.close_all()
        for _ in range(2):
            PromptModel.reset_schema_cache()
            model = self.model()
            with model.get_connection() as conn:
                self.assertEqual(catalog(conn), expected_catalog())
                self.assertIsNone(annotation_schema_error(conn, allow_missing=False))
                self.assertEqual(metadata(conn), [(1, 0)])
                self.assertEqual(image_tags(conn), [("img-only", 0)])
                # Running the migration again on a current database changes nothing.
                migrate_annotation_state(conn)
                self.assertEqual(metadata(conn), [(1, 0)])
                self.assertEqual(image_tags(conn), [("img-only", 0)])
            model.close_all()
        # A tag delete and a file delete cascade into the junction and bytes.
        PromptModel.reset_schema_cache()
        model = self.model()
        with model.get_connection() as conn:
            conn.execute("DELETE FROM tags WHERE name='img-only'")
            self.assertEqual(image_tags(conn), [])
            conn.execute(f"DELETE FROM {FILES}")
            self.assertEqual(rows(conn, f"SELECT * FROM {BYTES}"), [])
            self.assertIsNone(annotation_schema_error(conn, allow_missing=False))

    def test_old_database_without_group_migrates_without_touching_legacy_rows(self):
        self.require_module()
        model = self.model()
        with model.get_connection() as conn:
            populate_legacy(conn)
        model.close_all()
        self.drop_group(self.path)
        with closing(sqlite3.connect(self.path)) as raw:
            self.assertIsNone(annotation_schema_error(raw))
            self.assertEqual(
                annotation_schema_error(raw, allow_missing=False),
                "Missing image annotation schema",
            )
            legacy_rows_before = table_rows(raw, LEGACY_TABLES)
        self.assertTrue(PromptModel.verify_database_file(self.path)[0])
        self.assertFalse(
            PromptModel.verify_database_file(self.path, require_library_state=True)[0]
        )
        PromptModel.reset_schema_cache()

        model = self.model()

        with model.get_connection() as conn:
            self.assertEqual(catalog(conn), expected_catalog())
            self.assertIsNone(annotation_schema_error(conn, allow_missing=False))
            self.assertEqual(metadata(conn), [(1, 0)])
            legacy_rows_after = table_rows(conn, LEGACY_TABLES)
            # No backfill: existing prompt tags never become image tags.
            counts = [
                conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (FILES, BYTES, IMAGE_TAGS)
            ]
        self.assertEqual(counts, [0, 0, 0])
        self.assertEqual(legacy_rows_after, legacy_rows_before)
        self.assertEqual(
            PromptModel.verify_database_file(self.path, require_library_state=True),
            (True, "ok"),
        )

    def tampered(self, name, statements, *, checks_off=False):
        """A closed current database whose group was altered after the fact."""
        path = str(Path(self.temp.name) / f"{name.replace(' ', '-')}.db")
        model = self.model(path)
        with model.get_connection() as conn:
            populate_legacy(conn)
            insert_group_rows(conn)
        model.close_all()
        with closing(sqlite3.connect(path)) as raw, raw:
            raw.execute("PRAGMA foreign_keys=OFF")
            if checks_off:
                raw.execute("PRAGMA ignore_check_constraints=ON")
            for statement in statements:
                if isinstance(statement, tuple):
                    raw.execute(*statement)
                else:
                    raw.execute(statement)
        return path

    def assert_rejected_unrepaired(self, path):
        with closing(sqlite3.connect(path)) as raw:
            schema_before = raw.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name"
            ).fetchall()
            rows_before = table_rows(raw, TABLES)
            for allow_missing in (False, True):
                error = annotation_schema_error(raw, allow_missing=allow_missing)
                self.assertIsNotNone(error)
                self.assertIn("image annotation", error.lower())
        self.assertFalse(PromptModel.verify_database_file(path)[0])
        self.assertFalse(
            PromptModel.verify_database_file(path, require_library_state=True)[0]
        )
        PromptModel.reset_schema_cache()
        with self.assertRaises(sqlite3.DatabaseError):
            self.model(path)
        PromptModel.reset_schema_cache()
        with closing(sqlite3.connect(path)) as raw:
            self.assertEqual(
                raw.execute(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name"
                ).fetchall(),
                schema_before,
            )
            self.assertEqual(table_rows(raw, TABLES), rows_before)

    def test_partial_or_malformed_group_is_rejected_without_repair(self):
        self.require_module()
        cases = {
            "missing metadata": (f"DROP TABLE {METADATA}",),
            "missing files": (f"DROP TABLE {FILES}",),
            "missing bytes": (f"DROP TABLE {BYTES}",),
            "missing image tags": (f"DROP TABLE {IMAGE_TAGS}",),
            "reserved view": (
                f"DROP TABLE {FILES}",
                f"CREATE VIEW {FILES} AS SELECT 'x' AS id",
            ),
            "extra trigger": (
                f"CREATE TRIGGER media_annotation_trigger AFTER INSERT ON {FILES} "
                "BEGIN SELECT 1; END",
            ),
            "extra index": (
                f"CREATE INDEX idx_media_annotation_extra ON {FILES}(created_at)",
            ),
            "extra column": (
                f"DROP TABLE {IMAGE_TAGS}",
                DDL[IMAGE_TAGS].replace(
                    "position INTEGER", "note TEXT, position INTEGER"
                ),
            ),
            "wrong primary key": (
                f"DROP TABLE {FILES}",
                DDL[FILES].replace("id TEXT PRIMARY KEY", "id TEXT"),
            ),
            "wrong foreign key": (
                f"DROP TABLE {BYTES}",
                DDL[BYTES].replace("ON DELETE CASCADE", "ON DELETE RESTRICT"),
                INDEXES["idx_media_annotation_bytes_fingerprint"],
            ),
            "wrong check": (
                f"DROP TABLE {METADATA}",
                DDL[METADATA].replace("schema_version=1", "schema_version=2"),
                f"INSERT INTO {METADATA} VALUES(1,2,0)",
            ),
            "missing unique": (
                f"DROP TABLE {FILES}",
                DDL[FILES].replace("NOT NULL UNIQUE", "NOT NULL"),
            ),
            "missing index": ("DROP INDEX idx_media_image_tags_tag",),
            "non-unique position index": (
                "DROP INDEX idx_media_image_tags_position",
                INDEXES["idx_media_image_tags_position"].replace("UNIQUE ", ""),
            ),
            "wrong index columns": (
                "DROP INDEX idx_media_annotation_bytes_fingerprint",
                INDEXES["idx_media_annotation_bytes_fingerprint"].replace(
                    ", mtime_ns", ""
                ),
            ),
            "orphan tag": (f"INSERT INTO {IMAGE_TAGS} VALUES('{BYTE_ID}',404,1)",),
            "orphan byte": (
                (
                    f"INSERT INTO {BYTES} VALUES(?,?,?,1,1,?,?,0)",
                    (
                        OTHER_ID,
                        "9" * 8 + "-0000-4000-8000-" + "0" * 12,
                        DIGEST,
                        NOW,
                        NOW,
                    ),
                ),
            ),
            "bad file uuid": (
                (f"UPDATE {FILES} SET id=?", ("x" * 36,)),
                (f"UPDATE {BYTES} SET file_id=?", ("x" * 36,)),
            ),
            "uppercase byte uuid": (
                f"UPDATE {BYTES} SET id=upper(id)",
                f"UPDATE {IMAGE_TAGS} SET byte_id=upper(byte_id)",
            ),
            "null file id": ((f"INSERT INTO {FILES} VALUES(NULL,?,?)", ("/n", NOW)),),
            "blob ids": (
                f"UPDATE {FILES} SET id=CAST(id AS BLOB)",
                f"UPDATE {BYTES} SET file_id=CAST(file_id AS BLOB)",
            ),
            "no metadata row": (f"DELETE FROM {METADATA}",),
            "long created_at": ((f"UPDATE {FILES} SET created_at=?", ("x" * 65,)),),
            "blob verified_at": (f"UPDATE {BYTES} SET verified_at=X'00'",),
            "text mtime": (f"UPDATE {BYTES} SET mtime_ns='soon'",),
            "duplicate position": (
                "DROP INDEX idx_media_image_tags_position",
                "INSERT INTO tags(id,name) VALUES(77,'dup')",
                f"INSERT INTO {IMAGE_TAGS} VALUES('{BYTE_ID}',77,0)",
            ),
            "long locator": (
                (f"UPDATE {FILES} SET locator=?", ("x" * (MAX_LOCATOR + 1),)),
            ),
            "multibyte long locator": (
                (f"UPDATE {FILES} SET locator=?", ("é" * (MAX_LOCATOR + 1),)),
            ),
            "nul locator bypass": (
                (f"UPDATE {FILES} SET locator=?", ("x\0" + "y" * MAX_LOCATOR,)),
            ),
        }
        for label, statements in cases.items():
            with self.subTest(label):
                self.assert_rejected_unrepaired(self.tampered(label, statements))
        unchecked = {
            "two metadata rows": (f"INSERT INTO {METADATA} VALUES(2,1,0)",),
            "wrong schema version": (f"UPDATE {METADATA} SET schema_version=2",),
            "negative index revision": (
                f"UPDATE {METADATA} SET image_index_revision=-1",
            ),
            "bad digest": (f"UPDATE {BYTES} SET sha256=upper(sha256)",),
            "short digest": (f"UPDATE {BYTES} SET sha256=substr(sha256,1,63)",),
            "negative size": (f"UPDATE {BYTES} SET size=-1",),
            "negative tag revision": (f"UPDATE {BYTES} SET tag_revision=-1",),
            "negative position": (f"UPDATE {IMAGE_TAGS} SET position=-1",),
        }
        for label, statements in unchecked.items():
            with self.subTest(label):
                path = self.tampered(label, statements, checks_off=True)
                self.assert_rejected_unrepaired(path)
        accepted = {
            "locator at bound": ("x" * MAX_LOCATOR,),
            "multibyte locator at bound": ("é" * MAX_LOCATOR,),
        }
        for label, (locator,) in accepted.items():
            with self.subTest(label):
                path = self.tampered(
                    label, ((f"UPDATE {FILES} SET locator=?", (locator,)),)
                )
                with closing(sqlite3.connect(path)) as raw:
                    self.assertIsNone(annotation_schema_error(raw, allow_missing=False))
                self.assertEqual(
                    PromptModel.verify_database_file(path, require_library_state=True),
                    (True, "ok"),
                )

    def test_install_failure_rolls_back_and_retries(self):
        self.require_module()
        with patch(
            "database.annotation_schema.annotation_schema_error",
            side_effect=[None, "injected final validation failure"],
        ):
            with self.assertRaisesRegex(sqlite3.DatabaseError, "injected"):
                self.model()
        with closing(sqlite3.connect(self.path)) as raw:
            self.assertEqual(catalog(raw), set())
            self.assertIsNotNone(
                raw.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='saved_views'"
                ).fetchone()
            )
        PromptModel.reset_schema_cache()
        model = self.model()
        self.assertEqual(
            PromptModel.verify_database_file(model.db_path, require_library_state=True),
            (True, "ok"),
        )
        with model.get_connection() as conn:
            self.assertEqual(metadata(conn), [(1, 0)])

    def test_wal_backup_restore_round_trips_group(self):
        self.require_module()
        source_path = str(Path(self.temp.name) / "source.db")
        source = self.model(source_path)
        with source.get_connection() as conn:
            conn.execute("PRAGMA wal_autocheckpoint=0")
            conn.execute("INSERT INTO prompts(id,text,hash) VALUES(7,'p','p')")
            insert_group_rows(conn)
            conn.commit()
            source_files = rows(conn, f"SELECT * FROM {FILES}")
            source_bytes = rows(conn, f"SELECT * FROM {BYTES}")
        self.assertGreater(Path(source_path + "-wal").stat().st_size, 0)
        live = self.model()
        with live.get_connection() as conn:
            conn.execute("INSERT INTO prompts(id,text,hash) VALUES(1,'live','live')")
            insert_group_rows(conn, tag_name="live-only", locator="/out/live.png")
        source_before = Path(source_path).read_bytes()
        self.assertEqual(
            PromptModel.verify_database_file(source_path, require_library_state=True),
            (True, "ok"),
        )
        # The rows still sit in the WAL; a backup file must carry them too.
        backup_file = str(Path(self.temp.name) / "backup.db")
        self.assertTrue(source.backup_database(backup_file))
        with closing(sqlite3.connect(backup_file)) as raw:
            self.assertEqual(image_tags(raw), [("img-only", 0)])
            self.assertIsNone(annotation_schema_error(raw, allow_missing=False))

        safety = live.restore_from_file(source_path)

        self.assertTrue(Path(safety).is_file())
        self.assertEqual(Path(source_path).read_bytes(), source_before)
        with live.get_connection() as conn:
            restored_image_tags = image_tags(conn)
            self.assertEqual(rows(conn, f"SELECT * FROM {FILES}"), source_files)
            self.assertEqual(rows(conn, f"SELECT * FROM {BYTES}"), source_bytes)
            self.assertEqual(metadata(conn), [(1, 0)])
            self.assertIsNone(annotation_schema_error(conn, allow_missing=False))
            self.assertEqual(rows(conn, "SELECT id FROM prompts"), [(7,)])
        self.assertEqual(restored_image_tags, [("img-only", 0)])
        with closing(sqlite3.connect(safety)) as raw:
            self.assertEqual(image_tags(raw), [("live-only", 0)])
            self.assertEqual(raw.execute("SELECT id FROM prompts").fetchall(), [(1,)])
        # The same rows restore from the backup file as well.
        PromptModel.reset_schema_cache()
        live.restore_from_file(backup_file)
        with live.get_connection() as conn:
            self.assertEqual(image_tags(conn), [("img-only", 0)])
        PromptModel.close_all_instances()
        assert_closed(self, self.temp.name)

    def test_staged_failure_preserves_source_live_safety_and_handles(self):
        self.require_module()
        live = self.model()
        with live.get_connection() as conn:
            conn.execute("INSERT INTO prompts(id,text,hash) VALUES(1,'live','live')")
            insert_group_rows(conn, tag_name="live-only")
        # A source with a malformed group is refused before any change.
        bad = self.tampered(
            "restore bad",
            (f"UPDATE {BYTES} SET sha256=upper(sha256)",),
            checks_off=True,
        )
        # A staging failure while installing the group must be refused too.
        old = str(Path(self.temp.name) / "old.db")
        self.model(old).close_all()
        self.drop_group(old)
        for source, patched in ((bad, False), (old, True)):
            with self.subTest(source=Path(source).name):
                before = Path(source).read_bytes()
                safety = sorted(Path(self.temp.name).glob("live.db.backup_*"))
                if patched:
                    context = patch(
                        "database.models.migrate_annotation_state",
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
                assert_closed(self, source)
                with live.get_connection() as conn:
                    self.assertEqual(image_tags(conn), [("live-only", 0)])
                    self.assertEqual(metadata(conn), [(1, 0)])
        self.assertFalse(
            PromptModel.verify_database_file(old, require_library_state=True)[0]
        )
        self.assertTrue(PromptModel.verify_database_file(old)[0])

    def test_verify_requires_group_only_with_require_library_state(self):
        self.require_module()
        self.model().close_all()
        self.assertEqual(
            PromptModel.verify_database_file(self.path, require_library_state=True),
            (True, "ok"),
        )
        self.drop_group(self.path)
        self.assertEqual(PromptModel.verify_database_file(self.path), (True, "ok"))
        self.assertEqual(
            PromptModel.verify_database_file(self.path, require_library_state=True),
            (False, "Missing image annotation schema"),
        )


if __name__ == "__main__":
    unittest.main()
