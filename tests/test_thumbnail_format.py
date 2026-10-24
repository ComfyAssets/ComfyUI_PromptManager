"""Every preview writer and lookup follows the thumbnail settings (#317).

Automatic previews (capture and first view), the Activity job, the legacy
gallery routes, the listings, the history route, duplicates, enrichment and
the clear all name previews ``<stem>_thumb.<ext of the configured format>``,
write them with the configured quality and size limit, and still find older
previews (``.png`` and friends) until they are converted.

A thumbnail is only a grid or cover placeholder: inspection always reads the
original, and never resolves a request to a file under ``thumbnails/``.

Ordering is driven by events, never by timers.
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from tests.test_api_images import ThumbnailTestCase, make_png, parse_sse
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_images import ThumbnailTestCase, make_png, parse_sse

from PIL import Image, PngImagePlugin  # noqa: E402

import py.api.images as images_module  # noqa: E402
import py.api.thumbnail_auto as auto  # noqa: E402
from py.api import thumbnail_encode  # noqa: E402
from py.api.admin import _remove_thumbnail  # noqa: E402
from py.config import PromptManagerConfig  # noqa: E402

SERVE = "/prompt_manager/images/serve/"
STATUS = "/prompt_manager/images/thumbnails/status"
PLAN = "/prompt_manager/images/thumbnails/plan"
JOB = "/prompt_manager/jobs/thumbnails"
WAIT = 10
HAVE_FFMPEG = bool(shutil.which("ffmpeg"))


def graph(positive="portrait", negative="blurry"):
    return {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": positive}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": negative}},
        "3": {
            "class_type": "KSampler",
            "inputs": {"positive": ["1", 0], "negative": ["2", 0]},
        },
    }


def png_with_prompt(path, size=(64, 64)):
    info = PngImagePlugin.PngInfo()
    info.add_text("prompt", json.dumps(graph()))
    info.add_text("workflow", json.dumps({"nodes": [], "links": []}))
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", size, (9, 90, 200)) as image:
        image.save(path, pnginfo=info)
    return path


class FormatCase(ThumbnailTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self._orig = PromptManagerConfig.get_config()
        self.addCleanup(PromptManagerConfig.update_config, self._orig)
        self._settings("webp", 80, 512)
        thumbnail_encode.reset_encoders()
        self.addCleanup(thumbnail_encode.reset_encoders)
        self.addCleanup(self.api.close_auto_thumbnails)

    def _settings(self, fmt, quality, edge):
        PromptManagerConfig.update_config(
            {"thumbnails": {"format": fmt, "quality": quality, "edge": edge}}
        )

    def _thumb(self, name, suffix=".webp"):
        return self.output_dir / "thumbnails" / f"{name}_thumb{suffix}"

    async def _idle(self):
        loop = asyncio.get_running_loop()
        thumbnailer = self.api._auto_thumbnails()
        self.assertTrue(await loop.run_in_executor(None, thumbnailer.wait_idle, WAIT))

    async def _capture(self, path):
        self.assertEqual(self.api.queue_capture_thumbnail(path), "queued")
        await self._idle()

    async def _finished(self, job_id):
        job = self.api._jobs.get(job_id)
        await asyncio.wait_for(job.task, WAIT)
        return job

    def _format_of(self, path):
        with Image.open(path) as img:
            return img.format, img.size


class TestCaptureAndViewFollowTheSettings(FormatCase):
    async def test_capture_writes_webp_at_the_size_limit(self):
        self._settings("webp", 80, 200)
        src = make_png(self.output_dir / "a.png", size=(800, 400))
        await self._capture(src)
        self.assertEqual(self._format_of(self._thumb("a")), ("WEBP", (200, 100)))
        self.assertFalse(self._thumb("a", ".png").exists())

    async def test_capture_writes_jpeg_when_configured(self):
        self._settings("jpeg", 50, 300)
        src = make_png(self.output_dir / "sub" / "b.png", size=(600, 600))
        await self._capture(src)
        made = self.output_dir / "thumbnails" / "sub" / "b_thumb.jpg"
        self.assertEqual(self._format_of(made), ("JPEG", (300, 300)))

    async def test_quality_reaches_the_encoder(self):
        noisy = Image.effect_noise((400, 400), 80).convert("RGB")
        for name, quality in (("low", 10), ("high", 95)):
            noisy.save(self.output_dir / f"{name}.png")
            self._settings("webp", quality, 512)
            await self._capture(self.output_dir / f"{name}.png")
        self.assertLess(
            self._thumb("low").stat().st_size, self._thumb("high").stat().st_size
        )

    async def test_an_animated_source_previews_as_a_still(self):
        path = self.output_dir / "anim.gif"
        frames = [Image.new("RGB", (64, 64), c) for c in ((250, 0, 0), (0, 0, 250))]
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=40)
        await self._capture(path)
        with Image.open(self._thumb("anim")) as img:
            self.assertEqual(getattr(img, "n_frames", 1), 1)
            self.assertGreater(img.convert("RGB").getpixel((32, 32))[0], 200)

    async def test_a_video_preview_is_the_configured_format(self):
        frame = Image.new("RGB", (640, 360), (0, 200, 0))
        clip = self.output_dir / "clip.mp4"
        clip.write_bytes(b"not decoded")
        with patch.object(self.api, "_opencv_frame", return_value=frame):
            await self._capture(clip)
        self.assertEqual(self._format_of(self._thumb("clip"))[0], "WEBP")
        self.assertFalse(self._thumb("clip", ".jpg").exists())

    @unittest.skipUnless(HAVE_FFMPEG, "no ffmpeg")
    async def test_ffmpeg_video_frame_is_encoded_in_the_format(self):
        clip = self.output_dir / "real.mp4"
        subprocess.run(
            ["ffmpeg", "-v", "quiet", "-f", "lavfi", "-i",
             "color=c=red:s=320x240:d=2", "-y", str(clip)],
            check=True, timeout=60,
        )  # fmt: skip
        with patch.object(self.api, "_opencv_frame", return_value=None):
            await self._capture(clip)
        fmt, size = self._format_of(self._thumb("real"))
        self.assertEqual(fmt, "WEBP")
        self.assertEqual(size, (320, 240))

    async def test_view_makes_the_preview_and_sends_its_type(self):
        make_png(self.output_dir / "v.png", size=(64, 64))
        resp = await self.client.get(SERVE + "thumbnails/v_thumb.webp")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers["Content-Type"], "image/webp")
        self.assertEqual(resp.headers["Cache-Control"], "public, max-age=3600")
        self.assertTrue(self._thumb("v").is_file())

    async def test_a_jxl_preview_is_served_as_image_jxl(self):
        make_png(self.output_dir / "j.png")
        thumb = self._thumb("j", ".jxl")
        thumb.parent.mkdir(parents=True, exist_ok=True)
        thumb.write_bytes(b"\xff\x0a" + b"\x00" * 30)
        resp = await self.client.get(SERVE + "thumbnails/j_thumb.jxl")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers["Content-Type"], "image/jxl")
        self.assertEqual(resp.headers["Cache-Control"], "public, max-age=3600")

    async def test_a_converted_away_legacy_url_answers_the_current_preview(self):
        make_png(self.output_dir / "c.png")
        await self._capture(self.output_dir / "c.png")
        resp = await self.client.get(SERVE + "thumbnails/c_thumb.png?v=1")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers["Content-Type"], "image/webp")
        self.assertFalse(self._thumb("c", ".png").exists())

    async def test_preview_requests_keep_the_text_checks_for_new_extensions(self):
        accepted = ("thumbnails/a_thumb.jxl", "thumbnails/s/a_thumb.webp")
        for text in accepted:
            self.assertTrue(auto._is_preview_request(text), text)
        refused = (
            "thumbnails/a_thumb.JXL",
            "thumbnails\\a_thumb.jxl",
            "thumbnails/C:a_thumb.webp",
            "thumbnails//a_thumb.webp",
            "thumbnails/./a_thumb.jxl",
            "thumbnails/../a_thumb.jxl",
            "thumbnails/thumbnails/a_thumb.webp",
            "thumbnails/a_thumb.txt",
            "thumbnails/_thumb.webp",
        )
        for text in refused:
            self.assertFalse(auto._is_preview_request(text), text)
        make_png(self.outside_png())
        resp = await self.client.get(SERVE + "thumbnails/..%2F..%2Fout_thumb.webp")
        self.assertIn(resp.status, (403, 404))
        self.assertFalse((self.comfy_dir / "out_thumb.webp").exists())

    def outside_png(self):
        return self.comfy_dir / "out.png"

    async def test_jxl_without_an_encoder_writes_webp_once(self):
        self._settings("jxl", 80, 512)
        src = make_png(self.output_dir / "x.png", size=(64, 64))
        calls = []
        original = self.api._generate_one

        def counting(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)

        self.api._generate_one = counting
        with patch.object(thumbnail_encode, "detect_jxl_encoder", return_value=None):
            await self._capture(src)
            self.assertTrue(self._thumb("x").is_file())
            self.assertFalse(self._thumb("x", ".jxl").exists())
            self.assertEqual(self.api.queue_capture_thumbnail(src), "fresh")
            resp = await self.client.get(SERVE + "thumbnails/x_thumb.jxl")
            self.assertEqual(resp.headers["Content-Type"], "image/webp")
        self.assertEqual(len(calls), 1)


class TestListingsFindAnyOwnedPreview(FormatCase):
    def _listed(self, rel):
        return images_module.media_thumbnail(self.output_dir, Path(rel), 0)

    def test_missing_preview_lists_the_configured_name(self):
        make_png(self.output_dir / "m.png")
        url, ready = self._listed("m.png")
        self.assertEqual(url, SERVE + "thumbnails/m_thumb.webp?root=0")
        self.assertFalse(ready)

    def test_a_legacy_preview_is_listed_until_a_current_one_exists(self):
        make_png(self.output_dir / "l.png")
        make_png(self._thumb("l", ".png"))
        url, ready = self._listed("l.png")
        self.assertTrue(url.startswith(SERVE + "thumbnails/l_thumb.png?v="))
        self.assertTrue(ready)
        make_png(self._thumb("l"))
        url, _ready = self._listed("l.png")
        self.assertTrue(url.startswith(SERVE + "thumbnails/l_thumb.webp?v="))

    def test_a_video_lists_its_legacy_jpg(self):
        (self.output_dir / "v.mp4").write_bytes(b"x")
        make_png(self._thumb("v", ".jpg"))
        url, ready = self._listed("v.mp4")
        self.assertTrue(url.startswith(SERVE + "thumbnails/v_thumb.jpg?v="))
        self.assertTrue(ready)

    def test_duplicates_listing_and_enrichment_use_the_existing_preview(self):
        src = make_png(self.output_dir / "d.png")
        make_png(self._thumb("d"))
        described = self.api._describe_media_file(src, self.output_dir, "hash")
        self.assertTrue(
            described["thumbnail_url"].startswith(SERVE + "thumbnails/d_thumb.webp")
        )
        images = self.api._enrich_images([{"image_path": str(src)}])
        self.assertTrue(
            images[0]["thumbnail_url"].startswith(SERVE + "thumbnails/d_thumb.webp")
        )


class TestPlanStatusAndJobs(FormatCase):
    async def test_status_counts_missing_outdated_and_ready(self):
        make_png(self.output_dir / "new.png")  # missing
        make_png(self.output_dir / "old.png")
        make_png(self._thumb("old", ".png"))  # outdated (legacy PNG)
        make_png(self.output_dir / "cur.png")
        make_png(self._thumb("cur"))  # current
        body = await (await self.client.get(STATUS)).json()
        self.assertEqual(
            (body["pending"], body["outdated"], body["ready"], body["total_images"]),
            (1, 1, 2, 3),
        )
        plan = await (await self.client.get(PLAN, params={"mode": "missing"})).json()
        self.assertEqual((plan["targets"], plan["fresh"], plan["outdated"]), (1, 1, 1))
        self.assertEqual(
            (plan["format"], plan["quality"], plan["size"]), ("webp", 80, 512)
        )

    async def test_missing_job_writes_the_format_and_leaves_outdated_alone(self):
        self._settings("webp", 70, 160)
        make_png(self.output_dir / "n.png", size=(320, 320))
        make_png(self.output_dir / "o.png")
        legacy = make_png(self._thumb("o", ".png"))
        before = legacy.read_bytes()
        resp = await self.client.post(JOB, json={"mode": "missing"})
        self.assertEqual(resp.status, 202)
        job = await self._finished((await resp.json())["job"]["id"])
        self.assertEqual(job.state, "finished")
        self.assertEqual(job.scope["format"], "webp")
        self.assertEqual(self._format_of(self._thumb("n")), ("WEBP", (160, 160)))
        self.assertFalse(self._thumb("o").exists())
        self.assertEqual(legacy.read_bytes(), before)

    async def test_the_legacy_stream_uses_the_server_settings(self):
        # R3: the gallery's quality preset no longer sizes the preview.
        self._settings("webp", 80, 200)
        make_png(self.output_dir / "g.png", size=(800, 800))
        resp = await self.client.post(self.PROGRESS_URL + "?quality=high")
        events = dict(parse_sse(await resp.text()))
        self.assertEqual(events["complete"]["count"], 1)
        self.assertEqual(self._format_of(self._thumb("g")), ("WEBP", (200, 200)))

    async def test_the_blocking_legacy_generate_uses_the_server_settings(self):
        self._settings("jpeg", 80, 140)
        make_png(self.output_dir / "h.png", size=(700, 700))
        resp = await self.client.post(
            "/prompt_manager/images/generate-thumbnails", json={"quality": "low"}
        )
        self.assertEqual(resp.status, 200)
        made = self.output_dir / "thumbnails" / "h_thumb.jpg"
        self.assertEqual(self._format_of(made), ("JPEG", (140, 140)))

    async def test_clear_removes_every_preview_format(self):
        make_png(self.output_dir / "k.png")
        for suffix in (".webp", ".png", ".jpg"):
            make_png(self._thumb("k", suffix))
        jxl = self._thumb("k", ".jxl")
        jxl.write_bytes(b"\xff\x0a")
        resp = await self.client.post("/prompt_manager/images/clear-thumbnails")
        self.assertEqual(resp.status, 200)
        self.assertEqual((await resp.json())["cleared_files"], 4)
        self.assertTrue((self.output_dir / "k.png").exists())


class TestReviewFixes(FormatCase):
    async def test_a_legacy_preview_in_another_case_is_outdated(self):
        # Review M1: X.JPG's older X_thumb.JPG, and y.png's y_thumb.PNG.
        upper = self.output_dir / "X.JPG"
        Image.new("RGB", (64, 64)).save(upper, "JPEG")
        make_png(self.output_dir / "y.png")
        for name in ("X_thumb.JPG", "y_thumb.PNG"):
            legacy = self.output_dir / "thumbnails" / name
            legacy.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (32, 32)).save(legacy, "PNG" if "PNG" in name else "JPEG")
        body = await (await self.client.get(STATUS)).json()
        self.assertEqual((body["outdated"], body["pending"]), (2, 0))
        resp = await self.client.post(JOB, json={"mode": "convert"})
        job = await self._finished((await resp.json())["job"]["id"])
        self.assertEqual(job.counters["generated"], 2, job.log)
        names = sorted(os.listdir(self.output_dir / "thumbnails"))
        self.assertEqual(names, ["X_thumb.webp", "y_thumb.webp"])

    async def test_clear_also_removes_stray_temp_files(self):
        make_png(self.output_dir / "k.png")
        make_png(self._thumb("k"))
        folder = self.output_dir / "thumbnails"
        for name in (".k_thumb.part-1-2.webp", ".501one.part-3-4.jxl"):
            (folder / name).write_bytes(b"partial")
        keep = folder / "notes.txt"
        keep.write_bytes(b"x")
        resp = await self.client.post("/prompt_manager/images/clear-thumbnails")
        self.assertEqual((await resp.json())["cleared_files"], 3)
        self.assertEqual(os.listdir(folder), ["notes.txt"])

    def test_the_startup_sweep_removes_only_old_temp_files(self):
        folder = self.output_dir / "thumbnails" / "sub"
        folder.mkdir(parents=True)
        old = folder / ".a_thumb.part-1-2.webp"
        young = folder / ".b_thumb.part-1-2.webp"
        for path in (old, young):
            path.write_bytes(b"partial")
        when = old.stat().st_mtime - 3600
        os.utime(old, (when, when))
        self.assertEqual(self.api.sweep_thumbnail_temps(), 1)
        self.assertFalse(old.exists())
        self.assertTrue(young.exists())


class TestRemoval(FormatCase):
    def test_removing_a_source_removes_its_previews_in_every_format(self):
        make_png(self.output_dir / "r.png")
        for suffix in (".png", ".webp"):
            make_png(self._thumb("r", suffix))
        _remove_thumbnail(self.output_dir, Path("r.png"))
        self.assertFalse(self._thumb("r", ".png").exists())
        self.assertFalse(self._thumb("r").exists())

    def test_a_shared_preview_stays_while_another_source_owns_it(self):
        # R2: r.png and r.mp4 share r_thumb.webp; the PNG's legacy preview is its own.
        make_png(self.output_dir / "r.png")
        (self.output_dir / "r.mp4").write_bytes(b"x")
        make_png(self._thumb("r", ".png"))
        make_png(self._thumb("r"))
        _remove_thumbnail(self.output_dir, Path("r.png"))
        self.assertFalse(self._thumb("r", ".png").exists())
        self.assertTrue(self._thumb("r").exists())


class TestThumbnailsAreOnlyPlaceholders(FormatCase):
    """Maintainer constraint: metadata always comes from the original."""

    async def test_inspection_reads_the_original_not_its_webp_preview(self):
        png_with_prompt(self.output_dir / "image.png")
        await self._capture(self.output_dir / "image.png")
        self.assertTrue(self._thumb("image").is_file())
        opened = []
        original_open = Image.open

        def recording(fp, *args, **kwargs):
            opened.append(str(fp))
            return original_open(fp, *args, **kwargs)

        with patch.object(Image, "open", recording):
            resp = await self.client.get(
                "/prompt_manager/images/inspect/image.png", params={"root": "0"}
            )
        body = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertEqual(body["prompts"]["positive"]["text"], "portrait")
        self.assertEqual(body["prompts"]["negative"]["text"], "blurry")
        self.assertFalse([p for p in opened if "thumbnails" in p], opened)

    async def test_inspection_never_resolves_a_thumbnail_path(self):
        png_with_prompt(self.output_dir / "image.png")
        png_with_prompt(self._thumb("image", ".png"))
        await self._capture(self.output_dir / "image.png")
        for rel in (
            "thumbnails/image_thumb.webp",
            "thumbnails/image_thumb.png",
            "Thumbnails/image_thumb.webp",
        ):
            resp = await self.client.get(
                f"/prompt_manager/images/inspect/{rel}", params={"root": "0"}
            )
            self.assertIn(resp.status, (403, 404), rel)
            self.assertNotIn("portrait", await resp.text())


if __name__ == "__main__":
    unittest.main()
