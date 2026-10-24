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
import types
from pathlib import Path
from unittest.mock import patch

try:
    from tests.open_handles import open_handles_under
    from tests import test_api_duplicate_files as dup_tests
except ImportError:  # discovered with tests/ as the top-level directory
    import test_api_duplicate_files as dup_tests
    from open_handles import open_handles_under

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


def _fake_stat(real, **changes):
    """A stat result with some fields changed (st_dev, st_ino, st_mtime_ns...)."""
    fields = {name: getattr(real, name) for name in dir(real) if name.startswith("st_")}
    fields.update(changes)
    return types.SimpleNamespace(**fields)


class TestAnotherMount(SafetyTestCase):
    """Round 2, R1: one file reached through two mounts is never removed.

    The second source stands for the same folder seen through another
    filesystem: each test reports its file on another ``st_dev``.
    """

    MAYBE = "1 file may be the same file reached through another mount or share"

    def setUp(self):
        super().setUp()
        self.real_lstat, self.real_stat = os.lstat, os.stat

    def mount(self, rel, keeper_rel, *, ino="same", mtime_shift=0):
        """Copies at ``output/keeper_rel`` and ``renders/rel``; the patch."""
        second = self.second_source()
        GalleryConfig.MONITORING_DIRECTORIES = [
            self._root(self.output_dir),
            self._root(second),
        ]
        stamp = 1_700_000_000_123_456_789
        keeper = self.write(keeper_rel, b"one file")
        other = self.write(rel, b"one file", folder=second)
        os.utime(keeper, ns=(stamp, stamp))
        os.utime(other, ns=(stamp, stamp))
        target = os.path.normcase(str(other))
        keeper_ino = self.real_stat(keeper).st_ino

        def changed(real):
            return _fake_stat(
                real,
                st_dev=real.st_dev + 7,
                st_ino=keeper_ino if ino == "same" else real.st_ino + 1000,
                st_mtime_ns=real.st_mtime_ns + mtime_shift,
            )

        def lstat(path, *args, **kwargs):
            real = self.real_lstat(path, *args, **kwargs)
            return changed(real) if os.path.normcase(str(path)) == target else real

        def stat(path, *args, **kwargs):
            real = self.real_stat(path, *args, **kwargs)
            return changed(real) if os.path.normcase(str(path)) == target else real

        patches = (
            patch("os.lstat", side_effect=lstat),
            patch("os.stat", side_effect=stat),
        )
        return keeper, other, patches

    async def review_with(self, patches, keeper_rel):
        # The mount reports its device and file id to the find too.
        with patches[0], patches[1]:
            job, group = await self.found_group()
            plan = await self.review(job.id, [self.keep_of(group, keeper_rel, 0)])
        return job, group, plan

    async def test_nfs_and_smb_spellings_of_one_share_are_protected(self):
        # NFS and SMB report their own device and file id; SMB rounds mtime.
        keeper, other, patches = self.mount(
            "x.png", "x.png", ino="other", mtime_shift=-89
        )
        _job, _group, plan = await self.review_with(patches, "x.png")
        self.assertEqual(plan["scope"]["count"], 0)
        self.assertEqual(plan["protected"]["reasons"], [self.MAYBE])
        self.assertTrue(other.is_file() and keeper.is_file())

    async def test_a_loopback_mount_with_the_same_inode_is_protected(self):
        keeper, other, patches = self.mount("y.png", "sub/y.png", ino="same")
        _job, _group, plan = await self.review_with(patches, "sub/y.png")
        self.assertEqual(plan["protected"]["reasons"], [self.MAYBE])

    async def test_a_fuse_pool_path_is_protected(self):
        # mergerfs, rclone or sshfs synthesize both the device and the inode.
        keeper, other, patches = self.mount("x.png", "x.png", ino="other")
        _job, _group, plan = await self.review_with(patches, "x.png")
        self.assertEqual(plan["protected"]["reasons"], [self.MAYBE])

    async def test_a_true_copy_on_another_disk_is_still_offered(self):
        keeper, other, patches = self.mount("elsewhere.png", "x.png", ino="other")
        _job, _group, plan = await self.review_with(patches, "x.png")
        self.assertEqual(plan["scope"]["count"], 1)

    async def test_the_job_checks_again_before_the_unlink(self):
        keeper, other, patches = self.mount("x.png", "x.png", ino="other")
        job, group = await self.found_group()
        plan = await self.review(job.id, [self.keep_of(group, "x.png", 0)])
        self.assertEqual(plan["scope"]["count"], 1)  # looked separate then
        with patches[0], patches[1]:
            cleanup = await self.apply_plan(plan)
        self.assertEqual(cleanup.counters["removed"], 0)
        rows = {row["path"]: row["detail"] for row in cleanup.outcomes["kept"]}
        self.assertEqual(rows["x.png"], duplicate_files.MAYBE_SAME_FILE)
        self.assertTrue(other.is_file() and keeper.is_file())

    async def test_a_volume_without_file_ids_is_named_truthfully(self):
        keeper, other, patches = self.mount("x.png", "x.png")

        def zero(path, *args, **kwargs):
            return _fake_stat(self.real_stat(path), st_dev=0, st_ino=0)

        with patch("os.lstat", side_effect=zero), patch("os.stat", side_effect=zero):
            job, group = await self.found_group()
            plan = await self.review(job.id, [self.keep_of(group, "x.png", 0)])
        self.assertEqual(
            plan["protected"]["reasons"],
            ["1 file: can't confirm this is a separate file on this drive"],
        )

    async def test_the_kept_copy_is_held_open_during_the_unlink(self):
        self.trio()
        job, group = await self.found_group()
        plan = await self.review(job.id, [self.keep_of(group, "a.png")])
        real = duplicate_files._unlink
        held = []

        def unlink(path):
            held.append(bool(open_handles_under(self.output_dir / "a.png")))
            real(path)

        if not os.path.isdir("/proc/self/fd"):  # pragma: no cover - no /proc
            self.skipTest("open handles are read from /proc")
        with patch.object(duplicate_files, "_unlink", side_effect=unlink):
            await self.apply_plan(plan)
        self.assertEqual(held, [True, True])


class TestNestedSpellings(SafetyTestCase):
    """Round 2, R2: spellings and favorites by containment, not equality."""

    async def test_a_parent_spelling_of_a_nested_source(self):
        # The reviewer's probe: sources [output/sub, out-link -> output].
        self.need_symlinks()
        self.write("sub/a.png", b"same", mtime=1)
        self.write("sub/b.png", b"same", mtime=2)
        out_link = Path(self.comfy_dir) / "out-link"
        os.symlink(str(self.output_dir), str(out_link))
        GalleryConfig.MONITORING_DIRECTORIES = [
            self._root(self.output_dir / "sub"),
            str(out_link),
        ]
        prompt = self.save("Linked before sub was a source")
        self.api.db.link_image_to_prompt(prompt, str(out_link / "sub" / "b.png"))
        job, group = await self.found_group()
        plan = await self.review(job.id, [self.keep_of(group, "a.png")])
        self.assertIn("1 database link moves", plan["details"][0]["detail"])
        await self.apply_plan(plan)
        [image] = self.api.db.get_prompt_images(prompt)
        self.assertEqual(
            _locator(image["image_path"]), _locator(self.output_dir / "sub" / "a.png")
        )
        self.assertEqual(self.linked_files(), [])

    async def test_nested_sources_subfolder_first_with_a_parent_favorite(self):
        self.write("sub/a.png", b"same", mtime=1)
        self.write("sub/b.png", b"same", mtime=2)
        GalleryConfig.MONITORING_DIRECTORIES = [
            self._root(self.output_dir / "sub"),
            self._root(self.output_dir),
        ]
        parent_key = duplicate_files.favorite_root_key(self.output_dir)
        self.api.db.set_image_favorite(parent_key, "sub/b.png")
        prompt = self.save("Linked under the parent")
        self.api.db.link_image_to_prompt(prompt, str(self.output_dir / "sub" / "b.png"))
        job, group = await self.found_group()
        self.assertEqual({f["root_index"] for f in group["files"]}, {0})
        plan = await self.review(job.id, [self.keep_of(group, "a.png")])
        await self.apply_plan(plan)
        self.assertEqual(self.api.db.get_root_favorites(parent_key), {"sub/a.png"})
        self.assertEqual(self.linked_files(), [])


class TestLockedRowSaysWhereLinksAre(SafetyTestCase):
    """Round 2, m1."""

    async def test_the_locked_row_names_the_kept_copy(self):
        self.trio()
        job, group = await self.found_group()
        plan = await self.review(job.id, [self.keep_of(group, "a.png")])
        with (
            patch.object(
                duplicate_files, "_unlink", side_effect=PermissionError(13, "denied")
            ),
            self.assertLogs("prompt_manager", "WARNING"),
        ):
            cleanup = await self.apply_plan(plan)
        self.assertIn(
            "its links now name the kept copy", cleanup.outcomes["errors"][0]["detail"]
        )
