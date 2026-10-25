"""Automatic thumbnails (#249): previews are a cache the server keeps itself.

The approved mock (docs/concepts/workspace/README.md) has no repeated global
"generate thumbnails" prompt, so the workspace never asks:

- every listing gives an image or video a thumbnail URL, existing or not;
- the serve route makes a missing preview on first view (bounded, single
  flight per file) and falls back to the original when it cannot, remembering
  the failure so a broken file is not retried on every view;
- the image monitor queues each new output's preview on one background worker;
- a scan that leaves many previews missing starts the visible thumbnail job.

Ordering is driven by threading/asyncio events, never by timers.
"""

import asyncio
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from tests.test_api_images import ThumbnailTestCase, make_png
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_images import ThumbnailTestCase, make_png

from PIL import Image  # noqa: E402

import py.api.images as images_module  # noqa: E402
import py.api.thumbnail_auto as auto  # noqa: E402
from py.api.jobs import THUMBNAILS_JOB_KIND  # noqa: E402

SERVE = "/prompt_manager/images/serve/"
WAIT = 10


def _entry(api, root, rel):
    return images_module._output_image_entry(root / rel, root, 0)


class AutoThumbnailCase(ThumbnailTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.addCleanup(self.api.close_auto_thumbnails)

    def _count_writes(self):
        """Count every preview write (and its thread) through _generate_one."""
        calls = []
        lock = threading.Lock()
        original = self.api._generate_one

        def counting(src, *args, **kwargs):
            with lock:
                calls.append(Path(src).name)
            return original(src, *args, **kwargs)

        self.api._generate_one = counting
        return calls

    def _gate_writes(self):
        """Hold every preview write until released: (entered, release, state).

        ``state`` counts the writers in hand (``active``), their ``peak`` and
        the ``threads`` they ran on; ``state["changed"]`` is notified at each
        change, so a test waits for a writer's own state (never a timer).
        """
        entered = threading.Event()
        release = threading.Event()
        changed = threading.Condition()
        state = {"active": 0, "peak": 0, "threads": set(), "changed": changed}
        original = self.api._generate_one

        def gated(*args, **kwargs):
            with changed:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
                state["threads"].add(threading.current_thread().name)
                changed.notify_all()
            entered.set()
            try:
                release.wait(WAIT)
                return original(*args, **kwargs)
            finally:
                with changed:
                    state["active"] -= 1
                    changed.notify_all()

        self.api._generate_one = gated
        self.addCleanup(release.set)
        return entered, release, state

    async def _writers(self, state, count):
        """Wait until exactly ``count`` writers are in hand."""
        changed = state["changed"]

        def wait():
            with changed:
                return changed.wait_for(lambda: state["active"] == count, WAIT)

        loop = asyncio.get_running_loop()
        self.assertTrue(await loop.run_in_executor(None, wait))

    async def _waiting(self, count):
        """Wait until ``count`` view requests wait on the preview gate."""
        gate = self.api._preview_gate()
        await asyncio.wait_for(gate.settled(lambda: gate.waiting >= count), WAIT)

    async def _wait(self, event):
        loop = asyncio.get_running_loop()
        self.assertTrue(await loop.run_in_executor(None, event.wait, WAIT))

    async def _idle(self):
        loop = asyncio.get_running_loop()
        thumbnailer = self.api._auto_thumbnails()
        self.assertTrue(await loop.run_in_executor(None, thumbnailer.wait_idle, WAIT))

    def _hold(self, kind, title="Database restore (admin page)", admission="records"):
        """A running, non-cancellable registry hold of ``kind`` until cleanup."""
        gate = asyncio.Event()

        async def runner(job):
            await gate.wait()

        job = self.api._jobs.start(
            kind, title, admission=admission, cancellable=False, runner=runner
        )

        async def release():
            gate.set()
            await asyncio.wait_for(job.task, 5)

        self.addAsyncCleanup(release)
        return job


class TestListingsAlwaysCarryAThumbnailUrl(AutoThumbnailCase):
    def test_missing_image_preview_still_gets_a_thumbnail_url(self):
        make_png(self.output_dir / "sub" / "new.png", size=(64, 64))
        entry = _entry(self.api, self.output_dir, "sub/new.png")
        self.assertEqual(
            entry["thumbnail_url"],
            f"{SERVE}thumbnails/sub/new_thumb.webp?root=0",
        )
        self.assertFalse((self.output_dir / "thumbnails").exists(), "no write")

    def test_missing_video_preview_url_is_in_the_configured_format(self):
        (self.output_dir / "clip.mp4").write_bytes(b"not really a video")
        entry = _entry(self.api, self.output_dir, "clip.mp4")
        self.assertEqual(
            entry["thumbnail_url"], f"{SERVE}thumbnails/clip_thumb.webp?root=0"
        )

    def test_listing_says_whether_the_preview_file_exists(self):
        # M9: the legacy gallery's dot reads thumbnail_ready.
        make_png(self.output_dir / "a.png", size=(64, 64))
        self.assertIs(
            _entry(self.api, self.output_dir, "a.png")["thumbnail_ready"], False
        )
        make_png(self.output_dir / "thumbnails" / "a_thumb.webp")
        self.assertIs(
            _entry(self.api, self.output_dir, "a.png")["thumbnail_ready"], True
        )

    def test_media_inside_a_nested_thumbnails_folder_keeps_its_original(self):
        # M10: such a file is never previewed; its card shows the original.
        make_png(self.output_dir / "project" / "thumbnails" / "x.png", size=(64, 64))
        entry = _entry(self.api, self.output_dir, "project/thumbnails/x.png")
        self.assertIsNone(entry["thumbnail_url"])
        self.assertIs(entry["thumbnail_ready"], False)

    def test_existing_preview_keeps_its_mtime_version(self):
        make_png(self.output_dir / "a.png", size=(64, 64))
        make_png(self.output_dir / "thumbnails" / "a_thumb.webp")
        entry = _entry(self.api, self.output_dir, "a.png")
        self.assertRegex(
            entry["thumbnail_url"],
            r"^/prompt_manager/images/serve/thumbnails/a_thumb\.webp\?v=\d+&root=0$",
        )

    async def test_workspace_listing_never_returns_a_null_thumbnail(self):
        for name in ("one.png", "two.png", "three.png"):
            make_png(self.output_dir / name, size=(64, 64))
        resp = await self.client.get("/prompt_manager/images/output?limit=10")
        data = await resp.json()
        self.assertTrue(data["success"], data)
        urls = [image["thumbnail_url"] for image in data["images"]]
        self.assertEqual(len(urls), 3)
        for url in urls:
            self.assertIn("_thumb.webp", url)


class TestThumbnailOnFirstView(AutoThumbnailCase):
    async def test_missing_preview_is_generated_and_served(self):
        make_png(self.output_dir / "sub" / "big.png", size=(1200, 900))
        url = _entry(self.api, self.output_dir, "sub/big.png")["thumbnail_url"]

        resp = await self.client.get(url)

        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers["Content-Type"], "image/webp")
        body = await resp.read()
        thumb = self.output_dir / "thumbnails" / "sub" / "big_thumb.webp"
        self.assertTrue(thumb.is_file())
        self.assertEqual(body, thumb.read_bytes())
        with Image.open(thumb) as img:
            self.assertEqual(max(img.size), 512)
        # No temp sibling is ever left next to the preview.
        self.assertEqual(os.listdir(thumb.parent), ["big_thumb.webp"])

    async def test_upper_case_extension_source_is_found(self):
        make_png(self.output_dir / "Shot.PNG", size=(64, 64))
        url = _entry(self.api, self.output_dir, "Shot.PNG")["thumbnail_url"]
        self.assertIn("thumbnails/Shot_thumb.webp", url)
        resp = await self.client.get(url)
        self.assertEqual(resp.status, 200)
        self.assertTrue((self.output_dir / "thumbnails" / "Shot_thumb.webp").is_file())

    async def test_video_preview_is_generated_in_the_configured_format(self):
        # #317: a video's preview is written like an image's (WebP default);
        # an undecodable clip's preview is the placeholder frame.
        (self.output_dir / "clip.mp4").write_bytes(b"not really a video")
        url = _entry(self.api, self.output_dir, "clip.mp4")["thumbnail_url"]
        resp = await self.client.get(url)
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers["Content-Type"], "image/webp")
        self.assertTrue((self.output_dir / "thumbnails" / "clip_thumb.webp").is_file())

    async def test_concurrent_views_generate_once(self):
        make_png(self.output_dir / "a.png", size=(256, 256))
        url = _entry(self.api, self.output_dir, "a.png")["thumbnail_url"]
        calls = self._count_writes()
        entered, release, _state = self._gate_writes()

        first = asyncio.ensure_future(self.client.get(url))
        await self._wait(entered)
        others = [asyncio.ensure_future(self.client.get(url)) for _ in range(4)]
        # Every follower waits on the leader's flight before it is released.
        await self._waiting(5)
        release.set()
        responses = [await first] + [await task for task in others]

        self.assertEqual([r.status for r in responses], [200] * 5)
        self.assertEqual(calls, ["a.png"])

    async def test_on_demand_generation_is_bounded(self):
        names = [f"p{i}.png" for i in range(auto.ON_DEMAND_LIMIT + 2)]
        for name in names:
            make_png(self.output_dir / name, size=(64, 64))
        entered, release, state = self._gate_writes()
        tasks = [
            asyncio.ensure_future(
                self.client.get(_entry(self.api, self.output_dir, n)["thumbnail_url"])
            )
            for n in names
        ]
        # M8: the third writer's own state, then the two queued requests.
        await self._writers(state, auto.ON_DEMAND_LIMIT)
        await self._waiting(len(names))
        self.assertEqual(state["active"], auto.ON_DEMAND_LIMIT)
        release.set()
        responses = [await task for task in tasks]
        self.assertEqual([r.status for r in responses], [200] * len(names))
        self.assertLessEqual(state["peak"], auto.ON_DEMAND_LIMIT)

    async def test_a_burst_never_holds_default_executor_threads(self):
        # I2: waits are coroutines and writes run on the preview pool, so a
        # burst of distinct missing previews leaves every other route free.
        names = [f"b{i}.png" for i in range(auto.ON_DEMAND_LIMIT + 4)]
        for name in names:
            make_png(self.output_dir / name, size=(64, 64))
        entered, release, state = self._gate_writes()
        default_calls = []
        original = self.api._run_in_executor

        async def spy(func, *args, **kwargs):
            default_calls.append(getattr(func, "__name__", repr(func)))
            return await original(func, *args, **kwargs)

        self.api._run_in_executor = spy
        tasks = [
            asyncio.ensure_future(
                self.client.get(_entry(self.api, self.output_dir, n)["thumbnail_url"])
            )
            for n in names
        ]
        await self._writers(state, auto.ON_DEMAND_LIMIT)
        await self._waiting(len(names))
        self.assertEqual(default_calls, [], "no view waits on a default thread")
        self.assertTrue(all(name.startswith("pm-preview") for name in state["threads"]))
        status = await self.client.get("/prompt_manager/images/thumbnails/status")
        self.assertEqual(status.status, 200, "another route answers meanwhile")
        await status.read()
        release.set()
        responses = [await task for task in tasks]
        self.assertEqual([r.status for r in responses], [200] * len(names))
        for name in names:
            stem = name[:-4]
            self.assertTrue(
                (self.output_dir / "thumbnails" / f"{stem}_thumb.webp").is_file()
            )

    async def test_requests_beyond_the_cap_get_the_fallback_at_once(self):
        names = [f"c{i}.png" for i in range(auto.ON_DEMAND_LIMIT)]
        for name in names + ["late.png"]:
            make_png(self.output_dir / name, size=(64, 64))
        # Another stem: same-stem files share one preview name (#317 R2).
        (self.output_dir / "latev.mp4").write_bytes(b"not a video")
        entered, release, state = self._gate_writes()
        listings = []
        real_scandir = auto.os.scandir

        def counting(path):
            listings.append(str(path))
            return real_scandir(path)

        with (
            patch.object(auto, "MAX_WAITING", auto.ON_DEMAND_LIMIT),
            patch.object(auto.os, "scandir", counting),
        ):
            tasks = [
                asyncio.ensure_future(
                    self.client.get(
                        _entry(self.api, self.output_dir, n)["thumbnail_url"]
                    )
                )
                for n in names
            ]
            await self._writers(state, auto.ON_DEMAND_LIMIT)
            image = await self.client.get(f"{SERVE}thumbnails/late_thumb.webp?root=0")
            self.assertEqual(image.status, 200)
            self.assertEqual(image.headers["Cache-Control"], "no-store")
            self.assertEqual(
                await image.read(), (self.output_dir / "late.png").read_bytes()
            )
            video = await self.client.get(f"{SERVE}thumbnails/latev_thumb.webp?root=0")
            self.assertEqual(video.status, 200)
            self.assertEqual(video.headers["Content-Type"], "image/jpeg")
            self.assertEqual(video.headers["Cache-Control"], "no-store")
            await video.read()
            missing = await self.client.get(f"{SERVE}thumbnails/Nope_thumb.webp?root=0")
            self.assertEqual(missing.status, 404)
            # M2: no folder is listed for a request the cap turned away.
            self.assertEqual(listings, [])
            release.set()
            responses = [await task for task in tasks]
        self.assertEqual([r.status for r in responses], [200] * len(names))
        self.assertFalse((self.output_dir / "thumbnails" / "late_thumb.webp").exists())

    async def test_mixed_case_folder_listing_runs_once_inside_admission(self):
        make_png(self.output_dir / "Shot.Png", size=(64, 64))
        listings = []
        real_scandir = auto.os.scandir

        def counting(path):
            listings.append(str(path))
            return real_scandir(path)

        with patch.object(auto.os, "scandir", counting):
            resp = await self.client.get(f"{SERVE}thumbnails/Shot_thumb.webp?root=0")
        self.assertEqual(resp.status, 200)
        # A case-insensitive filesystem (macOS, Windows) finds it by its exact
        # name; a case-sensitive one lists the folder once.
        found_by_name = (self.output_dir / "Shot.png").exists()
        self.assertEqual(len(listings), 0 if found_by_name else 1)
        self.assertTrue((self.output_dir / "thumbnails" / "Shot_thumb.webp").is_file())

    async def test_broken_file_falls_back_to_the_original_and_is_remembered(self):
        broken = self.output_dir / "broken.png"
        broken.write_bytes(b"\x89PNG\r\n\x1a\n not an image")
        url = _entry(self.api, self.output_dir, "broken.png")["thumbnail_url"]
        calls = self._count_writes()

        first = await self.client.get(url)
        second = await self.client.get(url)

        for resp in (first, second):
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers["Content-Type"], "image/png")
            self.assertEqual(resp.headers["Cache-Control"], "no-store")
            self.assertEqual(await resp.read(), broken.read_bytes())
        self.assertEqual(calls, ["broken.png"], "a known failure is not retried")
        self.assertFalse(
            (self.output_dir / "thumbnails" / "broken_thumb.webp").exists()
        )

        status = await (
            await self.client.get("/prompt_manager/images/thumbnails/status")
        ).json()
        self.assertEqual(status["failed"], 1)

        # A changed file is tried again.
        later = broken.stat().st_mtime + 5
        make_png(broken, size=(64, 64))
        os.utime(broken, (later, later))
        third = await self.client.get(url)
        self.assertEqual(third.status, 200)
        self.assertEqual(calls, ["broken.png", "broken.png"])
        self.assertTrue(
            (self.output_dir / "thumbnails" / "broken_thumb.webp").is_file()
        )

    async def test_locked_preview_is_not_remembered_as_a_failure(self):
        make_png(self.output_dir / "a.png", size=(64, 64))
        url = _entry(self.api, self.output_dir, "a.png")["thumbnail_url"]
        locked = PermissionError(13, "in use")
        locked.winerror = 32

        with patch.object(images_module.os, "replace", side_effect=locked):
            resp = await self.client.get(url)
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers["Cache-Control"], "no-store")
            await resp.read()
        self.assertEqual(self.api._auto_thumbnails().failure_count, 0)

        resp = await self.client.get(url)
        self.assertEqual(resp.status, 200)
        self.assertTrue((self.output_dir / "thumbnails" / "a_thumb.webp").is_file())

    async def test_no_source_is_a_plain_uncacheable_404(self):
        resp = await self.client.get(f"{SERVE}thumbnails/nope_thumb.webp?root=0")
        self.assertEqual(resp.status, 404)
        self.assertEqual(resp.headers["Cache-Control"], "no-store")
        text = await resp.text()
        self.assertNotIn(str(self.output_dir), text)
        self.assertFalse((self.output_dir / "thumbnails").exists())

    def test_drive_unc_and_absolute_segments_are_refused_as_text(self):
        # M1: a drive or UNC segment would reset the anchor of a joined path.
        for path in (
            "thumbnails/D:/x_thumb.webp",
            "thumbnails/D:x_thumb.webp",
            "thumbnails/\\\\server\\share/x_thumb.webp",
            "thumbnails//server/share/x_thumb.webp",
            "/thumbnails/x_thumb.webp",
            "thumbnails/a/./x_thumb.webp",
            "thumbnails/C:\\Users\\x_thumb.webp",
        ):
            with self.subTest(path=path):
                self.assertFalse(auto._is_preview_request(path))

    async def test_refused_segments_never_reach_the_filesystem(self):
        with (
            patch.object(auto, "_thumbnail_source") as source,
            patch.object(auto, "_quick_source") as quick,
            patch.object(auto.os, "stat") as stat,
        ):
            for path in (
                "thumbnails/D:/x_thumb.webp",
                "thumbnails/\\\\server\\share/x_thumb.webp",
                "/thumbnails/x_thumb.webp",
            ):
                answer = await self.api._serve_missing_thumbnail(
                    [self.output_dir], path
                )
                self.assertIsNone(answer)
            source.assert_not_called()
            quick.assert_not_called()
            stat.assert_not_called()

    async def test_traversal_is_refused_before_any_filesystem_access(self):
        with patch.object(auto, "_thumbnail_source") as source:
            for path in (
                "thumbnails/../../etc/passwd_thumb.webp",
                "thumbnails/..%2F..%2Fx_thumb.webp",
            ):
                resp = await self.client.get(f"{SERVE}{path}?root=0")
                self.assertIn(resp.status, (403, 404))
            source.assert_not_called()

    async def test_symlinked_source_is_never_read(self):
        outside = Path(self._output_tmp.name) / "outside.png"
        make_png(outside, size=(64, 64))
        self._make_symlink(outside, self.output_dir / "link.png")
        resp = await self.client.get(f"{SERVE}thumbnails/link_thumb.webp?root=0")
        self.assertEqual(resp.status, 404)
        self.assertFalse((self.output_dir / "thumbnails").exists())

    async def test_thumbnails_folder_linked_out_of_the_root_is_never_written(self):
        make_png(self.output_dir / "a.png", size=(64, 64))
        elsewhere = Path(self._output_tmp.name) / "elsewhere"
        elsewhere.mkdir()
        self._make_symlink(elsewhere, self.output_dir / "thumbnails")
        resp = await self.client.get(f"{SERVE}thumbnails/a_thumb.webp?root=0")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers["Cache-Control"], "no-store")
        self.assertEqual(os.listdir(elsewhere), [])

    async def test_restore_hold_serves_the_original_and_writes_nothing(self):
        from py.api.jobs import RESTORE_JOB_KIND

        make_png(self.output_dir / "a.png", size=(64, 64))
        url = _entry(self.api, self.output_dir, "a.png")["thumbnail_url"]
        self._hold(RESTORE_JOB_KIND)
        resp = await self.client.get(url)
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers["Cache-Control"], "no-store")
        self.assertEqual(await resp.read(), (self.output_dir / "a.png").read_bytes())
        self.assertFalse((self.output_dir / "thumbnails").exists())
        self.assertEqual(self.api._auto_thumbnails().failure_count, 0)

    async def test_duplicate_removal_and_source_change_hold_writes(self):
        from py.api.jobs import DUPLICATE_CLEANUP_JOB_KIND
        from py.api.settings_routes import SOURCE_CHANGE_JOB_KIND

        make_png(self.output_dir / "a.png", size=(64, 64))
        for kind in (DUPLICATE_CLEANUP_JOB_KIND, SOURCE_CHANGE_JOB_KIND):
            with self.subTest(kind=kind):
                job = self._hold(kind, title=kind)
                self.assertIs(self.api._auto_thumbnail_blocker(), job)
                job.finish("finished")
        self.assertIsNone(self.api._auto_thumbnail_blocker())

    async def test_scan_and_thumbnail_jobs_do_not_hold_writes(self):
        # A scan writes records only and the thumbnail job writes the same
        # previews atomically, so a view during either still gets its preview.
        holds = (
            ("scan", "Scan for metadata", "records"),
            ("image-tags", "Add tags", "records"),
            (THUMBNAILS_JOB_KIND, "Thumbnail generation", "guarded"),
        )
        for index, (kind, title, admission) in enumerate(holds):
            with self.subTest(kind=kind):
                name = f"a{index}.png"
                make_png(self.output_dir / name, size=(64, 64))
                job = self._hold(kind, title=title, admission=admission)
                resp = await self.client.get(
                    _entry(self.api, self.output_dir, name)["thumbnail_url"]
                )
                self.assertEqual(resp.status, 200)
                thumb = self.output_dir / "thumbnails" / f"a{index}_thumb.webp"
                self.assertTrue(thumb.is_file())
                job.finish("finished")

    async def test_legacy_clear_holds_writes(self):
        make_png(self.output_dir / "a.png", size=(64, 64))
        self._hold(
            THUMBNAILS_JOB_KIND,
            title=images_module.THUMBNAIL_CLEAR_TITLE,
            admission="guarded",
        )
        resp = await self.client.get(
            _entry(self.api, self.output_dir, "a.png")["thumbnail_url"]
        )
        self.assertEqual(resp.status, 200)
        self.assertFalse((self.output_dir / "thumbnails").exists())


class TestVideoPreviewsNeverFallBackToTheVideo(AutoThumbnailCase):
    """I1: a video preview that cannot be made is the placeholder frame."""

    def _video(self, name="clip.webm"):
        path = self.output_dir / name
        path.write_bytes(b"AV1 stream no decoder here")
        return path

    async def _assert_placeholder(self, url):
        resp = await self.client.get(url)
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers["Content-Type"], "image/jpeg")
        body = await resp.read()
        self.assertTrue(body.startswith(b"\xff\xd8"))
        return resp

    async def test_opencv_that_cannot_open_the_file_falls_through_to_a_frame(self):
        from unittest.mock import MagicMock

        self._video()
        cv2 = MagicMock()
        cv2.VideoCapture.return_value.isOpened.return_value = False
        with patch.dict(sys.modules, {"cv2": cv2}):
            resp = await self.client.get(f"{SERVE}thumbnails/clip_thumb.webp?root=0")
        # The placeholder frame became the preview file, in the format (#317).
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers["Content-Type"], "image/webp")
        made = self.output_dir / "thumbnails" / "clip_thumb.webp"
        self.assertEqual(await resp.read(), made.read_bytes())

    async def test_a_held_video_preview_is_the_placeholder_and_writes_nothing(self):
        from py.api.jobs import RESTORE_JOB_KIND

        self._video()
        self._hold(RESTORE_JOB_KIND)
        resp = await self._assert_placeholder(
            f"{SERVE}thumbnails/clip_thumb.webp?root=0"
        )
        self.assertEqual(resp.headers["Cache-Control"], "no-store")
        self.assertFalse((self.output_dir / "thumbnails").exists())

    async def test_a_linked_out_folder_gives_a_video_the_placeholder(self):
        self._video()
        elsewhere = Path(self._output_tmp.name) / "elsewhere"
        elsewhere.mkdir()
        self._make_symlink(elsewhere, self.output_dir / "thumbnails")
        resp = await self._assert_placeholder(
            f"{SERVE}thumbnails/clip_thumb.webp?root=0"
        )
        self.assertEqual(resp.headers["Cache-Control"], "no-store")
        self.assertEqual(os.listdir(elsewhere), [])

    async def test_a_failed_video_preview_is_the_placeholder(self):
        self._video()
        with patch.object(self.api, "_generate_video_thumbnail", return_value=False):
            resp = await self._assert_placeholder(
                f"{SERVE}thumbnails/clip_thumb.webp?root=0"
            )
        self.assertEqual(resp.headers["Cache-Control"], "no-store")


class TestWriteSafety(AutoThumbnailCase):
    async def test_a_hold_that_starts_while_waiting_for_a_slot_stops_the_write(self):
        # M3: the holds are checked again once the slot is taken.
        from py.api.jobs import RESTORE_JOB_KIND

        names = [f"h{i}.png" for i in range(auto.ON_DEMAND_LIMIT + 1)]
        for name in names:
            make_png(self.output_dir / name, size=(64, 64))
        calls = self._count_writes()
        entered, release, state = self._gate_writes()
        tasks = [
            asyncio.ensure_future(
                self.client.get(_entry(self.api, self.output_dir, n)["thumbnail_url"])
            )
            for n in names
        ]
        await self._writers(state, auto.ON_DEMAND_LIMIT)
        await self._waiting(len(names))
        self._hold(RESTORE_JOB_KIND)
        release.set()
        responses = [await task for task in tasks]
        self.assertEqual([r.status for r in responses], [200] * len(names))
        self.assertEqual(len(calls), auto.ON_DEMAND_LIMIT, "the late one never wrote")
        # ... and once more before the rename: the gated writers wrote nothing.
        thumbs = self.output_dir / "thumbnails"
        self.assertEqual(sorted(os.listdir(thumbs)) if thumbs.exists() else [], [])

    async def test_disk_errors_are_not_remembered_but_decode_errors_are(self):
        # M4: only a file that cannot be decoded is remembered.
        import errno

        make_png(self.output_dir / "full.png", size=(64, 64))
        url = _entry(self.api, self.output_dir, "full.png")["thumbnail_url"]
        with patch.object(
            images_module,
            "_write_image_thumbnail",
            side_effect=OSError(errno.ENOSPC, "No space left on device"),
        ):
            resp = await self.client.get(url)
            self.assertEqual(resp.status, 200)
            await resp.read()
        self.assertEqual(self.api._auto_thumbnails().failure_count, 0)
        truncated = self.output_dir / "cut.png"
        data = make_png(self.output_dir / "whole.png", size=(256, 256)).read_bytes()
        truncated.write_bytes(data[: len(data) // 2])
        resp = await self.client.get(
            _entry(self.api, self.output_dir, "cut.png")["thumbnail_url"]
        )
        self.assertEqual(await resp.read(), truncated.read_bytes())
        self.assertEqual(self.api._auto_thumbnails().failure_count, 1)


class TestShutdown(AutoThumbnailCase):
    async def test_no_thread_remains_after_cleanup_and_capture_cannot_restart(self):
        from py.api import thumbnail_encode

        self.addCleanup(thumbnail_encode.reset_encoders)
        path = make_png(self.output_dir / "a.png", size=(64, 64))
        self.assertEqual(self.api.queue_capture_thumbnail(str(path)), "queued")
        thumbnailer = self.api._auto_thumbnails()
        worker = thumbnailer._worker
        await self._idle()
        make_png(self.output_dir / "viewed.png", size=(64, 64))
        resp = await self.client.get(
            _entry(self.api, self.output_dir, "viewed.png")["thumbnail_url"]
        )
        await resp.read()
        pool_threads = list(self.api._preview_pool_threads())
        self.assertTrue(pool_threads)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, lambda: self.api.close_auto_thumbnails(final=True)
        )
        for thread in [worker, *pool_threads]:
            await loop.run_in_executor(None, thread.join, WAIT)
            self.assertFalse(thread.is_alive(), thread.name)
        other = make_png(self.output_dir / "b.png", size=(64, 64))
        self.assertEqual(self.api.queue_capture_thumbnail(str(other)), "skipped")
        restarted = self.api._auto_thumbnailer
        self.assertTrue(restarted is None or restarted._worker is None)
        # #317: no encoder process (ffmpeg, cjxl) starts after the cleanup.
        with self.assertRaises(thumbnail_encode.EncoderClosed):
            thumbnail_encode.run_encoder([sys.executable, "-c", "pass"])

    async def test_an_idle_worker_leaves(self):
        path = make_png(self.output_dir / "a.png", size=(64, 64))
        self.api.queue_capture_thumbnail(str(path))
        worker = self.api._auto_thumbnails()._worker
        await self._idle()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, worker.join, WAIT)
        self.assertFalse(worker.is_alive())
        # The next output starts a fresh worker.
        other = make_png(self.output_dir / "b.png", size=(64, 64))
        self.assertEqual(self.api.queue_capture_thumbnail(str(other)), "queued")
        await self._idle()
        self.assertTrue((self.output_dir / "thumbnails" / "b_thumb.webp").is_file())

    async def test_a_reload_stops_the_old_worker(self):
        entered, release, _state = self._gate_writes()
        path = make_png(self.output_dir / "a.png", size=(64, 64))
        self.api.queue_capture_thumbnail(str(path))
        await self._wait(entered)
        old = self.api._auto_thumbnails()._worker
        old.pm_preview_token = object()  # as the module before a reload left it
        loop = asyncio.get_running_loop()
        stopping = loop.run_in_executor(None, auto._stop_stale_workers)
        release.set()
        await stopping
        await loop.run_in_executor(None, old.join, WAIT)
        self.assertFalse(old.is_alive())
        self.assertTrue(self.api._auto_thumbnails().closed)

    async def test_close_kills_ffmpeg_and_logs_a_join_that_timed_out(self):
        from unittest.mock import MagicMock

        entered, release, _state = self._gate_writes()
        path = make_png(self.output_dir / "a.png", size=(64, 64))
        self.api.queue_capture_thumbnail(str(path))
        await self._wait(entered)
        thumbnailer = self.api._auto_thumbnails()
        process = MagicMock()
        thumbnailer._track(process)
        with (
            patch.object(auto, "CLOSE_WAIT_SECONDS", 0),
            self.assertLogs("prompt_manager.api", "WARNING") as logs,
        ):
            self.api.close_auto_thumbnails()
        process.kill.assert_called_once()
        self.assertIn("still running", "\n".join(logs.output))
        release.set()


class TestHistorySharesTheAdmission(AutoThumbnailCase):
    async def _shared_flight(self, name, is_video, first):
        """Start the history route's or the serve route's flight (``first``),
        let the other one join it, release, and return both answers."""
        src = self.output_dir / name
        if is_video:
            src.write_bytes(b"AV1 stream no decoder here")
        else:
            make_png(src, size=(256, 256))
        url = _entry(self.api, self.output_dir, name)["thumbnail_url"]
        dst = self.output_dir / "thumbnails" / Path(url.split("?")[0]).name
        calls = self._count_writes()
        entered, release, _state = self._gate_writes()

        def history():
            return asyncio.ensure_future(self.api._admit_preview(src, dst, is_video))

        def view():
            return asyncio.ensure_future(self.client.get(url))

        leader = history() if first == "history" else view()
        await self._wait(entered)
        follower = view() if first == "history" else history()
        await self._waiting(2)
        release.set()
        answers = {first: await leader}
        answers["view" if first == "history" else "history"] = await follower
        self.assertEqual(len(calls), 1, "one flight, one write")
        return answers, dst

    async def test_a_view_joining_a_history_flight_gets_the_thumbnail(self):
        answers, dst = await self._shared_flight("a.png", False, "history")
        self.assertEqual(answers["history"], auto.READY)
        resp = answers["view"]
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers["Content-Type"], "image/webp")
        self.assertEqual(await resp.read(), dst.read_bytes())

    async def test_history_joining_a_view_flight_gets_the_outcome(self):
        answers, dst = await self._shared_flight("b.png", False, "view")
        self.assertEqual(answers["history"], auto.READY)
        self.assertEqual(await answers["view"].read(), dst.read_bytes())

    async def test_a_held_shared_flight_answers_each_route_in_its_own_type(self):
        from py.api.jobs import RESTORE_JOB_KIND

        make_png(self.output_dir / "c.png", size=(64, 64))
        # Another stem: same-stem files share one preview name (#317 R2).
        (self.output_dir / "cv.mp4").write_bytes(b"AV1 stream no decoder here")
        self._hold(RESTORE_JOB_KIND)
        for name, is_video in (("c.png", False), ("cv.mp4", True)):
            with self.subTest(name=name):
                src = self.output_dir / name
                url = _entry(self.api, self.output_dir, name)["thumbnail_url"]
                dst = self.output_dir / "thumbnails" / Path(url.split("?")[0]).name
                history = asyncio.ensure_future(
                    self.api._admit_preview(src, dst, is_video)
                )
                resp = await self.client.get(url)
                self.assertEqual(await history, auto.DEFERRED)
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.headers["Cache-Control"], "no-store")
                body = await resp.read()
                if is_video:
                    self.assertEqual(resp.headers["Content-Type"], "image/jpeg")
                    self.assertTrue(body.startswith(b"\xff\xd8"))
                else:
                    self.assertEqual(resp.headers["Content-Type"], "image/png")
                    self.assertEqual(body, src.read_bytes())

    def test_history_and_serve_route_share_one_flight_key(self):
        root = self.output_dir
        preview = root / "thumbnails" / "sub" / "a_thumb.webp"
        self.assertEqual(
            auto.flight_key(root, ("thumbnails", "sub", "a_thumb.webp")),
            auto.flight_key(
                Path(os.path.normcase(os.path.realpath(root))),
                preview.relative_to(root).parts,
            ),
        )


class TestThumbnailOnCapture(AutoThumbnailCase):
    async def test_monitor_output_is_queued_and_generated_in_the_background(self):
        path = make_png(self.output_dir / "run" / "ComfyUI_0001.png", size=(640, 480))
        outcome = self.api.queue_capture_thumbnail(str(path))
        self.assertEqual(outcome, "queued")
        await self._idle()
        thumb = self.output_dir / "thumbnails" / "run" / "ComfyUI_0001_thumb.webp"
        self.assertTrue(thumb.is_file())
        self.assertIn(
            "v=",
            _entry(self.api, self.output_dir, "run/ComfyUI_0001.png")["thumbnail_url"],
        )

    async def test_duplicates_coalesce_and_fresh_previews_are_skipped(self):
        path = make_png(self.output_dir / "a.png", size=(64, 64))
        calls = self._count_writes()
        entered, release, _state = self._gate_writes()
        self.assertEqual(self.api.queue_capture_thumbnail(str(path)), "queued")
        await self._wait(entered)
        # While it is being written a second event for it is not queued again.
        other = make_png(self.output_dir / "b.png", size=(64, 64))
        self.assertEqual(self.api.queue_capture_thumbnail(str(other)), "queued")
        self.assertEqual(self.api.queue_capture_thumbnail(str(other)), "skipped")
        release.set()
        await self._idle()
        self.assertEqual(sorted(calls), ["a.png", "b.png"])
        self.assertEqual(self.api.queue_capture_thumbnail(str(path)), "fresh")

    async def test_files_outside_the_roots_and_previews_are_ignored(self):
        outside = make_png(Path(self._output_tmp.name) / "x.png")
        preview = make_png(self.output_dir / "thumbnails" / "y_thumb.webp")
        text = self.output_dir / "notes.txt"
        text.write_text("x")
        for path in (outside, preview, text, self.output_dir / "missing.png"):
            self.assertEqual(self.api.queue_capture_thumbnail(str(path)), "skipped")

    async def test_worker_uses_the_worker_threads_setting(self):
        from py.config import PromptManagerConfig

        original = PromptManagerConfig.WORKER_THREADS
        PromptManagerConfig.WORKER_THREADS = 2
        self.addCleanup(setattr, PromptManagerConfig, "WORKER_THREADS", original)
        batches = []
        real_map = auto.map_parallel

        def recording(func, items, workers):
            batches.append((len(items), workers))
            return real_map(func, items, workers)

        patcher = patch.object(auto, "map_parallel", recording)
        patcher.start()
        self.addCleanup(patcher.stop)
        entered, release, state = self._gate_writes()
        paths = [
            make_png(self.output_dir / f"w{i}.png", size=(32, 32)) for i in range(5)
        ]
        self.api.queue_capture_thumbnail(str(paths[0]))
        await self._wait(entered)  # the worker holds a batch of one
        for path in paths[1:]:
            self.api.queue_capture_thumbnail(str(path))
        release.set()
        await self._idle()
        self.assertEqual(batches, [(1, 1), (2, 2), (2, 2)])
        self.assertLessEqual(state["peak"], 2)
        for i in range(5):
            self.assertTrue(
                (self.output_dir / "thumbnails" / f"w{i}_thumb.webp").is_file()
            )

    async def test_worker_survives_a_failing_write(self):
        bad = self.output_dir / "bad.png"
        bad.write_bytes(b"broken")
        good = make_png(self.output_dir / "good.png", size=(64, 64))
        self.api.queue_capture_thumbnail(str(bad))
        await self._idle()
        self.api.queue_capture_thumbnail(str(good))
        await self._idle()
        self.assertTrue((self.output_dir / "thumbnails" / "good_thumb.webp").is_file())
        self.assertEqual(self.api._auto_thumbnails().failure_count, 1)

    async def test_worker_writes_nothing_during_a_restore(self):
        from py.api.jobs import RESTORE_JOB_KIND

        path = make_png(self.output_dir / "a.png", size=(64, 64))
        self._hold(RESTORE_JOB_KIND)
        self.api.queue_capture_thumbnail(str(path))
        await self._idle()
        self.assertFalse((self.output_dir / "thumbnails").exists())

    async def test_a_worker_that_raises_never_blocks_the_next_one(self):
        # A raising config read stops the worker; the next enqueue starts one.
        thumbnailer = self.api._auto_thumbnails()
        real_workers = thumbnailer._workers
        failures = iter([RuntimeError("config unreadable")])

        def workers():
            failure = next(failures, None)
            if failure is not None:
                raise failure
            return real_workers()

        thumbnailer._workers = workers
        first = make_png(self.output_dir / "first.png", size=(32, 32))
        with self.assertLogs("prompt_manager.api", "WARNING"):
            self.assertEqual(self.api.queue_capture_thumbnail(str(first)), "queued")
            await self._idle()
        self.assertFalse(thumbnailer._worker_running)
        second = make_png(self.output_dir / "second.png", size=(32, 32))
        self.assertEqual(self.api.queue_capture_thumbnail(str(second)), "queued")
        await self._idle()
        for name in ("first", "second"):
            self.assertTrue(
                (self.output_dir / "thumbnails" / f"{name}_thumb.webp").is_file()
            )

    async def test_a_worker_that_cannot_start_is_started_by_the_next_enqueue(self):
        thumbnailer = self.api._auto_thumbnails()
        first = make_png(self.output_dir / "first.png", size=(32, 32))
        with (
            patch.object(
                threading.Thread,
                "start",
                side_effect=RuntimeError("can't start new thread"),
            ),
            self.assertLogs("prompt_manager.api", "WARNING"),
        ):
            self.assertEqual(self.api.queue_capture_thumbnail(str(first)), "queued")
        self.assertFalse(thumbnailer._worker_running)
        second = make_png(self.output_dir / "second.png", size=(32, 32))
        self.assertEqual(self.api.queue_capture_thumbnail(str(second)), "queued")
        await self._idle()
        for name in ("first", "second"):
            self.assertTrue(
                (self.output_dir / "thumbnails" / f"{name}_thumb.webp").is_file()
            )

    def test_queue_is_bounded(self):
        thumbnailer = auto.AutoThumbnailer(
            lambda *a: {"action": "generated"},
            lambda: True,
            self.api.logger,
            max_queued=2,
            start_worker=False,
        )
        paths = [Path(f"/x/{i}.png") for i in range(3)]
        added = [thumbnailer.enqueue(p, p.with_suffix(".t.png"), False) for p in paths]
        self.assertEqual(added, [True, True, False])
        thumbnailer.close()


class TestMonitorQueuesThumbnails(AutoThumbnailCase):
    def _handler(self):
        from utils.image_monitor import ImageGenerationHandler

        handler = ImageGenerationHandler(self.api.db, unittest.mock.MagicMock())
        handler.processing_delay = 0
        handler.settle_interval = 0
        return handler

    def test_settled_output_is_handed_to_the_thumbnail_queue(self):
        path = make_png(self.output_dir / "n.png", size=(32, 32))
        handler = self._handler()
        calls = []
        handler.thumbnail_queue = calls.append
        with patch.object(handler, "process_new_image"):
            handler._pending[str(path)] = (None, 0.0)
            handler._process_pending(str(path))
        self.assertEqual(calls, [str(path)])

    def test_a_failing_queue_never_breaks_the_monitor(self):
        path = make_png(self.output_dir / "n.png", size=(32, 32))
        handler = self._handler()

        def broken(_path):
            raise RuntimeError("boom")

        handler.thumbnail_queue = broken
        with patch.object(handler, "process_new_image"):
            handler._pending[str(path)] = (None, 0.0)
            with self.assertLogs("prompt_manager.image_monitor", "WARNING") as logs:
                handler._process_pending(str(path))  # no exception
        self.assertIn("could not be queued", "\n".join(logs.output))
        self.assertEqual(handler.pending_count(), 0)

    def test_monitor_wiring_reaches_new_and_existing_handlers(self):
        from utils.image_monitor import ImageMonitor
        from utils.prompt_tracker import PromptTracker

        monitor = ImageMonitor(self.api.db, PromptTracker(self.api.db))
        queue_fn = self.api.queue_capture_thumbnail
        monitor.set_thumbnail_queue(queue_fn)
        monitor.start_monitoring([str(self.output_dir)])
        try:
            self.assertEqual(monitor.handler.thumbnail_queue, queue_fn)
            monitor.set_thumbnail_queue(None)
            self.assertIsNone(monitor.handler.thumbnail_queue)
        finally:
            monitor.stop_monitoring()


class TestScanHandsOffThumbnails(AutoThumbnailCase):
    def _scan_job(self, added):
        from py.api.jobs import Job, SCAN_GROUP_NAMES

        job = Job(
            "scan", "Scan for metadata", admission="records", groups=SCAN_GROUP_NAMES
        )
        job.counters["added"] = added
        job.finish("finished")
        return job

    async def test_many_missing_previews_start_the_visible_thumbnail_job(self):
        for i in range(3):
            make_png(self.output_dir / f"s{i}.png", size=(32, 32))
        with patch.object(auto, "SCAN_THUMBNAIL_JOB_THRESHOLD", 3):
            outcome = await self.api._thumbnails_after_scan(self._scan_job(3))
        self.assertEqual(outcome, "job")
        job = self.api._jobs.active(THUMBNAILS_JOB_KIND)
        self.assertIsNotNone(job)
        self.assertTrue(job.cancellable)
        self.assertEqual(job.scope["mode"], "missing")
        await asyncio.wait_for(job.task, WAIT)
        self.assertEqual(job.counters["generated"], 3)

    async def test_few_missing_previews_go_through_the_worker(self):
        for i in range(2):
            make_png(self.output_dir / f"s{i}.png", size=(32, 32))
        with patch.object(auto, "SCAN_THUMBNAIL_JOB_THRESHOLD", 3):
            outcome = await self.api._thumbnails_after_scan(self._scan_job(2))
        self.assertEqual(outcome, "queued")
        self.assertIsNone(self.api._jobs.active(THUMBNAILS_JOB_KIND))
        await self._idle()
        for i in range(2):
            self.assertTrue(
                (self.output_dir / "thumbnails" / f"s{i}_thumb.webp").is_file()
            )

    async def test_a_scan_that_added_nothing_does_nothing(self):
        make_png(self.output_dir / "s.png", size=(32, 32))
        self.assertIsNone(await self.api._thumbnails_after_scan(self._scan_job(0)))
        self.assertFalse((self.output_dir / "thumbnails").exists())

    async def _run_scan(self, final):
        calls = []

        async def events(job, output_dirs=None):
            job.counters["added"] = 1
            yield {"type": final}

        async def hand_off(job, output_dirs=None):
            calls.append((job.state, output_dirs))

        self.api._scan_images_events = events
        self.api._hand_off_scan_thumbnails = hand_off
        job = self.api._start_scan_job("new")
        await asyncio.wait_for(job.task, WAIT)
        return calls

    async def test_a_finished_scan_hands_its_previews_on(self):
        self.assertEqual(await self._run_scan("complete"), [("finished", None)])

    async def test_a_cancelled_or_failed_scan_does_not(self):
        self.assertEqual(await self._run_scan("cancelled"), [])
        self.assertEqual(await self._run_scan("error"), [])

    async def test_hand_off_failures_are_logged_not_raised(self):
        async def broken(job, output_dirs=None):
            raise OSError("walk failed")

        self.api._thumbnails_after_scan = broken
        with self.assertLogs("prompt_manager.api", "WARNING"):
            self.assertIsNone(
                await self.api._hand_off_scan_thumbnails(self._scan_job(3))
            )

    async def test_a_scoped_scan_counts_only_its_sources(self):
        other = self.output_dir.parent / "other"
        make_png(other / "o.png", size=(32, 32))
        make_png(self.output_dir / "s.png", size=(32, 32))
        job = self._scan_job(1)
        job.scope["roots"] = [1]
        outcome = await self.api._thumbnails_after_scan(job, [self.output_dir, other])
        self.assertEqual(outcome, "queued")
        await self._idle()
        self.assertTrue((other / "thumbnails" / "o_thumb.webp").is_file())
        self.assertFalse((self.output_dir / "thumbnails").exists())

    async def test_nothing_missing_is_none(self):
        make_png(self.output_dir / "s.png", size=(32, 32))
        make_png(self.output_dir / "thumbnails" / "s_thumb.webp")
        self.assertEqual(
            await self.api._thumbnails_after_scan(self._scan_job(1)), "none"
        )

    async def test_a_busy_registry_is_reported_not_raised(self):
        for i in range(3):
            make_png(self.output_dir / f"s{i}.png", size=(32, 32))
        self._hold("restore")
        with patch.object(auto, "SCAN_THUMBNAIL_JOB_THRESHOLD", 3):
            outcome = await self.api._thumbnails_after_scan(self._scan_job(3))
        self.assertEqual(outcome, "busy")


if __name__ == "__main__":
    unittest.main()
