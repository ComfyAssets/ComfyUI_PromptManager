"""The workflow storage migration (database/workflow_storage.py).

Rewrites old-form rows (text inline) to the new form (a document id) in
batches. Each batch is one transaction, so a crash leaves every row in one
form or the other and readable either way; a rerun continues where the last
committed batch stopped. Rows are read one at a time, the stored text comes
back byte for byte, and no image or video file is ever opened.
"""

import builtins
import hashlib
import json
import os
import random
import sqlite3
import tracemalloc
import unittest
from unittest.mock import patch

from PIL import Image, PngImagePlugin

from database import workflow_documents as wd
from database import workflow_storage as ws
from database.models import PromptModel

try:
    from tests.test_workflow_documents import (
        DocumentsTestCase,
        edge_texts,
        random_text,
        sample_prompt_graph,
        sample_workflow,
    )
except ImportError:  # discovered with tests/ as the top-level directory
    from test_workflow_documents import (
        DocumentsTestCase,
        edge_texts,
        random_text,
        sample_prompt_graph,
        sample_workflow,
    )


def _digest(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class MigrationTestCase(DocumentsTestCase):
    def library(self, count=40, seed=11):
        """Old-form rows: duplicated runs, edge texts and random text."""
        rng = random.Random(seed)
        prompt_id = self.prompt()
        texts = edge_texts()
        expected = {}
        for index in range(count):
            run = index // 4  # four images per run share their texts
            if index % 10 == 9:
                workflow = texts[index % len(texts)]
                graph = random_text(rng)
            else:
                workflow = json.dumps(sample_workflow(run, nodes=20))
                graph = json.dumps(sample_prompt_graph(run))
            if index % 13 == 5:
                graph = None
            image_id = self.old_form_image(prompt_id, f"{index}.png", workflow, graph)
            expected[image_id] = (workflow, graph)
        return prompt_id, expected

    def texts(self):
        conn = self.conn()
        reader = wd.DocumentReader(conn)
        rows = conn.execute(
            "SELECT id, workflow_data, prompt_metadata, workflow_document_id, "
            "prompt_document_id FROM generated_images ORDER BY id"
        )
        result = {}
        for row in rows:
            data = wd.resolve_image_row(reader, row)
            result[row["id"]] = (data["workflow_data"], data["prompt_metadata"])
        return result

    def inline_ids(self):
        return {
            row[0]
            for row in self.conn().execute(
                "SELECT id FROM generated_images WHERE workflow_data IS NOT NULL "
                "OR prompt_metadata IS NOT NULL"
            )
        }

    def payloads(self, prompt_id):
        images = sorted(self.db.get_prompt_images(prompt_id), key=lambda i: i["id"])
        for image in images:
            image.pop("generation_time", None)
        return images

    def migrate(self, **kwargs):
        kwargs.setdefault("max_rows", 7)
        return self.db.migrate_workflow_storage(**kwargs)


class TestMigration(MigrationTestCase):
    def test_every_row_is_rewritten_and_reads_back_identical(self):
        prompt_id, expected = self.library()
        before = self.payloads(prompt_id)
        result = self.migrate()
        self.assertEqual(self.texts(), expected)
        self.assertEqual(self.inline_ids(), set())
        self.assertEqual(self.payloads(prompt_id), before)
        distinct = {t for pair in expected.values() for t in pair if t is not None}
        self.assertEqual(self.document_count(), len(distinct))
        self.assertEqual(result["rows"], len(expected))
        self.assertGreater(result["batches"], 1)
        self.assertFalse(result["stopped"])

    def test_a_second_run_changes_nothing(self):
        _prompt_id, expected = self.library(count=12)
        self.migrate()
        conn = self.conn()
        documents = list(conn.execute("SELECT * FROM workflow_documents ORDER BY id"))
        result = self.migrate()
        self.assertEqual(result["rows"], 0)
        self.assertEqual(
            [tuple(r) for r in documents],
            [tuple(r) for r in conn.execute("SELECT * FROM workflow_documents")],
        )
        self.assertEqual(self.texts(), expected)

    def test_a_crash_mid_migration_leaves_every_row_readable_and_resumes(self):
        prompt_id, expected = self.library()
        before = self.payloads(prompt_id)
        real = wd.store_text
        calls = []

        def crashing(conn, text, *args):
            calls.append(text)
            if len(calls) == 23:
                raise RuntimeError("power cut")
            return real(conn, text, *args)

        with patch.object(ws, "store_text", side_effect=crashing):
            with self.assertRaises(RuntimeError):
                self.migrate()
        self.assertFalse(self.conn().in_transaction)
        self.assertEqual(self.texts(), expected)
        self.assertEqual(self.payloads(prompt_id), before)
        remaining = self.inline_ids()
        self.assertTrue(0 < len(remaining) < len(expected))
        # The failed batch rolled back whole: what is left is a suffix by id.
        self.assertEqual(remaining, {i for i in expected if i >= min(remaining)})
        self.migrate()
        self.assertEqual(self.inline_ids(), set())
        self.assertEqual(self.texts(), expected)

    def test_a_crash_inside_a_batch_writes_nothing_of_that_batch(self):
        _prompt_id, expected = self.library(count=10)
        with patch.object(ws, "store_text", side_effect=RuntimeError("full")):
            with self.assertRaises(RuntimeError):
                self.migrate(max_rows=50)
        self.assertEqual(self.inline_ids(), set(expected))
        self.assertEqual(self.document_count(), 0)

    def test_a_document_that_reads_back_differently_keeps_its_row(self):
        _prompt_id, expected = self.library(count=6)
        with patch.object(ws, "read_stored_text", return_value="something else"):
            result = self.migrate(max_rows=50)
        self.assertEqual(result["errors"], sorted(expected))
        self.assertEqual(self.inline_ids(), set(expected))
        self.assertEqual(self.texts(), expected)
        self.assertEqual(self.document_count(), 0)

    def test_stop_between_batches_then_continue(self):
        _prompt_id, expected = self.library(count=30)
        seen = []
        result = self.migrate(on_batch=seen.append, should_stop=lambda: len(seen) >= 2)
        self.assertTrue(result["stopped"])
        self.assertEqual(result["rows"], 14)
        self.assertEqual(len(self.inline_ids()), len(expected) - 14)
        self.assertEqual(seen[-1]["rows"], 14)
        self.migrate()
        self.assertEqual(self.inline_ids(), set())
        self.assertEqual(self.texts(), expected)

    def test_values_that_are_not_text_stay_inline(self):
        # A TEXT column turns numbers into text; only a blob stays other.
        prompt_id = self.prompt()
        blob = self.old_form_image(prompt_id, "blob.png", b"\x00raw", "{}")
        other = self.old_form_image(prompt_id, "other.png", "42", b"\x01")
        result = self.migrate()
        self.assertEqual(result["protected"], 2)
        conn = self.conn()
        rows = {
            row[0]: tuple(row[1:])
            for row in conn.execute(
                "SELECT id, workflow_data, prompt_metadata, workflow_document_id, "
                "prompt_document_id FROM generated_images"
            )
        }
        self.assertEqual(rows[blob][:2], (b"\x00raw", None))
        self.assertIsNone(rows[blob][2])
        self.assertIsNotNone(rows[blob][3])
        self.assertEqual(rows[other][:2], (None, b"\x01"))
        self.assertEqual(self.texts()[other], ("42", b"\x01"))
        self.assertEqual(self.migrate()["protected"], 2)  # still there, still kept

    def test_a_row_an_older_version_rewrote_inline_takes_the_inline_text(self):
        prompt_id = self.prompt()
        image = self.db.link_image_to_prompt(
            prompt_id, os.path.join(self.tmpdir, "a.png"), {"workflow": {"v": 1}}
        )
        conn = self.conn()
        with conn:  # an older version updates the inline column only
            conn.execute(
                "UPDATE generated_images SET workflow_data=? WHERE id=?",
                ('{"v": 2}', image),
            )
        self.assertEqual(self.texts()[image][0], '{"v": 2}')
        self.migrate()
        self.assertEqual(self.texts()[image][0], '{"v": 2}')
        self.assertEqual(self.inline_ids(), set())
        stored = {row[0] for row in conn.execute("SELECT data FROM workflow_documents")}
        self.assertNotIn(b'{"v": 1}', {bytes(wd_data) for wd_data in stored})
        texts = [
            wd.read_stored_text(conn, None, row[0])
            for row in conn.execute("SELECT id FROM workflow_documents")
        ]
        self.assertNotIn('{"v": 1}', texts)

    def test_new_writes_during_the_migration_are_kept(self):
        prompt_id, expected = self.library(count=20)
        added = []

        def between(_progress):
            if not added:
                added.append(
                    self.db.link_image_to_prompt(
                        prompt_id,
                        os.path.join(self.tmpdir, "new.png"),
                        {"workflow": {"new": True}},
                    )
                )
                added.append(
                    self.old_form_image(prompt_id, "late.png", '{"late": 1}', None)
                )

        self.migrate(on_batch=between)
        texts = self.texts()
        self.assertEqual(texts[added[0]][0], '{"new": true}')
        self.assertEqual(texts[added[1]], ('{"late": 1}', None))
        self.assertEqual(self.inline_ids(), set())
        for image_id, pair in expected.items():
            self.assertEqual(texts[image_id], pair)

    def test_memory_stays_bounded_by_one_row(self):
        prompt_id = self.prompt()
        big = json.dumps(sample_workflow(1, nodes=3000))  # about 0.9 MB
        for index in range(40):
            self.old_form_image(
                prompt_id, f"{index}.png", big[:-1] + f', "n": {index}}}', None
            )
        tracemalloc.start()
        try:
            self.migrate(max_rows=200, max_bytes=4 * 2**20)
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 12 * 2**20, f"peak {peak / 2**20:.1f} MB")
        self.assertEqual(self.inline_ids(), set())

    def test_batches_are_bounded_by_bytes(self):
        prompt_id = self.prompt()
        text = "x" * 100_000
        for index in range(10):
            self.old_form_image(prompt_id, f"{index}.png", text + str(index), None)
        seen = []
        self.migrate(max_rows=200, max_bytes=250_000, on_batch=seen.append)
        self.assertEqual([batch["rows"] for batch in seen], [3, 6, 9, 10])

    def test_unreferenced_documents_are_swept_at_the_end(self):
        prompt_id = self.prompt()
        image = self.db.link_image_to_prompt(
            prompt_id, os.path.join(self.tmpdir, "a.png"), {"workflow": {"v": 1}}
        )
        conn = self.conn()
        with conn:
            conn.execute("DELETE FROM generated_images WHERE id=?", (image,))
        result = self.migrate()
        self.assertEqual(result["documents_removed"], 2)
        self.assertEqual(self.document_count(), 0)

    def test_no_media_file_is_opened_or_changed(self):
        media = os.path.join(self.tmpdir, "output")
        os.makedirs(media)
        prompt_id = self.prompt()
        for index in range(5):
            path = os.path.join(media, f"{index}.png")
            info = PngImagePlugin.PngInfo()
            info.add_text("workflow", json.dumps(sample_workflow(index)))
            with Image.new("RGB", (4, 4)) as image:
                image.save(path, pnginfo=info)
            conn = self.conn()
            with conn:
                conn.execute(
                    "INSERT INTO generated_images (prompt_id, image_path, filename, "
                    "file_path, workflow_data) VALUES (?, ?, ?, ?, ?)",
                    (prompt_id, path, f"{index}.png", path, '{"n": 1}'),
                )
        before = {
            name: (
                _digest(os.path.join(media, name)),
                os.stat(os.path.join(media, name)),
            )
            for name in os.listdir(media)
        }
        opened = []
        real_open, real_os_open = builtins.open, os.open

        def tracking_open(file, *args, **kwargs):
            opened.append(str(file))
            return real_open(file, *args, **kwargs)

        def tracking_os_open(path, *args, **kwargs):
            opened.append(str(path))
            return real_os_open(path, *args, **kwargs)

        with patch("builtins.open", tracking_open), patch("os.open", tracking_os_open):
            with patch.object(Image, "open", side_effect=AssertionError("opened")):
                self.migrate()
        self.assertEqual([p for p in opened if p.startswith(media)], [])
        for name, (digest, stat) in before.items():
            now = os.stat(os.path.join(media, name))
            self.assertEqual(_digest(os.path.join(media, name)), digest)
            self.assertEqual(now.st_mtime_ns, stat.st_mtime_ns)
        self.assertEqual(self.inline_ids(), set())


class TestPreview(MigrationTestCase):
    def test_preview_counts_and_writes_nothing(self):
        prompt_id, expected = self.library(count=20)
        self.old_form_image(prompt_id, "blob.png", b"raw", None)
        files = [self.db_path + suffix for suffix in ("", "-wal")]
        before = [_digest(path) for path in files]
        draft = self.db.preview_maintenance("workflow_storage")
        self.assertEqual([_digest(path) for path in files], before)
        self.assertEqual(draft.operation, "workflow_storage")
        self.assertEqual(draft.scope["count"], len(expected))
        self.assertEqual(draft.protected["count"], 1)
        self.assertFalse(draft.media["deletes_files"])
        self.assertIn("No image or video file", draft.media["label"])
        labels = [row["label"] for row in draft.details]
        self.assertIn("Database file", labels)
        self.assertIn("Older versions", labels)
        self.assertIn("Reclaim space", labels)

    def test_preview_without_octet_length_omits_the_inline_bytes(self):
        # SQLite before 3.43 (older Windows Python builds) has no octet_length.
        self.library(count=4)
        with patch.object(ws.sqlite3, "sqlite_version_info", (3, 37, 2)):
            draft = self.db.preview_maintenance("workflow_storage")
        labels = [row["label"] for row in draft.details]
        self.assertNotIn("Inline text", labels)
        self.assertEqual(draft.scope["count"], 4)

    def test_preview_of_a_migrated_library_has_nothing_to_do(self):
        self.library(count=5)
        self.migrate()
        draft = self.db.preview_maintenance("workflow_storage")
        self.assertEqual(draft.scope["count"], 0)

    def test_the_batch_reads_are_index_searches(self):
        conn = self.conn()
        for name, (sql, params) in ws.QUERY_PLAN_STATEMENTS.items():
            plan = " | ".join(
                row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params)
            )
            with self.subTest(name=name):
                self.assertIn("USING INTEGER PRIMARY KEY", plan, plan)


class TestReclaimSpace(MigrationTestCase):
    def test_reclaim_space_gives_the_freed_pages_back(self):
        prompt_id = self.prompt()
        big = json.dumps(sample_workflow(1, nodes=600))
        for index in range(60):
            self.old_form_image(prompt_id, f"{index}.png", big, big)
        expected = self.texts()
        self.migrate()
        conn = self.conn()
        with conn:  # a document a path forgot to release
            conn.execute(
                "INSERT INTO workflow_documents (sha256, byte_length, data) "
                "VALUES (zeroblob(32), 0, x'789c030000000001')"
            )
        result = self.db.reclaim_space()
        self.assertEqual(result["documents_removed"], 1)
        self.assertFalse(result["checkpoint_busy"])
        self.assertLess(result["after_bytes"], result["before_bytes"] / 4)
        wal = self.db_path + "-wal"
        self.assertEqual(os.path.getsize(wal) if os.path.exists(wal) else 0, 0)
        self.assertEqual(self.texts(), expected)


class TestRestore(MigrationTestCase):
    def test_restoring_a_pre_dedupe_backup_reads_and_migrates(self):
        prompt_id, expected = self.library(count=8)
        backup = os.path.join(self.tmpdir, "old-backup.db")
        conn = sqlite3.connect(backup)
        try:  # as a version before the dedupe wrote it: no table, no columns
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
            )
            conn.execute(
                "INSERT INTO prompts (id, text, hash) VALUES (?, 'a cat', 'h')",
                (prompt_id,),
            )
            for image_id, (workflow, graph) in expected.items():
                conn.execute(
                    "INSERT INTO generated_images (id, prompt_id, image_path, "
                    "filename, file_path, workflow_data, prompt_metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        image_id,
                        prompt_id,
                        f"/x/{image_id}",
                        "x",
                        f"/x/{image_id}",
                        workflow,
                        graph,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        self.migrate()  # the live library is new form now
        self.db.model.restore_from_file(backup)
        self.assertEqual(self.texts(), expected)
        self.assertEqual(self.inline_ids(), set(expected))
        self.migrate()
        self.assertEqual(self.texts(), expected)
        self.assertEqual(self.inline_ids(), set())

    def test_restoring_a_deduped_backup_reads_the_same(self):
        prompt_id, expected = self.library(count=8)
        self.migrate()
        backup = os.path.join(self.tmpdir, "new-backup.db")
        self.assertTrue(self.db.model.backup_database(backup))
        ok, reason = PromptModel.verify_database_file(backup)
        self.assertTrue(ok, reason)
        self.db.delete_prompt(prompt_id)
        self.assertEqual(self.document_count(), 0)
        self.db.model.restore_from_file(backup)
        self.assertEqual(self.texts(), expected)


if __name__ == "__main__":
    unittest.main()
