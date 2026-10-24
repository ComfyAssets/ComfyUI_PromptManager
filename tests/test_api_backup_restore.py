"""Route tests for GET /prompt_manager/backup and POST /prompt_manager/restore.

The backup must include rows still sitting in the WAL, the restore must
stream the upload to disk, reject anything that is not a healthy prompts
database and leave the live database untouched when it does.
"""

import asyncio
import glob
import hashlib
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_mock_server = MagicMock()
_mock_server.PromptServer.instance.routes = MagicMock()
sys.modules.setdefault("server", _mock_server)

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
    app_options = {}

    async def get_application(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "prompts.db")

        app = web.Application(**self.app_options)
        routes = web.RouteTableDef()
        self.api = PromptManagerAPI(db=PromptDatabase(self.db_path))
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


class TestRestoreHoldsTheRegistry(BackupRestoreTestCase):
    """A legacy restore and a workspace job refuse each other (#269 gap)."""

    def _digest(self):
        return {
            suffix: hashlib.sha256(Path(self.db_path + suffix).read_bytes()).hexdigest()
            for suffix in ("", "-wal")
            if os.path.exists(self.db_path + suffix)
        }

    async def test_legacy_restore_refused_while_scan_job_runs(self):
        self._save("keep me")
        upload = self._make_db_file(["restored"])
        gate = asyncio.Event()

        async def runner(job):
            await gate.wait()

        scan = self.api._jobs.start(
            "scan", "Scan for metadata", record_changing=True, scope={}, runner=runner
        )
        before = self._digest()
        try:
            resp = await self._restore(upload)
            self.assertEqual(resp.status, 409)
            self.assertEqual(
                await resp.json(),
                {"success": False, "error": "Scan for metadata already running"},
            )
            self.assertEqual(self._digest(), before)
            self.assertEqual(
                [p["text"] for p in self.api.db.search_prompts()], ["keep me"]
            )
            self.assertEqual(glob.glob(self.db_path + ".backup_*"), [])
        finally:
            gate.set()
            await asyncio.wait_for(scan.task, 5)

    async def test_scan_refused_while_legacy_restore_holds(self):
        upload = self._make_db_file(["restored"])
        entered = threading.Event()
        release = threading.Event()
        model = self.api.db.model
        real_verify = model.verify_database_file

        def gated(path, **kwargs):
            entered.set()
            release.wait(10)
            return real_verify(path, **kwargs)

        loop = asyncio.get_running_loop()
        with patch.object(model, "verify_database_file", gated):
            restore = asyncio.ensure_future(self._restore(upload))
            self.assertTrue(await loop.run_in_executor(None, entered.wait, 10))
            resp = await self.client.post("/prompt_manager/jobs/scan")
            body = await resp.json()
            listed = (await (await self.client.get("/prompt_manager/jobs")).json())[
                "jobs"
            ]
            release.set()
            restored = await restore

        self.assertEqual((resp.status, body["code"]), (409, "busy"))
        self.assertEqual(body["running"]["title"], "Database restore (admin page)")
        self.assertEqual(
            [(j["kind"], j["state"], j["cancellable"]) for j in listed],
            [("restore", "running", False)],
        )
        self.assertEqual(restored.status, 200)
        self.assertEqual(self.api._jobs.list()[0].state, "finished")
        self.assertIsNone(self.api._jobs.active("restore"))

    async def test_rejected_upload_ends_its_hold_failed(self):
        resp = await self._restore(self._write("notes.txt", b"not a database"))
        self.assertEqual(resp.status, 400)
        job = self.api._jobs.list()[0]
        self.assertEqual((job.kind, job.state), ("restore", "failed"))


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

    async def test_restore_cap_keeps_the_legacy_shape(self):
        self.api.restore_max_bytes = 1024
        upload = self._write("big.db", b"SQLite format 3\x00" + b"\x00" * 8192)
        resp = await self._restore(upload)
        data = await resp.json()
        self.assertEqual(resp.status, 400)
        self.assertEqual(data["code"], "too-large")
        self.assertNotIn(self.tmpdir, data["error"])

    async def test_non_multipart_request_is_rejected(self):
        resp = await self.client.post("/prompt_manager/restore", data=b"raw bytes")
        self.assertIn(resp.status, (400, 500))
        data = await resp.json()
        self.assertFalse(data["success"])


if __name__ == "__main__":
    unittest.main()


MIB = 2**20


class TestLargeRestores(BackupRestoreTestCase):
    """A real library's backup restores: room on disk decides, not 100 MB."""

    # As ComfyUI builds its app: client_max_size is --max-upload-size (100
    # MB by default). The restore streams with read_chunk, which it does not
    # cap.
    app_options = {"client_max_size": 100 * 2**20}

    def _large_db_file(self, texts, pad_bytes):
        path = self._make_db_file(texts, name="large.db")
        conn = sqlite3.connect(path)
        try:
            conn.execute("CREATE TABLE padding(b BLOB)")
            conn.execute("INSERT INTO padding VALUES (zeroblob(?))", (pad_bytes,))
            conn.commit()
        finally:
            conn.close()
        return path

    async def _stream_restore(self, path, chunked=False):
        with open(path, "rb") as fh:
            data = fh.read()
        form = aiohttp.FormData()
        form.add_field(
            "database_file",
            data,
            filename=os.path.basename(path),
            content_type="application/octet-stream",
        )
        # chunked=False must not be passed: aiohttp 3.14 then frames the body
        # as chunked while also sending a Content-Length.
        options = {"chunked": True} if chunked else {}
        return await self.client.post("/prompt_manager/restore", data=form, **options)

    def _spy_uploads(self):
        """Patch the upload's mkstemp; the list collects each temp path made."""
        created = []
        real_mkstemp = tempfile.mkstemp

        def spy(*args, **kwargs):
            result = real_mkstemp(*args, **kwargs)
            created.append(result[1])
            return result

        return patch("py.api.admin.tempfile.mkstemp", spy), created

    def _assert_removed(self, created):
        for path in created:
            for suffix in ("", "-wal", "-shm", "-journal"):
                self.assertFalse(os.path.exists(path + suffix), path + suffix)

    async def test_a_150_mb_backup_restores(self):
        self._save("before")
        upload = self._large_db_file(["restored"], 150 * MIB)
        self.assertGreater(os.path.getsize(upload), 150 * MIB)
        spy, created = self._spy_uploads()
        with spy:
            resp = await self._stream_restore(upload)
        data = await resp.json()
        self.assertEqual(resp.status, 200, data)
        self.assertEqual(
            [p["text"] for p in self.api.db.search_prompts()], ["restored"]
        )
        self.assertEqual(len(created), 1)
        self._assert_removed(created)

    async def test_a_short_disk_is_refused_up_front(self):
        from py.api import maintenance

        self._save("keep me")
        upload = self._make_db_file(["restored"])
        spy, created = self._spy_uploads()
        with spy, patch.object(maintenance, "_free_bytes", return_value=MIB):
            resp = await self._restore(upload)
        data = await resp.json()
        self.assertEqual(resp.status, 507)
        self.assertEqual(data["code"], "insufficient-space")
        self.assertTrue(
            data["error"].startswith("Not enough free disk space to restore a "),
            data["error"],
        )
        self.assertTrue(data["error"].endswith("Nothing was changed."))
        self.assertNotIn(self.tmpdir, data["error"])
        self.assertNotIn(tempfile.gettempdir(), data["error"])
        self.assertEqual([p["text"] for p in self.api.db.search_prompts()], ["keep me"])
        self.assertEqual(created, [])  # refused before anything was written

    async def test_a_short_disk_is_refused_while_streaming_without_a_length(self):
        from py.api import maintenance

        self._save("keep me")
        upload = self._large_db_file(["restored"], 4 * MIB)
        spy, created = self._spy_uploads()
        # Room for 1 MiB only, and no Content-Length to refuse up front.
        with spy, patch.object(maintenance, "restore_room", return_value=MIB):
            resp = await self._stream_restore(upload, chunked=True)
        data = await resp.json()
        self.assertEqual(resp.status, 507)
        self.assertEqual(data["code"], "insufficient-space")
        self.assertTrue(data["error"].endswith("Nothing was changed."))
        self.assertEqual([p["text"] for p in self.api.db.search_prompts()], ["keep me"])
        self.assertEqual(len(created), 1)
        self._assert_removed(created)


class TestRestoreRoom(unittest.TestCase):
    def test_room_counts_the_upload_its_staged_copy_and_the_swap(self):
        from py.api import maintenance

        tmp = tempfile.mkdtemp()
        try:
            db_path = os.path.join(tmp, "prompts.db")
            free = 10 * 2**30
            with (
                patch.object(maintenance, "_free_bytes", return_value=free),
                patch.object(maintenance, "_device", return_value="one disk"),
            ):
                room = maintenance.restore_room(db_path)
            # Upload + staged copy + WAL growth on one disk, no database yet.
            self.assertEqual(room, (free - maintenance.DISK_MARGIN_BYTES) // 3)
            self.assertEqual(maintenance.size_label(int(1.6 * 2**30)), "1.6 GB")
            self.assertEqual(maintenance.size_label(120 * 2**20), "120.0 MB")
        finally:
            os.rmdir(tmp)
