"""Delete selected images: the review and the apply job (#480).

``POST /prompt_manager/images/delete/preview`` reviews both modes;
``POST /prompt_manager/maintenance/apply {token, mode}`` removes the files'
records (library mode, with a removal mark) or moves the files to the trash
first (trash mode, the fake trash here). No path unlinks a media file.
"""

import asyncio
import os
import sys
import sqlite3
from pathlib import Path
from unittest.mock import patch

try:
    from tests import fake_trash
    from tests.disk_waits import DISK_BOUND_SECONDS
    from tests.test_api_duplicate_files import DuplicateFilesTestCase
except ImportError:  # discovered with tests/ as the top-level directory
    import fake_trash
    from disk_waits import DISK_BOUND_SECONDS
    from test_api_duplicate_files import DuplicateFilesTestCase

from py.api import image_delete
from py.api.image_delete import file_check, lexical_problem

PREVIEW = "/prompt_manager/images/delete/preview"
APPLY = "/prompt_manager/maintenance/apply"
LISTING = "/prompt_manager/images/output"


class ImageDeleteTestCase(DuplicateFilesTestCase):
    async def get_application(self):
        app = await super().get_application()
        self.fake = fake_trash.install(Path(self.tmpdir) / "fake-trash")
        self.addCleanup(fake_trash.uninstall)
        return app

    def target(self, path, index=0):
        roots = self.api._get_all_output_dirs()
        return {
            "root_index": index,
            "root_scope": self.api._gallery_root_scope(roots[index], index),
            "path": path,
        }

    async def review(self, paths, status=200):
        items = [p if isinstance(p, dict) else self.target(p) for p in paths]
        return await self.post(PREVIEW, {"items": items}, status)

    async def delete(self, paths, mode):
        plan = await self.review(paths)
        body = await self.post(APPLY, {"token": plan["token"], "mode": mode}, 202)
        job = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(job.task), DISK_BOUND_SECONDS)
        return plan, job

    async def listed(self):
        response = await self.client.get(LISTING, params={"mode": "workspace"})
        page = await response.json()
        return [image["path"] for image in page["images"]]

    def link(self, name, text="a prompt"):
        prompt_id = self.save(text)
        return self.api.db.link_image_to_prompt(
            str(prompt_id), str(self.output_dir / name)
        )

    def count(self, sql):
        conn = sqlite3.connect(self.api.db.model.db_path)
        try:
            return conn.execute(sql).fetchone()[0]
        finally:
            conn.close()

    @staticmethod
    def details(job, group):
        return [row["detail"] for row in job.detail()["outcomes"][group]]


class TestReview(ImageDeleteTestCase):
    async def test_review_counts_records_and_refuses_what_it_cannot_delete(self):
        self.write("a.png", b"aaa", mtime=1_000_000)
        self.write("b.png", b"bbb", mtime=2_000_000)
        self.link("a.png", "only a")
        self.fake.refuse[str(self.output_dir / "b.png")] = "no-trash"
        plan = await self.review(["a.png", "b.png", "missing.png"])
        self.assertEqual(plan["library"], {"count": 2})
        self.assertEqual(plan["trash"], {"count": 1})
        self.assertEqual(plan["refused"]["counts"], {"not-listed": 1})
        self.assertEqual(plan["trash_refused"]["counts"], {"no-trash": 1})
        self.assertEqual(plan["records"]["prompt_links"], 1)
        self.assertEqual(plan["orphaned_prompts"]["count"], 1)
        self.assertIn(plan["backup"]["policy"], ("on", "off"))
        self.assertNotIn(self.tmpdir, str(plan))
        # The review wrote nothing.
        self.assertEqual(self.count("SELECT COUNT(*) FROM generated_images"), 1)
        self.assertEqual(self.fake.moved, [])

    async def test_body_shapes_and_the_cap(self):
        await self.post(PREVIEW, {}, 400)
        await self.post(PREVIEW, {"items": [], "mode": "trash"}, 400)
        too_many = [self.target(f"{n}.png") for n in range(501)]
        await self.post(PREVIEW, {"items": too_many}, 400)

    async def test_mode_is_required_and_only_for_this_plan(self):
        self.write("a.png", b"aaa")
        plan = await self.review(["a.png"])
        await self.post(APPLY, {"token": plan["token"]}, 400)
        await self.post(APPLY, {"token": plan["token"], "mode": "delete"}, 400)
        self.assertTrue((self.output_dir / "a.png").exists())


class TestLibraryMode(ImageDeleteTestCase):
    async def test_records_go_and_the_file_stays_hidden(self):
        self.write("a.png", b"aaa", mtime=1_000_000)
        self.write("b.png", b"bbb", mtime=2_000_000)
        self.link("a.png")
        self.assertEqual(sorted(await self.listed()), ["a.png", "b.png"])
        before = os.stat(self.output_dir / "a.png")
        _plan, job = await self.delete(["a.png"], "library")
        self.assertEqual(job.state, "finished", job.summary())
        self.assertEqual(self.count("SELECT COUNT(*) FROM generated_images"), 0)
        after = os.stat(self.output_dir / "a.png")
        self.assertEqual(
            (before.st_size, before.st_mtime_ns), (after.st_size, after.st_mtime_ns)
        )
        self.assertEqual(await self.listed(), ["b.png"])
        # A later scan or link never brings it back.
        self.assertEqual(self.link("a.png", "rescanned"), 0)
        self.assertEqual(
            self.api._removed_paths([self.output_dir / "a.png"]),
            {str(self.output_dir / "a.png")},
        )
        self.assertEqual(self.fake.moved, [])

    async def test_a_modified_file_shows_again(self):
        self.write("a.png", b"aaa", mtime=1_000_000)
        await self.delete(["a.png"], "library")
        self.assertEqual(await self.listed(), [])
        self.write("a.png", b"new and longer", mtime=3_000_000)
        self.api.invalidate_gallery_cache()
        self.assertEqual(await self.listed(), ["a.png"])

    async def test_a_safety_backup_is_taken_first(self):
        self.write("a.png", b"aaa")
        with patch.object(self.api, "_safety_backup_policy", return_value="on"):
            await self.delete(["a.png"], "library")
        self.assertEqual(len(self.backups()), 1)


class TestTrashMode(ImageDeleteTestCase):
    async def test_the_file_moves_first_then_its_records_go(self):
        self.write("a.png", b"aaa")
        self.link("a.png")
        seen = {}
        original = self.fake.move

        def move(path, roots):
            # The records still exist while the file moves.
            seen["links"] = self.count("SELECT COUNT(*) FROM generated_images")
            return original(path, roots)

        self.fake.move = move
        _plan, job = await self.delete(["a.png"], "trash")
        self.assertEqual(job.state, "finished", job.summary())
        self.assertEqual(seen["links"], 1)
        self.assertFalse((self.output_dir / "a.png").exists())
        [(source, destination)] = self.fake.moved
        self.assertTrue(os.path.exists(destination))
        self.assertEqual(self.count("SELECT COUNT(*) FROM generated_images"), 0)
        self.assertEqual(self.api.db.removal_marks(), {})

    async def test_a_refused_move_changes_nothing(self):
        self.write("a.png", b"aaa")
        self.link("a.png")
        plan = await self.review(["a.png"])
        self.fake.refuse[str(self.output_dir / "a.png")] = "locked"
        body = await self.post(APPLY, {"token": plan["token"], "mode": "trash"}, 202)
        job = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(job.task), DISK_BOUND_SECONDS)
        self.assertTrue((self.output_dir / "a.png").exists())
        self.assertEqual(self.count("SELECT COUNT(*) FROM generated_images"), 1)
        self.assertEqual(self.api.db.removal_marks(), {})
        self.assertEqual(job.counters["not_changed"], 1)
        self.assertEqual(
            self.details(job, "not_changed"), [image_delete.REASONS["locked"]]
        )

    async def test_a_file_changed_after_the_review_is_not_changed(self):
        self.write("a.png", b"aaa", mtime=1_000_000)
        plan = await self.review(["a.png"])
        self.write("a.png", b"changed bytes", mtime=2_000_000)
        body = await self.post(APPLY, {"token": plan["token"], "mode": "trash"}, 202)
        job = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(job.task), DISK_BOUND_SECONDS)
        self.assertTrue((self.output_dir / "a.png").exists())
        self.assertEqual(self.fake.moved, [])
        self.assertEqual(
            self.details(job, "not_changed"), [image_delete.REASONS["changed"]]
        )

    async def test_a_records_failure_stops_the_job(self):
        self.write("a.png", b"aaa", mtime=1_000_000)
        self.write("b.png", b"bbb", mtime=2_000_000)
        with patch.object(
            type(self.api.db),
            "remove_image_records",
            side_effect=sqlite3.OperationalError("locked"),
        ):
            _plan, job = await self.delete(["a.png", "b.png"], "library")
        self.assertEqual(job.state, "failed")
        self.assertEqual(job.scope.get("failure"), "records-failed")
        self.assertEqual(job.counters["errors"], 1)
        self.assertEqual(
            self.details(job, "not_changed"), [image_delete.REASONS["not-attempted"]]
        )

    async def test_nothing_is_ever_unlinked(self):
        self.write("a.png", b"aaa")
        self.write("b.png", b"bbb")
        media = str(self.output_dir)
        real_remove, real_unlink = os.remove, os.unlink

        def guard(real):
            def call(path, *args, **kwargs):
                text = os.path.realpath(str(path))
                if (
                    text.startswith(os.path.realpath(media))
                    and "thumbnails" not in text
                ):
                    raise AssertionError(f"unlinked a media file: {path}")
                return real(path, *args, **kwargs)

            return call

        with (
            patch("os.remove", guard(real_remove)),
            patch("os.unlink", guard(real_unlink)),
        ):
            await self.delete(["a.png"], "trash")
            await self.delete(["b.png"], "library")
        self.assertTrue((self.output_dir / "b.png").exists())


class TestFileCheck(ImageDeleteTestCase):
    def test_lexical_codes(self):
        self.assertEqual(lexical_problem("thumbnails/a.png"), "thumbnail")
        self.assertEqual(lexical_problem(".Trash-1000/files/a.png"), "trash-folder")
        self.assertEqual(lexical_problem("x/$RECYCLE.BIN/a.png"), "trash-folder")
        self.assertEqual(lexical_problem("a.txt"), "not-media")
        self.assertIsNone(lexical_problem(".Trashy/a.png"))
        self.assertIsNone(lexical_problem("sub/a.png"))

    def test_links_and_folders_leading_outside_are_refused(self):
        root = str(self.output_dir)
        outside = Path(self.tmpdir) / "elsewhere"
        outside.mkdir()
        (outside / "x.png").write_bytes(b"x")
        try:
            os.symlink(outside / "x.png", self.output_dir / "link.png")
            os.symlink(outside, self.output_dir / "linked", target_is_directory=True)
        except OSError:
            self.skipTest("symlinks are not permitted here")
        self.assertEqual(
            file_check(str(self.output_dir / "link.png"), root, "link.png")[0], "link"
        )
        self.assertEqual(
            file_check(str(self.output_dir / "linked/x.png"), root, "linked/x.png")[0],
            "outside",
        )
        self.assertEqual(
            file_check(str(self.output_dir / "gone.png"), root, "gone.png")[0], "gone"
        )
        self.write("ok.png", b"ok")
        self.assertIsNone(
            file_check(str(self.output_dir / "ok.png"), root, "ok.png")[0]
        )

    def test_reasons_cover_every_code(self):
        from utils import trash

        for code in trash.CODES:
            self.assertIn(code, image_delete.REASONS)


class TestNativeBackend(ImageDeleteTestCase):
    """The real backend of this OS: Windows and macOS have no trash yet (#480)."""

    async def test_library_only_works_and_the_trash_is_unavailable_here(self):
        fake_trash.uninstall()
        self.write("a.png", b"aaa")
        self.link("a.png")
        plan = await self.review(["a.png"])
        native_trash = sys.platform not in ("win32", "darwin")
        if not native_trash:
            self.assertEqual(plan["trash"], {"count": 0})
            self.assertEqual(plan["trash_refused"]["counts"], {"no-trash": 1})
        body = await self.post(APPLY, {"token": plan["token"], "mode": "library"}, 202)
        job = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(asyncio.shield(job.task), DISK_BOUND_SECONDS)
        self.assertEqual(job.counters["removed"], 1, job.summary())
        self.assertTrue((self.output_dir / "a.png").exists())
        self.assertEqual(self.count("SELECT COUNT(*) FROM generated_images"), 0)
        self.assertEqual(await self.listed(), [])
