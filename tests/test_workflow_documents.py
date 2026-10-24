"""Each workflow and prompt graph stored once (database/workflow_documents.py).

``generated_images.workflow_data`` and ``prompt_metadata`` held the same large
JSON text again for every image of a run. The text now lives once in
``workflow_documents`` (keyed by the sha256 of its exact UTF-8 bytes, zlib
compressed) and image rows reference it. Old rows keep their inline text until
the migration rewrites them; every reader goes through one accessor, which
reads both forms, and gives back the exact text that was stored.
"""

import json
import os
import random
import shutil
import sqlite3
import sys
import tempfile
import unittest
import zlib
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import workflow_documents as wd  # noqa: E402
from database.models import PromptModel  # noqa: E402
from database.operations import PromptDatabase  # noqa: E402
from utils.hashing import generate_prompt_hash  # noqa: E402
from utils.json_strict import dumps_strict  # noqa: E402

try:
    from tests.open_handles import assert_closed
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed

NAN = float("nan")
INF = float("inf")


def sample_workflow(seed=0, nodes=40):
    """A ComfyUI-shaped UI workflow: nodes, links, widgets and groups."""
    rng = random.Random(seed)
    return {
        "last_node_id": nodes,
        "last_link_id": nodes * 2,
        "nodes": [
            {
                "id": index,
                "type": rng.choice(
                    ["KSampler", "CLIPTextEncode", "VAEDecode", "LoraLoader"]
                ),
                "pos": [rng.uniform(-2000, 2000), rng.uniform(-2000, 2000)],
                "size": {"0": 315, "1": 262},
                "flags": {},
                "order": index,
                "mode": 0,
                "inputs": [{"name": "model", "type": "MODEL", "link": index}],
                "outputs": [{"name": "LATENT", "type": "LATENT", "links": [index]}],
                "properties": {"Node name for S&R": "KSampler"},
                "widgets_values": [seed * 1000 + index, "fixed", 20, 7.5, "euler"],
            }
            for index in range(nodes)
        ],
        "links": [[i, i, 0, i + 1, 0, "MODEL"] for i in range(nodes)],
        "groups": [{"title": "Sampler ✨ 采样", "bounding": [0, 0, 100, 100]}],
        "config": {},
        "extra": {"ds": {"scale": 0.5, "offset": [0, 0]}},
        "version": 0.4,
    }


def sample_prompt_graph(seed=0):
    return {
        "3": {
            "class_type": "KSampler",
            "inputs": {"seed": seed, "steps": 20, "cfg": 7.0, "model": ["4", 0]},
        },
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": f"a cat {seed}"}},
    }


def edge_texts():
    """Texts whose bytes a parse-and-serialize round trip would change."""
    nested = "[" * 500 + "]" * 500
    return [
        "",
        "{}",
        "null",
        " ",
        "\n",
        '{"a": 1}',
        '{"a":1}',
        '{"a": 1, "a": 2}',  # duplicate key: a parse would keep one
        '{"b": 1, "a": 2}',  # key order
        '{"v": 1.0}',
        '{"v": 1e3}',  # number spelling
        '{"v": 1.10}',
        '{"v": NaN, "w": Infinity, "x": -Infinity}',  # ComfyUI's tokens
        '{"text": "NaN"}',
        '{"u": "\\u00e9\\ud83d\\ude00"}',  # escaped
        '{"u": "é 😀 中文 עברית é"}',  # raw unicode, astral, RTL, combining
        "trailing space ",
        '{"nul": "a\u0000b"}',
        "a\u0000b",
        "﻿{}",  # byte-order mark
        "  ",
        "not json at all",
        nested,
        json.dumps(sample_workflow(1)),
        json.dumps(sample_workflow(1), indent=2),
        json.dumps(sample_workflow(1), separators=(",", ":")),
        json.dumps({"v": NAN, "w": [INF, -INF]}),  # Python's lenient output
        dumps_strict({"v": NAN, "w": [INF, -INF]}),
    ]


def random_text(rng):
    """Arbitrary text of valid code points (no surrogates), ASCII to astral."""
    length = rng.choice([0, 1, 2, 7, 64, 1000, 20000])
    ranges = [(0x00, 0x7F), (0x80, 0x7FF), (0x800, 0xD7FF), (0xE000, 0xFFFF)]
    ranges.append((0x10000, 0x10FFFF))
    chars = []
    for _ in range(length):
        low, high = rng.choice(ranges)
        chars.append(chr(rng.randint(low, high)))
    return "".join(chars)


class DocumentsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = os.path.realpath(tempfile.mkdtemp())
        self.db_path = os.path.join(self.tmpdir, "prompts.db")
        self.db = PromptDatabase(self.db_path)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        PromptModel.close_all_instances()
        PromptModel.reset_schema_cache()
        assert_closed(self, self.tmpdir)
        shutil.rmtree(self.tmpdir)

    def conn(self):
        return self.db.model.get_connection()

    def prompt(self, text="a cat"):
        return self.db.save_prompt(text, prompt_hash=generate_prompt_hash(text))

    def old_form_image(self, prompt_id, name, workflow, prompt_graph):
        """A row as every version before the dedupe wrote it: text inline."""
        conn = self.conn()
        with conn:
            path = os.path.join(self.tmpdir, name)
            cursor = conn.execute(
                "INSERT INTO generated_images (prompt_id, image_path, filename, "
                "file_path, workflow_data, prompt_metadata, parameters) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (prompt_id, path, name, path, workflow, prompt_graph, "{}"),
            )
        return cursor.lastrowid

    def raw(self, image_id):
        return dict(
            self.conn()
            .execute(
                "SELECT workflow_data, prompt_metadata, workflow_document_id, "
                "prompt_document_id FROM generated_images WHERE id=?",
                (image_id,),
            )
            .fetchone()
        )

    def document_count(self):
        return (
            self.conn().execute("SELECT COUNT(*) FROM workflow_documents").fetchone()[0]
        )

    def linked(self, prompt_id, name, seed):
        return self.db.link_image_to_prompt(
            prompt_id,
            os.path.join(self.tmpdir, name),
            {"workflow": sample_workflow(seed), "prompt": sample_prompt_graph(seed)},
        )

    def store(self, text):
        conn = self.conn()
        with conn:
            return wd.store_text(conn, text)


class TestRoundTrip(DocumentsTestCase):
    def assert_round_trip(self, text):
        document_id = self.store(text)
        self.assertIsInstance(document_id, int)
        back = wd.read_stored_text(self.conn(), None, document_id)
        self.assertIsInstance(back, str)
        self.assertEqual(back, text)
        self.assertEqual(back.encode("utf-8"), text.encode("utf-8"))
        return document_id

    def test_edge_texts_come_back_byte_identical(self):
        for text in edge_texts():
            with self.subTest(text=text[:40]):
                self.assert_round_trip(text)

    def test_texts_that_parse_alike_stay_distinct(self):
        spaced = self.assert_round_trip('{"a": 1}')
        tight = self.assert_round_trip('{"a":1}')
        self.assertNotEqual(spaced, tight)

    def test_random_texts_come_back_byte_identical(self):
        rng = random.Random(20261024)
        for case in range(400):
            text = random_text(rng)
            with self.subTest(case=case):
                self.assert_round_trip(text)

    def test_random_workflows_come_back_byte_identical(self):
        rng = random.Random(7)
        for case in range(150):
            value = sample_workflow(rng.randint(0, 10**9), nodes=rng.randint(0, 60))
            if rng.random() < 0.3:
                value["nodes"].append({"widgets_values": [NAN, INF, -INF]})
            for text in (json.dumps(value), dumps_strict(value)):
                with self.subTest(case=case):
                    self.assert_round_trip(text)

    def test_huge_workflow_round_trips(self):
        text = json.dumps(sample_workflow(3, nodes=4000))
        self.assertGreater(len(text), 1_200_000)
        self.assert_round_trip(text)

    def test_identical_text_is_stored_once(self):
        text = json.dumps(sample_workflow(5))
        first = self.store(text)
        second = self.store(text)
        self.assertEqual(first, second)
        self.assertEqual(self.document_count(), 1)

    def test_null_stays_null(self):
        self.assertIsNone(self.store(None))
        self.assertIsNone(wd.read_stored_text(self.conn(), None, None))
        self.assertEqual(self.document_count(), 0)

    def test_document_is_compressed_utf8_keyed_by_sha256(self):
        text = "é" + json.dumps(sample_workflow(9))
        document_id = self.store(text)
        row = (
            self.conn()
            .execute(
                "SELECT sha256, byte_length, data FROM workflow_documents WHERE id=?",
                (document_id,),
            )
            .fetchone()
        )
        raw = text.encode("utf-8")
        import hashlib

        self.assertEqual(bytes(row["sha256"]), hashlib.sha256(raw).digest())
        self.assertEqual(row["byte_length"], len(raw))
        self.assertEqual(zlib.decompress(row["data"]), raw)
        self.assertLess(len(row["data"]), len(raw) / 3)

    def test_inline_text_wins_over_a_document(self):
        document_id = self.store("from the document")
        self.assertEqual(
            wd.read_stored_text(self.conn(), "inline", document_id), "inline"
        )
        self.assertEqual(wd.read_stored_text(self.conn(), "", document_id), "")

    def test_a_tampered_document_reads_as_unreadable_never_as_other_text(self):
        document_id = self.store('{"a": 1}')
        conn = self.conn()
        for data in (zlib.compress(b'{"a": 2}'), b"not zlib"):
            with conn:
                conn.execute(
                    "UPDATE workflow_documents SET data=? WHERE id=?",
                    (data, document_id),
                )
            with self.subTest(data=data), self.assertLogs(wd.logger, level="WARNING"):
                self.assertIsNone(wd.read_stored_text(conn, None, document_id))

    def test_a_missing_document_reads_as_unreadable(self):
        with self.assertLogs(wd.logger, level="WARNING"):
            self.assertIsNone(wd.read_stored_text(self.conn(), None, 424242))

    def test_reader_memo_returns_the_same_text(self):
        text = json.dumps(sample_workflow(2))
        document_id = self.store(text)
        reader = wd.DocumentReader(self.conn())
        self.assertEqual(reader.text(None, document_id), text)
        self.assertEqual(reader.text(None, document_id), text)
        self.assertEqual(reader.text("inline", document_id), "inline")


class TestSchema(DocumentsTestCase):
    def test_fresh_database_has_the_table_columns_and_indexes(self):
        conn = self.conn()
        self.assertIsNone(wd.workflow_documents_schema_error(conn, allow_missing=False))
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(generated_images)")
        }
        self.assertTrue(
            {"workflow_document_id", "prompt_document_id"}.issubset(columns)
        )
        indexes = {
            row[1] for row in conn.execute("PRAGMA index_list(generated_images)")
        }
        self.assertIn("idx_image_workflow_document", indexes)
        self.assertIn("idx_image_prompt_document", indexes)

    def test_no_trigger_is_installed(self):
        # Every restore validator refuses triggers; GC is done in code.
        row = (
            self.conn()
            .execute("SELECT name FROM sqlite_master WHERE type='trigger'")
            .fetchone()
        )
        self.assertIsNone(row)

    def test_current_database_verifies_and_restores(self):
        image = self.db.link_image_to_prompt(
            self.prompt(), os.path.join(self.tmpdir, "a.png"), {"workflow": {"n": 1}}
        )
        self.assertTrue(image)
        ok, reason = self.db.model.verify_database_file(
            self.db_path, require_library_state=True
        )
        self.assertTrue(ok, reason)

    def test_an_old_database_opens_migrates_and_keeps_its_inline_rows(self):
        PromptModel.close_all_instances()
        PromptModel.reset_schema_cache()
        old_path = os.path.join(self.tmpdir, "old.db")
        conn = sqlite3.connect(old_path)
        try:
            conn.executescript(
                "CREATE TABLE prompts (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "text TEXT NOT NULL, created_at TIMESTAMP, updated_at TIMESTAMP, "
                "category TEXT, tags TEXT, rating INTEGER, notes TEXT, "
                "hash TEXT UNIQUE);"
                "CREATE TABLE generated_images (id INTEGER PRIMARY KEY "
                "AUTOINCREMENT, prompt_id INTEGER NOT NULL, image_path TEXT NOT "
                "NULL, filename TEXT NOT NULL, generation_time TIMESTAMP, "
                "file_size INTEGER, width INTEGER, height INTEGER, format TEXT, "
                "workflow_data TEXT, prompt_metadata TEXT, parameters TEXT, "
                "file_path TEXT, FOREIGN KEY (prompt_id) REFERENCES prompts(id) "
                "ON DELETE CASCADE, UNIQUE(prompt_id, file_path));"
                "INSERT INTO prompts (id, text, hash) VALUES (1, 'a cat', 'h');"
                "INSERT INTO generated_images (prompt_id, image_path, filename, "
                "file_path, workflow_data, prompt_metadata) VALUES (1, '/x/a.png', "
                "'a.png', '/x/a.png', '{\"n\": NaN}', '{\"3\": {}}');"
            )
            conn.commit()
        finally:
            conn.close()
        ok, reason = PromptModel.verify_database_file(old_path)
        self.assertTrue(ok, reason)
        old = PromptDatabase(old_path)
        [image] = old.get_prompt_images(1)
        self.assertEqual(image["workflow_data"], {"n": None})
        self.assertEqual(image["prompt_metadata"], {"3": {}})
        row = (
            old.model.get_connection()
            .execute("SELECT workflow_data, workflow_document_id FROM generated_images")
            .fetchone()
        )
        self.assertEqual(tuple(row), ('{"n": NaN}', None))
        old.close_all()

    def test_verify_refuses_a_malformed_documents_table(self):
        bad = os.path.join(self.tmpdir, "bad.db")
        PromptModel.close_all_instances()
        conn = sqlite3.connect(self.db_path)
        try:
            with sqlite3.connect(bad) as target:
                conn.backup(target)
            target.close()
        finally:
            conn.close()
        cases = {
            "table": "DROP TABLE workflow_documents; CREATE TABLE "
            "workflow_documents (id INTEGER PRIMARY KEY, sha256 TEXT, data TEXT)",
            "values": "INSERT INTO workflow_documents (sha256, byte_length, data) "
            "VALUES (zeroblob(32), -1, x'00')",
            "reference": "PRAGMA foreign_keys=OFF; INSERT INTO prompts (text, hash) "
            "VALUES ('p', 'h2'); INSERT INTO generated_images (prompt_id, image_path,"
            " filename, workflow_document_id) VALUES (last_insert_rowid(), '/a', 'a',"
            " 99)",
            "index": "DROP INDEX idx_image_workflow_document",
        }
        for name, script in cases.items():
            copy = os.path.join(self.tmpdir, f"bad-{name}.db")
            with open(bad, "rb") as source, open(copy, "wb") as target:
                target.write(source.read())
            conn = sqlite3.connect(copy)
            try:
                conn.executescript(script)
                conn.commit()
            finally:
                conn.close()
            with self.subTest(name=name):
                ok, reason = PromptModel.verify_database_file(copy)
                self.assertFalse(ok)
                self.assertIn("orkflow", reason)


class TestSchemaErrors(unittest.TestCase):
    """Each way a restored file's document schema can be wrong is named."""

    IMAGES = (
        "CREATE TABLE generated_images (id INTEGER PRIMARY KEY, "
        "workflow_data TEXT, prompt_metadata TEXT{extra})"
    )
    TABLE = (
        "CREATE TABLE workflow_documents (id INTEGER PRIMARY KEY, sha256 BLOB NOT "
        "NULL UNIQUE, byte_length INTEGER NOT NULL, data BLOB NOT NULL{extra})"
    )
    REFS = (
        ", workflow_document_id {kind}, prompt_document_id "
        "INTEGER REFERENCES workflow_documents(id)"
    )
    GOOD_REF = "INTEGER REFERENCES workflow_documents(id)"

    def schema(self, images_extra="", table_extra="", indexes=True, table=True):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute(self.IMAGES.format(extra=images_extra))
        if table:
            conn.execute(self.TABLE.format(extra=table_extra))
        if indexes and images_extra:
            for column, index in wd.INDEXES.items():
                conn.execute(f"CREATE INDEX {index} ON generated_images({column})")
        return conn

    def test_each_error_is_named(self):
        refs = self.REFS.format(kind=self.GOOD_REF)
        cases = {
            "Missing workflow document schema": self.schema(table=False),
            "Incomplete workflow document schema": self.schema(),
            "Workflow documents have an invalid table definition": self.schema(
                refs, table_extra=", extra TEXT"
            ),
            "Workflow document references have an invalid column": self.schema(
                self.REFS.format(kind="TEXT REFERENCES workflow_documents(id)")
            ),
            "Workflow document references have invalid foreign keys": self.schema(
                self.REFS.format(kind="INTEGER")
            ),
            "Workflow document references are missing their index": self.schema(
                refs, indexes=False
            ),
        }
        for message, conn in cases.items():
            with self.subTest(message=message):
                self.assertEqual(
                    wd.workflow_documents_schema_error(conn, allow_missing=False),
                    message,
                )
        self.assertIsNone(wd.workflow_documents_schema_error(self.schema(table=False)))
        self.assertIsNone(
            wd.workflow_documents_schema_error(self.schema(refs), allow_missing=False)
        )

    def test_an_index_on_another_table_is_not_the_index(self):
        conn = self.schema(self.REFS.format(kind=self.GOOD_REF), indexes=False)
        conn.execute("CREATE TABLE other (workflow_document_id INTEGER)")
        conn.execute(
            "CREATE INDEX idx_image_workflow_document ON other(workflow_document_id)"
        )
        self.assertEqual(
            wd.workflow_documents_schema_error(conn),
            "Workflow document references are missing their index",
        )

    def test_migrate_refuses_a_bad_schema_and_leaves_it_alone(self):
        conn = self.schema(table_extra="")  # the table without the columns
        with self.assertRaisesRegex(sqlite3.DatabaseError, "Incomplete"):
            wd.migrate_workflow_documents(conn)
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(generated_images)")
        }
        self.assertNotIn("workflow_document_id", columns)

    def test_migrate_validates_what_it_installed(self):
        conn = self.schema(table=False)
        with patch.object(
            wd,
            "workflow_documents_schema_error",
            side_effect=[None, "Workflow documents broke"],
        ):
            with self.assertRaisesRegex(sqlite3.DatabaseError, "broke"):
                wd.migrate_workflow_documents(conn)
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
        self.assertNotIn("workflow_documents", tables)


class TestEdgeErrors(DocumentsTestCase):
    def test_store_refuses_values_that_cannot_be_documents(self):
        conn = self.conn()
        with self.assertRaises(TypeError):
            wd.store_text(conn, b"bytes")
        with patch.object(wd, "MAX_DOCUMENT_BYTES", 8):
            with self.assertRaises(ValueError):
                wd.store_text(conn, "123456789")
            self.assertFalse(wd.storable("12345678é"))
            self.assertTrue(wd.storable("1234567"))
            self.assertEqual(
                wd.stored_values(conn, {"workflow_data": "123456789"}),
                {"workflow_data": "123456789", "workflow_document_id": None},
            )
        conn.rollback()

    def test_a_hash_match_with_another_length_is_a_mismatch(self):
        document_id = self.store("abc")
        conn = self.conn()
        with conn:
            conn.execute(
                "UPDATE workflow_documents SET byte_length=5 WHERE id=?",
                (document_id,),
            )
        with self.assertRaises(wd.DocumentMismatch):
            wd.store_text(conn, "abc")
        conn.rollback()
        for length in (-1, 2, 4):
            with conn:
                conn.execute(
                    "UPDATE workflow_documents SET byte_length=? WHERE id=?",
                    (length, document_id),
                )
            with self.subTest(length=length), self.assertLogs(wd.logger, "WARNING"):
                self.assertIsNone(wd.read_stored_text(conn, None, document_id))

    def test_the_reader_memo_is_bounded(self):
        ids = [self.store(f"text {index}") for index in range(40)]
        reader = wd.DocumentReader(self.conn())
        for index, document_id in enumerate(ids):
            self.assertEqual(reader.text(None, document_id), f"text {index}")
        self.assertEqual(len(reader._memo), wd.DocumentReader.MEMO)

    def test_a_row_without_text_columns_is_left_alone(self):
        reader = wd.DocumentReader(self.conn())
        self.assertEqual(wd.resolve_image_row(reader, {"id": 1}), {"id": 1})
        self.assertEqual(
            wd.resolve_image_row(reader, {"workflow_document_id": None}),
            {"workflow_data": None},
        )

    def test_documents_of_rows_takes_only_known_columns(self):
        with self.assertRaises(ValueError):
            wd.documents_of_rows(self.conn(), "image_path", ["x"])


class TestWritersStoreNewForm(DocumentsTestCase):
    def test_link_image_stores_documents_not_inline_text(self):
        workflow = sample_workflow(1)
        graph = sample_prompt_graph(1)
        prompt_id = self.prompt()
        first = self.db.link_image_to_prompt(
            prompt_id,
            os.path.join(self.tmpdir, "a.png"),
            {"workflow": workflow, "prompt": graph},
        )
        second = self.db.link_image_to_prompt(
            prompt_id,
            os.path.join(self.tmpdir, "b.png"),
            {"workflow": workflow, "prompt": graph},
        )
        a, b = self.raw(first), self.raw(second)
        self.assertIsNone(a["workflow_data"])
        self.assertIsNone(a["prompt_metadata"])
        self.assertIsNotNone(a["workflow_document_id"])
        self.assertEqual(a["workflow_document_id"], b["workflow_document_id"])
        self.assertEqual(a["prompt_document_id"], b["prompt_document_id"])
        self.assertEqual(self.document_count(), 2)
        conn = self.conn()
        self.assertEqual(
            wd.read_stored_text(conn, None, a["workflow_document_id"]),
            dumps_strict(workflow),
        )

    def test_readers_answer_the_same_for_both_forms(self):
        workflow, graph = sample_workflow(4), sample_prompt_graph(4)
        workflow["nodes"][0]["widgets_values"] = [NAN, INF]
        prompt_id = self.prompt()
        new_id = self.db.link_image_to_prompt(
            prompt_id,
            os.path.join(self.tmpdir, "new.png"),
            {"workflow": workflow, "prompt": graph},
        )
        old_id = self.old_form_image(
            prompt_id, "old.png", dumps_strict(workflow), dumps_strict(graph)
        )
        new, old = self.db.get_image_by_id(new_id), self.db.get_image_by_id(old_id)
        for key in ("workflow_data", "prompt_metadata", "parameters"):
            self.assertEqual(new[key], old[key])
        self.assertEqual(new["workflow_data"]["nodes"][0]["widgets_values"], [None] * 2)
        self.assertNotIn("workflow_document_id", new)
        self.assertNotIn("prompt_document_id", old)
        listed = {
            image["id"]: image
            for reader in (
                lambda: self.db.get_prompt_images(prompt_id),
                lambda: self.db.get_recent_images(10),
                lambda: self.db.get_all_images(),
                lambda: self.db.search_images_by_prompt("cat"),
            )
            for image in reader()
        }
        self.assertEqual(listed[new_id]["workflow_data"], new["workflow_data"])
        self.assertEqual(listed[old_id]["prompt_metadata"], graph)
        info_new = self.db.get_image_prompt_info(os.path.join(self.tmpdir, "new.png"))
        info_old = self.db.get_image_prompt_info(os.path.join(self.tmpdir, "old.png"))
        self.assertEqual(info_new["workflow_data"], info_old["workflow_data"])
        self.assertEqual(info_new["prompt_metadata"], graph)

    def test_diagnostics_recent_images_resolve_documents(self):
        prompt_id = self.prompt()
        self.db.link_image_to_prompt(
            prompt_id,
            os.path.join(self.tmpdir, "d.png"),
            {"workflow": {"n": 1}, "prompt": {"3": {}}},
        )
        from utils import diagnostics

        tool = diagnostics.GalleryDiagnostics(self.db_path)
        result = tool.check_images_table()
        tool_images = result["recent_images"]
        self.assertEqual(tool_images[0]["workflow_data"], '{"n": 1}')
        self.assertNotIn("workflow_document_id", tool_images[0])


class TestGarbage(DocumentsTestCase):
    """A document no row references is removed with its last reference."""

    def test_delete_image_releases_only_unshared_documents(self):
        prompt_id = self.prompt()
        shared_a = self.linked(prompt_id, "a.png", 1)
        self.linked(prompt_id, "b.png", 1)
        alone = self.linked(prompt_id, "c.png", 2)
        self.assertEqual(self.document_count(), 4)
        self.db.delete_image(shared_a)
        self.assertEqual(self.document_count(), 4)
        self.db.delete_image(alone)
        self.assertEqual(self.document_count(), 2)

    def test_deleting_prompts_releases_their_images_documents(self):
        first, second = self.prompt("one"), self.prompt("two")
        self.linked(first, "a.png", 1)
        self.linked(second, "b.png", 2)
        self.db.delete_prompt(first)
        self.assertEqual(self.document_count(), 2)
        self.db.update_prompt_metadata(second, category="gone")
        self.assertEqual(self.db.delete_prompts_by_category("gone"), 1)
        self.assertEqual(self.document_count(), 0)

    def test_cleanup_missing_images_releases_documents(self):
        prompt_id = self.prompt()
        self.linked(prompt_id, "gone.png", 1)
        self.db.cleanup_missing_images()
        self.assertEqual(self.document_count(), 0)
        self.linked(prompt_id, "gone-too.png", 2)
        self.assertEqual(self.db.cleanup_missing_images(roots=[self.tmpdir]), 1)
        self.assertEqual(self.document_count(), 0)

    def test_sweep_removes_what_a_path_without_release_left(self):
        prompt_id = self.prompt()
        image = self.linked(prompt_id, "a.png", 1)
        conn = self.conn()
        with conn:
            conn.execute("DELETE FROM generated_images WHERE id=?", (image,))
        self.assertEqual(self.document_count(), 2)
        with conn:
            self.assertEqual(wd.sweep_documents(conn), 2)
        self.assertEqual(self.document_count(), 0)

    def test_startup_sweeps_unreferenced_documents(self):
        prompt_id = self.prompt()
        image = self.linked(prompt_id, "a.png", 1)
        conn = self.conn()
        with conn:
            conn.execute("DELETE FROM generated_images WHERE id=?", (image,))
        PromptModel.close_all_instances()
        PromptModel.reset_schema_cache()
        self.db = PromptDatabase(self.db_path)
        self.assertEqual(self.document_count(), 0)

    def test_release_keeps_a_document_the_other_column_references(self):
        prompt_id = self.prompt()
        text = '{"same": true}'
        first = self.db.link_image_to_prompt(
            prompt_id, os.path.join(self.tmpdir, "a.png"), {"workflow": {"same": True}}
        )
        second = self.db.link_image_to_prompt(
            prompt_id, os.path.join(self.tmpdir, "b.png"), {"prompt": {"same": True}}
        )
        self.assertEqual(
            self.raw(first)["workflow_document_id"],
            self.raw(second)["prompt_document_id"],
        )
        self.db.delete_image(first)
        self.assertEqual(
            wd.read_stored_text(
                self.conn(), None, self.raw(second)["prompt_document_id"]
            ),
            text,
        )


class TestMoreGarbagePaths(DocumentsTestCase):
    def test_bulk_delete_prompts_releases_documents(self):
        first, second = self.prompt("one"), self.prompt("two")
        self.linked(first, "a.png", 1)
        self.linked(second, "b.png", 2)
        self.assertEqual(self.db.bulk_delete_prompts([first, second]), 2)
        self.assertEqual(self.document_count(), 0)

    def test_duplicate_prompt_merge_releases_images_it_could_not_move(self):
        primary = self.prompt("A cat")
        copy = self.db.save_prompt("a cat ", prompt_hash="other-hash")
        self.linked(primary, "same.png", 1)
        self.linked(copy, "same.png", 2)  # collides with the primary's link
        self.assertEqual(self.document_count(), 4)
        self.db.cleanup_duplicates()
        self.assertEqual(self.document_count(), 2)

    def test_maintenance_delete_ids_releases_documents(self):
        from database.maintenance import _delete_ids

        prompt_id = self.prompt()
        image = self.linked(prompt_id, "a.png", 1)
        other = self.prompt("other")
        self.linked(other, "b.png", 2)
        conn = self.conn()
        with conn:
            _delete_ids(conn, "generated_images", [image])
        self.assertEqual(self.document_count(), 2)
        with conn:
            _delete_ids(conn, "prompts", [other])
        self.assertEqual(self.document_count(), 0)

    def test_failed_insert_of_a_known_link_leaves_no_new_document(self):
        prompt_id = self.prompt()
        path = os.path.join(self.tmpdir, "a.png")
        self.db.link_image_to_prompt(prompt_id, path, {"workflow": {"v": 1}})
        before = self.document_count()
        self.assertEqual(
            self.db.link_image_to_prompt(prompt_id, path, {"workflow": {"v": 2}}), 0
        )
        self.assertEqual(self.document_count(), before)


class TestPairIngestionDocuments(DocumentsTestCase):
    def resolution(self):
        return {
            "state": "resolved",
            "positive": {"text": "cat", "provenance": {"source": "api"}},
            "negative": {"text": "blur", "provenance": {"source": "api"}},
        }

    def test_ingestion_stores_new_form_and_replaces_documents_it_updates(self):
        from database.pair_ingestion import ingest_resolved_pair

        path = os.path.join(self.tmpdir, "scan.png")
        first = {"workflow": sample_workflow(1), "prompt": sample_prompt_graph(1)}
        added = ingest_resolved_pair(self.db, path, first, self.resolution())
        self.assertEqual(added["status"], "added")
        row = self.raw(added["image_id"])
        self.assertIsNone(row["workflow_data"])
        self.assertIsNotNone(row["workflow_document_id"])
        again = ingest_resolved_pair(self.db, path, first, self.resolution())
        self.assertEqual(again["status"], "unchanged")
        second = {"workflow": sample_workflow(2), "prompt": sample_prompt_graph(1)}
        updated = ingest_resolved_pair(self.db, path, second, self.resolution())
        self.assertEqual(updated["status"], "enriched")
        self.assertEqual(self.document_count(), 2)  # the first workflow went
        image = self.db.get_image_by_id(added["image_id"])
        self.assertEqual(image["workflow_data"], sample_workflow(2))

    def test_an_old_form_row_with_the_same_text_is_unchanged(self):
        from database.pair_ingestion import ingest_resolved_pair

        path = os.path.join(self.tmpdir, "old.png")
        metadata = {"workflow": sample_workflow(3), "prompt": sample_prompt_graph(3)}
        added = ingest_resolved_pair(self.db, path, metadata, self.resolution())
        conn = self.conn()
        with conn:  # rewrite it as an older version stored it
            conn.execute(
                "UPDATE generated_images SET workflow_data=?, prompt_metadata=?, "
                "workflow_document_id=NULL, prompt_document_id=NULL WHERE id=?",
                (
                    dumps_strict(metadata["workflow"], sort_keys=True),
                    dumps_strict(metadata["prompt"], sort_keys=True),
                    added["image_id"],
                ),
            )
            wd.sweep_documents(conn)
        again = ingest_resolved_pair(self.db, path, metadata, self.resolution())
        self.assertEqual(again["status"], "unchanged")
        self.assertEqual(self.document_count(), 0)


class TestQueryPlans(DocumentsTestCase):
    """Every statement the store and the migration run is an index search."""

    def plan(self, sql, params=()):
        rows = self.conn().execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
        return " | ".join(row[3] for row in rows)

    def test_statements_use_indexes(self):
        for name, (sql, params) in wd.QUERY_PLAN_STATEMENTS.items():
            with self.subTest(name=name):
                plan = self.plan(sql, params)
                self.assertNotRegex(plan, r"\bSCAN (generated_images|gi)\b(?! USING)")
                self.assertTrue("USING" in plan or "SEARCH" in plan, f"{name}: {plan}")


if __name__ == "__main__":
    unittest.main()
