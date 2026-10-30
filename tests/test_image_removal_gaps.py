"""Removal-mark gaps after #480, and trash folders inside output roots (#501).

- Thumbnail jobs and the legacy auto-tag listing skip a library-removed file.
- Missing-file links also forgets the marks of files that are gone (only under
  a source that can be read now).
- Every walker skips a trash folder that sits inside an output root.
"""

import asyncio
import os
import shutil
import sqlite3
from pathlib import Path
from unittest.mock import patch

try:
    from tests.disk_waits import DISK_BOUND_SECONDS
    from tests.test_image_delete_routes import ImageDeleteTestCase
except ImportError:  # discovered with tests/ as the top-level directory
    from disk_waits import DISK_BOUND_SECONDS
    from test_image_delete_routes import ImageDeleteTestCase

from py.api import images as images_module
from py.api.admin import _collect_media_files
from py.api.autotag_routes import AutotagRoutesMixin
from utils.image_monitor import ImageGenerationHandler

MAINTENANCE_PREVIEW = "/prompt_manager/maintenance/preview"
MAINTENANCE_APPLY = "/prompt_manager/maintenance/apply"


class _GapCase(ImageDeleteTestCase):
    def marks(self):
        conn = sqlite3.connect(self.api.db.model.db_path)
        try:
            return [
                row[0] for row in conn.execute("SELECT locator FROM image_removals")
            ]
        finally:
            conn.close()

    def revision(self):
        conn = sqlite3.connect(self.api.db.model.db_path)
        try:
            return conn.execute(
                "SELECT revision FROM image_removals_meta WHERE singleton_id=1"
            ).fetchone()[0]
        finally:
            conn.close()


class TestThumbnailJobsSkipHiddenFiles(_GapCase):
    async def test_missing_and_rebuild_plans_leave_a_removed_file_out(self):
        self.write("hidden.png", b"hidden bytes", mtime=1_000_000)
        self.write("shown.png", b"shown bytes", mtime=2_000_000)
        await self.delete(["hidden.png"], "library")
        thumbs = self.output_dir / "thumbnails"
        for mode in ("missing", "rebuild"):
            with self.subTest(mode=mode):
                plan = self.api._plan_thumbnails(self.output_dir, thumbs, mode=mode)
                sources = [src.name for src, _dst, _video in plan["targets"]]
                self.assertEqual(sources, ["shown.png"])
                self.assertEqual(plan["missing"], 1)

    async def test_a_changed_file_gets_its_preview_again(self):
        self.write("hidden.png", b"hidden bytes", mtime=1_000_000)
        await self.delete(["hidden.png"], "library")
        self.write("hidden.png", b"new and longer bytes", mtime=3_000_000)
        plan = self.api._plan_thumbnails(
            self.output_dir, self.output_dir / "thumbnails"
        )
        self.assertEqual([t[0].name for t in plan["targets"]], ["hidden.png"])


class TestLegacyAutotagListing(_GapCase):
    async def test_scan_output_dir_leaves_a_removed_file_out(self):
        self.write("hidden.png", b"hidden bytes", mtime=1_000_000)
        self.write("shown.png", b"shown bytes", mtime=2_000_000)
        await self.delete(["hidden.png"], "library")
        with patch.object(
            self.api, "_find_comfyui_output_dir", return_value=str(self.output_dir)
        ):
            response = await self.client.get("/prompt_manager/scan_output_dir")
            body = await response.json()
        self.assertEqual([image["filename"] for image in body["images"]], ["shown.png"])


class TestMissingFileLinksPrunesMarks(_GapCase):
    async def links_review(self, status=200):
        return await self.post(
            MAINTENANCE_PREVIEW, {"operation": "missing_links", "options": {}}, status
        )

    async def apply(self, token, status=202):
        body = await self.post(MAINTENANCE_APPLY, {"token": token}, status)
        if status != 202:
            return body
        job = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(job.task), DISK_BOUND_SECONDS)
        return job

    async def removed(self, name):
        self.write("other.png", b"another output")  # the source lists something
        self.write(name, f"bytes of {name}".encode(), mtime=1_000_000)
        await self.delete([name], "library")
        self.assertEqual(len(self.marks()), 1)

    async def test_the_mark_of_a_gone_file_is_counted_and_forgotten(self):
        await self.removed("gone.png")
        # Not an unlink by PromptManager: the user deleted it elsewhere.
        os.remove(self.output_dir / "gone.png")
        plan = await self.links_review()
        self.assertEqual(plan["scope"]["count"], 1)
        self.assertEqual(
            plan["scope"]["label"], "1 removed image whose file is gone or changed"
        )
        before = self.revision()
        job = await self.apply(plan["token"])
        self.assertEqual(job.state, "finished", job.summary())
        self.assertEqual(self.marks(), [])
        self.assertGreater(self.revision(), before)

    async def test_the_mark_of_a_present_file_stays(self):
        await self.removed("kept.png")
        plan = await self.links_review()
        self.assertEqual(plan["scope"]["count"], 0)
        self.assertEqual(plan["scope"]["label"], "No links to missing files")
        self.assertEqual(len(self.marks()), 1)

    async def test_a_mark_under_an_unreadable_source_stays(self):
        await self.removed("away.png")
        os.remove(self.output_dir / "away.png")
        with patch(
            "database.maintenance.readable_roots",
            side_effect=lambda r: [False] * len(r),
        ):
            plan = await self.links_review()
            self.assertEqual(plan["scope"]["count"], 0)
        self.assertEqual(len(self.marks()), 1)

    async def test_the_mark_of_a_changed_file_is_forgotten(self):
        await self.removed("changed.png")
        self.write("changed.png", b"other and longer bytes", mtime=4_000_000)
        plan = await self.links_review()
        self.assertEqual(plan["scope"]["count"], 1)
        job = await self.apply(plan["token"])
        self.assertEqual(job.state, "finished", job.summary())
        self.assertEqual(self.marks(), [])

    async def test_marks_under_an_empty_source_stay(self):
        # An unplugged drive leaves its mount point: an empty, readable folder.
        await self.removed("mounted.png")
        for child in list(self.output_dir.iterdir()):
            if child.is_dir():
                shutil.rmtree(child)
            else:
                os.remove(child)
        plan = await self.links_review()
        self.assertEqual(plan["scope"]["count"], 0)
        self.assertEqual(len(self.marks()), 1)

    async def test_a_name_the_folder_still_lists_is_not_gone(self):
        # A case-sensitive NTFS folder: lstat of the normcased locator fails.
        await self.removed("Listed.png")
        real_lstat = os.lstat
        locator = self.marks()[0]

        def lstat(path, *args, **kwargs):
            if os.fspath(path) == locator:
                raise FileNotFoundError(path)
            return real_lstat(path, *args, **kwargs)

        with (
            patch("database.maintenance.os.lstat", side_effect=lstat),
            patch("database.maintenance.os.path.normcase", side_effect=str.lower),
        ):
            from database.maintenance import _stale_marks

            stale = _stale_marks([(locator, 1, 1)], [str(self.output_dir)])
        self.assertEqual(stale, [])

    async def test_a_file_back_before_the_apply_changes_the_plan(self):
        await self.removed("back.png")
        path = self.output_dir / "back.png"
        saved = Path(self.tmpdir) / "saved.png"
        shutil.copy2(path, saved)
        os.remove(path)
        plan = await self.links_review()
        shutil.copy2(saved, path)
        job = await self.apply(plan["token"])
        self.assertEqual(job.state, "failed", job.summary())
        self.assertEqual(len(self.marks()), 1)


class TestWalkersSkipTrashFolders(_GapCase):
    def seed(self):
        self.write("kept.png", b"kept")
        self.write(
            "x.png", b"trashed x", folder=self.output_dir / ".Trash-1000" / "files"
        )
        self.write(
            "y.png", b"trashed y", folder=self.output_dir / "sub" / "$RECYCLE.BIN"
        )
        self.write("z.png", b"trashed z", folder=self.output_dir / ".Trashes")

    def test_walk_media_and_the_settled_entry(self):
        self.seed()
        walked = sorted(
            Path(entry.path).name
            for entry in images_module._walk_media(self.output_dir, 100)
        )
        self.assertEqual(walked, ["kept.png"])
        for relative in (
            os.path.join(".Trash-1000", "files", "x.png"),
            os.path.join("sub", "$RECYCLE.BIN", "y.png"),
        ):
            with self.subTest(relative=relative):
                self.assertIsNone(
                    images_module._settled_entry(self.output_dir, relative)
                )

    def test_scan_duplicate_and_autotag_walkers(self):
        self.seed()
        collected = [
            Path(p).name for p in _collect_media_files([self.output_dir], [".png"])
        ]
        self.assertEqual(collected, ["kept.png"])
        found, _truncated = AutotagRoutesMixin._collect_scan_files(self.output_dir)
        self.assertEqual([p.name for p in found], ["kept.png"])

    def test_the_image_monitor(self):
        monitor = ImageGenerationHandler.__new__(ImageGenerationHandler)
        monitor.supported_extensions = (".png",)
        base = str(self.output_dir)
        self.assertTrue(monitor.is_image_file(os.path.join(base, "a.png")))
        for folder in (".Trash-1000", "$Recycle.Bin", ".Trashes", ".trash"):
            with self.subTest(folder=folder):
                self.assertFalse(
                    monitor.is_image_file(os.path.join(base, folder, "files", "a.png"))
                )

    def test_the_image_monitor_watches_a_root_named_like_a_trash_folder(self):
        monitor = ImageGenerationHandler.__new__(ImageGenerationHandler)
        monitor.supported_extensions = (".png",)
        root = os.path.join(str(self.output_dir), ".Trash-1000")
        monitor.watched_roots = (root,)
        self.assertTrue(monitor.is_image_file(os.path.join(root, "sub", "a.png")))
        self.assertFalse(
            monitor.is_image_file(os.path.join(root, "sub", ".Trashes", "a.png"))
        )

    async def test_the_workspace_listing(self):
        self.seed()
        self.assertEqual(await self.listed(), ["kept.png"])

    def test_a_root_named_like_a_trash_folder_still_lists(self):
        root = Path(self.tmpdir) / ".Trash-1000"
        (root / "inner").mkdir(parents=True)
        (root / "inner" / "a.png").write_bytes(b"a")
        walked = [Path(e.path).name for e in images_module._walk_media(root, 10)]
        self.assertEqual(walked, ["a.png"])
