"""Bulk image tagging as an Activity job: Add tags and Auto-tag (#249 D1).

``POST /prompt_manager/jobs/image-tags`` starts one record-changing
``image-tags`` job over 1..500 listed images. Add tags unions a fixed set
into each file's byte record; Auto-tag runs one legacy engine on a private
capture of each still image (under the shared model gate, never
downloading) and unions its top N suggestions. No real model is loaded: the
engine is B2's fake service.
"""

import asyncio
import hashlib
import json
import os
import sqlite3
import threading
import unittest
from unittest.mock import patch

try:
    from test_api_annotation_suggest import SCORED, WD14, FakeTagger, SuggestTestCase
    from test_api_images import make_png
except ImportError:
    from tests.test_api_annotation_suggest import (
        SCORED,
        WD14,
        FakeTagger,
        SuggestTestCase,
    )
    from tests.test_api_images import make_png

from database.execution_history_schema import rotate_history_epoch
from py import model_gate
from py.api import jobs as jobs_module
from py.config import AutotagConfig
from utils.media_capture import CaptureCapacity

START = "/prompt_manager/jobs/image-tags"
WAIT = 30.0
KIND = getattr(jobs_module, "IMAGE_TAG_JOB_KIND", "image-tags")
GROUPS = ("tagged", "unchanged", "skipped", "errors")


class HookedTagger(FakeTagger):
    """The B2 fake plus a hook that runs inside each inference."""

    def __init__(self):
        super().__init__()
        self.on_generate = None

    def generate_tag_suggestions(self, image_path, **kwargs):
        result = super().generate_tag_suggestions(image_path, **kwargs)
        if self.on_generate is not None:
            self.on_generate(image_path)
        return result


class ImageTagJobTestCase(SuggestTestCase):
    async def get_application(self):
        app = await super().get_application()
        self.tagger = HookedTagger()
        patcher = patch("py.autotag.get_autotag_service", return_value=self.tagger)
        patcher.start()
        self.addCleanup(patcher.stop)
        return app

    async def tearDownAsync(self):
        # Every job of the test has ended before the app goes away.
        for job in self.api._jobs.list():
            if job.task is not None and not job.task.done():
                job.task.cancel()
                await asyncio.gather(job.task, return_exceptions=True)
        await super().tearDownAsync()

    # -- helpers ---------------------------------------------------------

    def png(self, path, size=(8, 6), color=(200, 120, 40)):
        make_png(self.output_dir / path, size=size, color=color)
        return self.output_dir / path

    def digest(self, path):
        return hashlib.sha256((self.output_dir / path).read_bytes()).hexdigest()

    def stored(self, path):
        return self.api.db.read_image_tags(self.locator(path), self.digest(path))

    def add_body(self, paths, tags, **changes):
        return {
            "mode": "add",
            "targets": [self.target(p) for p in paths],
            "tags": list(tags),
            **changes,
        }

    def auto_body(self, paths, model_type="wd14-vit", options=None, **changes):
        return {
            "mode": "auto",
            "targets": [self.target(p) for p in paths],
            "model_type": model_type,
            "options": dict(WD14) if options is None else options,
            **changes,
        }

    async def start(self, body):
        response = await self.client.post(START, json=body)
        return response.status, await self.json_of(response)

    async def start_ok(self, body):
        status, data = await self.start(body)
        self.assertEqual(status, 202, data)
        return self.api._jobs.get(data["job"]["id"])

    async def run_job(self, body):
        job = await self.start_ok(body)
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        return job

    def rows(self, job, group):
        return [(row["path"], row["detail"]) for row in job.outcomes[group]]

    async def wait_for(self, event):
        ok = await asyncio.get_running_loop().run_in_executor(None, event.wait, WAIT)
        self.assertTrue(ok, "the fake engine was never entered")


class TestAddTags(ImageTagJobTestCase):
    async def test_add_job_unions_tags_on_each_target_and_records_outcomes(self):
        for name in ("a.png", "b.png", "c.png"):
            self.png(name, color=(len(name), ord(name[0]), 9))
        self.saved_set("a.png", (self.output_dir / "a.png").read_bytes(), ["keep_me"])
        self.saved_set(
            "c.png", (self.output_dir / "c.png").read_bytes(), ["Sandstone", "desert"]
        )

        job = await self.run_job(
            self.add_body(
                ["a.png", "b.png", "c.png", "gone.png"], ["sandstone", "desert"]
            )
        )

        self.assertEqual(job.kind, KIND)
        self.assertEqual(job.title, "Add tags to images")
        self.assertEqual(job.state, "finished")
        self.assertTrue(job.record_changing)
        self.assertEqual(job.scope, {"mode": "add", "images": 4})
        self.assertEqual(
            job.counters, {"tagged": 2, "unchanged": 1, "skipped": 1, "errors": 0}
        )
        self.assertEqual(
            self.stored("a.png")["tags"], ["keep_me", "sandstone", "desert"]
        )
        self.assertEqual(self.stored("b.png")["tags"], ["sandstone", "desert"])
        # The stored spelling wins; nothing new means no write.
        self.assertEqual(self.stored("c.png")["tags"], ["Sandstone", "desert"])
        self.assertEqual(self.stored("c.png")["tag_revision"], 1)
        self.assertEqual(
            self.rows(job, "tagged"),
            [("a.png", "Added 2 tags"), ("b.png", "Added 2 tags")],
        )
        self.assertEqual(self.rows(job, "unchanged"), [("c.png", "No new tags")])
        self.assertEqual(self.rows(job, "skipped"), [("gone.png", "File not found")])
        self.assertEqual(job.outcomes["tagged"][0]["root_index"], 0)
        self.assertEqual((job.processed, job.total, job.progress), (4, 4, 100))
        self.assertTrue(any("+ " in line and "a.png" in line for line in job.log))
        self.assertTrue(job.log[-1].endswith("Add tags finished · 4 files processed"))
        # Add tags never needs the model.
        self.assertEqual(self.tagger.load_calls, [])
        self.assertIsNone(model_gate.holder())

    async def test_add_tags_vocabulary_names_become_global_tags(self):
        self.png("a.png")

        await self.run_job(self.add_body(["a.png"], ["brand_new_tag"]))

        names = set(self.api.db.get_all_tags())
        self.assertIn("brand_new_tag", names)

    async def test_add_over_the_tag_cap_is_an_error_row_with_zero_writes(self):
        self.png("a.png")
        full = [f"t{i:03d}" for i in range(100)]
        self.saved_set("a.png", (self.output_dir / "a.png").read_bytes(), full)

        job = await self.run_job(self.add_body(["a.png"], ["one_more"]))

        self.assertEqual(job.counters["errors"], 1)
        self.assertEqual(
            self.rows(job, "errors"),
            [("a.png", "An image can carry at most 100 tags.")],
        )
        self.assertEqual(self.stored("a.png")["tags"], full)

    async def test_video_skipped_in_auto_and_tagged_in_add(self):
        (self.output_dir / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" * 8)

        added = await self.run_job(self.add_body(["clip.mp4"], ["motion"]))
        self.assertEqual(self.rows(added, "tagged"), [("clip.mp4", "Added 1 tag")])
        self.assertEqual(self.stored("clip.mp4")["tags"], ["motion"])

        auto = await self.run_job(self.auto_body(["clip.mp4"]))
        self.assertEqual(
            self.rows(auto, "skipped"), [("clip.mp4", "Video: manual tags only")]
        )
        self.assertEqual(self.tagger.calls, [])
        self.assertEqual(self.stored("clip.mp4")["tags"], ["motion"])

    async def test_not_a_still_image_and_too_large_are_skipped(self):
        (self.output_dir / "broken.png").write_bytes(b"not an image")
        self.png("big.png")
        self.api._capture_store().max_file = 16

        auto = await self.run_job(self.auto_body(["big.png"]))
        self.assertEqual(
            self.rows(auto, "skipped"), [("big.png", "Too large to verify")]
        )

        self.api._capture_store().max_file = 2**20
        auto = await self.run_job(self.auto_body(["broken.png"]))
        self.assertEqual(
            self.rows(auto, "skipped"), [("broken.png", "Not a supported still image")]
        )

        self.api._capture_store().max_file = 16
        added = await self.run_job(self.add_body(["big.png"], ["x"]))
        self.assertEqual(
            self.rows(added, "skipped"), [("big.png", "Too large to verify")]
        )
        self.assertEqual(self.tagger.calls, [])


class TestAutoTag(ImageTagJobTestCase):
    async def test_auto_job_applies_the_requested_count_per_file_without_removing_existing(  # noqa: E501
        self,
    ):
        self.png("a.png")
        self.png("b.png", color=(1, 2, 3))
        self.saved_set("a.png", (self.output_dir / "a.png").read_bytes(), ["keep_me"])
        options = {**WD14, "suggestion_limit": 2}

        job = await self.run_job(self.auto_body(["a.png", "b.png"], options=options))

        self.assertEqual(job.title, "Auto-tag images")
        self.assertEqual(
            job.scope, {"mode": "auto", "images": 2, "model_type": "wd14-vit"}
        )
        self.assertEqual(job.state, "finished")
        self.assertEqual(
            self.api.db.read_image_tags(self.locator("a.png"), self.digest("a.png"))[
                "tags"
            ],
            ["keep_me", "sandstone", "desert"],
        )
        self.assertEqual(self.stored("b.png")["tags"], ["sandstone", "desert"])
        self.assertEqual(
            job.counters, {"tagged": 2, "unchanged": 0, "skipped": 0, "errors": 0}
        )
        # Inference read the private captures only, never the current files.
        for path in self.tagger.read_paths:
            self.assertNotIn(str(self.output_dir), path)
        self.assertEqual([call["limit"] for call in self.tagger.calls], [2, 2])

        # limit 3: at most three names are added to a file.
        self.png("c.png", color=(9, 9, 9))
        options = {**WD14, "suggestion_limit": 3}
        job = await self.run_job(self.auto_body(["c.png"], options=options))
        self.assertEqual(self.stored("c.png")["tags"], [name for name, _ in SCORED[:3]])
        self.assertEqual(self.rows(job, "tagged"), [("c.png", "Added 3 tags")])

        # Omitted: the configured default.
        AutotagConfig.SUGGESTION_LIMIT = 4
        self.png("d.png", color=(7, 7, 7))
        await self.run_job(self.auto_body(["d.png"]))
        self.assertEqual(self.tagger.calls[-1]["limit"], 4)
        self.assertEqual(len(self.stored("d.png")["tags"]), 4)

        # Suggestions the file already carries add nothing.
        job = await self.run_job(self.auto_body(["d.png"]))
        self.assertEqual(self.rows(job, "unchanged"), [("d.png", "No new tags")])

    async def test_auto_job_holds_gate_and_never_downloads(self):
        self.png("a.png")
        self.png("b.png", color=(1, 2, 3))
        entered, proceed = threading.Event(), threading.Event()
        self.tagger.block = (entered, proceed)

        job = await self.start_ok(self.auto_body(["a.png", "b.png"]))
        await self.wait_for(entered)
        holder = model_gate.holder()
        self.assertIsNotNone(holder)
        self.assertTrue(holder.startswith("image-tag-job:"), holder)
        # A legacy route cannot swap the model under the running job.
        response = await self.client.post("/prompt_manager/autotag/unload")
        self.assertEqual(response.status, 409)
        self.assertEqual((await response.json())["code"], "model-busy")
        proceed.set()
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)

        self.assertEqual(job.counters["tagged"], 2)
        self.assertEqual(self.tagger.load_calls, [("wd14-vit", True)])
        self.tagger.download_model.assert_not_called()
        self.assertIsNone(model_gate.holder())

    async def test_a_missing_backend_package_fails_the_job_before_the_first_file(self):
        self.png("a.png")
        self.tagger.missing["wd14-vit"] = ["onnxruntime"]

        with self.assertNoLogs(self.api.logger, level="ERROR"):
            job = await self.run_job(self.auto_body(["a.png"]))

        self.assertEqual(job.state, "failed")
        self.assertEqual(job.processed, 0)
        self.assertTrue(job.status_line.startswith("WD14 ViT needs onnxruntime."))
        self.assertIn("pip install onnxruntime", job.status_line)
        self.assertEqual(self.tagger.load_calls, [])

    async def test_model_unavailable_fails_the_job_before_the_first_file(self):
        self.png("a.png")
        self.tagger.downloaded["wd14-vit"] = False

        job = await self.run_job(self.auto_body(["a.png"]))

        self.assertEqual(job.state, "failed")
        self.assertEqual(job.processed, 0)
        self.assertEqual(job.counters, dict.fromkeys(GROUPS, 0))
        self.assertTrue(
            any(
                line.endswith(
                    "is not downloaded. Download it in System › Auto-tagging."
                )
                for line in job.log
            ),
            job.log,
        )
        self.assertEqual(self.tagger.load_calls, [])
        self.tagger.download_model.assert_not_called()
        self.assertIsNone(model_gate.holder())
        self.assertEqual(self.api._capture_store().stats()["captures"], 0)

    async def test_model_load_failure_fails_the_job_and_frees_the_gate(self):
        self.png("a.png")
        self.tagger.load_error = RuntimeError("/secret/models/wd14 exploded")

        job = await self.run_job(self.auto_body(["a.png"]))

        self.assertEqual(job.state, "failed")
        self.assertEqual(job.processed, 0)
        self.assertNotIn("/secret", json.dumps(job.detail()))
        self.assertIsNone(model_gate.holder())

    async def test_inference_failure_is_an_error_row_and_the_job_goes_on(self):
        self.png("a.png")
        self.png("b.png", color=(1, 2, 3))
        failing = {"first": True}

        def explode(_path):
            if failing.pop("first", False):
                raise RuntimeError("/secret/path boom")

        self.tagger.on_generate = explode

        job = await self.run_job(self.auto_body(["a.png", "b.png"]))

        self.assertEqual(job.counters["errors"], 1)
        self.assertEqual(job.counters["tagged"], 1)
        self.assertEqual(
            self.rows(job, "errors"),
            [("a.png", "Auto-tagging failed. Check server logs for details.")],
        )
        self.assertNotIn("/secret", json.dumps(job.report()))
        self.assertIsNone(model_gate.holder())

    async def test_changed_file_is_skipped_not_tagged(self):
        self.png("a.png")
        before = self.digest("a.png")

        def rewrite(_capture_path):
            make_png(self.output_dir / "a.png", size=(8, 6), color=(1, 1, 1))

        self.tagger.on_generate = rewrite

        job = await self.run_job(self.auto_body(["a.png"]))

        self.assertEqual(self.rows(job, "skipped"), [("a.png", "File changed")])
        self.assertEqual(job.counters["tagged"], 0)
        locator = self.locator("a.png")
        self.assertEqual(self.api.db.read_image_tags(locator, before)["tags"], [])
        self.assertEqual(self.stored("a.png")["tags"], [])

    async def test_captures_released_and_handles_closed(self):
        try:
            from open_handles import assert_closed
        except ImportError:
            from tests.open_handles import assert_closed
        for index in range(3):
            self.png(f"f{index}.png", color=(index, 0, 0))

        await self.run_job(self.auto_body([f"f{index}.png" for index in range(3)]))

        store = self.api._capture_store()
        self.assertEqual(store.stats()["captures"], 0)
        assert_closed(self, store.directory)
        assert_closed(self, str(self.output_dir))


class TestStartAndExclusion(ImageTagJobTestCase):
    async def test_start_returns_model_busy_or_job_busy(self):
        self.png("a.png")
        other = model_gate.new_owner("legacy-batch")
        self.assertTrue(model_gate.try_acquire(other))
        try:
            status, body = await self.start(self.auto_body(["a.png"]))
            self.assertEqual((status, body["code"]), (409, "model-busy"))
            self.assertEqual(self.api._jobs.list(), [])
            # Add tags needs no model: it starts while the gate is held.
            job = await self.run_job(self.add_body(["a.png"], ["x"]))
            self.assertEqual(job.state, "finished")
        finally:
            model_gate.release(other)

        entered, proceed = threading.Event(), threading.Event()
        self.tagger.block = (entered, proceed)
        job = await self.start_ok(self.auto_body(["a.png"]))
        await self.wait_for(entered)
        try:
            for body in (self.add_body(["a.png"], ["y"]), self.auto_body(["a.png"])):
                resp = await self.client.post(START, json=body)
                body = await resp.json()
                self.assertEqual((resp.status, body["code"]), (409, "busy"))
                self.assertEqual(
                    body["running"], {"id": job.id, "title": "Auto-tag images"}
                )
        finally:
            proceed.set()
            await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        # A refused start never leaves the gate taken.
        self.assertIsNone(model_gate.holder())

    async def test_image_tag_job_refused_while_legacy_restore_holds(self):
        self.png("a.png")
        async with self.api._jobs.hold(
            "restore", "Database restore (admin page)", admission="records"
        ):
            status, body = await self.start(self.add_body(["a.png"], ["x"]))
            self.assertEqual((status, body["code"]), (409, "busy"))
            self.assertEqual(body["running"]["title"], "Database restore (admin page)")
            status, body = await self.start(self.auto_body(["a.png"]))
            self.assertEqual((status, body["code"]), (409, "busy"))
            self.assertIsNone(model_gate.holder())
        async with self.api._jobs.hold(
            "thumbnails", "Thumbnail generation (gallery page)", admission="guarded"
        ):
            status, body = await self.start(self.add_body(["a.png"], ["x"]))
            self.assertEqual((status, body["code"]), (409, "busy"))

    async def test_record_changing_excludes_running_scan(self):
        self.png("a.png")
        gate = asyncio.Event()

        async def scan_runner(job):
            await gate.wait()

        scan = self.api._jobs.start(
            "scan",
            "Scan for metadata",
            record_changing=True,
            scope={"files": "new"},
            runner=scan_runner,
        )
        try:
            status, body = await self.start(self.add_body(["a.png"], ["x"]))
            self.assertEqual((status, body["code"]), (409, "busy"))
            self.assertEqual(body["running"]["title"], "Scan for metadata")
            status, body = await self.start(self.auto_body(["a.png"]))
            self.assertEqual((status, body["code"]), (409, "busy"))
            self.assertIsNone(model_gate.holder())
        finally:
            gate.set()
            await scan.task

        # The other way round: a running tagging job keeps the scan out.
        entered, proceed = threading.Event(), threading.Event()
        self.tagger.block = (entered, proceed)
        job = await self.start_ok(self.auto_body(["a.png"]))
        await self.wait_for(entered)
        try:
            resp = await self.client.post("/prompt_manager/jobs/scan", json={})
            self.assertEqual(resp.status, 409)
            self.assertEqual((await resp.json())["running"]["id"], job.id)
        finally:
            proceed.set()
            await asyncio.wait_for(asyncio.shield(job.task), WAIT)

    async def test_a_start_that_loses_the_slot_race_frees_the_gate(self):
        self.png("a.png")
        gate = asyncio.Event()

        async def scan_runner(job):
            await gate.wait()

        scan = self.api._jobs.start(
            "scan",
            "Scan for metadata",
            record_changing=True,
            scope={},
            runner=scan_runner,
        )
        try:
            # The pre-check saw a free slot; registry.start still refuses.
            with patch.object(self.api._jobs, "blocker", side_effect=[None, scan]):
                status, body = await self.start(self.auto_body(["a.png"]))
            self.assertEqual((status, body["code"]), (409, "busy"))
            self.assertIsNone(model_gate.holder())
        finally:
            gate.set()
            await scan.task

    async def test_cancel_stops_at_file_boundary_and_keeps_written_files(self):
        for index in range(3):
            self.png(f"f{index}.png", color=(index, 5, 5))
        entered, proceed = threading.Event(), threading.Event()
        self.tagger.block = (entered, proceed)

        job = await self.start_ok(self.auto_body([f"f{i}.png" for i in range(3)]))
        await self.wait_for(entered)
        response = await self.client.post(f"/prompt_manager/jobs/{job.id}/cancel")
        self.assertTrue((await response.json())["cancellation_requested"])
        proceed.set()
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)

        self.assertEqual(job.state, "cancelled")
        self.assertEqual(job.counters["tagged"], 1)
        self.assertEqual(job.processed, 1)
        self.assertEqual(len(self.stored("f0.png")["tags"]), 10)
        self.assertEqual(self.stored("f1.png")["tags"], [])
        self.assertEqual(len(self.tagger.calls), 1)
        self.assertTrue(job.log[-1].endswith("Auto-tag cancelled · 1 file processed"))
        self.assertIsNone(model_gate.holder())
        self.assertEqual(self.api._capture_store().stats()["captures"], 0)

    async def test_stale_root_scope_refuses_the_start(self):
        self.png("a.png")
        body = self.add_body(["a.png"], ["x"])
        body["targets"][0]["root_scope"] = "0" * 64

        status, data = await self.start(body)

        self.assertEqual((status, data["code"]), (409, "source-changed"))
        self.assertEqual(self.api._jobs.list(), [])

    async def test_restore_during_the_job_stops_it_without_writes(self):
        self.png("a.png")
        self.png("b.png", color=(1, 2, 3))

        def rotate(_path):
            conn = sqlite3.connect(self._temp_db.name)
            try:
                rotate_history_epoch(conn)
            finally:
                conn.close()

        self.tagger.on_generate = rotate

        job = await self.run_job(self.auto_body(["a.png", "b.png"]))

        self.assertEqual(job.state, "failed")
        self.assertEqual(job.counters["tagged"], 0)
        self.assertEqual(
            self.rows(job, "errors"),
            [("a.png", "Prompt storage was restored; start the job again.")],
        )
        self.assertEqual(self.stored("a.png")["tags"], [])
        self.assertEqual(len(self.tagger.calls), 1)
        self.assertIsNone(model_gate.holder())

    async def test_invalid_bodies_rejected(self):
        self.png("a.png")
        target = self.target("a.png")
        bad_bodies = [
            {"mode": "add", "targets": [], "tags": ["x"]},
            {"mode": "add", "targets": [target] * 501, "tags": ["x"]},
            {"mode": "add", "targets": "a.png", "tags": ["x"]},
            {"mode": "add", "targets": [{**target, "path": "../a.png"}], "tags": ["x"]},
            {"mode": "add", "targets": [{**target, "root_index": True}], "tags": ["x"]},
            {"mode": "add", "targets": [{**target, "extra": 1}], "tags": ["x"]},
            {"mode": "add", "targets": [{**target, "path": "a.txt"}], "tags": ["x"]},
            {"mode": "add", "targets": [target], "tags": []},
            {"mode": "add", "targets": [target], "tags": "x"},
            {"mode": "add", "targets": [target], "tags": ["bad\nname"]},
            {"mode": "add", "targets": [target]},
            {"mode": "add", "targets": [target], "tags": ["x"], "extra": 1},
            {"mode": "add", "targets": [target], "tags": ["x"], "model_type": "hf"},
            {"mode": "remove", "targets": [target], "tags": ["x"]},
            {"targets": [target], "tags": ["x"]},
            {"mode": "auto", "targets": [target], "model_type": "wd14-vit"},
            {"mode": "auto", "targets": [target], "model_type": "x", "options": {}},
            {
                "mode": "auto",
                "targets": [target],
                "model_type": "wd14-vit",
                "options": {},
            },
            {
                "mode": "auto",
                "targets": [target],
                "model_type": "wd14-vit",
                "options": {**WD14, "general_threshold": 2},
            },
            {
                "mode": "auto",
                "targets": [target],
                "model_type": "wd14-vit",
                "options": {**WD14, "suggestion_limit": 0},
            },
            {
                "mode": "auto",
                "targets": [target],
                "model_type": "gguf",
                "options": dict(WD14),
            },
            {
                "mode": "auto",
                "targets": [target],
                "model_type": "wd14-vit",
                "options": dict(WD14),
                "tags": ["x"],
            },
        ]
        for body in bad_bodies:
            with self.subTest(body=json.dumps(body)[:120]):
                status, data = await self.start(body)
                self.assertIn(status, (400, 403), data)
                self.assertFalse(data["success"])
        too_many = {
            "mode": "add",
            "targets": [target],
            "tags": [f"t{i}" for i in range(101)],
        }
        status, data = await self.start(too_many)
        self.assertEqual((status, data["code"]), (400, "tag-limit"))
        response = await self.client.post(START, data="not json")
        self.assertEqual(response.status, 400)
        self.assertEqual(self.api._jobs.list(), [])
        self.assertIsNone(model_gate.holder())

    async def test_duplicate_targets_are_tagged_once(self):
        self.png("a.png")

        job = await self.run_job(self.add_body(["a.png", "a.png"], ["x"]))

        self.assertEqual(job.scope["images"], 1)
        self.assertEqual(job.counters["tagged"], 1)

    async def test_rows_and_report_carry_no_absolute_paths(self):
        self.png("sub/a.png")
        self.png("sub/b.png", color=(3, 3, 3))
        job = await self.run_job(
            self.auto_body(["sub/a.png", "sub/b.png", "sub/missing.png"])
        )

        for url in (
            START.rsplit("/", 1)[0],
            f"/prompt_manager/jobs/{job.id}",
            f"/prompt_manager/jobs/{job.id}/report",
        ):
            response = await self.client.get(url)
            self.assertEqual(response.status, 200)
            body = json.loads(await response.text())
            self.assert_no_paths(body)
        detail = job.detail()
        self.assertEqual(
            [row["path"] for row in detail["outcomes"]["tagged"]],
            ["sub/a.png", "sub/b.png"],
        )
        self.assertTrue(
            all(row["root"] == "output" for row in detail["outcomes"]["tagged"])
        )
        self.assertTrue(any("output/sub/a.png" in line for line in detail["log"]))


class TestFixRound(ImageTagJobTestCase):
    """The final review's fixes: estimate, body bound, busy captures, roots."""

    async def test_status_line_estimates_minutes_left_after_the_minimum(self):
        for index in range(3):
            self.png(f"f{index}.png", color=(index, 7, 7))
        clock = iter([0.0, 0.0, 120.0, 240.0])
        lines = []
        self.tagger.on_generate = lambda _path: lines.append(
            self.api._jobs.list()[0].status_line
        )

        with patch(
            "py.api.image_tag_jobs._monotonic", side_effect=lambda: next(clock, 240.0)
        ):
            await self.run_job(self.auto_body([f"f{i}.png" for i in range(3)]))

        # Two files make the minimum (the scan's MIN_BATCHES_FOR_ESTIMATE); then
        # 120 s per file leaves about 2 min for the third.
        self.assertEqual(
            lines,
            [
                "Processing file 1 of 3",
                "Processing file 2 of 3",
                "Processing file 3 of 3 · about 2 min left",
            ],
        )

    async def test_an_ended_job_shows_no_time_left(self):
        """#379 live check: a finished or cancelled job never says "min left"."""
        for index in range(3):
            self.png(f"f{index}.png", color=(index, 7, 7))
        clock = iter([0.0, 0.0, 120.0, 240.0])
        with patch(
            "py.api.image_tag_jobs._monotonic", side_effect=lambda: next(clock, 240.0)
        ):
            job = await self.run_job(self.auto_body([f"f{i}.png" for i in range(3)]))
        self.assertEqual(job.state, "finished")
        self.assertEqual(job.status_line, "Auto-tag finished · 3 files processed")

        self.png("g0.png", color=(1, 1, 1))
        self.png("g1.png", color=(2, 2, 2))
        entered, proceed = threading.Event(), threading.Event()
        self.tagger.block = (entered, proceed)
        job = await self.start_ok(self.auto_body(["g0.png", "g1.png"]))
        await self.wait_for(entered)
        await self.client.post(f"/prompt_manager/jobs/{job.id}/cancel")
        self.tagger.block = None
        proceed.set()
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        self.assertEqual(job.state, "cancelled")
        self.assertEqual(job.status_line, "Auto-tag cancelled · 1 file processed")
        self.assertNotIn("left", job.status_line)

    def maximal_target(self, number):
        """A distinct target whose path is 1024 non-ASCII code points long."""
        head = f"{number:03d}/" + ("界" * 200 + "/") * 5
        path = head + "界" * (1024 - len(head) - 4) + ".png"
        self.assertEqual(len(path), 1024)
        return self.target(path)

    async def test_start_body_bounds(self):
        from py.api import image_tag_jobs

        body = {
            "mode": "add",
            "targets": [self.maximal_target(n) for n in range(500)],
            "tags": ["x"],
        }
        # Escaped as \uXXXX (6 bytes a code point), far over the old 1 MiB.
        payload = json.dumps(body).encode("utf-8")
        self.assertGreater(len(payload), 3 * 1000 * 1000)
        response = await self.client.post(
            START, data=payload, headers={"Content-Type": "application/json"}
        )
        data = await self.json_of(response)
        self.assertEqual(response.status, 202, data)
        self.assertEqual(data["job"]["scope"]["images"], 500)
        job = self.api._jobs.get(data["job"]["id"])
        await self.client.post(f"/prompt_manager/jobs/{job.id}/cancel")
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)

        cap = image_tag_jobs.MAX_JOB_BODY_BYTES
        self.assertGreaterEqual(cap, len(payload))
        over = b'{"mode":"add","targets":[],"tags":["x"]'
        over += b" " * (cap + 1 - len(over) - 1) + b"}"
        self.assertEqual(len(over), cap + 1)
        response = await self.client.post(
            START, data=over, headers={"Content-Type": "application/json"}
        )
        self.assertEqual(response.status, 413)

    def saturate(self, store):
        with store._lock:
            store._workers += store.max_workers
        self.addCleanup(self.unsaturate, store)

    def unsaturate(self, store):
        with store._lock:
            if store._workers >= store.max_workers:
                store._workers -= store.max_workers

    async def test_busy_capture_workers_are_retried_then_left_for_a_rerun(self):
        self.png("a.png")
        self.png("b.png", color=(1, 2, 3))
        store = self.api._capture_store()
        self.saturate(store)

        with patch("py.api.image_tag_jobs.CAPACITY_BACKOFF", (0.001, 0.001)):
            added = await self.run_job(self.add_body(["a.png"], ["x"]))
            auto = await self.run_job(self.auto_body(["b.png"]))

        retry = "The file could not be read now. Run the job again for it."
        for job, path in ((added, "a.png"), (auto, "b.png")):
            self.assertEqual(job.state, "finished")
            self.assertEqual(self.rows(job, "skipped"), [(path, retry)])
            self.assertEqual(job.counters["errors"], 0)
            self.assertNotIn("Tag dialog", json.dumps(job.detail()))
        self.assertEqual(self.tagger.calls, [])
        self.assertEqual(self.stored("a.png")["tags"], [])

    async def test_a_busy_capture_worker_that_frees_up_is_tagged(self):
        self.png("a.png")
        store = self.api._capture_store()
        real = store.verify
        tries = []

        def verify(root, relative):
            tries.append(relative)
            if len(tries) < 3:
                raise CaptureCapacity("Too many captures are in progress")
            return real(root, relative)

        with (
            patch.object(store, "verify", side_effect=verify),
            patch("py.api.image_tag_jobs.CAPACITY_BACKOFF", (0.001, 0.001, 0.001)),
        ):
            job = await self.run_job(self.add_body(["a.png"], ["x"]))

        self.assertEqual(len(tries), 3)
        self.assertEqual(self.rows(job, "tagged"), [("a.png", "Added 1 tag")])
        self.assertEqual(self.stored("a.png")["tags"], ["x"])

    async def test_output_roots_are_read_once_per_job_off_the_event_loop(self):
        for index in range(3):
            self.png(f"f{index}.png", color=(index, 1, 1))
        body = self.add_body([f"f{i}.png" for i in range(3)], ["x"])
        original = self.api._get_all_output_dirs
        threads = []

        def lookup():
            threads.append(threading.current_thread())
            return original()

        with patch.object(self.api, "_get_all_output_dirs", side_effect=lookup):
            job = await self.run_job(body)

        self.assertEqual(job.counters["tagged"], 3)
        # One read when the start checks the scopes, one for the whole run.
        self.assertEqual(len(threads), 2)
        self.assertNotIn(threading.main_thread(), threads)
        self.assertEqual(job.outcomes["tagged"][0]["root"], "output")


if __name__ == "__main__":
    unittest.main()


class TestFilterStart(ImageTagJobTestCase):
    """#392: "Select all N matching" starts the job with the listing filter."""

    async def test_filter_start_tags_every_matching_file_in_listing_order(self):
        for number in range(6):
            path = self.png(f"bulk/{number:02}.png", color=(number, 1, 1))
            os.utime(path, (100 + number, 100 + number))
        self.png("other/x.png")

        job = await self.run_job(
            {
                "mode": "add",
                "filter": {"subfolder": "bulk", "sort": "oldest"},
                "tags": ["picked"],
            }
        )

        self.assertEqual(job.scope, {"mode": "add", "images": 6})
        self.assertEqual(job.counters["tagged"], 6)
        self.assertEqual(
            [path for path, _detail in self.rows(job, "tagged")],
            [f"bulk/{number:02}.png" for number in range(6)],
        )
        self.assertEqual(self.stored("bulk/03.png")["tags"], ["picked"])
        self.assertEqual(self.stored("other/x.png")["tags"], [])

    async def test_auto_filter_start_runs_the_model_on_the_matching_files(self):
        self.png("bulk/a.png")
        self.png("other/b.png", color=(1, 2, 3))

        job = await self.run_job(
            {
                "mode": "auto",
                "filter": {"subfolder": "bulk"},
                "model_type": "wd14-vit",
                "options": dict(WD14),
            }
        )

        self.assertEqual(job.scope["images"], 1)
        self.assertEqual(job.counters["tagged"], 1)
        self.assertEqual(self.stored("other/b.png")["tags"], [])

    async def test_filter_with_no_match_is_refused_without_a_job(self):
        self.png("a.png")

        status, data = await self.start(
            {"mode": "add", "filter": {"subfolder": "missing"}, "tags": ["x"]}
        )

        self.assertEqual((status, data["code"]), (400, "no-images"))
        self.assertEqual(self.api._jobs.list(), [])

    async def test_filter_with_a_deleted_collection_is_a_404_without_a_job(self):
        self.png("a.png")

        status, data = await self.start(
            {"mode": "add", "filter": {"collection": "77"}, "tags": ["x"]}
        )

        self.assertEqual((status, data["code"]), (404, "collection-not-found"))
        self.assertEqual(self.api._jobs.list(), [])

    async def test_filter_over_the_cap_is_refused_without_a_job(self):
        from py.api import image_tag_jobs

        for number in range(3):
            self.png(f"{number}.png", color=(number, 0, 0))
        with patch.object(image_tag_jobs, "MAX_FILTER_TARGETS", 2):
            status, data = await self.start(
                {"mode": "add", "filter": {}, "tags": ["x"]}
            )

        self.assertEqual((status, data["code"]), (400, "too-many-images"))
        self.assertEqual(self.api._jobs.list(), [])
        self.assertEqual(image_tag_jobs.MAX_FILTER_TARGETS, 10_000)

    async def test_filter_bodies_are_validated(self):
        self.png("a.png")
        target = self.target("a.png")
        for body in (
            {"mode": "add", "filter": {}, "targets": [target], "tags": ["x"]},
            {"mode": "add", "filter": "all", "tags": ["x"]},
            {"mode": "add", "filter": {"sort": "sideways"}, "tags": ["x"]},
            {"mode": "add", "filter": {"cursor": "x"}, "tags": ["x"]},
            {"mode": "add", "filter": {"root": "7"}, "tags": ["x"]},
            {"mode": "add", "filter": {}, "tags": []},
        ):
            with self.subTest(body=body):
                status, data = await self.start(body)
                self.assertEqual(status, 400, data)
        self.assertEqual(self.api._jobs.list(), [])

    async def test_a_filter_whose_source_was_renumbered_is_refused(self):
        self.png("a.png")
        scope = self.target("a.png")["root_scope"]

        job = await self.run_job(
            {"mode": "add", "filter": {"root": "0", "root_scope": scope}, "tags": ["x"]}
        )
        self.assertEqual(job.counters["tagged"], 1)
        status, data = await self.start(
            {
                "mode": "add",
                "filter": {"root": "0", "root_scope": "f" * 64},
                "tags": ["x"],
            }
        )
        self.assertEqual((status, data["code"]), (409, "source-changed"))
        status, data = await self.start(
            {"mode": "add", "filter": {"root_scope": scope}, "tags": ["x"]}
        )
        self.assertEqual(status, 400, data)
