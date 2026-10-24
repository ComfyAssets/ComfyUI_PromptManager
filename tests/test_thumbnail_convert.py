"""Convert outdated thumbnails (#317): an explicit, cancellable Activity job.

``POST /prompt_manager/jobs/thumbnails {mode: "convert"}`` runs under the
thumbnail job's kind, admission (``guarded``) and worker threads. For every
media file whose preview is in another format than the one written now, it
writes the new preview from the SOURCE (verified, atomic), then removes that
file's old-format previews under ``thumbnails/``. A failure keeps the old
preview; previews whose source is gone (orphans) are never touched. The final
line reports converted / failed and bytes before → after.

Ordering is driven by events, never by timers.
"""

import asyncio
import os
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from tests.test_api_images import ThumbnailTestCase, make_png
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_images import ThumbnailTestCase, make_png

from PIL import Image  # noqa: E402

from py.api import thumbnail_encode  # noqa: E402
from py.config import PromptManagerConfig  # noqa: E402

JOB = "/prompt_manager/jobs/thumbnails"
PLAN = "/prompt_manager/images/thumbnails/plan"
STATUS = "/prompt_manager/images/thumbnails/status"
WAIT = 10


def big_png(path, size=(512, 512)):
    """A noisy PNG: its PNG preview is large, a WebP one much smaller."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.effect_noise(size, 40).convert("RGB").save(path, "PNG")
    return path


class ConvertCase(ThumbnailTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        saved = PromptManagerConfig.get_config()["thumbnails"]
        self.addCleanup(PromptManagerConfig.update_config, {"thumbnails": saved})
        PromptManagerConfig.update_config(
            {"thumbnails": {"format": "webp", "quality": 80, "edge": 512}}
        )
        thumbnail_encode.reset_encoders()
        self.addCleanup(thumbnail_encode.reset_encoders)
        self.addCleanup(self.api.close_auto_thumbnails)

    def _legacy(self, name, suffix=".png", source=True):
        """A media file (unless ``source`` is False) and its old-format preview."""
        if source:
            big_png(self.output_dir / f"{name}.png")
        preview = big_png(self._thumb_path(name, suffix), size=(256, 256))
        if source:
            later = (self.output_dir / f"{name}.png").stat().st_mtime + 5
            os.utime(preview, (later, later))
        return preview

    async def _convert(self):
        resp = await self.client.post(JOB, json={"mode": "convert"})
        body = await resp.json()
        self.assertEqual(resp.status, 202, body)
        job = self.api._jobs.get(body["job"]["id"])
        await asyncio.wait_for(job.task, WAIT)
        return body["job"], job


class TestConvertOutdated(ConvertCase):
    async def test_plan_counts_the_outdated_previews(self):
        self._legacy("a")
        self._legacy("b")
        make_png(self.output_dir / "new.png")  # missing: not this job's work
        plan = await (await self.client.get(PLAN, params={"mode": "convert"})).json()
        self.assertEqual(
            (plan["mode"], plan["targets"], plan["outdated"]), ("convert", 2, 2)
        )
        self.assertEqual(self.api._jobs.list(), [])

    async def test_converts_from_the_source_and_removes_the_old_preview(self):
        old = self._legacy("a")
        original = (self.output_dir / "a.png").read_bytes()
        orphan = self._legacy("gone", source=False)
        make_png(self.output_dir / "new.png")
        before = old.stat().st_size

        summary, job = await self._convert()

        self.assertEqual(
            (summary["kind"], summary["admission"], summary["cancellable"]),
            ("thumbnails", "guarded", True),
        )
        self.assertEqual(summary["title"], "Convert outdated thumbnails")
        self.assertEqual(summary["scope"]["mode"], "convert")
        self.assertEqual(job.state, "finished")
        self.assertEqual(job.counters["generated"], 1)
        self.assertEqual(job.counters["errors"], 0)
        new = self._thumb_path("a")
        with Image.open(new) as img:
            self.assertEqual((img.format, max(img.size)), ("WEBP", 512))
        self.assertFalse(old.exists(), "the old PNG preview is removed")
        self.assertEqual((self.output_dir / "a.png").read_bytes(), original)
        self.assertTrue(orphan.exists(), "an orphan preview is never touched")
        self.assertFalse(self._thumb_path("new").exists(), "missing is not converted")
        after = new.stat().st_size
        self.assertRegex(job.status_line, r"^1 converted · 0 failed · .+ → .+$")
        self.assertTrue(any("Conversion finished" in line for line in job.log))
        self.assertLess(after, before)
        status = await (await self.client.get(STATUS)).json()
        self.assertEqual((status["outdated"], status["pending"]), (0, 1))

    async def test_a_source_that_fails_keeps_its_old_preview(self):
        old = self._legacy("bad")
        (self.output_dir / "bad.png").write_bytes(b"\x89PNG broken")
        later = old.stat().st_mtime + 10
        os.utime(old, (later, later))
        keep = old.read_bytes()

        _, job = await self._convert()

        self.assertEqual(job.counters["errors"], 1)
        self.assertEqual(job.counters["generated"], 0)
        self.assertEqual(old.read_bytes(), keep)
        self.assertFalse(self._thumb_path("bad").exists())
        self.assertRegex(job.status_line, r"^0 converted · 1 failed")

    async def test_a_video_without_a_frame_keeps_its_old_preview(self):
        (self.output_dir / "clip.mp4").write_bytes(b"no decoder")
        old = big_png(self._thumb_path("clip", ".jpg"), size=(64, 64))
        keep = old.read_bytes()
        with (
            patch.object(self.api, "_opencv_frame", return_value=None),
            patch.object(self.api, "_ffmpeg_frame", return_value=None),
        ):
            _, job = await self._convert()
        self.assertEqual(job.counters["errors"], 1)
        self.assertEqual(old.read_bytes(), keep)
        self.assertFalse(self._thumb_path("clip").exists(), "never the placeholder")

    async def test_an_old_preview_that_is_a_link_is_left(self):
        big_png(self.output_dir / "l.png")
        target = big_png(self.comfy_dir / "elsewhere.png", size=(32, 32))
        link = self._thumb_path("l", ".png")
        link.parent.mkdir(parents=True, exist_ok=True)
        self._make_symlink(target, link)
        _, job = await self._convert()
        self.assertTrue(link.is_symlink())
        self.assertTrue(target.exists())
        self.assertEqual(job.counters["errors"], 0)

    async def test_a_preview_already_current_only_loses_its_leftovers(self):
        old = self._legacy("c")
        current = big_png(self._thumb_path("c"), size=(64, 64))
        later = old.stat().st_mtime + 5
        os.utime(current, (later, later))
        written = current.read_bytes()
        _, job = await self._convert()
        self.assertEqual(job.counters["generated"], 1)
        self.assertEqual(current.read_bytes(), written, "not written again")
        self.assertFalse(old.exists())

    async def test_cancel_stops_at_a_file_boundary_keeping_what_converted(self):
        original = PromptManagerConfig.WORKER_THREADS
        PromptManagerConfig.WORKER_THREADS = 1
        self.addCleanup(setattr, PromptManagerConfig, "WORKER_THREADS", original)
        olds = [self._legacy(f"p{i}") for i in range(6)]
        done = []
        real = self.api._convert_one
        job_ids = []

        def counting(*args, **kwargs):
            result = real(*args, **kwargs)
            done.append(result)
            if len(done) == 2:
                self.assertTrue(self.api._jobs.cancel(job_ids[0]))
            return result

        self.api._convert_one = counting
        resp = await self.client.post(JOB, json={"mode": "convert"})
        job_ids.append((await resp.json())["job"]["id"])
        job = self.api._jobs.get(job_ids[0])
        await asyncio.wait_for(job.task, WAIT)

        self.assertEqual(job.state, "cancelled")
        self.assertEqual(job.counters["generated"], 2)
        converted = [p for p in olds if not p.exists()]
        self.assertEqual(len(converted), 2)
        for old in olds:
            webp = old.with_suffix(".webp")
            self.assertEqual(webp.exists(), not old.exists(), old.name)
        self.assertTrue(any("Conversion cancelled" in line for line in job.log))

    async def test_refused_beside_a_running_thumbnail_job_or_scan(self):
        self._legacy("a")
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        real = self.api._convert_one

        def gated(*args, **kwargs):
            entered.set()
            release.wait(WAIT)
            return real(*args, **kwargs)

        self.api._convert_one = gated
        resp = await self.client.post(JOB, json={"mode": "convert"})
        first = (await resp.json())["job"]["id"]
        loop = asyncio.get_running_loop()
        self.assertTrue(await loop.run_in_executor(None, entered.wait, WAIT))
        again = await self.client.post(JOB, json={"mode": "missing"})
        self.assertEqual(again.status, 409)
        scan = await self.client.post("/prompt_manager/jobs/scan")
        self.assertEqual(scan.status, 409)
        release.set()
        await asyncio.wait_for(self.api._jobs.get(first).task, WAIT)

    async def test_the_legacy_gallery_stream_never_attaches_to_a_conversion(self):
        try:
            from tests.test_api_images import parse_sse
        except ImportError:
            from test_api_images import parse_sse

        self._legacy("a")
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        real = self.api._convert_one

        def gated(*args, **kwargs):
            entered.set()
            release.wait(WAIT)
            return real(*args, **kwargs)

        self.api._convert_one = gated
        resp = await self.client.post(JOB, json={"mode": "convert"})
        first = (await resp.json())["job"]["id"]
        loop = asyncio.get_running_loop()
        self.assertTrue(await loop.run_in_executor(None, entered.wait, WAIT))
        stream = await self.client.post(self.PROGRESS_URL)
        events = parse_sse(await stream.text())
        self.assertEqual(events[-1][0], "error")
        self.assertIn("Convert outdated thumbnails", events[-1][1]["error"])
        release.set()
        await asyncio.wait_for(self.api._jobs.get(first).task, WAIT)

    async def test_nothing_outdated_says_so(self):
        big_png(self.output_dir / "a.png")
        _, job = await self._convert()
        self.assertEqual(job.state, "finished")
        self.assertEqual(job.status_line, "No outdated previews to convert")

    async def test_the_source_vanishing_keeps_the_old_preview(self):
        old = self._legacy("v")
        real = self.api._generate_one

        def vanish(src, *args, **kwargs):
            os.remove(src)
            return real(src, *args, **kwargs)

        self.api._generate_one = vanish
        _, job = await self._convert()
        self.assertEqual(job.counters["errors"], 1)
        self.assertTrue(old.exists(), "a source gone mid-run makes an orphan")

    async def test_same_stem_sources_convert_once_and_count_bytes_once(self):
        # Review I2 / R2: x.png and x.jpg share x_thumb.webp. The pair is
        # converted once, from the image first in the source order (.png),
        # and every removed file's bytes count once.
        original = PromptManagerConfig.WORKER_THREADS
        PromptManagerConfig.WORKER_THREADS = 2
        self.addCleanup(setattr, PromptManagerConfig, "WORKER_THREADS", original)
        from py.api.thumbnail_convert import readable_bytes

        png = self.output_dir / "x.png"
        Image.new("RGB", (300, 300), (220, 20, 20)).save(png)
        jpg = self.output_dir / "x.jpg"
        Image.new("RGB", (300, 300), (20, 20, 220)).save(jpg)
        old_png = big_png(self._thumb_path("x", ".png"), size=(200, 200))
        old_jpg = self._thumb_path("x", ".jpg")
        Image.effect_noise((200, 200), 40).convert("RGB").save(old_jpg, "JPEG")
        later = max(png.stat().st_mtime, jpg.stat().st_mtime) + 5
        for path in (old_png, old_jpg):
            os.utime(path, (later, later))
        before = old_png.stat().st_size + old_jpg.stat().st_size

        _, job = await self._convert()

        self.assertEqual(job.state, "finished")
        self.assertEqual(
            (job.counters["generated"], job.counters["errors"]), (2, 0), job.log
        )
        new = self._thumb_path("x")
        self.assertFalse(old_png.exists() or old_jpg.exists())
        with Image.open(new) as img:
            red, _green, blue = img.convert("RGB").getpixel((5, 5))
        self.assertGreater(red, blue, "the image first in the source order wins")
        self.assertEqual(
            job.status_line,
            f"2 converted · 0 failed · {readable_bytes(before)} → "
            f"{readable_bytes(new.stat().st_size)}",
        )

    async def test_an_old_preview_already_gone_counts_as_removed(self):
        old = self._legacy("g")
        real = self.api._generate_one

        def remove_first(src, *args, **kwargs):
            os.remove(old)
            return real(src, *args, **kwargs)

        self.api._generate_one = remove_first
        _, job = await self._convert()
        self.assertEqual((job.counters["generated"], job.counters["errors"]), (1, 0))
        self.assertRegex(job.status_line, r"^1 converted · 0 failed · 0 B → ")

    async def test_a_current_preview_that_does_not_decode_is_written_again(self):
        # Review M4: a kept current preview is decoded before old ones go.
        old = self._legacy("m")
        current = self._thumb_path("m")
        current.write_bytes(b"RIFF not a webp")
        later = old.stat().st_mtime + 5
        os.utime(current, (later, later))
        _, job = await self._convert()
        self.assertEqual((job.counters["generated"], job.counters["errors"]), (1, 0))
        with Image.open(current) as img:
            self.assertEqual(img.format, "WEBP")
        self.assertFalse(old.exists())

    async def test_a_format_change_underneath_stops_the_job(self):
        # Review I4 (defense in depth): the job never removes previews in a
        # format chosen after it started.
        original = PromptManagerConfig.WORKER_THREADS
        PromptManagerConfig.WORKER_THREADS = 1
        self.addCleanup(setattr, PromptManagerConfig, "WORKER_THREADS", original)
        olds = [self._legacy(f"f{i}") for i in range(4)]
        real = self.api._convert_one
        calls = []

        def switching(*args, **kwargs):
            result = real(*args, **kwargs)
            calls.append(result)
            if len(calls) == 1:
                PromptManagerConfig.update_config(
                    {"thumbnails": {"format": "jpeg", "quality": 80, "edge": 512}}
                )
                jpeg = self._thumb_path("f3", ".jpg")
                Image.new("RGB", (16, 16)).save(jpeg, "JPEG")
            return result

        self.api._convert_one = switching
        _, job = await self._convert()
        self.assertEqual(job.state, "cancelled")
        self.assertIn("format changed", job.status_line)
        self.assertEqual(job.counters["generated"], 1)
        self.assertTrue(self._thumb_path("f3", ".jpg").exists(), "the new format stays")
        self.assertEqual(sum(1 for p in olds if p.exists()), 3)

    async def test_the_new_preview_is_never_removed_as_an_old_one(self):
        # Re-review N1: on a case-insensitive disk x_thumb.JPG (an old
        # spelling) can be the x_thumb.jpg just written. A hard link stands
        # in for that second name of one file.
        PromptManagerConfig.update_config(
            {"thumbnails": {"format": "jpeg", "quality": 80, "edge": 512}}
        )
        src = self.output_dir / "x.jpg"
        Image.new("RGB", (64, 64), (200, 10, 10)).save(src, "JPEG")
        alias = self._thumb_path("x", ".JPG")
        alias.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (16, 16)).save(alias, "JPEG")
        later = src.stat().st_mtime + 5
        os.utime(alias, (later, later))
        real = self.api._generate_one

        def same_file(source, dst, *args, **kwargs):
            made = real(source, dst, *args, **kwargs)
            os.remove(alias)
            os.link(made["path"], alias)
            return made

        self.api._generate_one = same_file
        _, job = await self._convert()
        self.assertTrue(self._thumb_path("x", ".jpg").exists())
        self.assertTrue(alias.exists(), "the new preview's other name is kept")
        self.assertEqual(job.counters["errors"], 0)

    def test_same_file_falls_back_to_the_path_without_inode_numbers(self):
        from py.api import thumbnail_convert as tc

        a = self.output_dir / "a_thumb.jpg"
        b = self.output_dir / "b_thumb.jpg"
        for path in (a, b):
            path.write_bytes(b"x")
        real_stat = os.stat

        def no_inode(path, *args, **kwargs):
            result = real_stat(path, *args, **kwargs)
            return os.stat_result(
                (result.st_mode, 0, result.st_dev) + tuple(result)[3:]
            )

        with patch.object(tc.os, "stat", side_effect=no_inode):
            self.assertFalse(tc._same_file(a, b))
            self.assertTrue(tc._same_file(a, a))
            self.assertTrue(tc._same_file(a, self.output_dir / "." / "a_thumb.jpg"))
        self.assertFalse(tc._same_file(a, self.output_dir / "gone.jpg"))

    async def test_convert_goes_on_past_the_per_batch_cap(self):
        import py.api.images as images_module

        olds = [self._legacy(f"c{i}") for i in range(8)]
        with patch.object(images_module, "MAX_THUMBNAIL_FILES", 3):
            _, job = await self._convert()
        self.assertEqual(job.state, "finished")
        self.assertEqual(job.counters["generated"], 8)
        self.assertFalse(any(old.exists() for old in olds))
        self.assertEqual(job.summary()["total"], 8)
        self.assertRegex(job.status_line, r"^8 converted · 0 failed · ")
        self.assertNotIn("next run", job.status_line)

    def test_byte_sizes_are_readable(self):
        from py.api.thumbnail_convert import readable_bytes

        self.assertEqual(readable_bytes(0), "0 B")
        self.assertEqual(readable_bytes(1536), "1.5 KB")
        self.assertEqual(readable_bytes(5 * 1024 * 1024), "5.0 MB")
        self.assertEqual(readable_bytes(3 * 1024**3), "3.0 GB")


if __name__ == "__main__":
    unittest.main()
