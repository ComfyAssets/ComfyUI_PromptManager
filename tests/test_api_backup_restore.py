"""Route tests for GET /prompt_manager/backup and POST /prompt_manager/restore.

The backup must include rows still sitting in the WAL, the restore must
stream the upload to disk, reject anything that is not a healthy prompts
database and leave the live database untouched when it does.
"""

import glob
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database.models import PromptModel  # noqa: E402

try:
    from tests.open_handles import assert_closed  # noqa: E402
except ImportError:  # discovered with tests/ as the top-level directory
    from open_handles import assert_closed  # noqa: E402

import aiohttp
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from database.operations import PromptDatabase
from py.api import PromptManagerAPI, _public_path
from utils.hashing import generate_prompt_hash


def _texts(path):
    conn = sqlite3.connect(path)
    try:
        return sorted(r[0] for r in conn.execute("SELECT text FROM prompts"))
    finally:
        conn.close()


class BackupRestoreTestCase(AioHTTPTestCase):
    async def get_application(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "prompts.db")

        app = web.Application()
        routes = web.RouteTableDef()
        self.api = PromptManagerAPI()
        self.api.db = PromptDatabase(self.db_path)
        self.api.add_routes(routes)
        app.router.add_routes(routes)
        return app

    async def tearDownAsync(self):
        await super().tearDownAsync()  # closes the aiohttp test client
        PromptModel.close_all_instances()  # restore may have swapped api.db
        assert_closed(self, self.tmpdir)
        for name in os.listdir(self.tmpdir):
            os.unlink(os.path.join(self.tmpdir, name))
        os.rmdir(self.tmpdir)

    def _save(self, text):
        return self.api.db.save_prompt(
            text=text, prompt_hash=generate_prompt_hash(text)
        )

    def _write(self, name, data):
        path = os.path.join(self.tmpdir, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def _make_db_file(self, texts, name="upload.db"):
        path = os.path.join(self.tmpdir, name)
        db = PromptDatabase(path)
        for text in texts:
            db.save_prompt(text=text, prompt_hash=generate_prompt_hash(text))
        db.close_all()
        return path

    async def _restore(self, path, field="database_file"):
        with open(path, "rb") as fh:
            form = aiohttp.FormData()
            form.add_field(
                field,
                fh.read(),
                filename=os.path.basename(path),
                content_type="application/octet-stream",
            )
        return await self.client.post("/prompt_manager/restore", data=form)


class TestBackupRoute(BackupRestoreTestCase):
    async def test_backup_download_contains_rows_still_in_the_wal(self):
        for n in range(3):
            self._save(f"backup me {n}")
        self.assertGreater(os.path.getsize(self.db_path + "-wal"), 0)
        created = []
        real_mkstemp = tempfile.mkstemp

        def spy_mkstemp(*args, **kwargs):
            result = real_mkstemp(*args, **kwargs)
            created.append(result[1])
            return result

        with patch("py.api.admin.tempfile.mkstemp", spy_mkstemp):
            resp = await self.client.get("/prompt_manager/backup")

        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.content_type, "application/octet-stream")
        disposition = resp.headers["Content-Disposition"]
        self.assertIn("attachment", disposition)
        self.assertIn("prompts_backup_", disposition)
        self.assertTrue(disposition.rstrip('"').endswith(".db"))

        body = await resp.read()
        downloaded = self._write("downloaded.db", body)
        self.assertEqual(
            _texts(downloaded), ["backup me 0", "backup me 1", "backup me 2"]
        )
        # The server-side temp file is gone once the response is complete
        self.assertEqual(len(created), 1)
        self.assertFalse(os.path.exists(created[0]))

    async def test_backup_of_missing_database_is_404(self):
        self.api.db.model.db_path = os.path.join(self.tmpdir, "nope.db")
        resp = await self.client.get("/prompt_manager/backup")
        self.assertEqual(resp.status, 404)
        data = await resp.json()
        self.assertFalse(data["success"])

    async def test_backup_failure_is_500(self):
        self._save("x")
        with patch.object(self.api.db.model, "backup_database", return_value=False):
            resp = await self.client.get("/prompt_manager/backup")
        self.assertEqual(resp.status, 500)
        data = await resp.json()
        self.assertFalse(data["success"])
        self.assertIn("backup", data["error"].lower())


class TestRestoreRoute(BackupRestoreTestCase):
    async def test_restore_replaces_database_and_reports_backup(self):
        self._save("old prompt")
        old_db = self.api.db
        upload = self._make_db_file(["restored a", "restored b"])

        resp = await self._restore(upload)

        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["prompt_count"], 2)
        # The response names the safety backup without the server's layout
        backups = glob.glob(self.db_path + ".backup_*")
        self.assertEqual(len(backups), 1)
        self.assertEqual(data["backup_created"], _public_path(backups[0]))
        self.assertNotIn(self.tmpdir, data["backup_created"])
        self.assertEqual(_texts(backups[0]), ["old prompt"])
        self.assertIsNot(self.api.db, old_db)
        self.assertEqual(self.api.db.model.db_path, self.db_path)

        texts = sorted(p["text"] for p in self.api.db.search_prompts())
        self.assertEqual(texts, ["restored a", "restored b"])

    async def test_restore_leaves_no_temp_files_behind(self):
        upload = self._make_db_file(["restored"])
        created = []
        real_mkstemp = tempfile.mkstemp

        def spy_mkstemp(*args, **kwargs):
            result = real_mkstemp(*args, **kwargs)
            created.append(result[1])
            return result

        with patch("py.api.admin.tempfile.mkstemp", spy_mkstemp):
            resp = await self._restore(upload)

        self.assertEqual(resp.status, 200)
        self.assertEqual(len(created), 1)
        for suffix in ("", "-wal", "-shm", "-journal"):
            self.assertFalse(os.path.exists(created[0] + suffix))

    async def test_text_file_is_rejected_and_database_untouched(self):
        self._save("keep me")
        upload = self._write("notes.txt", b"this is not a database")

        resp = await self._restore(upload)

        self.assertEqual(resp.status, 400)
        data = await resp.json()
        self.assertFalse(data["success"])
        self.assertIn("SQLite", data["error"])
        self.assertEqual([p["text"] for p in self.api.db.search_prompts()], ["keep me"])
        self.assertFalse(
            any(n.startswith("prompts.db.backup_") for n in os.listdir(self.tmpdir))
        )

    async def test_database_without_prompts_table_is_rejected(self):
        self._save("keep me")
        path = os.path.join(self.tmpdir, "other.db")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE things (id INTEGER)")
        conn.commit()
        conn.close()

        resp = await self._restore(path)

        self.assertEqual(resp.status, 400)
        data = await resp.json()
        self.assertFalse(data["success"])
        self.assertIn("prompts", data["error"])
        self.assertEqual([p["text"] for p in self.api.db.search_prompts()], ["keep me"])

    async def test_wrong_field_name_is_rejected(self):
        upload = self._make_db_file(["restored"])
        resp = await self._restore(upload, field="file")
        self.assertEqual(resp.status, 400)
        data = await resp.json()
        self.assertFalse(data["success"])
        self.assertIn("database_file", data["error"])

    async def test_empty_upload_is_rejected(self):
        upload = self._write("empty.db", b"")
        resp = await self._restore(upload)
        self.assertEqual(resp.status, 400)
        data = await resp.json()
        self.assertFalse(data["success"])
        self.assertIn("empty", data["error"].lower())

    async def test_upload_over_the_size_limit_is_rejected_while_streaming(self):
        self._save("keep me")
        self.api.restore_max_bytes = 1024
        upload = self._write("big.db", b"SQLite format 3\x00" + b"\x00" * 8192)

        resp = await self._restore(upload)

        self.assertEqual(resp.status, 400)
        data = await resp.json()
        self.assertFalse(data["success"])
        self.assertIn("too large", data["error"].lower())
        self.assertEqual([p["text"] for p in self.api.db.search_prompts()], ["keep me"])

    async def test_non_multipart_request_is_rejected(self):
        resp = await self.client.post("/prompt_manager/restore", data=b"raw bytes")
        self.assertIn(resp.status, (400, 500))
        data = await resp.json()
        self.assertFalse(data["success"])


if __name__ == "__main__":
    unittest.main()
