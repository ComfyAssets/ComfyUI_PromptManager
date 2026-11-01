"""One folder mode (#320 PR 2): previews in ``<folder>/<source key>``.

Folder mode is reachable from config.json and tests only (D4B). These tests
set it on the resolver directly (``preview_store.configure``) and drive
every route that reads, writes, lists or removes previews: they must touch
only ``<folder>/<key>``, never ``<source>/thumbnails``, never through a link,
and nothing at all while the folder is unavailable. Waits are events; no
sleeps.
"""

import asyncio
import json
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from tests.test_thumbnail_auto import AutoThumbnailCase, _entry, WAIT
    from tests.test_api_images import make_png
except ImportError:  # discovered with tests/ as the top-level directory
    from test_thumbnail_auto import AutoThumbnailCase, _entry, WAIT
    from test_api_images import make_png

from py.api import preview_location, preview_store, thumbnail_settings  # noqa: E402

SERVE = "/prompt_manager/images/serve/"


class FolderModeCase(AutoThumbnailCase):
    """The output root's previews live in ``<comfy>/thumbs/<key>``."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.folder = self.use_folder(self.comfy_dir / "thumbs")
        self.old_store = self.output_dir / "thumbnails"

    def use_folder(self, folder, install="install-a"):
        folder.mkdir(exist_ok=True)
        marker_id = preview_location.init_folder(str(folder), created_by_pm=True)
        self.addCleanup(preview_store.configure, None)
        preview_store.configure(
            {"mode": "folder", "path": str(folder), "id": marker_id, "install": install}
        )
        self.assertTrue(preview_store.current().available)
        return folder

    def key_folder(self):
        store = preview_store.ensure_store(self.output_dir, create=True)
        self.assertIsNotNone(store)
        return store

    def preview(self, rel, ext=".webp"):
        return self.key_folder() / (thumbnail_settings.preview_stem(rel) + ext)

    def marker(self):
        return json.loads(
            (self.folder / preview_location.MARKER_NAME).read_text(encoding="utf-8")
        )


class TestFolderModeRoutes(FolderModeCase):
    async def test_the_key_is_a_readable_name_plus_a_hash_in_the_marker(self):
        key = self.key_folder().name
        self.assertRegex(key, r"^output-[0-9a-f]{8}$")
        entry = self.marker()["keys"][key]
        self.assertEqual(entry["path"], str(self.output_dir))
        self.assertEqual(entry["install"], "install-a")

    async def test_another_case_of_the_source_finds_the_same_key(self):
        key = self.key_folder()
        other = Path(str(self.output_dir).swapcase())
        if not other.is_dir():
            self.skipTest("a case-sensitive volume")
        self.assertEqual(preview_store.store_dir(other), key)

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
    async def test_a_linked_spelling_of_the_source_finds_the_same_key(self):
        key = self.key_folder()
        link = self.comfy_dir / "output-link"
        try:
            link.symlink_to(self.output_dir, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks not permitted")
        self.assertEqual(preview_store.store_dir(link), key)

    async def test_an_existing_preview_is_listed_and_served_from_the_key_folder(self):
        make_png(self.output_dir / "sub" / "a.png", size=(64, 64))
        preview = self.preview("sub/a.png")
        make_png(preview, size=(32, 32))
        url = _entry(self.api, self.output_dir, "sub/a.png")["thumbnail_url"]
        self.assertIn("v=", url)
        self.assertTrue(url.startswith(SERVE + "thumbnails/sub/a_thumb.webp"))
        resp = await self.client.get(url)
        self.assertEqual(resp.status, 200)
        self.assertEqual(await resp.read(), preview.read_bytes())

    async def test_a_missing_preview_is_made_in_the_key_folder(self):
        make_png(self.output_dir / "b.png", size=(640, 480))
        url = _entry(self.api, self.output_dir, "b.png")["thumbnail_url"]
        resp = await self.client.get(url)
        self.assertEqual(resp.status, 200)
        self.assertTrue(self.preview("b.png").is_file())
        self.assertFalse(self.old_store.exists())

    async def test_a_leftover_in_the_source_folder_is_never_served(self):
        make_png(self.output_dir / "c.png", size=(640, 480))
        self.old_store.mkdir()
        (self.old_store / "c_thumb.webp").write_bytes(b"leftover")
        resp = await self.client.get(SERVE + "thumbnails/c_thumb.webp?root=0")
        self.assertEqual(resp.status, 200)
        self.assertNotEqual(await resp.read(), b"leftover")

    async def test_other_text_under_thumbnails_is_refused(self):
        self.old_store.mkdir()
        make_png(self.old_store / "notes.png", size=(8, 8))
        resp = await self.client.get(SERVE + "thumbnails/notes.png?root=0")
        self.assertEqual(resp.status, 403)

    async def test_traversal_through_thumbnails_is_refused(self):
        for text in (
            "thumbnails/../../thumbs/x_thumb.webp",
            "thumbnails/..%2F..%2Fx_thumb.webp",
            "thumbnails/%2e%2e/x_thumb.webp",
        ):
            with self.subTest(text=text):
                resp = await self.client.get(SERVE + text + "?root=0")
                self.assertIn(resp.status, (403, 404))

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
    async def test_a_link_out_of_the_key_folder_is_never_served_or_written(self):
        outside = self.comfy_dir / "outside"
        outside.mkdir()
        make_png(outside / "d_thumb.webp", size=(8, 8))
        make_png(self.output_dir / "sub" / "d.png", size=(64, 64))
        try:
            (self.key_folder() / "sub").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks not permitted")
        resp = await self.client.get(SERVE + "thumbnails/sub/d_thumb.webp?root=0")
        self.assertEqual(resp.status, 403)
        self.assertFalse(preview_store.owns(self.output_dir, outside / "d_thumb.webp"))
        self.assertFalse(
            preview_store.owns(self.output_dir, self.key_folder() / "sub" / "x.webp")
        )

    @unittest.skipUnless(os.name == "nt", "junctions are Windows only")
    async def test_a_junction_inside_a_store_is_refused(self):
        import _winapi

        outside = self.comfy_dir / "outside"
        outside.mkdir()
        (outside / "e_thumb.webp").write_bytes(b"x")
        _winapi.CreateJunction(str(outside), str(self.key_folder() / "sub"))
        self.assertTrue(preview_location.is_link_like(self.key_folder() / "sub"))
        resp = await self.client.get(SERVE + "thumbnails/sub/e_thumb.webp?root=0")
        self.assertEqual(resp.status, 403)

    async def test_capture_writes_into_the_key_folder(self):
        path = make_png(self.output_dir / "run" / "f.png", size=(64, 64))
        self.assertEqual(self.api.queue_capture_thumbnail(str(path)), "queued")
        await self._idle()
        self.assertTrue(self.preview("run/f.png").is_file())
        self.assertFalse(self.old_store.exists())

    async def test_clear_removes_previews_only(self):
        make_png(self.output_dir / "g.png", size=(64, 64))
        payload = await asyncio.get_running_loop().run_in_executor(
            None, self.api._generate_gallery_thumbnails_sync, [self.output_dir]
        )
        self.assertTrue(payload["success"])
        keep = self.key_folder() / "readme.txt"
        keep.write_text("not a preview", encoding="utf-8")
        resp = await self.client.post("/prompt_manager/images/clear-thumbnails")
        self.assertEqual(resp.status, 200)
        self.assertFalse(self.preview("g.png").exists())
        self.assertTrue(keep.exists())
        self.assertTrue((self.folder / preview_location.MARKER_NAME).is_file())
        self.assertTrue(self.output_dir.joinpath("g.png").is_file())

    async def test_removing_a_file_removes_its_preview_in_the_key_folder(self):
        from py.api.admin import _remove_thumbnail

        make_png(self.output_dir / "h.png", size=(64, 64))
        preview = self.preview("h.png")
        preview.write_bytes(b"x")
        self.assertTrue(_remove_thumbnail(self.output_dir, Path("h.png")))
        self.assertFalse(preview.exists())

    async def test_history_cover_is_authorized_by_the_store(self):
        from py.api import history_current

        preview = self.preview("i.png")
        preview.write_bytes(b"x")
        target = history_current._canonical(preview)
        self.assertTrue(
            history_current._target_authorized(
                target, self.output_dir, None, self.output_dir
            )
        )
        stray = self.comfy_dir / "stray_thumb.webp"
        stray.write_bytes(b"x")
        self.assertFalse(
            history_current._target_authorized(
                history_current._canonical(stray),
                self.output_dir,
                None,
                self.output_dir,
            )
        )

    async def test_the_thumbnail_folder_is_never_recreated_by_a_write(self):
        make_png(self.output_dir / "j.png", size=(64, 64))
        self.key_folder()
        for child in sorted(self.folder.rglob("*"), reverse=True):
            child.rmdir() if child.is_dir() else child.unlink()
        self.folder.rmdir()
        with self.assertRaises(FileNotFoundError):
            preview_store.make_parent(self.folder / "output-x" / "sub" / "a.webp")
        self.assertFalse(self.folder.exists())


class TestUnavailableFolder(FolderModeCase):
    def go_missing(self):
        (self.folder / preview_location.MARKER_NAME).unlink()
        self.assertFalse(preview_store.refresh())
        location = preview_store.current()
        self.assertEqual((location.available, location.problem), (False, "missing"))

    async def test_a_folder_without_its_marker_is_missing_and_writes_nothing(self):
        make_png(self.output_dir / "k.png", size=(640, 480))
        self.go_missing()
        entry = _entry(self.api, self.output_dir, "k.png")
        self.assertFalse(entry.get("thumbnail_ready", False))
        resp = await self.client.get(SERVE + "thumbnails/k_thumb.webp?root=0")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers["Cache-Control"], "no-store")
        self.assertEqual(
            await resp.read(), (self.output_dir / "k.png").read_bytes()
        )  # the original
        self.assertEqual(
            sorted(p.name for p in self.folder.rglob("*") if p.is_file()), []
        )
        self.assertFalse(self.old_store.exists())

    async def test_thumbnail_work_is_refused_while_unavailable(self):
        self.go_missing()
        for method, url, body in (
            ("POST", "/prompt_manager/images/generate-thumbnails", {}),
            ("POST", "/prompt_manager/images/generate-thumbnails/progress", None),
            ("POST", "/prompt_manager/images/clear-thumbnails", None),
            ("POST", "/prompt_manager/jobs/thumbnails", {"mode": "missing"}),
        ):
            with self.subTest(url=url):
                resp = await self.client.request(method, url, json=body)
                self.assertEqual(resp.status, 409)
                self.assertEqual((await resp.json())["code"], "location-unavailable")

    async def test_status_reports_the_location_without_a_path(self):
        self.go_missing()
        resp = await self.client.get("/prompt_manager/images/thumbnails/status")
        location = (await resp.json())["location"]
        self.assertEqual(
            location, {"mode": "folder", "available": False, "problem": "missing"}
        )

    async def test_another_folders_marker_is_a_mismatch(self):
        marker = self.marker()
        marker["id"] = "someone-else"
        (self.folder / preview_location.MARKER_NAME).write_text(
            json.dumps(marker), encoding="utf-8"
        )
        self.assertFalse(preview_store.refresh())
        self.assertEqual(preview_store.current().problem, "marker-mismatch")

    async def test_the_temp_sweep_skips_an_unavailable_folder(self):
        stray = self.key_folder() / ".l_thumb.part-1-1.webp"
        stray.write_bytes(b"x")
        old = os.path.getmtime(stray) - 10 * 24 * 3600
        os.utime(stray, (old, old))
        self.go_missing()
        self.assertEqual(self.api.sweep_thumbnail_temps(), 0)
        self.assertTrue(stray.exists())

    async def test_a_stalled_store_call_marks_it_unreachable_and_stops_io(self):
        make_png(self.output_dir / "m.png", size=(64, 64))
        self.preview("m.png").write_bytes(b"x")
        ticks = iter([0.0, preview_store.IO_BUDGET + 1.0])
        with patch.object(preview_store, "_clock", lambda: next(ticks, 99.0)):
            _entry(self.api, self.output_dir, "m.png")  # one slow lookup
        self.assertEqual(preview_store.current().problem, "unreachable")
        calls = []
        real = thumbnail_settings.owned_in_folder
        with patch.object(
            thumbnail_settings,
            "owned_in_folder",
            lambda *a, **k: calls.append(a) or real(*a, **k),
        ):
            entry = _entry(self.api, self.output_dir, "m.png")
        self.assertEqual(calls, [])  # no store I/O while unreachable
        self.assertNotIn("v=", entry["thumbnail_url"])
        self.assertTrue(preview_store.refresh())  # the probe finds it again

    async def test_a_check_that_blocks_is_unreachable_within_the_budget(self):
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def blocking(path, expected_id):
            entered.set()
            release.wait(WAIT)
            return True, None, None

        with (
            patch.object(preview_store, "IO_BUDGET", 0.2),
            patch.object(preview_location, "folder_state", blocking),
        ):
            self.assertFalse(preview_store.refresh())
        self.assertTrue(entered.is_set())
        self.assertEqual(preview_store.current().problem, "unreachable")
        release.set()

    async def test_an_invalid_config_value_writes_nothing_anywhere(self):
        preview_store.configure({"mode": "folder", "path": "relative/thumbs"})
        location = preview_store.current()
        self.assertEqual((location.available, location.problem), (False, "invalid"))
        make_png(self.output_dir / "n.png", size=(640, 480))
        resp = await self.client.get(SERVE + "thumbnails/n_thumb.webp?root=0")
        self.assertEqual(resp.status, 200)
        self.assertFalse(self.old_store.exists())


class TestSharedFolders(FolderModeCase):
    async def test_two_installs_keep_each_others_keys(self):
        self.key_folder()
        other_root = self.comfy_dir / "other"
        other_root.mkdir()
        setting = preview_store.setting()
        preview_store.configure(
            {
                "mode": "folder",
                "path": setting.path,
                "id": setting.id,
                "install": "install-b",
            }
        )
        self.assertIsNotNone(preview_store.ensure_store(other_root))
        keys = self.marker()["keys"]
        installs = sorted(entry["install"] for entry in keys.values())
        self.assertEqual(installs, ["install-a", "install-b"])

    async def test_init_folder_adopts_an_existing_marker(self):
        marker_id = self.marker()["id"]
        self.assertEqual(preview_location.init_folder(str(self.folder)), marker_id)


if __name__ == "__main__":
    unittest.main()


class TestHostileMarker(FolderModeCase):
    """A hand-edited or foreign marker never takes a write or a delete out of
    the thumbnail folder (shared and UNC folders can be edited by others)."""

    def plant(self, keys):
        marker = self.marker()
        marker["keys"] = keys
        (self.folder / preview_location.MARKER_NAME).write_text(
            json.dumps(marker), encoding="utf-8"
        )
        self.assertTrue(preview_store.refresh())

    def outside_files(self):
        return sorted(
            p.relative_to(self.comfy_dir).as_posix()
            for p in self.comfy_dir.rglob("*")
            if p.is_file()
            and not p.is_relative_to(self.folder)
            and not p.is_relative_to(self.output_dir)
            and p.suffix in (".webp", ".jpg", ".png")
        )

    async def test_keys_that_are_not_one_folder_name_are_dropped(self):
        entry = {"path": str(self.output_dir), "identity": "x", "install": "i"}
        self.plant(
            {
                "../escape-0123abcd": entry,
                str(self.comfy_dir / "abs-0123abcd"): entry,
                "a/b-0123abcd": entry,
                "a\\b-0123abcd": entry,
                "Upper-0123abcd": entry,
                "nohash": entry,
                "fine-0123abcd": "not a dict",
            }
        )
        self.assertEqual(preview_location.read_marker(str(self.folder))["keys"], {})
        store = self.key_folder()
        self.assertEqual(store.parent, self.folder)
        make_png(self.output_dir / "p.png", size=(640, 480))
        resp = await self.client.get(SERVE + "thumbnails/p_thumb.webp?root=0")
        self.assertEqual(resp.status, 200)
        self.assertTrue((store / "p_thumb.webp").is_file())
        self.assertEqual(self.outside_files(), [])

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
    async def test_a_key_folder_that_is_a_link_is_never_written_or_cleared(self):
        outside = self.comfy_dir / "outside"
        outside.mkdir()
        victim = outside / "q_thumb.webp"
        victim.write_bytes(b"keep me")
        store = self.key_folder()
        store.rmdir()
        try:
            store.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks not permitted")
        make_png(self.output_dir / "q.png", size=(640, 480))
        from py.api.admin import _remove_thumbnail

        self.assertFalse(_remove_thumbnail(self.output_dir, Path("q.png")))
        resp = await self.client.post("/prompt_manager/images/clear-thumbnails")
        self.assertIn(resp.status, (200, 400))
        resp = await self.client.get(SERVE + "thumbnails/q_thumb.webp?root=0")
        self.assertIn(resp.status, (200, 403))
        self.assertEqual(victim.read_bytes(), b"keep me")
        self.assertEqual(sorted(p.name for p in outside.iterdir()), ["q_thumb.webp"])

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
    async def test_a_linked_folder_below_a_key_is_never_deleted_through(self):
        outside = self.comfy_dir / "outside"
        outside.mkdir()
        victim = outside / "r_thumb.webp"
        victim.write_bytes(b"keep me")
        try:
            (self.key_folder() / "sub").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks not permitted")
        make_png(self.output_dir / "sub" / "r.png", size=(64, 64))
        from py.api.admin import _remove_thumbnail

        self.assertFalse(_remove_thumbnail(self.output_dir, Path("sub/r.png")))
        resp = await self.client.post("/prompt_manager/images/clear-thumbnails")
        self.assertEqual(resp.status, 200)
        self.assertEqual(victim.read_bytes(), b"keep me")

    @unittest.skipUnless(os.name == "nt", "junctions are Windows only")
    async def test_a_key_folder_that_is_a_junction_is_never_written_or_cleared(self):
        import _winapi

        outside = self.comfy_dir / "outside"
        outside.mkdir()
        victim = outside / "v_thumb.webp"
        victim.write_bytes(b"keep me")
        store = self.key_folder()
        store.rmdir()
        _winapi.CreateJunction(str(outside), str(store))
        make_png(self.output_dir / "v.png", size=(640, 480))
        from py.api.admin import _remove_thumbnail

        self.assertFalse(_remove_thumbnail(self.output_dir, Path("v.png")))
        resp = await self.client.post("/prompt_manager/images/clear-thumbnails")
        self.assertIn(resp.status, (200, 400))
        resp = await self.client.get(SERVE + "thumbnails/v_thumb.webp?root=0")
        self.assertIn(resp.status, (200, 403))
        self.assertEqual(victim.read_bytes(), b"keep me")
        self.assertEqual(sorted(p.name for p in outside.iterdir()), ["v_thumb.webp"])

    def link_key_folder_outside(self):
        """The source's key folder replaced by a link to a folder outside."""
        outside = self.comfy_dir / "outside"
        outside.mkdir()
        store = self.key_folder()
        store.rmdir()
        try:
            store.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks not permitted")
        return outside

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
    async def test_convert_never_plans_or_deletes_through_a_linked_key_folder(self):
        outside = self.link_key_folder_outside()
        old = outside / "c_thumb.png"
        old.write_bytes(b"keep me")
        make_png(self.output_dir / "c.png", size=(640, 480))
        # A linked store is refused before any planning (bot review, P1).
        self.assertIsNone(preview_store.ensure_store(self.output_dir))
        plan = self.api._plan_gallery_thumbnails([self.output_dir], mode="convert")
        self.assertEqual(plan["targets"], [])
        # And every delete passes the ownership check at the moment it runs.
        removed, kept = self.api._remove_old_previews([(old, 7)])
        self.assertEqual((removed, kept), (0, [old]))
        self.assertEqual(old.read_bytes(), b"keep me")

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
    async def test_the_temp_sweep_never_walks_into_a_link(self):
        from py.api import thumbnail_encode

        outside = self.comfy_dir / "outside"
        outside.mkdir()
        victim = outside / ".v_thumb.part-1-1.webp"
        victim.write_bytes(b"keep me")
        os.utime(victim, (1, 1))
        try:
            (self.key_folder() / "sub").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks not permitted")
        real_walk = os.walk

        def windows_walk(top, *args, **kwargs):
            # Windows walks into a junction even with followlinks=False.
            kwargs["followlinks"] = True
            return real_walk(top, *args, **kwargs)

        with patch.object(thumbnail_encode.os, "walk", windows_walk):
            self.assertEqual(self.api.sweep_thumbnail_temps(), 0)
            thumbnail_encode.sweep_stray_temps(self.key_folder(), min_age=0)
            resp = await self.client.post("/prompt_manager/images/clear-thumbnails")
        self.assertEqual(resp.status, 200)
        self.assertEqual(victim.read_bytes(), b"keep me")

    def test_the_temp_sweep_leaves_a_temp_the_store_does_not_own(self):
        from py.api import thumbnail_encode

        temp = self.key_folder() / ".w_thumb.part-1-1.webp"
        temp.write_bytes(b"x")
        with patch.object(preview_store, "may_write", return_value=False):
            self.assertEqual(
                thumbnail_encode.sweep_stray_temps(self.key_folder(), min_age=0), (0, 0)
            )
        self.assertTrue(temp.exists())

    async def test_a_write_outside_the_folder_is_refused(self):
        with self.assertRaises(OSError):
            preview_store.make_parent(self.comfy_dir / "elsewhere" / "a_thumb.webp")
        self.assertFalse((self.comfy_dir / "elsewhere").exists())

    async def test_an_unmounted_folder_takes_no_write_before_the_next_check(self):
        """An unmounted mount point is an empty folder: the flag may still
        say available, but without the marker nothing is made there."""
        store = self.key_folder()
        store.rmdir()
        (self.folder / preview_location.MARKER_NAME).unlink()
        self.assertTrue(preview_store.current().available)  # not checked yet
        with self.assertRaises(OSError):
            preview_store.make_parent(store / "sub" / "s_thumb.webp")
        self.assertEqual(list(self.folder.iterdir()), [])


class TestStartupNeverFallsBackToTheSources(AutoThumbnailCase):
    async def test_a_folder_setting_is_in_force_before_its_check(self):
        self.addCleanup(preview_store.configure, None)
        folder = self.comfy_dir / "thumbs"
        value = {"mode": "folder", "path": str(folder), "id": "x"}
        preview_store.configure(value, check=False)
        location = preview_store.current()
        self.assertEqual((location.mode, location.available), ("folder", False))
        make_png(self.output_dir / "t.png", size=(640, 480))
        resp = await self.client.get(SERVE + "thumbnails/t_thumb.webp?root=0")
        self.assertEqual(resp.status, 200)
        self.assertFalse((self.output_dir / "thumbnails").exists())

    async def test_a_failed_startup_check_leaves_the_folder_invalid(self):
        from py.config import PromptManagerConfig

        self.addCleanup(preview_store.configure, None)
        value = {"mode": "folder", "path": str(self.comfy_dir / "thumbs"), "id": "x"}
        with (
            patch.object(PromptManagerConfig, "THUMBNAIL_LOCATION", value),
            patch.object(
                type(self.api),
                "_thumbnail_location_context",
                side_effect=RuntimeError("boom"),
            ),
        ):
            self.api.start_preview_location()
        location = preview_store.current()
        self.assertEqual(
            (location.mode, location.available, location.problem),
            ("folder", False, "invalid"),
        )


class TestNoStoreIoOnTheEventLoop(FolderModeCase):
    async def test_legacy_listings_do_not_stat_the_folder(self):
        make_png(self.output_dir / "u.png", size=(64, 64))
        self.preview("u.png").write_bytes(b"x")
        calls = []
        real = thumbnail_settings.owned_in_folder
        with patch.object(
            thumbnail_settings,
            "owned_in_folder",
            lambda *a, **k: calls.append(threading.current_thread().name)
            or real(*a, **k),
        ):
            images = self.api._enrich_images(
                [{"image_path": str(self.output_dir / "u.png")}]
            )
        self.assertEqual(calls, [])
        self.assertEqual(images[0]["thumbnail_url"], SERVE + "thumbnails/u_thumb.webp")
