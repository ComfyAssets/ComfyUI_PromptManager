"""One resolver for where previews live on disk (#320 PR 1).

``py/api/preview_store.py`` owns every mapping between an image source, a
media file and its preview on disk. In source mode (the only mode so far)
it returns exactly the paths the code built by hand before
(``<source>/thumbnails/<rel>_thumb<ext>``), so nothing changes on disk. The
structural test keeps new ``thumbnails`` path joins out of the rest of the
backend.
"""

import asyncio
import os
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path
import unittest.mock
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

sys.modules.setdefault("server", MagicMock())

from py.api import preview_store, thumbnail_settings  # noqa: E402
from py.api.thumbnail_encode import (  # noqa: E402
    PreviewWriteHeld,
    replace_unless_held,
)

REPO = Path(__file__).resolve().parent.parent
WAIT = 10


class SourceModeCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "output"
        self.root.mkdir()


class TestSourceModePaths(SourceModeCase):
    def test_default_location_is_inside_each_source(self):
        location = preview_store.current()
        self.assertEqual(location.mode, preview_store.SOURCE_MODE)
        self.assertTrue(location.available)
        self.assertIsNone(location.problem)

    def test_store_of_a_source_is_its_thumbnails_folder(self):
        self.assertEqual(preview_store.store_dir(self.root), self.root / "thumbnails")

    def test_preview_path_matches_the_old_join(self):
        rel = Path("sub", "deep", "a.png")
        self.assertEqual(
            preview_store.preview_path(self.root, rel, ".webp"),
            self.root / "thumbnails/sub/deep/a_thumb.webp",
        )
        self.assertEqual(
            preview_store.preview_path(self.root, "a.mp4", ".jpg"),
            self.root / "thumbnails" / "a_thumb.jpg",
        )

    def test_preview_path_defaults_to_the_written_extension(self):
        from py.api.thumbnail_encode import writer_settings

        self.assertEqual(
            preview_store.preview_path(self.root, "a.png").suffix,
            writer_settings().extension,
        )

    def test_request_path_maps_url_parts_to_the_old_join(self):
        parts = ("thumbnails", "sub", "a_thumb.webp")
        self.assertEqual(
            preview_store.request_path(self.root, parts), self.root.joinpath(*parts)
        )

    def test_request_path_is_none_for_anything_but_preview_parts(self):
        self.assertIsNone(preview_store.request_path(self.root, ("sub", "a.png")))
        self.assertIsNone(preview_store.request_path(self.root, ("Thumbnails", "a")))
        self.assertIsNone(preview_store.request_path(self.root, ("thumbnails",)))

    def test_ensure_store_is_the_store(self):
        self.assertEqual(
            preview_store.ensure_store(self.root), self.root / "thumbnails"
        )

    def test_owns_a_preview_inside_the_store(self):
        inside = self.root / "thumbnails" / "sub" / "a_thumb.webp"
        self.assertTrue(preview_store.owns(self.root, inside))
        self.assertFalse(preview_store.owns(self.root, self.root / "a.png"))
        self.assertFalse(
            preview_store.owns(self.root, self.root.parent / "elsewhere.webp")
        )

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
    def test_owns_refuses_a_store_linked_out_of_the_source(self):
        outside = Path(self._tmp.name) / "outside"
        outside.mkdir()
        try:
            (self.root / "thumbnails").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks not permitted")
        self.assertFalse(
            preview_store.owns(self.root, self.root / "thumbnails" / "a_thumb.webp")
        )

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
    def test_owns_refuses_a_preview_folder_linked_out_of_the_store(self):
        outside = Path(self._tmp.name) / "outside"
        outside.mkdir()
        (self.root / "thumbnails").mkdir()
        try:
            (self.root / "thumbnails" / "sub").symlink_to(
                outside, target_is_directory=True
            )
        except OSError:
            self.skipTest("symlinks not permitted")
        self.assertFalse(
            preview_store.owns(
                self.root, self.root / "thumbnails" / "sub" / "a_thumb.webp"
            )
        )

    def test_store_container_is_the_source(self):
        self.assertEqual(preview_store.store_container(self.root), self.root)

    def test_given_store_argument_is_kept_unless_it_is_the_default(self):
        default = self.root / "thumbnails"
        other = Path(self._tmp.name) / "custom"
        self.assertEqual(preview_store.given_store(self.root, default), default)
        self.assertEqual(preview_store.given_store(self.root, other), other)


class TestPreviewText(unittest.TestCase):
    def test_preview_text_is_an_exact_thumbnails_prefix_and_preview_name(self):
        for text in (
            "thumbnails/a_thumb.webp",
            "thumbnails/sub/a_thumb.jxl",
            "thumbnails/a_thumb.JPG",
            "thumbnails/a_thumb.Png",
        ):
            with self.subTest(text=text):
                self.assertTrue(preview_store.is_preview_text(text))

    def test_other_text_is_not_preview_text(self):
        for text in (
            "Thumbnails/a_thumb.webp",
            "thumbnails/a.png",
            "thumbnails/_thumb.webp",
            "thumbnails/a_thumb.txt",
            "sub/thumbnails/a_thumb.webp",
            "thumbnails/../a_thumb.webp",
            "thumbnails//a_thumb.webp",
            "thumbnails/./a_thumb.webp",
            "thumbnails\\a_thumb.webp",
            "thumbnails/c:a_thumb.webp",
            "thumbnails/sub/thumbnails/a_thumb.webp",
            "thumbnails",
            "",
        ):
            with self.subTest(text=text):
                self.assertFalse(preview_store.is_preview_text(text))


class TestWritesAndTheSwitch(SourceModeCase):
    def _temp_and_dst(self):
        folder = self.root / "thumbnails"
        folder.mkdir()
        tmp = folder / ".a_thumb.part-1-1.webp"
        tmp.write_bytes(b"new")
        return tmp, folder / "a_thumb.webp"

    def test_a_write_from_the_current_generation_lands(self):
        tmp, dst = self._temp_and_dst()
        replace_unless_held(tmp, dst, preview_store.write_guard())
        self.assertEqual(dst.read_bytes(), b"new")

    def test_a_write_whose_generation_changed_is_dropped(self):
        tmp, dst = self._temp_and_dst()
        guard = preview_store.write_guard()
        preview_store.bump_generation()
        with self.assertRaises(PreviewWriteHeld):
            replace_unless_held(tmp, dst, guard)
        self.assertFalse(dst.exists())

    def test_the_hold_check_still_applies(self):
        tmp, dst = self._temp_and_dst()
        with self.assertRaises(PreviewWriteHeld):
            replace_unless_held(tmp, dst, preview_store.write_guard(lambda: False))
        self.assertFalse(dst.exists())

    def test_a_switch_waits_for_a_rename_in_flight(self):
        """The rename and the generation bump exclude each other: a bump that
        starts during a rename completes after it (event-driven)."""
        tmp, dst = self._temp_and_dst()
        in_check = threading.Event()
        release = threading.Event()

        def slow_hold():
            in_check.set()
            release.wait(WAIT)
            return True

        guard = preview_store.write_guard(slow_hold)
        writer = threading.Thread(target=replace_unless_held, args=(tmp, dst, guard))
        writer.start()
        self.assertTrue(in_check.wait(WAIT))
        bumped = threading.Event()
        bumper = threading.Thread(
            target=lambda: (preview_store.bump_generation(), bumped.set())
        )
        bumper.start()
        self.assertFalse(bumped.wait(0.05))  # blocked behind the rename
        release.set()
        writer.join(WAIT)
        bumper.join(WAIT)
        self.assertTrue(bumped.is_set())
        self.assertEqual(dst.read_bytes(), b"new")


class TestOnlyTheResolverBuildsStorePaths(unittest.TestCase):
    """No backend module joins ``thumbnails`` onto a path by hand any more."""

    ALLOWED = {
        Path("py/api/preview_store.py"),
        Path("py/api/thumbnail_settings.py"),  # URL text
    }
    # A path join with a literal "thumbnails" operand.
    JOIN = re.compile(
        r"""/\s*["']thumbnails["']"""
        r"""|["']thumbnails["']\s*/"""
        r"""|joinpath\([^)]*["']thumbnails["']"""
        r"""|os\.path\.join\([^)]*["']thumbnails["']"""
        r"""|["']thumbnails/"""
    )

    def test_no_thumbnails_join_outside_the_resolver(self):
        offenders = []
        for folder in ("py", "utils", "database"):
            for path in sorted((REPO / folder).rglob("*.py")):
                rel = path.relative_to(REPO)
                if rel in self.ALLOWED:
                    continue
                for number, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), 1
                ):
                    code = line.split("#", 1)[0]
                    if self.JOIN.search(code):
                        offenders.append(f"{rel}:{number}: {line.strip()}")
        self.assertEqual(offenders, [])


class TestSettingsLookupsFollowTheStore(SourceModeCase):
    def test_owned_thumbnails_reads_the_store(self):
        folder = self.root / "thumbnails" / "sub"
        folder.mkdir(parents=True)
        (folder / "a_thumb.webp").write_bytes(b"x")
        self.assertEqual(
            thumbnail_settings.owned_thumbnails(self.root, Path("sub", "a.png")),
            [folder / "a_thumb.webp"],
        )


try:
    from tests.test_thumbnail_auto import AutoThumbnailCase, _entry
    from tests.test_api_images import make_png
except ImportError:  # discovered with tests/ as the top-level directory
    from test_thumbnail_auto import AutoThumbnailCase, _entry
    from test_api_images import make_png


class RedirectedStoreCase(AutoThumbnailCase):
    """Every caller follows the resolver: with the store of the output root
    moved to ``<tmp>/elsewhere/output-key`` (a stand-in for a later mode),
    previews are read, written, listed and removed there and only there."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.container = self.comfy_dir / "elsewhere"
        self.store = self.container / "output-key"
        self.store.mkdir(parents=True)
        self.old_store = self.output_dir / "thumbnails"
        root = self.output_dir
        real_store = preview_store.store_dir
        real_container = preview_store.store_container

        def store_dir(r, location=None):
            return self.store if Path(r) == root else real_store(r, location)

        def store_container(r, location=None):
            if Path(r) == root:
                return self.container
            return real_container(r, location)

        for name, fake in (
            ("store_dir", store_dir),
            ("store_container", store_container),
        ):
            patcher = unittest.mock.patch.object(preview_store, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _preview(self, rel, ext=".webp"):
        return self.store / (thumbnail_settings.preview_stem(rel) + ext)


class TestEveryCallerFollowsTheStore(RedirectedStoreCase):
    async def test_an_existing_preview_is_served_from_the_store(self):
        make_png(self.output_dir / "sub" / "a.png", size=(64, 64))
        preview = self._preview("sub/a.png")
        make_png(preview, size=(32, 32))
        url = _entry(self.api, self.output_dir, "sub/a.png")["thumbnail_url"]
        self.assertIn("v=", url)  # the listing found it in the store
        resp = await self.client.get(url)
        self.assertEqual(resp.status, 200)
        self.assertEqual(await resp.read(), preview.read_bytes())

    async def test_a_missing_preview_is_made_in_the_store_on_first_view(self):
        make_png(self.output_dir / "sub" / "b.png", size=(640, 480))
        url = _entry(self.api, self.output_dir, "sub/b.png")["thumbnail_url"]
        resp = await self.client.get(url)
        self.assertEqual(resp.status, 200)
        self.assertTrue(self._preview("sub/b.png").is_file())
        self.assertFalse(self.old_store.exists())

    async def test_a_leftover_in_the_source_folder_is_not_served(self):
        make_png(self.output_dir / "c.png", size=(640, 480))
        leftover = self.old_store / "c_thumb.webp"
        leftover.parent.mkdir()
        leftover.write_bytes(b"old preview bytes")
        resp = await self.client.get(
            "/prompt_manager/images/serve/thumbnails/c_thumb.webp?root=0"
        )
        self.assertEqual(resp.status, 200)
        self.assertNotEqual(await resp.read(), b"old preview bytes")
        self.assertTrue(self._preview("c.png").is_file())

    async def test_capture_writes_into_the_store(self):
        path = make_png(self.output_dir / "run" / "d.png", size=(64, 64))
        self.assertEqual(self.api.queue_capture_thumbnail(str(path)), "queued")
        await self._idle()
        self.assertTrue(self._preview("run/d.png").is_file())
        self.assertFalse(self.old_store.exists())

    async def test_generate_and_clear_follow_the_store(self):
        make_png(self.output_dir / "e.png", size=(64, 64))
        payload = await asyncio.get_running_loop().run_in_executor(
            None, self.api._generate_gallery_thumbnails_sync, [self.output_dir]
        )
        self.assertTrue(payload["success"])
        self.assertTrue(self._preview("e.png").is_file())
        self.assertFalse(self.old_store.exists())
        self.assertTrue(payload["thumbnails_path"].endswith("output-key"))
        cleared, _size = self.api._clear_gallery_thumbnails_sync([self.output_dir])
        self.assertEqual(cleared, 1)
        self.assertFalse(self._preview("e.png").exists())
        self.assertTrue(self.store.is_dir())  # the store itself stays

    async def test_removing_a_file_removes_its_preview_in_the_store(self):
        from py.api.admin import _remove_thumbnail

        make_png(self.output_dir / "f.png", size=(64, 64))
        preview = self._preview("f.png")
        preview.write_bytes(b"x")
        self.assertTrue(_remove_thumbnail(self.output_dir, Path("f.png")))
        self.assertFalse(preview.exists())

    async def test_the_temp_sweep_walks_the_store(self):
        stray = self.store / ".g_thumb.part-1-1.webp"
        stray.write_bytes(b"x")
        old = os.path.getmtime(stray) - 10 * 24 * 3600
        os.utime(stray, (old, old))
        self.assertEqual(self.api.sweep_thumbnail_temps(), 1)
        self.assertFalse(stray.exists())

    async def test_history_cover_writes_into_the_store(self):
        from py.api import history_current

        self.assertTrue(
            history_current._preview_writable(self.output_dir, self._preview("h.png"))
        )
        self.assertFalse(
            history_current._preview_writable(
                self.output_dir, self.old_store / "h_thumb.webp"
            )
        )


class TestCaptureResolvesAtWriteTime(RedirectedStoreCase):
    def test_a_queued_capture_follows_a_store_that_moved_before_its_write(self):
        from py.api import thumbnail_auto as auto

        written = []
        thumbnailer = auto.AutoThumbnailer(
            lambda src, dst, *a, **k: written.append(dst) or {"action": "generated"},
            lambda: True,
            self.api.logger,
            start_worker=False,
        )
        self.addCleanup(thumbnailer.close)
        src = make_png(self.output_dir / "i.png", size=(32, 32))
        deferred = auto.DeferredPreview(self.output_dir, Path("i.png"))
        self.assertTrue(thumbnailer.enqueue(src, deferred, False))
        moved = self.container / "moved"
        moved.mkdir()
        self.store = moved  # the location changes before the worker runs
        thumbnailer._write_queued(thumbnailer._queue.popleft())
        self.assertEqual(written, [moved / "i_thumb.webp"])

    def test_serve_and_history_share_one_flight_key(self):
        from py.api import thumbnail_auto as auto

        parts = ("thumbnails", "sub", "a_thumb.webp")
        self.assertEqual(
            auto._request_key(self.output_dir, parts),
            auto._dst_key(self._preview("sub/a.png")),
        )


if __name__ == "__main__":
    unittest.main()
