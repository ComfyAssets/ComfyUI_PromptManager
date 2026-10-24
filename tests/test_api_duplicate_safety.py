"""Duplicate-file removal safety (#249 System S6 final review: C1, I1, I2, I5, M9).

- C1: one physical file reached through two listed sources, a symlinked
  source or a hardlink is never offered or removed as a copy of itself.
- I1: each file's records move to its verified kept copy before that file
  is unlinked, so no database row ever names a deleted file.
- I2: links stored under another spelling of the source move too.
- I5: image tags move to the kept copy (their union) and the removed copy's
  pending Needs review items are settled.
- M9: the preview binds files by size, mtime and inode without hashing.
"""

import asyncio
import hashlib
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

try:
    from tests import test_api_duplicate_files as dup_tests
except ImportError:  # discovered with tests/ as the top-level directory
    import test_api_duplicate_files as dup_tests

from py.api import duplicate_files  # noqa: E402
from py.config import GalleryConfig  # noqa: E402

WAIT = 10


def _locator(path):
    return os.path.normcase(os.path.realpath(str(path)))


class SafetyTestCase(dup_tests.DuplicateFilesTestCase):
    def need_symlinks(self):
        if not dup_tests.TestReview._can_symlink(self.tmpdir):  # pragma: no cover
            self.skipTest("symlinks are not available")

    def linked_files(self):
        """Every image record's path that names a file that does not exist."""
        with self.api.db.model.get_connection() as conn:
            rows = conn.execute("SELECT image_path FROM generated_images").fetchall()
        return [row[0] for row in rows if not os.path.exists(row[0])]

    async def apply_plan(self, plan):
        body = await self.apply(plan["token"])
        cleanup = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(cleanup.task), WAIT)
        return cleanup


class TestOnePhysicalFile(SafetyTestCase):
    """C1: the kept copy survives every alias of itself."""

    async def test_a_folder_listed_twice_never_loses_its_only_copy(self):
        self.need_symlinks()
        only = self.write("a.png", b"the only copy")
        alias = Path(self.tmpdir) / "Output-alias"
        os.symlink(str(self.output_dir), str(alias))
        with patch.object(
            self.api, "_get_all_output_dirs", return_value=[self.output_dir, alias]
        ):
            job = await self.find()
            page = await self.groups(job.id)
        self.assertEqual(page["total"], 0)
        self.assertEqual(only.read_bytes(), b"the only copy")

    async def test_a_symlinked_source_inside_another_is_one_file(self):
        self.need_symlinks()
        only = self.write("sub/c.png", b"one file")
        linked = self.output_dir / "linked"  # walked by neither as a link
        os.symlink(str(self.output_dir / "sub"), str(linked))
        GalleryConfig.MONITORING_DIRECTORIES = [
            self._root(self.output_dir),
            str(linked),
        ]
        self.assertEqual(len(self.api._get_all_output_dirs()), 2)
        job = await self.find()
        self.assertEqual((await self.groups(job.id))["total"], 0)
        self.assertTrue(only.is_file())

    async def test_a_hardlink_is_never_a_copy_to_remove(self):
        first = self.write("a.png", b"same bytes", mtime=1)
        os.link(str(first), str(self.output_dir / "b.png"))
        self.write("c.png", b"same bytes", mtime=3)
        job, group = await self.found_group()
        self.assertEqual(len(group["files"]), 2)  # a.png and b.png are one file
        # c.png becomes a hardlink of the keeper after the find: protected.
        (self.output_dir / "c.png").unlink()
        os.link(str(first), str(self.output_dir / "c.png"))
        kept = group["files"][0]["path"]
        plan = await self.review(job.id, [self.keep_of(group, kept)])
        self.assertEqual(plan["scope"]["count"], 0)
        self.assertEqual(
            plan["protected"]["reasons"], ["1 file is the same file as its kept copy"]
        )

    async def test_the_job_refuses_a_file_that_became_its_kept_copy(self):
        first = self.write("a.png", b"same bytes", mtime=1)
        self.write("b.png", b"same bytes", mtime=2)
        job, group = await self.found_group()
        plan = await self.review(job.id, [self.keep_of(group, "a.png")])
        (self.output_dir / "b.png").unlink()
        os.link(str(first), str(self.output_dir / "b.png"))
        cleanup = await self.apply_plan(plan)
        self.assertEqual(cleanup.counters["removed"], 0)
        rows = {row["path"]: row["detail"] for row in cleanup.outcomes["kept"]}
        self.assertEqual(rows["b.png"], duplicate_files.SAME_FILE)
        self.assertTrue((self.output_dir / "b.png").is_file())


class TestRecordsMoveBeforeTheUnlink(SafetyTestCase):
    """I1: a deleted file is never named by a database row."""

    async def test_a_failure_between_two_files_leaves_no_row_on_a_deleted_file(self):
        self.write("a.png", b"same", mtime=1)
        self.write("b.png", b"same", mtime=2)
        self.write("c.png", b"same", mtime=3)
        prompt = self.save("Linked to every copy")
        for name in ("b.png", "c.png"):
            self.api.db.link_image_to_prompt(prompt, str(self.output_dir / name))
        job, group = await self.found_group()
        plan = await self.review(job.id, [self.keep_of(group, "a.png")])
        real = self.api.db.move_copy_records
        calls = []

        def move(*args, **kwargs):
            calls.append(args)
            if len(calls) == 2:
                raise sqlite3.OperationalError("database is locked")
            return real(*args, **kwargs)

        with patch.object(self.api.db, "move_copy_records", side_effect=move):
            with self.assertLogs("prompt_manager", "ERROR"):
                cleanup = await self.apply_plan(plan)
        self.assertEqual(self.linked_files(), [])
        self.assertEqual(cleanup.counters["removed"], 1)
        self.assertEqual(cleanup.state, "failed")
        [error] = cleanup.outcomes["errors"]
        self.assertEqual(error["detail"], duplicate_files.RECORDS_FAILED)
        self.assertNotIn("Missing-file", cleanup.status_line)
        self.assertEqual(
            cleanup.status_line.split(" · ")[:2],
            ["Stopped after an error", "Removed 1 duplicate file, 1 not removed"],
        )

    async def test_a_failed_unlink_leaves_its_links_on_the_kept_copy(self):
        self.trio()
        prompt = self.save("On c")
        self.api.db.link_image_to_prompt(prompt, str(self.output_dir / "sub" / "c.png"))
        job, group = await self.found_group()
        plan = await self.review(job.id, [self.keep_of(group, "a.png")])
        real = duplicate_files._unlink

        def unlink(path):
            if os.path.basename(path) == "c.png":
                raise PermissionError(13, "denied")
            real(path)

        with patch.object(duplicate_files, "_unlink", side_effect=unlink):
            with self.assertLogs("prompt_manager", "WARNING"):
                cleanup = await self.apply_plan(plan)
        [image] = self.api.db.get_prompt_images(prompt)
        self.assertEqual(os.path.basename(image["image_path"]), "a.png")
        self.assertTrue((self.output_dir / "sub" / "c.png").is_file())
        self.assertEqual(self.linked_files(), [])
        self.assertEqual(
            cleanup.outcomes["errors"][0]["detail"], duplicate_files.LOCKED_FILE
        )


class TestEverySpelling(SafetyTestCase):
    """I2: links under the monitor's spelling of a source move too."""

    async def test_links_under_a_symlinked_source_spelling_move(self):
        self.need_symlinks()
        self.trio()
        spelled = Path(self.comfy_dir) / "out-link"
        os.symlink(str(self.output_dir), str(spelled))
        GalleryConfig.MONITORING_DIRECTORIES = [str(spelled)]
        prompt = self.save("Linked through the symlink")
        self.api.db.link_image_to_prompt(prompt, str(spelled / "b.png"))
        job, group = await self.found_group()
        plan = await self.review(job.id, [self.keep_of(group, "a.png")])
        self.assertIn("1 database link moves", plan["details"][0]["detail"])
        await self.apply_plan(plan)
        [image] = self.api.db.get_prompt_images(prompt)
        self.assertEqual(
            _locator(image["image_path"]), _locator(self.output_dir / "a.png")
        )
        self.assertEqual(self.linked_files(), [])


class TestTagsAndReview(SafetyTestCase):
    """I5: tags join the kept copy; pending review items of the copy settle."""

    def tag(self, path, tags):
        data = Path(path).read_bytes()
        info = os.stat(path)
        return self.api.db.replace_image_tags(
            locator=_locator(path),
            sha256=hashlib.sha256(data).hexdigest(),
            size=info.st_size,
            mtime_ns=info.st_mtime_ns,
            expected_epoch=self.api.db.get_execution_epoch(),
            expected_tag_revision=0,
            tags=tags,
        )

    def tags_of(self, path):
        data = Path(path).read_bytes()
        return self.api.db.read_image_tags(
            _locator(path), hashlib.sha256(data).hexdigest()
        )["tags"]

    def queue(self, path):
        data = Path(path).read_bytes()
        info = os.stat(path)
        return self.api.db.record_review_item(
            locator=_locator(path),
            spelled_path=os.path.realpath(str(path)),
            sha256=hashlib.sha256(data).hexdigest(),
            size=info.st_size,
            mtime_ns=info.st_mtime_ns,
            epoch=self.api.db.get_execution_epoch(),
            resolver_version=1,
            evidence={"candidates": []},
            candidate_count=0,
            limited=True,
        )

    def pending(self):
        return self.api.db.review_queue_page(limit=50)["items"]

    async def test_tags_move_as_a_union_and_pending_items_settle(self):
        self.write("a.png", b"same", mtime=1)
        self.write("b.png", b"same", mtime=2)
        keeper, removed = self.output_dir / "a.png", self.output_dir / "b.png"
        self.tag(keeper, ["kept"])
        self.tag(removed, ["moved", "kept"])
        self.assertTrue(self.queue(removed))
        job, group = await self.found_group()
        plan = await self.review(job.id, [self.keep_of(group, "a.png")])
        self.assertIn("image tags join the kept copy", plan["details"][0]["detail"])
        self.assertIn("1 Needs review item is settled", plan["details"][0]["detail"])
        await self.apply_plan(plan)
        self.assertEqual(self.tags_of(keeper), ["kept", "moved"])
        self.assertEqual(self.pending(), [])


class TestPreviewDoesNotHash(SafetyTestCase):
    """M9: the preview binds by size, mtime and inode; the job hashes."""

    async def test_preview_reads_no_file_and_spots_a_touched_one(self):
        self.trio()
        job, group = await self.found_group()
        calls = []
        real = self.api._calculate_file_hash

        def spy(path):
            calls.append(path)
            return real(path)

        self.api._calculate_file_hash = spy
        os.utime(self.output_dir / "b.png", (5_000_000, 5_000_000))
        plan = await self.review(job.id, [self.keep_of(group, "a.png")])
        self.assertEqual(calls, [])
        self.assertEqual(plan["scope"]["count"], 1)
        self.assertEqual(
            plan["protected"]["reasons"], ["1 file changed since the search"]
        )
        cleanup = await self.apply_plan(plan)
        self.assertEqual(cleanup.counters["removed"], 1)
        self.assertGreaterEqual(len(calls), 2)  # the job hashes keeper and file
