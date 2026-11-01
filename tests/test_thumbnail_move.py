"""Move thumbnails (#320 PR 3): the reviewed job in every direction, its
failure, cancel and restart paths, Recreate and the removed-sources scope.

Each test runs against a temp config.json (``AdminAPITestCase``) and a temp
journal; jobs are awaited through their task, never a timer.
"""

import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from tests.test_api_admin import AdminAPITestCase
    from tests.test_api_images import make_png
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_admin import AdminAPITestCase
    from test_api_images import make_png

from py.api import preview_location, preview_store  # noqa: E402
from py.api import thumbnail_move_files as files  # noqa: E402
from py.api import thumbnail_move_plan as move_plan  # noqa: E402
from py.config import PromptManagerConfig  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PLAN = "/prompt_manager/images/thumbnails/plan"
START = "/prompt_manager/jobs/thumbnails"


class MoveCase(AdminAPITestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.api._get_all_output_dirs = lambda: [self.output_dir]
        self.api.recover_thumbnail_move()
        self.journal = Path(self.tmpdir) / "data" / "thumbnail-move.json"
        for target in (move_plan,):
            patcher = patch.object(target, "journal_path", lambda: str(self.journal))
            patcher.start()
            self.addCleanup(patcher.stop)
        default = patch(
            "py.api.thumbnail_move_plan.default_path",
            lambda: str(Path(self.tmpdir) / "default-thumbs"),
        )
        default.start()
        self.addCleanup(default.stop)
        self.addCleanup(preview_store.configure, None)
        self.addCleanup(setattr, PromptManagerConfig, "THUMBNAIL_LOCATION", None)
        self.old_store = self.output_dir / "thumbnails"
        self.folder = Path(self.tmpdir) / "thumbs"
        # Two media files with previews, an orphan, a temp and a note.
        make_png(self.output_dir / "a.png", size=(8, 8))
        make_png(self.output_dir / "sub" / "b.png", size=(8, 8))
        make_png(self.old_store / "a_thumb.webp", size=(4, 4))
        make_png(self.old_store / "sub" / "b_thumb.webp", size=(4, 4))
        make_png(self.old_store / "gone_thumb.webp", size=(4, 4))
        (self.old_store / ".a_thumb.part-1-1.webp").write_bytes(b"t")
        (self.old_store / "notes.txt").write_text("mine", encoding="utf-8")

    async def plan(self, location):
        resp = await self.client.post(PLAN, json={"mode": "move", "location": location})
        return resp.status, await resp.json()

    async def move(self, location, confirmed=True):
        status, plan = await self.plan(location)
        self.assertEqual(status, 200, plan)
        resp = await self.client.post(
            START, json={"mode": "move", "token": plan["token"], "confirmed": confirmed}
        )
        body = await resp.json()
        self.assertEqual(resp.status, 202, body)
        job = self.api._jobs.get(body["job"]["id"])
        await job.task
        return plan, job

    def config(self):
        with open(self.config_path, encoding="utf-8") as handle:
            return json.load(handle)

    def key_files(self, folder=None):
        folder = folder or self.folder
        return sorted(
            p.relative_to(folder).as_posix()
            for p in folder.rglob("*")
            if p.is_file() and p.name != preview_location.MARKER_NAME
        )


class TestMoveToAFolder(MoveCase):
    async def test_review_counts_what_moves_is_removed_and_stays(self):
        status, plan = await self.plan({"mode": "folder", "path": str(self.folder)})
        self.assertEqual(status, 200, plan)
        totals = plan["totals"]
        self.assertEqual(
            (
                totals["files"],
                totals["orphans"],
                totals["temps"],
                totals["left_in_place"],
            ),
            (2, 1, 1, 1),
        )
        self.assertTrue(plan["confirm_required"])
        self.assertTrue(plan["to"]["will_create"])
        self.assertFalse(self.folder.exists())  # the review writes nothing

    async def test_a_move_copies_switches_and_removes_old_copies(self):
        before = (self.output_dir / "a.png").read_bytes()
        _plan, job = await self.move({"mode": "folder", "path": str(self.folder)})
        self.assertEqual(job.state, "finished", job.log)
        self.assertEqual(job.counters["moved"], 2)
        keys = self.key_files()
        self.assertEqual(len(keys), 2)
        self.assertTrue(all(re.match(r"^output-[0-9a-f]{8}/", k) for k in keys))
        location = self.config()["thumbnails"]["location"]
        self.assertEqual(
            (location["mode"], location["path"]), ("folder", str(self.folder))
        )
        self.assertEqual(location["cleanup"], [])
        self.assertEqual(preview_store.current().mode, "folder")
        # Old copies, the orphan and the temp went; the note stays, so its
        # folder stays; originals are untouched.
        self.assertEqual(
            sorted(p.name for p in self.old_store.rglob("*")), ["notes.txt"]
        )
        self.assertEqual((self.output_dir / "a.png").read_bytes(), before)
        self.assertFalse(self.journal.exists())
        copied = next(self.folder.rglob("a_thumb.webp"))
        self.assertEqual(copied.stat().st_mtime_ns % files.MTIME_STEP_NS, 0)

    async def test_the_move_back_removes_the_folder_it_made(self):
        await self.move({"mode": "folder", "path": str(self.folder)})
        _plan, job = await self.move({"mode": "source"})
        self.assertEqual(job.state, "finished", job.log)
        self.assertTrue((self.old_store / "a_thumb.webp").is_file())
        self.assertTrue((self.old_store / "sub" / "b_thumb.webp").is_file())
        self.assertFalse(self.folder.exists())
        self.assertEqual(self.config()["thumbnails"]["location"]["mode"], "source")

    async def test_folder_to_folder_keeps_the_key_names(self):
        await self.move({"mode": "folder", "path": str(self.folder)})
        first = self.key_files()
        other = Path(self.tmpdir) / "thumbs2"
        _plan, job = await self.move({"mode": "folder", "path": str(other)})
        self.assertEqual(job.state, "finished", job.log)
        self.assertEqual(self.key_files(other), first)
        self.assertFalse(self.folder.exists())

    async def test_previews_of_sources_no_longer_listed_go_when_leaving(self):
        await self.move({"mode": "folder", "path": str(self.folder)})
        setting = preview_store.setting()

        def add(marker):
            marker["keys"]["old-0123abcd"] = {
                "path": "/gone",
                "identity": "/gone",
                "install": setting.install,
            }

        preview_location.write_marker(str(self.folder), add)
        make_png(self.folder / "old-0123abcd" / "x_thumb.webp", size=(4, 4))
        preview_store.refresh()
        status, plan = await self.plan({"mode": "source"})
        self.assertEqual(plan["totals"]["unlisted_sources"], 1)
        _plan, job = await self.move({"mode": "source"})
        self.assertFalse(self.folder.exists())

    async def test_confirmation_and_a_stale_token(self):
        status, plan = await self.plan({"mode": "folder", "path": str(self.folder)})
        resp = await self.client.post(
            START, json={"mode": "move", "token": plan["token"]}
        )
        self.assertEqual((await resp.json())["code"], "confirmation-required")
        resp = await self.client.post(
            START, json={"mode": "move", "token": "nope", "confirmed": True}
        )
        self.assertEqual(resp.status, 409)
        self.assertEqual((await resp.json())["code"], "plan-stale")

    async def test_a_folder_that_is_too_small_is_refused(self):
        with patch.object(move_plan, "_free", lambda _path: 1):
            status, body = await self.plan({"mode": "folder", "path": str(self.folder)})
        self.assertEqual(status, 507)
        self.assertEqual(body["code"], "insufficient-space")

    async def test_validation_refusals_come_back_in_place(self):
        status, body = await self.plan(
            {"mode": "folder", "path": str(self.output_dir / "thumbs")}
        )
        self.assertEqual((status, body["code"]), (400, "location-served"))
        status, body = await self.plan({"mode": "source"})
        self.assertEqual(body["code"], "location-same")


class TestFailures(MoveCase):
    async def test_a_per_file_error_still_switches_and_removes_its_old_copy(self):
        real = files.copy_one

        def failing(source, destination, *args, **kwargs):
            if Path(source).name == "a_thumb.webp":
                raise files.CopyFailed("Could not be read; made again on view")
            return real(source, destination, *args, **kwargs)

        with patch.object(files, "copy_one", failing):
            _plan, job = await self.move({"mode": "folder", "path": str(self.folder)})
        self.assertEqual(job.state, "finished")
        self.assertEqual((job.counters["moved"], job.counters["errors"]), (1, 1))
        self.assertFalse((self.old_store / "a_thumb.webp").exists())

    async def test_a_folder_wide_error_changes_nothing(self):
        def gone(*_args, **_kwargs):
            raise files.FolderGone("disk full")

        with patch.object(files, "copy_one", gone):
            _plan, job = await self.move({"mode": "folder", "path": str(self.folder)})
        self.assertEqual(job.state, "failed")
        self.assertIn("nothing changed", job.status_line)
        self.assertFalse(self.folder.exists())
        self.assertTrue((self.old_store / "a_thumb.webp").is_file())
        self.assertEqual(preview_store.current().mode, "source")
        self.assertFalse(self.journal.exists())

    async def test_cancel_before_the_switch_changes_nothing(self):
        status, plan = await self.plan({"mode": "folder", "path": str(self.folder)})
        real = self.api._move_copy_one

        def cancel_first(pair, **kwargs):
            job = self.api._jobs.active("thumbnails")
            job.cancelled = True
            return real(pair, **kwargs)

        with patch.object(self.api, "_move_copy_one", cancel_first):
            resp = await self.client.post(
                START, json={"mode": "move", "token": plan["token"], "confirmed": True}
            )
            job = self.api._jobs.get((await resp.json())["job"]["id"])
            await job.task
        self.assertEqual(job.state, "cancelled")
        self.assertFalse(self.folder.exists())
        self.assertEqual(preview_store.current().mode, "source")

    async def test_an_exit_during_the_copy_leaves_the_journal_for_the_next_start(self):
        status, plan = await self.plan({"mode": "folder", "path": str(self.folder)})
        real = self.api._move_copy_one
        exiting = {"now": False}

        def exit_after(pair, **kwargs):
            result = real(pair, **kwargs)
            exiting["now"] = True  # the interpreter starts exiting
            return result

        from py.api import thumbnail_move

        with (
            patch.object(self.api, "_move_copy_one", exit_after),
            patch.object(
                thumbnail_move.exit_signal, "requested", lambda: exiting["now"]
            ),
        ):
            resp = await self.client.post(
                START, json={"mode": "move", "token": plan["token"], "confirmed": True}
            )
            job = self.api._jobs.get((await resp.json())["job"]["id"])
            await job.task
        self.assertTrue(self.journal.exists())
        self.assertTrue(self.key_files())  # partial copies were left
        # The next start undoes it: copies and the created folder go.
        self.api._move_recovered = False
        self.api.recover_thumbnail_move()
        self.assertFalse(self.journal.exists())
        self.assertFalse(self.folder.exists())
        self.assertTrue((self.old_store / "a_thumb.webp").is_file())


class TestRestart(MoveCase):
    async def test_a_journal_left_after_the_switch_is_forgotten(self):
        await self.move({"mode": "folder", "path": str(self.folder)})
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        self.journal.write_text(
            json.dumps(
                {"state": "copying", "to": {"mode": "folder", "path": str(self.folder)}}
            ),
            encoding="utf-8",
        )
        self.api._move_recovered = False
        self.api.recover_thumbnail_move()
        self.assertFalse(self.journal.exists())
        self.assertTrue(self.key_files())

    async def test_old_copies_that_waited_are_removed_by_the_finishing_job(self):
        with patch.object(files, "remove_file", lambda path, **_k: False):
            _plan, job = await self.move({"mode": "folder", "path": str(self.folder)})
        self.assertTrue(preview_store.setting().cleanup)
        self.assertTrue((self.old_store / "a_thumb.webp").exists())
        job = await self.api.finish_thumbnail_move()
        await job.task
        self.assertEqual(job.title, "Move thumbnails (finishing)")
        self.assertFalse((self.old_store / "a_thumb.webp").exists())
        self.assertEqual(self.config()["thumbnails"]["location"]["cleanup"], [])


class TestAdmission(MoveCase):
    async def test_automatic_previews_and_setting_changes_wait_for_the_move(self):
        status, plan = await self.plan({"mode": "folder", "path": str(self.folder)})
        seen = {}
        real = self.api._move_copy_one

        def observe(pair, **kwargs):
            seen["blocker"] = self.api._auto_thumbnail_blocker()
            return real(pair, **kwargs)

        with patch.object(self.api, "_move_copy_one", observe):
            resp = await self.client.post(
                START, json={"mode": "move", "token": plan["token"], "confirmed": True}
            )
            job = self.api._jobs.get((await resp.json())["job"]["id"])
            refused = self.api._thumbnail_change_refused({"THUMBNAIL_QUALITY": 5})
            self.assertEqual(refused.status, 409)
            await job.task
        self.assertIs(seen["blocker"], job)

    def test_the_move_never_trims_memory_and_copies_on_the_job_pool(self):
        text = "".join(
            (REPO / "py" / "api" / name).read_text(encoding="utf-8")
            for name in ("thumbnail_move.py", "thumbnail_prune.py")
        )
        self.assertNotIn("_release_job_memory", text)
        self.assertIn("map_parallel", (REPO / "py/api/thumbnail_move.py").read_text())


class TestPrune(MoveCase):
    async def test_only_this_installs_removed_sources_go(self):
        await self.move({"mode": "folder", "path": str(self.folder)})
        install = preview_store.setting().install

        def add(marker):
            marker["keys"]["old-0123abcd"] = {"identity": "/gone", "install": install}
            marker["keys"]["theirs-0123abcd"] = {"identity": "/x", "install": "other"}

        preview_location.write_marker(str(self.folder), add)
        make_png(self.folder / "old-0123abcd" / "x_thumb.webp", size=(4, 4))
        make_png(self.folder / "theirs-0123abcd" / "y_thumb.webp", size=(4, 4))
        preview_store.refresh()
        resp = await self.client.get(PLAN + "?mode=prune")
        body = await resp.json()
        self.assertEqual([s["key"] for s in body["sources"]], ["old-0123abcd"])
        self.assertIn("1 source no longer listed", body["line"])
        resp = await self.client.post(START, json={"mode": "prune"})
        job = self.api._jobs.get((await resp.json())["job"]["id"])
        await job.task
        self.assertFalse((self.folder / "old-0123abcd").exists())
        self.assertTrue((self.folder / "theirs-0123abcd" / "y_thumb.webp").exists())
        keys = preview_location.read_marker(str(self.folder))["keys"]
        self.assertNotIn("old-0123abcd", keys)


class TestRecreate(MoveCase):
    async def test_recreate_and_its_refusals(self):
        await self.move({"mode": "folder", "path": str(self.folder)})
        url = "/prompt_manager/images/thumbnails/location/recreate"
        resp = await self.client.post(url)
        self.assertEqual((await resp.json())["code"], "location-has-marker")
        for child in sorted(self.folder.rglob("*"), reverse=True):
            child.rmdir() if child.is_dir() else child.unlink()
        self.folder.rmdir()
        self.assertFalse(preview_store.refresh())
        resp = await self.client.post(url)
        self.assertEqual(resp.status, 200, await resp.text())
        self.assertTrue(preview_store.current().available)
        self.assertNotEqual(self.config()["thumbnails"]["location"]["id"], None)

    async def test_use_this_folder_adopts_another_marker(self):
        await self.move({"mode": "folder", "path": str(self.folder)})
        marker = preview_location.read_marker(str(self.folder))
        marker["id"] = "someone-else"
        (self.folder / preview_location.MARKER_NAME).write_text(
            json.dumps(marker), encoding="utf-8"
        )
        self.assertFalse(preview_store.refresh())
        resp = await self.client.post(
            "/prompt_manager/images/thumbnails/location/adopt"
        )
        self.assertEqual(resp.status, 200, await resp.text())
        self.assertTrue(preview_store.current().available)
        self.assertEqual(self.config()["thumbnails"]["location"]["id"], "someone-else")


class TestFiles(unittest.TestCase):
    def test_mtime_rounds_up_to_two_seconds(self):
        self.assertEqual(files.rounded_mtime_ns(0), 0)
        self.assertEqual(files.rounded_mtime_ns(1), files.MTIME_STEP_NS)
        self.assertEqual(
            files.rounded_mtime_ns(files.MTIME_STEP_NS), files.MTIME_STEP_NS
        )

    def test_long_windows_destinations_are_per_file_errors(self):
        self.assertTrue(files.too_long("D:\\" + "x" * 240, long_paths=False))
        self.assertFalse(files.too_long("D:\\x", long_paths=False))

    def test_a_held_open_file_is_retried_then_left(self):
        waits = []
        error = OSError("in use")
        error.winerror = 32
        with patch.object(files.os, "unlink", side_effect=error):
            self.assertFalse(files.remove_file("x", sleep=waits.append))
        self.assertEqual(waits, [0.5, 1.0, 2.0])


if __name__ == "__main__":
    unittest.main()


class TestReviewFindings(MoveCase):
    async def test_moving_back_never_removes_previews_a_waiting_cleanup_named(self):
        await self.move({"mode": "folder", "path": str(self.folder)})
        other = Path(self.tmpdir) / "thumbs2"
        # The old copies in the first folder cannot go yet (held open).
        with patch.object(files, "remove_file", lambda path, **_k: False):
            await self.move({"mode": "folder", "path": str(other)})
        self.assertTrue(preview_store.setting().cleanup)
        _plan, job = await self.move({"mode": "folder", "path": str(self.folder)})
        self.assertEqual(job.state, "finished", job.log)
        self.assertEqual(len(self.key_files()), 2)  # the previews moved back stay

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
    def test_empty_folder_removal_never_walks_through_a_link(self):
        outside = Path(self.tmpdir) / "outside"
        (outside / "empty").mkdir(parents=True)
        (outside / "Thumbs.db").write_bytes(b"x")
        store = Path(self.tmpdir) / "store"
        store.mkdir()
        try:
            (store / "link").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks not permitted")
        files.remove_empty_folders(store)
        self.assertTrue((outside / "empty").is_dir())
        self.assertTrue((outside / "Thumbs.db").exists())

    @unittest.skipUnless(os.name == "nt", "junctions are Windows only")
    def test_empty_folder_removal_never_walks_through_a_junction(self):
        import _winapi

        outside = Path(self.tmpdir) / "outside-j"
        (outside / "empty").mkdir(parents=True)
        (outside / "Thumbs.db").write_bytes(b"x")
        store = Path(self.tmpdir) / "store-j"
        store.mkdir()
        _winapi.CreateJunction(str(outside), str(store / "junction"))
        files.remove_empty_folders(store)
        self.assertTrue((outside / "empty").is_dir())
        self.assertTrue((outside / "Thumbs.db").exists())

    async def test_an_unexpected_error_while_copying_changes_nothing(self):
        def broken(*_args, **_kwargs):
            raise PermissionError("denied")

        with patch.object(files, "_make_folders", broken):
            _plan, job = await self.move({"mode": "folder", "path": str(self.folder)})
        self.assertEqual(job.state, "failed")
        self.assertFalse(self.journal.exists())
        self.assertFalse(self.folder.exists())
        self.assertEqual(preview_store.current().mode, "source")

    async def test_a_journal_that_cannot_be_removed_never_undoes_the_switch(self):
        from py.api import thumbnail_move

        real = thumbnail_move._remove

        def stuck(path):
            if str(path) == str(self.journal):
                raise PermissionError("in use")
            return real(path)

        with patch.object(thumbnail_move, "_remove", stuck):
            _plan, job = await self.move({"mode": "folder", "path": str(self.folder)})
        self.assertEqual(job.state, "finished", job.log)
        self.assertEqual(len(self.key_files()), 2)
        self.assertEqual(preview_store.current().mode, "folder")

    async def test_a_hostile_key_is_never_pruned(self):
        await self.move({"mode": "folder", "path": str(self.folder)})
        victim = Path(self.tmpdir) / "victim"
        make_png(victim / "z_thumb.webp", size=(4, 4))
        marker = preview_location.read_marker(str(self.folder))
        marker["keys"]["../victim"] = {"identity": "/gone", "install": marker_install()}
        (self.folder / preview_location.MARKER_NAME).write_text(
            json.dumps(marker), encoding="utf-8"
        )
        preview_store.refresh()
        resp = await self.client.post(START, json={"mode": "prune"})
        job = self.api._jobs.get((await resp.json())["job"]["id"])
        await job.task
        self.assertTrue((victim / "z_thumb.webp").exists())

    def test_a_hand_edited_source_store_must_be_a_thumbnails_folder(self):
        elsewhere = Path(self.tmpdir) / "photos"
        make_png(elsewhere / "keep_thumb.webp", size=(4, 4))
        self.assertTrue(self.api._remove_store(elsewhere, None, None))
        self.assertTrue((elsewhere / "keep_thumb.webp").exists())


def marker_install():
    return preview_store.setting().install
