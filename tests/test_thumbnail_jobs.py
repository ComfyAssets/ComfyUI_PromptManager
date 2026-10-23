"""Thumbnail generation as an Activity job (#249 System S5, Task 12).

``GET /prompt_manager/images/thumbnails/plan`` counts what a run would do and
writes nothing. ``POST /prompt_manager/jobs/thumbnails`` starts the
``thumbnails`` job (admission ``guarded``, cancellable at a file boundary).
The legacy gallery stream ``POST /images/generate-thumbnails/progress`` starts
or attaches to that job and streams its unchanged events from it, so a client
that goes away no longer stops the work.

Ordering is driven by threading/asyncio events, never by timers.
"""

import asyncio
import hashlib
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from tests.test_api_images import ThumbnailTestCase, make_png, parse_sse
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_images import ThumbnailTestCase, make_png, parse_sse

from PIL import Image  # noqa: E402

import py.api.images as images_module  # noqa: E402
from py.api.thumbnail_jobs import thumbnail_options  # noqa: E402
from py.config import PromptManagerConfig  # noqa: E402

PLAN_URL = "/prompt_manager/images/thumbnails/plan"
JOB_URL = "/prompt_manager/jobs/thumbnails"


def _tree_state(root):
    """{relative path: (size, mtime_ns, sha256)} of every file under ``root``."""
    state = {}
    for folder, _dirs, files in os.walk(root):
        for name in files:
            path = Path(folder) / name
            data = path.read_bytes()
            stat = path.stat()
            state[path.relative_to(root).as_posix()] = (
                stat.st_size,
                stat.st_mtime_ns,
                hashlib.sha256(data).hexdigest(),
            )
    return state


class ThumbnailJobCase(ThumbnailTestCase):
    """Helpers: start, gate and wait for thumbnail jobs."""

    async def _start(self, **body):
        resp = await self.client.post(JOB_URL, json=body)
        return resp.status, await resp.json()

    async def _finished(self, job_id):
        job = self.api._jobs.get(job_id)
        await asyncio.wait_for(job.task, 10)
        return job

    def _gate_generation(self):
        """Block every file in the worker until released; returns (entered, release)."""
        entered = threading.Event()
        release = threading.Event()
        original = self.api._generate_one

        def gated(*args, **kwargs):
            entered.set()
            release.wait(10)
            return original(*args, **kwargs)

        self.api._generate_one = gated
        self.addCleanup(release.set)
        return entered, release

    async def _wait(self, event):
        loop = asyncio.get_running_loop()
        self.assertTrue(await loop.run_in_executor(None, event.wait, 10))

    @staticmethod
    def _watch_subscribers(job):
        """(subscribed, left): threading events for a stream joining or leaving."""
        subscribed, left = threading.Event(), threading.Event()

        class Watched(set):
            def add(self, item):
                super().add(item)
                subscribed.set()

            def discard(self, item):
                super().discard(item)
                left.set()

        job._subscribers = Watched(job._subscribers)
        return subscribed, left

    def _status_lines(self):
        """Every status line any job is given, in order."""
        from py.api.jobs import Job

        lines = []
        original = Job.update

        def update(job, **kwargs):
            if kwargs.get("status_line") is not None:
                lines.append(kwargs["status_line"])
            return original(job, **kwargs)

        patcher = patch.object(Job, "update", update)
        patcher.start()
        self.addCleanup(patcher.stop)
        return lines

    def _one_worker(self):
        original = PromptManagerConfig.WORKER_THREADS
        PromptManagerConfig.WORKER_THREADS = 1  # batches of 4
        self.addCleanup(setattr, PromptManagerConfig, "WORKER_THREADS", original)

    def _run_scan(self):
        gate = asyncio.Event()

        async def runner(job):
            await gate.wait()

        job = self.api._jobs.start(
            "scan", "Scan for metadata", admission="records", scope={}, runner=runner
        )

        async def release():
            gate.set()
            await asyncio.wait_for(job.task, 5)

        self.addAsyncCleanup(release)
        return job


class TestThumbnailPlan(ThumbnailJobCase):
    async def test_plan_is_read_only_and_counts_cap(self):
        for name in ("a", "b", "c"):
            make_png(self.output_dir / f"{name}.png", size=(64, 64))
        fresh = make_png(self._thumb_path("a"))
        source_time = (self.output_dir / "a.png").stat().st_mtime
        os.utime(fresh, (source_time + 10, source_time + 10))
        before = _tree_state(self.comfy_dir)

        with patch.object(images_module, "MAX_THUMBNAIL_FILES", 1):
            missing = await self.client.get(
                PLAN_URL, params={"mode": "missing", "size": "512", "quality": "high"}
            )
            rebuild = await self.client.get(
                PLAN_URL, params={"mode": "rebuild", "size": "256", "quality": "low"}
            )

        self.assertEqual(missing.status, 200)
        self.assertEqual(
            await missing.json(),
            {
                "success": True,
                "targets": 1,
                "fresh": 1,
                "remaining": 1,
                "roots": ["output"],
                "mode": "missing",
                "size": 512,
                "quality": "high",
            },
        )
        data = await rebuild.json()
        # Rebuild regenerates fresh previews too: all three are work, one fits the cap.
        self.assertEqual((data["targets"], data["fresh"], data["remaining"]), (1, 1, 2))
        self.assertEqual(
            (data["mode"], data["size"], data["quality"]), ("rebuild", 256, "low")
        )
        self.assertEqual(_tree_state(self.comfy_dir), before)
        self.assertEqual(self.api._jobs.list(), [])

    async def test_plan_defaults_to_missing_previews_at_the_concept_size(self):
        make_png(self.output_dir / "a.png")
        data = await (await self.client.get(PLAN_URL)).json()
        self.assertEqual(
            (data["mode"], data["size"], data["quality"], data["targets"]),
            ("missing", 512, "medium", 1),
        )

    async def test_size_bounds_validated(self):
        make_png(self.output_dir / "a.png")
        for params in (
            {"size": "127"},
            {"size": "2049"},
            {"size": "512.5"},
            {"size": "big"},
            {"mode": "all"},
            {"quality": "ultra"},
        ):
            with self.subTest(params=params):
                resp = await self.client.get(PLAN_URL, params=params)
                body = await resp.json()
                self.assertEqual(resp.status, 400)
                self.assertEqual(
                    (body["success"], body["code"]), (False, "invalid-request")
                )
        for body in (
            {"size": 127},
            {"size": 2049},
            {"size": "512"},
            {"size": 512.0},
            {"size": True},
            {"mode": "rebuild-all"},
            {"quality": None},
            [],
        ):
            with self.subTest(body=body):
                resp = await self.client.post(JOB_URL, json=body)
                self.assertEqual(resp.status, 400)
                self.assertEqual((await resp.json())["code"], "invalid-request")
        for edge in (128, 2048):
            with self.subTest(edge=edge):
                resp = await self.client.get(PLAN_URL, params={"size": str(edge)})
                self.assertEqual(resp.status, 200)
        self.assertEqual(self.api._jobs.list(), [])
        self.assertFalse(self._thumb_path("a").exists())


class TestThumbnailJob(ThumbnailJobCase):
    async def test_job_generates_missing_previews_at_the_chosen_edge(self):
        make_png(self.output_dir / "a.png", size=(800, 400))
        make_png(self.output_dir / "sub" / "b.png", size=(64, 64))
        (self.output_dir / "bad.png").write_bytes(b"\x89PNG not really")

        status, body = await self._start(mode="missing", size=200, quality="medium")

        self.assertEqual(status, 202)
        summary = body["job"]
        self.assertEqual(
            (summary["kind"], summary["title"], summary["admission"]),
            ("thumbnails", "Thumbnail generation", "guarded"),
        )
        self.assertTrue(summary["cancellable"])
        self.assertEqual(set(summary["counters"]), {"generated", "skipped", "errors"})
        self.assertEqual(
            summary["scope"], {"mode": "missing", "size": 200, "quality": "medium"}
        )
        job = await self._finished(summary["id"])
        self.assertEqual(job.state, "finished")
        self.assertEqual(job.counters, {"generated": 2, "skipped": 0, "errors": 1})
        self.assertEqual((job.processed, job.total, job.progress), (3, 3, 100))
        self.assertEqual(job.scope["output_folders"], 1)
        with Image.open(self._thumb_path("a")) as thumb:
            self.assertEqual(thumb.size, (200, 100))
        rows = {row["path"]: row for row in job.outcomes["generated"]}
        self.assertEqual(set(rows), {"a.png", "sub/b.png"})
        self.assertEqual(
            (rows["a.png"]["root"], rows["a.png"]["root_index"]), ("output", 0)
        )
        self.assertEqual(job.outcomes["errors"][0]["path"], "bad.png")
        self.assertTrue(any("Thumbnails finished" in line for line in job.log))
        # Rows and log lines never carry the server's absolute layout.
        self.assertNotIn(str(self.comfy_dir), repr(job.detail()))

    async def test_fresh_previews_are_counted_as_skipped(self):
        make_png(self.output_dir / "a.png")
        make_png(self.output_dir / "b.png")
        fresh = make_png(self._thumb_path("a"))
        source_time = (self.output_dir / "a.png").stat().st_mtime
        os.utime(fresh, (source_time + 10, source_time + 10))

        _, body = await self._start()
        job = await self._finished(body["job"]["id"])

        self.assertEqual(job.counters, {"generated": 1, "skipped": 1, "errors": 0})
        self.assertEqual([r["path"] for r in job.outcomes["skipped"]], ["a.png"])

    async def test_rebuild_never_touches_originals(self):
        for name in ("a", "b"):
            make_png(self.output_dir / f"{name}.png", size=(400, 400))
        _, body = await self._start(size=128)
        await self._finished(body["job"]["id"])
        originals = {
            name: hashlib.sha256(
                (self.output_dir / f"{name}.png").read_bytes()
            ).hexdigest()
            for name in ("a", "b")
        }

        _, body = await self._start(mode="rebuild", size=256, quality="high")
        job = await self._finished(body["job"]["id"])

        self.assertEqual(job.counters, {"generated": 2, "skipped": 0, "errors": 0})
        for name in ("a", "b"):
            digest = hashlib.sha256(
                (self.output_dir / f"{name}.png").read_bytes()
            ).hexdigest()
            self.assertEqual(digest, originals[name], name)
            with Image.open(self._thumb_path(name)) as thumb:
                self.assertEqual(thumb.size, (256, 256))

    async def test_rebuild_keeps_old_preview_when_generation_fails(self):
        source = make_png(self.output_dir / "a.png", size=(64, 64))
        _, body = await self._start()
        await self._finished(body["job"]["id"])
        preview = self._thumb_path("a").read_bytes()
        source.write_bytes(b"\x89PNG broken now")
        later = self._thumb_path("a").stat().st_mtime + 10
        os.utime(self._thumb_path("a"), (later, later))  # still fresh

        _, body = await self._start(mode="rebuild")
        job = await self._finished(body["job"]["id"])

        self.assertEqual(job.counters["errors"], 1)
        self.assertEqual(self._thumb_path("a").read_bytes(), preview)
        leftovers = [p.name for p in self._thumb_path("a").parent.iterdir()]
        self.assertEqual(leftovers, ["a_thumb.png"])  # no temp file left

    async def test_rebuild_keeps_an_old_video_preview_instead_of_a_placeholder(self):
        clip = self.output_dir / "clip.mp4"
        clip.write_bytes(b"\x00")
        preview = make_png(self.output_dir / "thumbnails" / "clip_thumb.jpg")
        old = preview.read_bytes()
        with (
            patch.dict(sys.modules, {"cv2": None}),
            patch("subprocess.run", side_effect=FileNotFoundError("no ffmpeg")),
        ):
            _, body = await self._start(mode="rebuild")
            job = await self._finished(body["job"]["id"])
        self.assertEqual(job.counters["errors"], 1)
        self.assertEqual(preview.read_bytes(), old)

    async def test_quality_sets_the_encoder_not_the_edge(self):
        noise = bytes(
            (i * 7919 + (i >> 3) * 104729) % 251 for i in range(600 * 600 * 3)
        )
        with Image.frombytes("RGB", (600, 600), noise) as img:
            img.save(self.output_dir / "photo.jpg", "JPEG", quality=95)
        sizes = {}
        for quality in ("low", "high"):
            _, body = await self._start(mode="rebuild", size=300, quality=quality)
            await self._finished(body["job"]["id"])
            thumb = self._thumb_path("photo", ".jpg")
            with Image.open(thumb) as image:
                self.assertEqual(image.size, (300, 300))
            sizes[quality] = thumb.stat().st_size
        self.assertLess(sizes["low"], sizes["high"])

    async def test_no_output_folder_fails_the_job(self):
        self.api._get_all_output_dirs = lambda: []
        _, body = await self._start()
        job = await self._finished(body["job"]["id"])
        self.assertEqual(job.state, "failed")
        self.assertEqual(job.status_line, "ComfyUI output directory not found")

    async def test_cancel_stops_at_file_boundary_keeping_written_previews(self):
        for i in range(6):
            make_png(self.output_dir / f"p{i}.png")
        original = PromptManagerConfig.WORKER_THREADS
        PromptManagerConfig.WORKER_THREADS = 1
        self.addCleanup(setattr, PromptManagerConfig, "WORKER_THREADS", original)
        written = []
        real_write = images_module._write_image_thumbnail
        job_ids = []

        def counting(src, dst, *args, **kwargs):
            real_write(src, dst, *args, **kwargs)
            written.append(dst)
            if len(written) == 2:
                self.assertTrue(self.api._jobs.cancel(job_ids[0]))

        with patch.object(images_module, "_write_image_thumbnail", counting):
            _, body = await self._start()
            job_ids.append(body["job"]["id"])
            job = await self._finished(body["job"]["id"])

        self.assertEqual(job.state, "cancelled")
        self.assertEqual(job.counters["generated"], 2)
        self.assertEqual(len(written), 2)
        for dst in written:
            self.assertTrue(dst.is_file())
        made = sorted((self.output_dir / "thumbnails").glob("*_thumb.png"))
        self.assertEqual(made, sorted(written))
        self.assertTrue(any("Thumbnails cancelled" in line for line in job.log))

    async def test_a_second_thumbnail_job_is_refused_naming_the_first(self):
        make_png(self.output_dir / "a.png")
        entered, release = self._gate_generation()
        _, first = await self._start()
        await self._wait(entered)

        status, body = await self._start(mode="rebuild")

        self.assertEqual((status, body["code"]), (409, "busy"))
        self.assertEqual(body["running"]["id"], first["job"]["id"])
        release.set()
        await self._finished(first["job"]["id"])

    async def test_thumbnail_job_refused_while_scan_runs(self):
        make_png(self.output_dir / "a.png")
        self._run_scan()

        status, body = await self._start()

        self.assertEqual((status, body["code"]), (409, "busy"))
        self.assertEqual(body["running"]["title"], "Scan for metadata")
        self.assertFalse(self._thumb_path("a").exists())

    async def test_scan_refused_while_thumbnail_job_runs(self):
        make_png(self.output_dir / "a.png")
        entered, release = self._gate_generation()
        _, started = await self._start()
        await self._wait(entered)

        resp = await self.client.post("/prompt_manager/jobs/scan")
        body = await resp.json()

        self.assertEqual((resp.status, body["code"]), (409, "busy"))
        self.assertEqual(body["running"]["title"], "Thumbnail generation")
        release.set()
        await self._finished(started["job"]["id"])

    async def test_clear_refused_while_thumbnail_job_runs(self):
        make_png(self.output_dir / "a.png")
        kept = make_png(self.output_dir / "thumbnails" / "old_thumb.png")
        entered, release = self._gate_generation()
        _, started = await self._start()
        await self._wait(entered)

        resp = await self.client.post("/prompt_manager/images/clear-thumbnails")

        self.assertEqual(resp.status, 409)
        self.assertEqual(
            await resp.json(),
            {"success": False, "error": "Thumbnail generation already running"},
        )
        self.assertTrue(kept.exists())
        release.set()
        await self._finished(started["job"]["id"])

    async def test_every_exit_sets_a_truthful_final_status_line(self):
        """I3: finished, nothing to do, cancelled and failed each say what happened."""
        lines = self._status_lines()
        make_png(self.output_dir / "a.png")
        (self.output_dir / "bad.png").write_bytes(b"\x89PNG nope")
        _, body = await self._start()
        job = await self._finished(body["job"]["id"])
        self.assertEqual(job.status_line, "1 preview written · 1 error")

        # Every preview fresh: the job finishes with nothing to do.
        source_time = (self.output_dir / "a.png").stat().st_mtime
        bad_thumb = make_png(self._thumb_path("bad"))
        for thumb in (self._thumb_path("a"), bad_thumb):
            os.utime(thumb, (source_time + 10, source_time + 10))
        os.utime(self.output_dir / "bad.png", (source_time, source_time))
        _, body = await self._start()
        job = await self._finished(body["job"]["id"])
        self.assertEqual((job.state, job.total), ("finished", 0))
        self.assertEqual(
            job.status_line, "No previews to generate · 2 already up to date"
        )
        self.assertNotIn("Processing file 0 of 0", lines)

    async def test_an_empty_library_says_there_is_nothing_to_preview(self):
        lines = self._status_lines()
        _, body = await self._start()
        job = await self._finished(body["job"]["id"])
        self.assertEqual(
            (job.state, job.status_line), ("finished", "No images or videos to preview")
        )
        self.assertNotIn("Processing file 0 of 0", lines)

    async def test_a_cancelled_job_says_what_it_kept(self):
        for i in range(6):
            make_png(self.output_dir / f"p{i}.png")
        self._one_worker()
        real_write = images_module._write_image_thumbnail
        job_ids = []
        written = []

        def counting(src, dst, *args, **kwargs):
            real_write(src, dst, *args, **kwargs)
            written.append(dst)
            if len(written) == 1:
                self.api._jobs.cancel(job_ids[0])

        with patch.object(images_module, "_write_image_thumbnail", counting):
            _, body = await self._start()
            job_ids.append(body["job"]["id"])
            job = await self._finished(body["job"]["id"])
        self.assertEqual(job.state, "cancelled")
        self.assertEqual(job.status_line, "Cancelled · 1 preview written")

    async def test_an_internal_error_says_the_job_failed(self):
        def broken(*_args, **_kwargs):
            raise RuntimeError("boom")

        self.api._plan_gallery_thumbnails = broken
        _, body = await self._start()
        job = await self._finished(body["job"]["id"])
        self.assertEqual(
            (job.state, job.status_line),
            ("failed", "Thumbnail generation stopped on an internal error."),
        )

    async def test_cancel_during_planning_stops_the_walk_and_starts_nothing(self):
        """M2: the walk checks Cancel per file; no ``start`` frame is published."""
        for i in range(10):
            make_png(self.output_dir / f"p{i}.png")
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        checked = []
        real_fresh = images_module._thumbnail_is_fresh

        def gated_fresh(src, dst):
            checked.append(src.name)
            if len(checked) == 1:
                entered.set()
                release.wait(10)
            return real_fresh(src, dst)

        with patch.object(images_module, "_thumbnail_is_fresh", gated_fresh):
            stream = asyncio.ensure_future(self.client.post(self.PROGRESS_URL))
            await self._wait(entered)
            job = self.api._jobs.active("thumbnails")
            self.assertTrue(self.api._jobs.cancel(job.id))
            release.set()
            events = parse_sse(await (await stream).text())
            await asyncio.wait_for(job.task, 10)

        self.assertEqual(checked, ["p0.png"])  # the walk stopped at the next file
        self.assertEqual([name for name, _ in events], ["job", "status", "error"])
        self.assertEqual(job.state, "cancelled")
        self.assertEqual(job.status_line, "Cancelled before any preview was written")
        self.assertEqual(job.total, 0)
        self.assertFalse((self.output_dir / "thumbnails").exists())

    async def test_a_locked_preview_is_a_readable_coded_error(self):
        """M6: Windows refuses to replace a preview another process has open."""
        make_png(self.output_dir / "a.png")
        locked = PermissionError(13, "Access is denied")
        locked.winerror = 5

        def refuse(*_args, **_kwargs):
            raise locked

        with patch.object(images_module, "_write_image_thumbnail", refuse):
            _, body = await self._start(mode="rebuild")
            job = await self._finished(body["job"]["id"])

        self.assertEqual(job.counters["errors"], 1)
        [row] = job.outcomes["errors"]
        self.assertEqual(row["detail"], images_module.PREVIEW_IN_USE)
        self.assertNotIn("WinError", repr(job.detail()))
        self.assertTrue(any(images_module.PREVIEW_IN_USE in line for line in job.log))

    async def test_a_locked_video_preview_is_the_same_coded_error(self):
        clip = self.output_dir / "clip.mp4"
        clip.write_bytes(b"\x00")
        locked = PermissionError(13, "Access is denied")
        locked.winerror = 32

        def refuse(*_args, **_kwargs):
            raise locked

        with (
            patch.dict(sys.modules, {"cv2": None}),
            patch("subprocess.run", side_effect=FileNotFoundError("no ffmpeg")),
            patch.object(images_module, "_atomic_save", refuse),
        ):
            _, body = await self._start()
            job = await self._finished(body["job"]["id"])
        [row] = job.outcomes["errors"]
        self.assertEqual(row["detail"], images_module.PREVIEW_IN_USE)

    async def test_a_permission_error_off_windows_names_the_permission(self):
        make_png(self.output_dir / "a.png")

        def refuse(*_args, **_kwargs):
            raise PermissionError(13, "Permission denied")

        with patch.object(images_module, "_write_image_thumbnail", refuse):
            _, body = await self._start()
            job = await self._finished(body["job"]["id"])
        [row] = job.outcomes["errors"]
        self.assertEqual(row["detail"], images_module.PREVIEW_NOT_WRITABLE)

    async def test_ffmpeg_fallback_keeps_quality_and_aspect_ratio(self):
        """M1: without OpenCV, ffmpeg keeps the chosen quality and the aspect ratio."""
        (self.output_dir / "clip.mp4").write_bytes(b"\x00")
        commands = []

        def fake_ffmpeg(cmd, **_kwargs):
            commands.append(cmd)
            Path(cmd[-1]).write_bytes(b"jpeg")
            return type("Done", (), {"returncode": 0, "stderr": ""})()

        for quality, qscale in (("high", "2"), ("medium", "4"), ("low", "8")):
            with (
                patch.dict(sys.modules, {"cv2": None}),
                patch("subprocess.run", side_effect=fake_ffmpeg),
            ):
                _, body = await self._start(mode="rebuild", size=640, quality=quality)
                job = await self._finished(body["job"]["id"])
            self.assertEqual(job.counters["generated"], 1, quality)
            cmd = commands[-1]
            self.assertNotIn("-s", cmd)
            self.assertEqual(
                cmd[cmd.index("-vf") + 1],
                "scale=640:640:force_original_aspect_ratio=decrease",
            )
            self.assertEqual(cmd[cmd.index("-q:v") + 1], qscale, quality)

    async def test_the_placeholder_uses_the_chosen_quality(self):
        (self.output_dir / "clip.mp4").write_bytes(b"\x00")
        saved = []
        real_save = images_module._atomic_save

        def recording(img, dst, fmt=None, **kwargs):
            saved.append(kwargs)
            return real_save(img, dst, fmt, **kwargs)

        with (
            patch.dict(sys.modules, {"cv2": None}),
            patch("subprocess.run", side_effect=FileNotFoundError("no ffmpeg")),
            patch.object(images_module, "_atomic_save", recording),
        ):
            _, body = await self._start(quality="low")
            await self._finished(body["job"]["id"])
        self.assertEqual(saved, [images_module.THUMBNAIL_ENCODINGS["low"]])


# The legacy stream's frames and their keys, as the gallery page reads them.
LEGACY_KEYS = {
    "job": {"id", "started"},
    "status": {"phase", "message"},
    "start": {
        "total_images",
        "skipped",
        "remaining",
        "phase",
        "image_count",
        "video_count",
        "message",
    },
    "progress": {
        "processed",
        "total_images",
        "generated",
        "skipped",
        "error_count",
        "percentage",
        "rate",
        "eta",
        "elapsed",
        "current_file",
        "file_type",
        "action",
    },
    "complete": {
        "count",
        "skipped",
        "total_images",
        "remaining",
        "errors",
        "error_count",
        "elapsed_time",
        "processing_rate",
        "message",
    },
}


class TestLegacyStreamOnTheJob(ThumbnailJobCase):
    async def test_legacy_progress_events_unchanged_on_success(self):
        make_png(self.output_dir / "a.png", size=(400, 400))
        make_png(self.output_dir / "b.png")

        resp = await self.client.post(f"{self.PROGRESS_URL}?quality=low")
        events = parse_sse(await resp.text())

        self.assertEqual(
            [name for name, _ in events],
            ["job", "status", "start", "progress", "progress", "complete"],
        )
        for name, data in events:
            self.assertEqual(set(data), LEGACY_KEYS[name], name)
        complete = events[-1][1]
        self.assertEqual((complete["count"], complete["total_images"]), (2, 2))
        self.assertEqual(
            events[1][1],
            {"phase": "scanning", "message": "Scanning for images and videos..."},
        )
        with Image.open(self._thumb_path("a")) as thumb:
            self.assertEqual(max(thumb.size), 150)  # the legacy preset still sizes
        [job] = self.api._jobs.list()
        self.assertEqual(
            (job.kind, job.title, job.state, job.cancellable),
            ("thumbnails", "Thumbnail generation", "finished", True),
        )
        self.assertEqual(job.counters["generated"], 2)
        # I1: the first frame names the job this stream started, for its Cancel.
        self.assertEqual(events[0][1], {"id": job.id, "started": True})

    async def test_legacy_file_errors_still_stream(self):
        make_png(self.output_dir / "good.png")
        (self.output_dir / "bad.png").write_bytes(b"\x89PNG nope")

        resp = await self.client.post(self.PROGRESS_URL)
        events = parse_sse(await resp.text())

        self.assertEqual(
            [d for e, d in events if e == "file_error"],
            [{"file": "bad.png", "error": "Not a valid image file"}],
        )

    async def test_thumbnail_job_survives_client_disconnect(self):
        """I2: the stream sees its client go after batch 1; every batch still runs.

        One worker thread makes batches of 4, so 9 files take three batches. The
        first file waits until the client is gone; the second batch waits until
        the stream has seen it go (its subscription dropped) and records whether
        the job was still running then. A stream that stopped the work on
        disconnect would leave 4 previews; the job writes all 9.
        """
        for i in range(9):
            make_png(self.output_dir / f"p{i}.png")
        self._one_worker()
        first, closed = threading.Event(), threading.Event()
        state = {"calls": 0, "left": None, "running_when_left": None}
        original = self.api._generate_one

        def gated(*args, **kwargs):
            state["calls"] += 1
            if state["calls"] == 1:
                first.set()
                closed.wait(10)
            elif state["calls"] == 5:  # the second batch
                state["running_when_left"] = state["left"].wait(10)
            return original(*args, **kwargs)

        self.api._generate_one = gated
        self.addCleanup(closed.set)

        resp = await self.client.post(self.PROGRESS_URL)
        self.assertEqual(resp.status, 200)
        await self._wait(first)
        job = self.api._jobs.active("thumbnails")
        _subscribed, state["left"] = self._watch_subscribers(job)
        resp.close()  # the gallery tab went away
        closed.set()
        await self._wait(state["left"])  # the server saw the client go
        self.assertEqual(job.subscriber_count, 0)
        await asyncio.wait_for(job.task, 10)

        self.assertEqual(job.counters["generated"], 9)
        self.assertEqual(job.state, "finished")
        self.assertTrue(state["running_when_left"])
        for i in range(9):
            self.assertTrue(self._thumb_path(f"p{i}").is_file())

    async def test_legacy_stream_attaches_to_a_running_job(self):
        make_png(self.output_dir / "a.png")
        entered, release = self._gate_generation()
        _, started = await self._start(mode="missing", size=256)
        await self._wait(entered)

        job = self.api._jobs.get(started["job"]["id"])
        subscribed, _left = self._watch_subscribers(job)
        attached = asyncio.ensure_future(self.client.post(self.PROGRESS_URL))
        await self._wait(subscribed)  # M4: an event with a ceiling, never a poll
        release.set()
        events = parse_sse(await (await attached).text())

        self.assertEqual(events[-1][0], "complete")
        # I1: the stream names the job it did not start: the gallery never cancels it.
        self.assertEqual(events[0], ("job", {"id": job.id, "started": False}))
        # One job: the stream followed the running one with its own options.
        [listed] = self.api._jobs.list()
        self.assertIs(listed, job)
        self.assertEqual(job.scope["size"], 256)

    async def test_cancel_from_activity_ends_the_legacy_stream_with_an_error(self):
        for i in range(2):
            make_png(self.output_dir / f"p{i}.png")
        entered, release = self._gate_generation()
        stream = asyncio.ensure_future(self.client.post(self.PROGRESS_URL))
        await self._wait(entered)
        job = self.api._jobs.active("thumbnails")
        self.assertTrue(self.api._jobs.cancel(job.id))
        release.set()

        events = parse_sse(await (await stream).text())

        self.assertEqual(events[-1][0], "error")
        self.assertEqual(events[-1][1]["error"], "Thumbnail generation was cancelled")
        await asyncio.wait_for(job.task, 10)
        self.assertEqual(job.state, "cancelled")

    async def test_a_job_started_while_the_stream_prepares_is_attached(self):
        """M3: a job that starts during ``prepare`` is followed, not refused."""
        from aiohttp import web

        make_png(self.output_dir / "a.png")
        entered, release = self._gate_generation()
        real_prepare = web.StreamResponse.prepare
        raced = []

        async def prepare(response, request):
            if not raced and type(response) is web.StreamResponse:
                options, _ = thumbnail_options({"size": 256})
                raced.append(self.api._start_thumbnail_job(options))
            return await real_prepare(response, request)

        with patch.object(web.StreamResponse, "prepare", prepare):
            stream = asyncio.ensure_future(self.client.post(self.PROGRESS_URL))
            await self._wait(entered)
            release.set()
            events = parse_sse(await (await stream).text())

        [job] = raced
        self.assertEqual(events[0], ("job", {"id": job.id, "started": False}))
        self.assertEqual(events[-1][0], "complete")
        self.assertEqual(self.api._jobs.list(), [job])

    async def test_a_refused_stream_names_no_job(self):
        make_png(self.output_dir / "a.png")
        self._run_scan()
        events = parse_sse(await (await self.client.post(self.PROGRESS_URL)).text())
        self.assertEqual(
            events,
            [
                (
                    "error",
                    {
                        "error": "Scan for metadata already running",
                        "message": "Scan for metadata already running",
                    },
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
