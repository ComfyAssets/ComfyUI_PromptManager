"""Duplicate cleanup keeps images' own categories (#332 MC11).

The kept copy's own category wins; a kept copy without one receives the
removed copy's. The removed copy's row is never deleted, so a give-back
(the kept copy changed) and a failed delete lose nothing.
"""

import asyncio
import os
from unittest.mock import patch

try:
    from tests.test_api_duplicate_files import WAIT, DuplicateFilesTestCase
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_duplicate_files import WAIT, DuplicateFilesTestCase

from py.api import duplicate_files  # noqa: E402
from py.api.images import image_category_key  # noqa: E402
from py.config import GalleryConfig  # noqa: E402


class TestDuplicateCleanupKeepsCategories(DuplicateFilesTestCase):
    def key(self, path):
        return image_category_key(self.api._get_all_output_dirs(), path)

    def category(self, path):
        return self.api.db.image_category(*self.key(path))

    def set_category(self, path, value):
        self.api.db.set_image_categories([self.key(path)], value)

    async def run_cleanup(self, keep="a.png"):
        job, group = await self.found_group()
        plan = await self.review(job.id, [self.keep_of(group, keep)])
        body = await self.apply(plan["token"])
        cleanup = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(cleanup.task), WAIT)
        return cleanup

    async def test_kept_copy_without_a_category_receives_the_removed_one(self):
        self.trio()
        b = self.output_dir / "b.png"
        self.set_category(b, "Portraits")
        cleanup = await self.run_cleanup()
        self.assertFalse(b.exists())
        self.assertEqual(self.category(self.output_dir / "a.png"), "Portraits")
        # The removed copy's row stays (hidden while its file is gone).
        self.assertEqual(self.category(b), "Portraits")
        self.assertIn(
            "Kept 1 image category on the kept copies", "\n".join(cleanup.log)
        )

    async def test_kept_copy_keeps_its_own_category(self):
        self.trio()
        self.set_category(self.output_dir / "a.png", "Landscapes")
        self.set_category(self.output_dir / "b.png", "Portraits")
        self.set_category(self.output_dir / "sub" / "c.png", "Scans")
        cleanup = await self.run_cleanup()
        self.assertEqual(self.category(self.output_dir / "a.png"), "Landscapes")
        self.assertNotIn("image categor", "\n".join(cleanup.log))

    async def test_two_removed_copies_give_the_first_category(self):
        self.trio()
        self.set_category(self.output_dir / "b.png", "Portraits")
        self.set_category(self.output_dir / "sub" / "c.png", "Scans")
        cleanup = await self.run_cleanup()
        self.assertIn(self.category(self.output_dir / "a.png"), {"Portraits", "Scans"})
        self.assertIn(
            "Kept 1 image category on the kept copies", "\n".join(cleanup.log)
        )

    async def test_give_back_loses_no_category(self):
        self.trio()
        self.set_category(self.output_dir / "b.png", "Portraits")
        with patch.object(duplicate_files, "still_kept", return_value=False):
            cleanup = await self.run_cleanup()
        self.assertEqual(cleanup.counters["removed"], 0)
        rows = {row["path"]: row["detail"] for row in cleanup.outcomes["kept"]}
        self.assertEqual(
            rows["b.png"], duplicate_files.ROW_DETAILS[duplicate_files.KEEPER_GONE]
        )
        self.assertTrue((self.output_dir / "b.png").is_file())
        self.assertEqual(self.category(self.output_dir / "b.png"), "Portraits")

    async def test_a_failed_delete_keeps_both_categories(self):
        self.trio()
        self.set_category(self.output_dir / "b.png", "Portraits")
        real = duplicate_files._unlink

        def unlink(path):
            if os.path.basename(path) == "b.png":
                raise PermissionError(13, "denied")
            real(path)

        with patch.object(duplicate_files, "_unlink", side_effect=unlink):
            await self.run_cleanup()
        self.assertTrue((self.output_dir / "b.png").is_file())
        self.assertEqual(self.category(self.output_dir / "b.png"), "Portraits")
        self.assertEqual(self.category(self.output_dir / "a.png"), "Portraits")

    async def test_nested_sources_use_the_deepest_source_keys(self):
        self.write("sub/a.png", b"same", mtime=1)
        self.write("sub/b.png", b"same", mtime=2)
        # The subfolder is listed first, so the cleanup's listed root is the
        # file key's source; the outer source still holds the files.
        GalleryConfig.MONITORING_DIRECTORIES = [
            self._root(self.output_dir / "sub"),
            self._root(self.output_dir),
        ]
        sub_key = duplicate_files.favorite_root_key(self.output_dir / "sub")
        self.api.db.set_image_categories([(sub_key, "b.png")], "Portraits")
        await self.run_cleanup()
        self.assertFalse((self.output_dir / "sub" / "b.png").exists())
        self.assertEqual(self.api.db.image_category(sub_key, "a.png"), "Portraits")
        outer_key = duplicate_files.favorite_root_key(self.output_dir)
        self.assertIsNone(self.api.db.image_category(outer_key, "sub/a.png"))

    async def test_outer_source_listed_first_still_keys_by_the_deepest(self):
        self.write("sub/a.png", b"same", mtime=1)
        self.write("sub/b.png", b"same", mtime=2)
        GalleryConfig.MONITORING_DIRECTORIES = [
            self._root(self.output_dir),
            self._root(self.output_dir / "sub"),
        ]
        sub_key = duplicate_files.favorite_root_key(self.output_dir / "sub")
        self.api.db.set_image_categories([(sub_key, "b.png")], "Portraits")
        job, group = await self.found_group()
        [kept] = [f for f in group["files"] if f["path"].endswith("a.png")][:1]
        plan = await self.review(
            job.id, [self.keep_of(group, kept["path"], kept["root_index"])]
        )
        body = await self.apply(plan["token"])
        cleanup = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(cleanup.task), WAIT)
        self.assertFalse((self.output_dir / "sub" / "b.png").exists())
        self.assertEqual(self.api.db.image_category(sub_key, "a.png"), "Portraits")
