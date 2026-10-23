"""Needs review schema group (#249): install, validation and restore safety."""

import hashlib
import json
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
from database.operations import PromptDatabase

try:  # guarded so a missing module fails assertions, not test loading
    from database.review_schema import (
        canonical_evidence,
        evidence_digest,
        migrate_review_state,
        review_schema_error,
    )
except ImportError:
    canonical_evidence = None
    evidence_digest = None
    migrate_review_state = None
    review_schema_error = None

METADATA = "media_review_metadata"
ITEMS = "media_review_items"
DECISIONS = "media_review_decisions"
TABLES = (METADATA, ITEMS, DECISIONS)
ANNOTATION_TABLES = (
    "media_annotation_metadata",
    "media_annotation_files",
    "media_annotation_bytes",
    "media_image_tags",
)
MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
# The exact DDL the plan pins; the module must produce precisely these objects.
DDL = {
    METADATA: (
        f"CREATE TABLE {METADATA}(singleton_id INTEGER PRIMARY KEY "
        "CHECK(singleton_id=1), schema_version INTEGER NOT NULL "
        "CHECK(schema_version=1), review_index_revision INTEGER NOT NULL "
        "DEFAULT 0 CHECK(review_index_revision>=0))"
    ),
    ITEMS: (
        f"CREATE TABLE {ITEMS}(id TEXT PRIMARY KEY CHECK(length(id)=36), "
        "byte_id TEXT NOT NULL REFERENCES media_annotation_bytes(id) "
        "ON DELETE RESTRICT, spelled_path TEXT NOT NULL CHECK(length(CAST("
        "spelled_path AS BLOB)) BETWEEN 1 AND 16384), "
        "resolver_version INTEGER NOT NULL, "
        "evidence_hash TEXT NOT NULL CHECK(length(evidence_hash)=64 AND "
        "evidence_hash NOT GLOB '*[^0-9a-f]*'), evidence TEXT NOT NULL "
        f"CHECK(length(CAST(evidence AS BLOB)) <= {MAX_EVIDENCE_BYTES}), "
        "candidate_count INTEGER NOT NULL CHECK(candidate_count BETWEEN 0 AND 16), "
        "limited INTEGER NOT NULL CHECK(limited IN (0,1)), state TEXT NOT NULL "
        "CHECK(state IN ('pending','selected','unresolved')), revision INTEGER "
        "NOT NULL CHECK(revision>=0), created_at TEXT NOT NULL, updated_at TEXT "
        "NOT NULL, UNIQUE(byte_id, resolver_version, evidence_hash))"
    ),
    DECISIONS: (
        f"CREATE TABLE {DECISIONS}(id TEXT PRIMARY KEY CHECK(length(id)=36), "
        f"item_id TEXT NOT NULL REFERENCES {ITEMS}(id) ON DELETE RESTRICT, "
        "decision_revision INTEGER NOT NULL, created_at TEXT NOT NULL, "
        "outcome TEXT NOT NULL CHECK(outcome IN ('selected','unresolved')), "
        "candidate_hash TEXT CHECK(candidate_hash IS NULL OR "
        "(length(candidate_hash)=64 AND candidate_hash NOT GLOB '*[^0-9a-f]*')), "
        "evidence TEXT NOT NULL "
        f"CHECK(length(CAST(evidence AS BLOB)) <= {MAX_EVIDENCE_BYTES}), "
        "pair_id INTEGER REFERENCES prompt_pairs(id) ON DELETE SET NULL, "
        "image_id INTEGER REFERENCES generated_images(id) ON DELETE SET NULL, "
        "UNIQUE(item_id, decision_revision), "
        "CHECK((outcome='selected') = (candidate_hash IS NOT NULL)))"
    ),
}
INDEXES = {
    "idx_media_review_items_queue": (
        f"CREATE INDEX idx_media_review_items_queue ON {ITEMS}"
        "(state, created_at, id)"
    ),
    "idx_media_review_decisions_pair": (
        f"CREATE INDEX idx_media_review_decisions_pair ON {DECISIONS}(pair_id)"
    ),
    "idx_media_review_decisions_image": (
        f"CREATE INDEX idx_media_review_decisions_image ON {DECISIONS}(image_id)"
    ),
}
AUTOINDEXES = {ITEMS: 2, DECISIONS: 2}
LEGACY_TABLES = (
    "prompts",
    "generated_images",
    "tags",
    "prompt_tags",
    "prompt_pairs",
    "prompt_pair_tags",
    "image_favorites",
) + ANNOTATION_TABLES
FILE_ID = "0f8e4b2a-1c3d-4e5f-8a9b-0c1d2e3f4a5b"
BYTE_ID = "6a1b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d"
ITEM_ID = "1d2e3f4a-5b6c-4d7e-8f9a-0b1c2d3e4f5a"
DECISION_ID = "2e3f4a5b-6c7d-4e8f-9a0b-1c2d3e4f5a6b"
OTHER_ID = "7b2c3d4e-5f6a-4b7c-9d0e-1f2a3b4c5d6e"
DIGEST = hashlib.sha256(b"img").hexdigest()
CANDIDATE = hashlib.sha256(b"candidate").hexdigest()
NOW = "2026-10-22T00:00:00.000000+00:00"
EVIDENCE = '{"candidates":[],"sha256":"' + DIGEST + '"}'
EVIDENCE_HASH = hashlib.sha256(EVIDENCE.encode("ascii")).hexdigest()


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


def rows(conn, sql, *params):
    """Plain tuples whatever row factory *conn* uses."""
    return [tuple(row) for row in conn.execute(sql, params)]


def metadata(conn):
    return rows(conn, f"SELECT schema_version,review_index_revision FROM {METADATA}")


def table_rows(conn, tables):
    found = {}
    for table in tables:
        present = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if present:
            found[table] = rows(conn, f"SELECT * FROM {table}")  # nosec B608
    return found


def insert_byte(conn, file_id=FILE_ID, byte_id=BYTE_ID, locator="/out/img.png"):
    conn.execute(
        "INSERT INTO media_annotation_files VALUES(?,?,?)", (file_id, locator, NOW)
    )
    conn.execute(
        "INSERT INTO media_annotation_bytes VALUES(?,?,?,?,?,?,?,0)",
        (byte_id, file_id, DIGEST, 3, 1_700_000_000_000_000_000, NOW, NOW),
    )


def insert_item(conn, *, item_id=ITEM_ID, evidence=EVIDENCE, candidates=2, **extra):
    values = {
        "id": item_id,
        "byte_id": BYTE_ID,
        "spelled_path": "/out/img.png",
        "resolver_version": 1,
        "evidence_hash": hashlib.sha256(evidence.encode("utf-8")).hexdigest(),
        "evidence": evidence,
        "candidate_count": candidates,
        "limited": 0,
        "state": "pending",
        "revision": 0,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(extra)
    columns = ",".join(values)
    marks = ",".join("?" * len(values))
    conn.execute(
        f"INSERT INTO {ITEMS}({columns}) VALUES({marks})",  # nosec B608
        tuple(values.values()),
    )


def insert_decision(conn, *, decision_id=DECISION_ID, **extra):
    values = {
        "id": decision_id,
        "item_id": ITEM_ID,
        "decision_revision": 1,
        "created_at": NOW,
        "outcome": "selected",
        "candidate_hash": CANDIDATE,
        "evidence": EVIDENCE,
        "pair_id": None,
        "image_id": None,
    }
    values.update(extra)
    columns = ",".join(values)
    marks = ",".join("?" * len(values))
    conn.execute(
        f"INSERT INTO {DECISIONS}({columns}) VALUES({marks})",  # nosec B608
        tuple(values.values()),
    )


def insert_group_rows(conn, **item):
    insert_byte(conn)
    insert_item(conn, **item)
    insert_decision(conn)


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


def full_capacity_evidence():
    """Sixteen candidates of control, lone-surrogate, astral and combining
    text, exactly 8 MiB."""
    text = '\x00\x07\x1f \ud800 é é "q" \\ \U0001f9ea'
    candidates = [
        {
            "hash": hashlib.sha256(str(n).encode()).hexdigest(),
            "negative": {"text": text * 3},
            "positive": {"text": text * 5},
            "sampler_id": str(n),
            "state": "resolved",
        }
        for n in range(16)
    ]
    value = {"candidates": candidates, "pad": ""}
    base = len(canonical_evidence(value).encode("utf-8"))
    value["pad"] = "x" * (MAX_EVIDENCE_BYTES - base)
    return value


class ReviewSchemaTests(unittest.TestCase):
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
            review_schema_error, "database.review_schema is not implemented"
        )

    def drop_tables(self, path, tables=TABLES):
        """Remove tables from a closed database file (an old backup)."""
        with closing(sqlite3.connect(path)) as raw, raw:
            raw.execute("PRAGMA foreign_keys=OFF")
            for table in reversed(tables):
                raw.execute(f"DROP TABLE {table}")

    def test_fresh_install_and_reopen_is_idempotent(self):
        self.require_module()
        model = self.model()
        with model.get_connection() as conn:
            self.assertEqual(catalog(conn), expected_catalog())
            self.assertIsNone(review_schema_error(conn, allow_missing=False))
            self.assertEqual(metadata(conn), [(1, 0)])
            insert_group_rows(conn)
            snapshot = table_rows(conn, TABLES)
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
                self.assertIsNone(review_schema_error(conn, allow_missing=False))
                self.assertEqual(table_rows(conn, TABLES), snapshot)
                # Running the migration again on a current database changes nothing.
                migrate_review_state(conn)
                self.assertEqual(table_rows(conn, TABLES), snapshot)
            model.close_all()
        # Items and decisions keep the byte record and the item they cite.
        PromptModel.reset_schema_cache()
        model = self.model()
        with model.get_connection() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM media_annotation_files")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(f"DELETE FROM {ITEMS}")
            self.assertEqual(table_rows(conn, TABLES), snapshot)

    def test_startup_validates_stored_evidence_once(self):
        """A reopen streams the evidence once and the annotation group twice
        (its own install), never again for the review group (I3)."""
        self.require_module()
        import database.annotation_schema as annotation_module
        import database.review_schema as review_module

        model = self.model()
        with model.get_connection() as conn:
            insert_group_rows(conn)
        model.close_all()
        for _ in range(2):
            PromptModel.reset_schema_cache()
            with (
                patch.object(
                    review_module,
                    "_evidence_error",
                    wraps=review_module._evidence_error,
                ) as evidence,
                patch.object(
                    review_module,
                    "annotation_schema_error",
                    wraps=review_module.annotation_schema_error,
                ) as nested,
                patch.object(
                    annotation_module,
                    "_value_error",
                    wraps=annotation_module._value_error,
                ) as annotation_rows,
            ):
                model = self.model()
                model.get_connection()
            self.assertEqual(evidence.call_count, 1)
            self.assertEqual(nested.call_count, 0)
            self.assertEqual(annotation_rows.call_count, 2)
            model.close_all()

    def test_old_database_without_group_migrates_without_touching_legacy_rows(self):
        self.require_module()
        for label, dropped in (
            ("annotations only", TABLES),
            ("predates annotations", ANNOTATION_TABLES + TABLES),
        ):
            with self.subTest(label):
                path = str(Path(self.temp.name) / f"{label.replace(' ', '-')}.db")
                model = self.model(path)
                with model.get_connection() as conn:
                    populate_legacy(conn)
                    if dropped == TABLES:
                        insert_byte(conn)
                model.close_all()
                self.drop_tables(path, dropped)
                with closing(sqlite3.connect(path)) as raw:
                    self.assertIsNone(review_schema_error(raw))
                    self.assertEqual(
                        review_schema_error(raw, allow_missing=False),
                        "Missing review queue schema",
                    )
                    legacy_rows_before = table_rows(raw, LEGACY_TABLES)
                self.assertTrue(PromptModel.verify_database_file(path)[0])
                self.assertFalse(
                    PromptModel.verify_database_file(path, require_library_state=True)[
                        0
                    ]
                )
                PromptModel.reset_schema_cache()

                model = self.model(path)

                with model.get_connection() as conn:
                    self.assertEqual(catalog(conn), expected_catalog())
                    self.assertIsNone(review_schema_error(conn, allow_missing=False))
                    self.assertEqual(metadata(conn), [(1, 0)])
                    counts = [
                        conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                        for t in (ITEMS, DECISIONS)
                    ]
                    legacy_rows_after = {
                        table: value
                        for table, value in table_rows(conn, LEGACY_TABLES).items()
                        if table in legacy_rows_before
                    }
                self.assertEqual(counts, [0, 0])
                self.assertEqual(legacy_rows_after, legacy_rows_before)
                self.assertEqual(
                    PromptModel.verify_database_file(path, require_library_state=True),
                    (True, "ok"),
                )
                model.close_all()

    def test_annotations_only_installation_is_valid(self):
        self.require_module()
        model = self.model()
        with model.get_connection() as conn:
            insert_byte(conn)
        model.close_all()
        self.drop_tables(self.path)
        with closing(sqlite3.connect(self.path)) as raw:
            self.assertIsNone(review_schema_error(raw))
        self.assertEqual(PromptModel.verify_database_file(self.path), (True, "ok"))
        self.assertEqual(
            PromptModel.verify_database_file(self.path, require_library_state=True),
            (False, "Missing review queue schema"),
        )

    def test_review_group_without_annotation_group_is_invalid(self):
        self.require_module()
        model = self.model()
        model.close_all()
        self.drop_tables(self.path, ANNOTATION_TABLES)
        with closing(sqlite3.connect(self.path)) as raw:
            for allow_missing in (True, False):
                error = review_schema_error(raw, allow_missing=allow_missing)
                self.assertIsNotNone(error)
                self.assertIn("image annotation", error.lower())
        self.assertFalse(PromptModel.verify_database_file(self.path)[0])
        PromptModel.reset_schema_cache()
        with self.assertRaises(sqlite3.DatabaseError):
            self.model()
        # An invalid annotation group invalidates the review group beside it.
        path = self.tampered(
            "bad annotations",
            ("UPDATE media_annotation_bytes SET sha256=upper(sha256)",),
            checks_off=True,
        )
        with closing(sqlite3.connect(path)) as raw:
            self.assertIn("image annotation", review_schema_error(raw).lower())

    def tampered(self, name, statements, *, checks_off=False, rows_in=True):
        """A closed current database whose group was altered after the fact."""
        path = str(Path(self.temp.name) / f"{name.replace(' ', '-')}.db")
        model = self.model(path)
        with model.get_connection() as conn:
            populate_legacy(conn)
            if rows_in:
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
                error = review_schema_error(raw, allow_missing=allow_missing)
                self.assertIsNotNone(error)
                self.assertIn("review", error.lower())
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
            "missing items": (f"DROP TABLE {DECISIONS}", f"DROP TABLE {ITEMS}"),
            "missing decisions": (f"DROP TABLE {DECISIONS}",),
            "reserved view": (
                f"DROP TABLE {DECISIONS}",
                f"CREATE VIEW {DECISIONS} AS SELECT 'x' AS id",
            ),
            "extra trigger": (
                f"CREATE TRIGGER media_review_trigger AFTER INSERT ON {ITEMS} "
                "BEGIN SELECT 1; END",
            ),
            "extra index": (
                f"CREATE INDEX idx_media_review_extra ON {ITEMS}(updated_at)",
            ),
            "extra column": (
                f"DROP TABLE {DECISIONS}",
                DDL[DECISIONS].replace("evidence TEXT", "note TEXT, evidence TEXT"),
            ),
            "wrong primary key": (
                f"DROP TABLE {DECISIONS}",
                DDL[DECISIONS].replace("id TEXT PRIMARY KEY", "id TEXT", 1),
            ),
            "wrong foreign key": (
                f"DROP TABLE {DECISIONS}",
                DDL[DECISIONS].replace(
                    "generated_images(id) ON DELETE SET NULL",
                    "generated_images(id) ON DELETE CASCADE",
                ),
                INDEXES["idx_media_review_decisions_pair"],
                INDEXES["idx_media_review_decisions_image"],
            ),
            "wrong check": (
                f"DROP TABLE {METADATA}",
                DDL[METADATA].replace("schema_version=1", "schema_version=2"),
                f"INSERT INTO {METADATA} VALUES(1,2,0)",
            ),
            "missing unique": (
                "DROP INDEX idx_media_review_decisions_pair",
                "DROP INDEX idx_media_review_decisions_image",
                f"DROP TABLE {DECISIONS}",
                DDL[DECISIONS].replace(" UNIQUE(item_id, decision_revision),", ""),
                INDEXES["idx_media_review_decisions_pair"],
                INDEXES["idx_media_review_decisions_image"],
            ),
            "missing queue index": ("DROP INDEX idx_media_review_items_queue",),
            "wrong index columns": (
                "DROP INDEX idx_media_review_items_queue",
                INDEXES["idx_media_review_items_queue"].replace(", id", ""),
            ),
            "orphan item": ((f"UPDATE {ITEMS} SET byte_id=?", (OTHER_ID,)),),
            "orphan decision": ((f"UPDATE {DECISIONS} SET item_id=?", (OTHER_ID,)),),
            "dangling pair": (f"UPDATE {DECISIONS} SET pair_id=404",),
            "bad item uuid": (
                (f"UPDATE {ITEMS} SET id=?", ("x" * 36,)),
                (f"UPDATE {DECISIONS} SET item_id=?", ("x" * 36,)),
            ),
            "uppercase decision uuid": (f"UPDATE {DECISIONS} SET id=upper(id)",),
            "null decision id": (f"UPDATE {DECISIONS} SET id=NULL",),
            "blob ids": (f"UPDATE {DECISIONS} SET id=CAST(id AS BLOB)",),
            "no metadata row": (f"DELETE FROM {METADATA}",),
            "long created_at": ((f"UPDATE {ITEMS} SET created_at=?", ("x" * 65,)),),
            "blob updated_at": (f"UPDATE {ITEMS} SET updated_at=X'00'",),
            "text resolver version": (f"UPDATE {ITEMS} SET resolver_version='one'",),
            "blob spelled path": (
                f"UPDATE {ITEMS} SET spelled_path=CAST(spelled_path AS BLOB)",
            ),
            "evidence hash mismatch": (
                (f"UPDATE {ITEMS} SET evidence_hash=?", (CANDIDATE,)),
            ),
        }
        for label, statements in cases.items():
            with self.subTest(label):
                self.assert_rejected_unrepaired(self.tampered(label, statements))
        unchecked = {
            "two metadata rows": (f"INSERT INTO {METADATA} VALUES(2,1,0)",),
            "wrong schema version": (f"UPDATE {METADATA} SET schema_version=2",),
            "negative index revision": (
                f"UPDATE {METADATA} SET review_index_revision=-1",
            ),
            "uppercase evidence hash": (
                f"UPDATE {ITEMS} SET evidence_hash=upper(evidence_hash)",
            ),
            "bad state": (f"UPDATE {ITEMS} SET state='done'",),
            "limited two": (f"UPDATE {ITEMS} SET limited=2",),
            "empty spelled path": (f"UPDATE {ITEMS} SET spelled_path=''",),
            "negative revision": (f"UPDATE {ITEMS} SET revision=-1",),
            "negative candidates": (f"UPDATE {ITEMS} SET candidate_count=-1",),
            "bad outcome": (f"UPDATE {DECISIONS} SET outcome='maybe'",),
            "short candidate hash": (
                f"UPDATE {DECISIONS} SET candidate_hash=substr(candidate_hash,1,63)",
            ),
            "negative decision revision": (
                f"UPDATE {DECISIONS} SET decision_revision=-1",
            ),
        }
        for label, statements in unchecked.items():
            with self.subTest(label):
                path = self.tampered(label, statements, checks_off=True)
                self.assert_rejected_unrepaired(path)

    def test_selected_decision_requires_candidate_hash(self):
        self.require_module()
        model = self.model()
        with model.get_connection() as conn:
            insert_byte(conn)
            insert_item(conn)
            for outcome, candidate in (("selected", None), ("unresolved", CANDIDATE)):
                with self.subTest(outcome):
                    with self.assertRaises(sqlite3.IntegrityError):
                        insert_decision(conn, outcome=outcome, candidate_hash=candidate)
            insert_decision(conn, outcome="unresolved", candidate_hash=None)
            insert_decision(conn, decision_id=OTHER_ID, decision_revision=2)
            self.assertIsNone(review_schema_error(conn, allow_missing=False))
            self.assertEqual(
                rows(conn, f"SELECT outcome FROM {DECISIONS} ORDER BY 1"),
                [("selected",), ("unresolved",)],
            )
            with self.assertRaises(sqlite3.IntegrityError):  # one per revision
                insert_decision(conn, decision_id=ITEM_ID, decision_revision=2)
        model.close_all()
        path = self.tampered(
            "selected without hash",
            (f"UPDATE {DECISIONS} SET candidate_hash=NULL",),
            checks_off=True,
        )
        self.assert_rejected_unrepaired(path)

    def test_oversize_or_noncanonical_evidence_rejected(self):
        self.require_module()
        self.assertEqual(
            canonical_evidence({"b": [1, "é"], "a": None}),
            '{"a":null,"b":[1,"\\u00e9"]}',
        )
        self.assertEqual(
            evidence_digest(EVIDENCE), hashlib.sha256(EVIDENCE.encode()).hexdigest()
        )
        # The store's non-finite policy (utils.json_strict): written as null.
        self.assertEqual(
            canonical_evidence({"x": float("nan"), "y": [float("-inf")]}),
            '{"x":null,"y":[null]}',
        )
        for bad in (
            [],
            "text",
            {"x": object()},
            {"x": object(), "y": float("nan")},
            {1: 1, "a": 2},
        ):
            with self.subTest(value=repr(bad)), self.assertRaises(ValueError):
                canonical_evidence(bad)
        with self.assertRaises(ValueError):
            canonical_evidence({"pad": "x" * MAX_EVIDENCE_BYTES})
        oversize = '{"pad":"' + "x" * (MAX_EVIDENCE_BYTES - 9) + '"}'
        self.assertEqual(len(oversize), MAX_EVIDENCE_BYTES + 1)
        model = self.model()
        with model.get_connection() as conn:
            insert_byte(conn)
            with self.assertRaises(sqlite3.IntegrityError):
                insert_item(conn, evidence=oversize)
        model.close_all()
        noncanonical = {
            "spaces": '{"candidates": [], "sha256": "x"}',
            "unsorted": '{"sha256":"x","candidates":[]}',
            "raw unicode": '{"text":"é"}',
            "duplicate key": '{"a":1,"a":2}',
            "not an object": "[]",
            "not json": "{",
            "nan": '{"a":NaN}',
            "empty": "",
        }
        for label, evidence in noncanonical.items():
            for table in (ITEMS, DECISIONS):
                with self.subTest(label, table=table):
                    digest = hashlib.sha256(evidence.encode("utf-8")).hexdigest()
                    statements = [
                        (f"UPDATE {table} SET evidence=?", (evidence,)),
                        (f"UPDATE {ITEMS} SET evidence_hash=?", (digest,)),
                    ]
                    if table == DECISIONS:
                        statements.pop()
                    self.assert_rejected_unrepaired(
                        self.tampered(f"{label} {table}", statements)
                    )
        for table in (ITEMS, DECISIONS):
            with self.subTest("oversize", table=table):
                digest = hashlib.sha256(oversize.encode("ascii")).hexdigest()
                statements = [
                    (f"UPDATE {table} SET evidence=?", (oversize,)),
                    (f"UPDATE {ITEMS} SET evidence_hash=?", (digest,)),
                ]
                path = self.tampered(f"oversize {table}", statements, checks_off=True)
                self.assert_rejected_unrepaired(path)

    def test_seventeen_candidates_rejected(self):
        self.require_module()
        model = self.model()
        with model.get_connection() as conn:
            insert_byte(conn)
            with self.assertRaises(sqlite3.IntegrityError):
                insert_item(conn, candidates=17)
            insert_item(conn, candidates=16)
            self.assertIsNone(review_schema_error(conn, allow_missing=False))
        model.close_all()
        path = self.tampered(
            "seventeen", (f"UPDATE {ITEMS} SET candidate_count=17",), checks_off=True
        )
        self.assert_rejected_unrepaired(path)

    def test_prompt_or_image_deletion_nulls_navigation_and_keeps_decision(self):
        self.require_module()
        db = PromptDatabase(self.path)
        self.models.append(db.model)
        with db.model.get_connection() as conn:
            populate_legacy(conn)
            conn.execute(
                "INSERT INTO prompt_pairs(id,positive_prompt_id,negative_prompt_id) "
                "VALUES(8,9,NULL)"
            )
            insert_byte(conn)
            insert_item(conn)
            insert_decision(conn, pair_id=8, image_id=4)
            conn.commit()
        self.assertTrue(db.delete_prompt_pair(8))
        with db.model.get_connection() as conn:
            self.assertEqual(
                rows(conn, f"SELECT pair_id, image_id FROM {DECISIONS}"), [(None, 4)]
            )
        with db.model.get_connection() as conn:
            conn.execute("DELETE FROM prompt_pairs WHERE id=6")
            conn.commit()
        self.assertTrue(db.delete_prompt(7))
        with db.model.get_connection() as conn:
            self.assertEqual(
                rows(
                    conn,
                    f"SELECT id, item_id, outcome, candidate_hash, pair_id, image_id "
                    f"FROM {DECISIONS}",
                ),
                [(DECISION_ID, ITEM_ID, "selected", CANDIDATE, None, None)],
            )
            self.assertEqual(rows(conn, f"SELECT id FROM {ITEMS}"), [(ITEM_ID,)])
            self.assertIsNone(review_schema_error(conn, allow_missing=False))
            # The SET NULL actions look decisions up by index, not by a scan.
            for column in ("pair_id", "image_id"):
                plan = " ".join(
                    str(row[3])
                    for row in conn.execute(
                        f"EXPLAIN QUERY PLAN UPDATE {DECISIONS} "  # nosec B608
                        f"SET {column}=NULL WHERE {column}=?",
                        (1,),
                    )
                )
                self.assertIn(f"idx_media_review_decisions_{column[:-3]}", plan)

    def test_full_capacity_unicode_control_pair_round_trips_within_8_mib(self):
        self.require_module()
        value = full_capacity_evidence()
        evidence = canonical_evidence(value)
        self.assertEqual(len(evidence.encode("utf-8")), MAX_EVIDENCE_BYTES)
        self.assertTrue(evidence.isascii())
        source_path = str(Path(self.temp.name) / "source.db")
        source = self.model(source_path)
        with source.get_connection() as conn:
            conn.execute("PRAGMA wal_autocheckpoint=0")
            insert_byte(conn)
            insert_item(conn, evidence=evidence, candidates=16)
            insert_decision(conn, evidence=evidence)
            conn.commit()
            source_rows = table_rows(conn, TABLES)
        self.assertGreater(Path(source_path + "-wal").stat().st_size, 0)
        live = self.model()
        backup_file = str(Path(self.temp.name) / "backup.db")
        self.assertTrue(source.backup_database(backup_file))
        for restore_from in (source_path, backup_file):
            with self.subTest(Path(restore_from).name):
                live.restore_from_file(restore_from)
                with live.get_connection() as conn:
                    self.assertEqual(table_rows(conn, TABLES), source_rows)
                    self.assertIsNone(review_schema_error(conn, allow_missing=False))
                    stored = conn.execute(
                        f"SELECT i.evidence, d.evidence FROM {ITEMS} i "
                        f"JOIN {DECISIONS} d ON d.item_id = i.id"
                    ).fetchone()
                self.assertEqual(json.loads(stored[0]), value)
                self.assertEqual(json.loads(stored[1]), value)
        PromptModel.close_all_instances()
        assert_closed(self, self.temp.name)

    def test_install_failure_rolls_back_and_retries(self):
        self.require_module()
        with patch(
            "database.review_schema.review_schema_error",
            side_effect=[None, "injected final validation failure"],
        ):
            with self.assertRaisesRegex(sqlite3.DatabaseError, "injected"):
                self.model()
        with closing(sqlite3.connect(self.path)) as raw:
            self.assertEqual(catalog(raw), set())
            self.assertIsNotNone(
                raw.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='media_annotation_bytes'"
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
            insert_group_rows(conn)
            conn.execute(f"UPDATE {METADATA} SET review_index_revision=3")
            conn.commit()
            source_rows = table_rows(conn, TABLES)
        self.assertGreater(Path(source_path + "-wal").stat().st_size, 0)
        live = self.model()
        with live.get_connection() as conn:
            insert_byte(conn)
            insert_item(conn, item_id=OTHER_ID)
        source_before = Path(source_path).read_bytes()
        backup_file = str(Path(self.temp.name) / "backup.db")
        self.assertTrue(source.backup_database(backup_file))
        with closing(sqlite3.connect(backup_file)) as raw:
            self.assertEqual(table_rows(raw, TABLES), source_rows)
            self.assertIsNone(review_schema_error(raw, allow_missing=False))

        safety = live.restore_from_file(source_path)

        self.assertEqual(Path(source_path).read_bytes(), source_before)
        with live.get_connection() as conn:
            self.assertEqual(table_rows(conn, TABLES), source_rows)
            self.assertEqual(metadata(conn), [(1, 3)])
        with closing(sqlite3.connect(safety)) as raw:
            self.assertEqual(rows(raw, f"SELECT id FROM {ITEMS}"), [(OTHER_ID,)])
        PromptModel.reset_schema_cache()
        live.restore_from_file(backup_file)
        with live.get_connection() as conn:
            self.assertEqual(table_rows(conn, TABLES), source_rows)
        PromptModel.close_all_instances()
        assert_closed(self, self.temp.name)

    def test_staged_failure_preserves_source_live_safety_and_handles(self):
        self.require_module()
        live = self.model()
        with live.get_connection() as conn:
            insert_byte(conn)
            insert_item(conn, item_id=OTHER_ID)
            live_rows = table_rows(conn, TABLES)
        bad = self.tampered(
            "restore bad", (f"UPDATE {ITEMS} SET state='done'",), checks_off=True
        )
        old = str(Path(self.temp.name) / "old.db")
        self.model(old).close_all()
        self.drop_tables(old)
        for source, patched in ((bad, False), (old, True)):
            with self.subTest(source=Path(source).name):
                before = Path(source).read_bytes()
                safety = sorted(Path(self.temp.name).glob("live.db.backup_*"))
                if patched:
                    context = patch(
                        "database.models.migrate_review_state",
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
                    self.assertEqual(table_rows(conn, TABLES), live_rows)
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
        self.drop_tables(self.path)
        self.assertEqual(PromptModel.verify_database_file(self.path), (True, "ok"))
        self.assertEqual(
            PromptModel.verify_database_file(self.path, require_library_state=True),
            (False, "Missing review queue schema"),
        )


if __name__ == "__main__":
    unittest.main()
