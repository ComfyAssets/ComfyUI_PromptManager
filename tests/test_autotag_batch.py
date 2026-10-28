"""Tag output images tags each image, never its prompt (#379, PR 1).

``POST /prompt_manager/jobs/autotag`` runs the ``image-tags`` loop over every
still image the output folders list, linked to a prompt or not: each image
gets its own model tags in its image tag set and is marked auto-tagged.
"Skip already tagged" skips images whose current bytes are marked, counted
before the loop without opening them. Prompt tags and prompt text never
change. No real model is loaded: the engine is a fake that answers per image.
"""

import asyncio
import json
import ntpath
import os
import posixpath
import re
import sqlite3
import threading
from pathlib import Path
from unittest.mock import patch

try:
    from test_api_images import make_png
    from test_image_tag_jobs import HookedTagger, ImageTagJobTestCase
except ImportError:
    from tests.test_api_images import make_png
    from tests.test_image_tag_jobs import HookedTagger, ImageTagJobTestCase

from database.execution_history_schema import rotate_history_epoch
from py import model_gate
from py.api import admin as admin_module
from py.api import image_tag_jobs as jobs_module
from py.config import PromptManagerConfig
from utils.hashing import generate_prompt_hash

JOB = "/prompt_manager/jobs/autotag"
PREVIEW = "/prompt_manager/autotag/batch/preview"
WAIT = 30.0
WD14 = {"general_threshold": 0.35, "character_threshold": 0.85}
CANCELLED = "Cancelled. Tags already added were kept."
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


class PerImageTagger(HookedTagger):
    """Answers each image from its own bytes: ``by_bytes`` maps exact file
    contents to the names the "model" sees; any other image gets SCORED."""

    def __init__(self):
        super().__init__()
        self.by_bytes = {}

    def generate_tag_suggestions(self, image_path, **kwargs):
        result = super().generate_tag_suggestions(image_path, **kwargs)
        names = self.by_bytes.get(self.read_bytes[-1])
        if names is None:
            return result
        return [(name, 0.9) for name in names][: kwargs["limit"]]


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _absolute(text):
    return posixpath.isabs(text) or ntpath.isabs(text) or bool(_DRIVE.match(text))


class BatchTestCase(ImageTagJobTestCase):
    async def get_application(self):
        app = await super().get_application()
        self.tagger = PerImageTagger()
        patcher = patch("py.autotag.get_autotag_service", return_value=self.tagger)
        patcher.start()
        self.addCleanup(patcher.stop)
        backup = PromptManagerConfig.SAFETY_BACKUP
        self.addCleanup(setattr, PromptManagerConfig, "SAFETY_BACKUP", backup)
        PromptManagerConfig.SAFETY_BACKUP = False
        return app

    # -- helpers ---------------------------------------------------------

    def image(self, name, color, sees=None):
        path = make_png(self.output_dir / name, size=(8, 6), color=color)
        if sees is not None:
            self.tagger.by_bytes[path.read_bytes()] = list(sees)
        return path

    async def relist(self):
        """Walk the output folder again (a cached listing is used as it is)."""
        await self.api._listing(self.output_dir, fresh=True)

    def prompt(self, text, tags=()):
        return self.api.db.save_prompt(
            text=text,
            category=None,
            tags=list(tags),
            rating=None,
            prompt_hash=generate_prompt_hash(text),
        )

    def link(self, prompt_id, path):
        return self.api.db.link_image_to_prompt(prompt_id, str(path))

    def prompt_rows(self):
        conn = sqlite3.connect(self._temp_db.name)
        try:
            return {
                table: conn.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall()
                for table in ("prompts", "prompt_tags")
            }
        finally:
            conn.close()

    def marked(self):
        conn = sqlite3.connect(self._temp_db.name)
        try:
            return {
                row[0]
                for row in conn.execute(
                    "SELECT f.locator FROM media_image_autotags m JOIN "
                    "media_annotation_bytes b ON b.id=m.byte_id JOIN "
                    "media_annotation_files f ON f.id=b.file_id"
                )
            }
        finally:
            conn.close()

    async def preview(self, existing="skip", model_type="wd14-vit"):
        response = await self.client.post(
            PREVIEW, json={"model_type": model_type, "existing": existing}
        )
        return response.status, await self.json_of(response)

    async def start_batch(self, existing="skip", model_type="wd14-vit", options=None):
        if options is None:
            options = (
                dict(WD14)
                if model_type.startswith("wd14")
                else {"instructions": "Tags please."}
            )
        response = await self.client.post(
            JOB,
            json={"model_type": model_type, "existing": existing, "options": options},
        )
        return response.status, await self.json_of(response)

    async def run_batch(self, **kwargs):
        status, data = await self.start_batch(**kwargs)
        self.assertEqual(status, 202, data)
        job = self.api._jobs.get(data["job"]["id"])
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        return job


class TestPerImageWrites(BatchTestCase):
    async def test_two_images_of_one_prompt_get_their_own_tags_and_no_prompt_changes(
        self,
    ):
        prompt_id = self.prompt("a girl at the beach", tags=["beach"])
        a = self.image("a.png", (10, 20, 30), sees=["1girl", "bird"])
        b = self.image("b.png", (40, 50, 60), sees=["1girl", "waves"])
        self.link(prompt_id, a)
        self.link(prompt_id, b)
        before = self.prompt_rows()

        job = await self.run_batch(existing="skip")

        self.assertEqual(job.state, "finished", job.status_line)
        self.assertEqual(self.stored("a.png")["tags"], ["1girl", "bird"])
        self.assertEqual(self.stored("b.png")["tags"], ["1girl", "waves"])
        self.assertEqual(self.prompt_rows(), before)
        self.assertEqual(self.api.db.get_prompt_by_id(prompt_id)["tags"], ["beach"])
        self.assertEqual(self.marked(), {self.locator("a.png"), self.locator("b.png")})
        self.assertEqual(
            job.counters, {"tagged": 2, "unchanged": 0, "skipped": 0, "errors": 0}
        )
        self.assertEqual(job.status_line, "Tagged 2 images")
        self.assertEqual((job.processed, job.total, job.progress), (2, 2, 100))
        self.assertEqual(job.scope["images"], 2)
        # Inference read the private captures, one per image.
        self.assertEqual(len(self.tagger.calls), 2)

    async def test_unlinked_images_are_tagged_and_videos_are_not_targets(self):
        self.image("free.png", (1, 2, 3), sees=["tree"])
        (self.output_dir / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" * 8)

        job = await self.run_batch()

        self.assertEqual(self.stored("free.png")["tags"], ["tree"])
        self.assertEqual(job.total, 1)
        self.assertEqual(job.status_line, "Tagged 1 image")
        rows = [row["path"] for rows in job.outcomes.values() for row in rows]
        self.assertNotIn("clip.mp4", rows)

    async def test_hand_tags_do_not_block_a_skip_run(self):
        path = self.image("c.png", (7, 7, 7), sees=["bird"])
        self.saved_set("c.png", path.read_bytes(), ["keeper"])

        job = await self.run_batch(existing="skip")

        self.assertEqual(self.stored("c.png")["tags"], ["keeper", "bird"])
        self.assertEqual(job.counters["tagged"], 1)

    async def test_skip_counts_marked_images_without_opening_them_and_merge_retags(
        self,
    ):
        self.image("a.png", (1, 1, 1), sees=["bird"])
        self.image("b.png", (2, 2, 2), sees=["cat"])
        first = await self.run_batch()
        self.assertEqual(first.counters["tagged"], 2)
        self.tagger.calls.clear()

        captures = []
        store = self.api._capture_store()
        real_capture = store.capture

        def spy(*args, **kwargs):
            captures.append(args)
            return real_capture(*args, **kwargs)

        with patch.object(store, "capture", side_effect=spy):
            skipped = await self.run_batch(existing="skip")
        self.assertEqual(captures, [])
        self.assertEqual(self.tagger.calls, [])
        self.assertEqual(
            skipped.counters, {"tagged": 0, "unchanged": 0, "skipped": 2, "errors": 0}
        )
        self.assertEqual(
            sorted(self.rows(skipped, "skipped")),
            [("a.png", "Already auto-tagged"), ("b.png", "Already auto-tagged")],
        )
        self.assertFalse(any("Already auto-tagged" in line for line in skipped.log))
        self.assertEqual((skipped.processed, skipped.total), (2, 2))
        self.assertEqual(skipped.status_line, "Tagged 0 images")

        self.tagger.by_bytes[(self.output_dir / "a.png").read_bytes()] = ["bird", "sky"]
        merged = await self.run_batch(existing="merge")
        self.assertEqual(len(self.tagger.calls), 2)
        self.assertEqual(self.stored("a.png")["tags"], ["bird", "sky"])
        self.assertEqual(self.rows(merged, "tagged"), [("a.png", "Added 1 tag")])
        self.assertEqual(self.rows(merged, "unchanged"), [("b.png", "No new tags")])

    async def test_changed_bytes_are_tagged_again(self):
        self.image("a.png", (1, 1, 1), sees=["bird"])
        await self.run_batch()
        self.image("a.png", (9, 9, 9), sees=["cat"])
        job = await self.run_batch(existing="skip")
        self.assertEqual(self.rows(job, "tagged"), [("a.png", "Added 1 tag")])
        self.assertEqual(self.stored("a.png")["tags"], ["cat"])

    async def test_an_image_with_no_model_tags_is_marked_and_skipped_next_time(self):
        self.image("plain.png", (3, 3, 3), sees=[])
        job = await self.run_batch()
        self.assertEqual(self.rows(job, "unchanged"), [("plain.png", "No new tags")])
        self.assertEqual(self.marked(), {self.locator("plain.png")})
        again = await self.run_batch()
        self.assertEqual(
            self.rows(again, "skipped"), [("plain.png", "Already auto-tagged")]
        )

    async def test_unsupported_still_is_marked_and_skipped_by_the_next_run(self):
        (self.output_dir / "broken.png").write_bytes(b"not an image")
        job = await self.run_batch()
        self.assertEqual(
            self.rows(job, "skipped"), [("broken.png", "Not a supported still image")]
        )
        self.assertEqual(self.marked(), {self.locator("broken.png")})
        again = await self.run_batch()
        self.assertEqual(
            self.rows(again, "skipped"), [("broken.png", "Already auto-tagged")]
        )
        self.assertEqual(self.tagger.calls, [])

    async def test_too_large_is_skipped_before_any_copy(self):
        self.image("big.png", (5, 5, 5))
        self.api._capture_store().max_file = 16
        store = self.api._capture_store()
        with patch.object(store, "capture", side_effect=AssertionError("copied")):
            job = await self.run_batch()
        self.assertEqual(
            self.rows(job, "skipped"), [("big.png", "Too large to verify")]
        )
        self.assertEqual(self.marked(), set())

    async def test_overlapping_roots_list_a_file_once(self):
        self.image("sub/a.png", (4, 4, 4), sees=["bird"])
        sub = self.output_dir / "sub"
        self.api._get_all_output_dirs = lambda: [self.output_dir, sub]
        job = await self.run_batch()
        self.assertEqual(job.total, 1)
        self.assertEqual(len(self.tagger.calls), 1)
        self.assertEqual(self.rows(job, "tagged"), [("sub/a.png", "Added 1 tag")])
        status, data = await self.preview()
        self.assertEqual((status, data["images"]), (200, 1))

    async def test_rows_log_and_status_carry_no_absolute_paths(self):
        self.image("a.png", (1, 1, 1))
        (self.output_dir / "broken.png").write_bytes(b"not an image")
        job = await self.run_batch()
        leaked = [s for s in _strings(job.report()) if _absolute(s)]
        self.assertEqual(leaked, [])
        self.assertNotIn(str(self.output_dir), json.dumps(job.report()))


class TestBatchLifecycle(BatchTestCase):
    async def test_cancel_after_one_image_keeps_it_and_the_rerun_skips_it(self):
        for n in range(3):
            self.image(f"{n}.png", (n, n, n), sees=[f"t{n}"])
        entered, proceed = threading.Event(), threading.Event()
        self.tagger.block = (entered, proceed)
        status, data = await self.start_batch()
        self.assertEqual(status, 202, data)
        job = self.api._jobs.get(data["job"]["id"])
        try:
            await self.wait_for(entered)
            cancel = await self.client.post(f"/prompt_manager/jobs/{job.id}/cancel")
            self.assertEqual(cancel.status, 200)
        finally:
            self.tagger.block = None
            proceed.set()
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        self.assertEqual(job.state, "cancelled")
        self.assertEqual(job.status_line, CANCELLED)
        self.assertEqual(job.counters["tagged"], 1)
        self.assertEqual(len(self.marked()), 1)
        self.assertIsNone(model_gate.holder())

        lines = []
        real_update = type(job).update

        def spy(self_job, **kwargs):
            if kwargs.get("status_line"):
                lines.append(kwargs["status_line"])
            return real_update(self_job, **kwargs)

        with patch.object(type(job), "update", spy):
            rerun = await self.run_batch()
        self.assertEqual(rerun.counters["skipped"], 1)
        self.assertEqual(rerun.counters["tagged"], 2)
        self.assertIn("Processing file 2 of 3", lines)
        self.assertIn("Processing file 3 of 3", lines)
        self.assertNotIn("Processing file 1 of 3", lines)
        self.assertEqual((rerun.processed, rerun.total), (3, 3))

    async def test_cancel_in_each_phase_writes_nothing(self):
        self.image("a.png", (1, 1, 1))
        for phase in (
            "_safety_backup_step",
            "_prepare_tagging_model",
            "_autotag_targets",
        ):
            with self.subTest(phase):
                real = getattr(self.api, phase)

                async def cancelling(*args, _real=real, **kwargs):
                    result = await _real(*args, **kwargs)
                    self.api._jobs.active("autotag").cancelled = True
                    return result

                with patch.object(self.api, phase, side_effect=cancelling):
                    job = await self.run_batch()
                self.assertEqual(job.state, "cancelled")
                self.assertEqual(job.status_line, CANCELLED)
                self.assertEqual(self.marked(), set())
                self.assertEqual(self.tagger.calls, [])
                self.assertIsNone(model_gate.holder())

    async def test_cancel_between_prepass_chunks_keeps_counts(self):
        for n in range(3):
            self.image(f"{n}.png", (n, n, n))
        await self.run_batch()
        seen = []
        real = jobs_module._prepass_chunk

        def chunk(db, items, skip_marked, max_file):
            result = real(db, items, skip_marked, max_file)
            job = self.api._jobs.active("autotag")
            seen.append(job.processed)
            job.cancelled = True  # cancel after the first chunk
            return result

        with (
            patch.object(jobs_module, "PREPASS_CHUNK", 1),
            patch.object(jobs_module, "_prepass_chunk", side_effect=chunk),
        ):
            job = await self.run_batch()
        self.assertEqual(job.state, "cancelled")
        self.assertEqual(job.counters["skipped"], 1)
        self.assertEqual((job.processed, job.total), (1, 3))
        self.assertEqual(seen, [0])

    async def test_the_job_ends_only_after_its_cleanup(self):
        self.image("a.png", (1, 1, 1))
        PromptManagerConfig.SAFETY_BACKUP = True
        states = []

        async def prune(job, _db):
            states.append((job.state, model_gate.holder() is not None))

        with (
            patch("py.api.maintenance.take_safety_backup", return_value="db.backup_x"),
            patch.object(self.api, "_prune_backup_pool", side_effect=prune),
        ):
            job = await self.run_batch()
        self.assertEqual(job.state, "finished")
        self.assertEqual(states, [("running", True)])
        self.assertIsNone(model_gate.holder())

    async def test_prepass_publishes_its_progress(self):
        for n in range(2):
            self.image(f"{n}.png", (n, n, n))
        await self.run_batch()
        published = []
        real = type(self.api._jobs).start

        def start(registry, *args, **kwargs):
            job = real(registry, *args, **kwargs)
            original = job.publish
            job.publish = lambda payload: (
                published.append(dict(payload)),
                original(payload),
            )
            return job

        with patch.object(type(self.api._jobs), "start", start):
            await self.run_batch()
        self.assertIn({"type": "progress", "progress": 100}, published)
        self.assertEqual(published[-1], {"type": "finished", "progress": 100})

    async def test_restore_mid_run_fails_the_job(self):
        self.image("a.png", (1, 1, 1))
        self.image("b.png", (2, 2, 2))

        def restore(_path):
            conn = sqlite3.connect(self._temp_db.name)
            try:
                rotate_history_epoch(conn)
            finally:
                conn.close()

        self.tagger.on_generate = restore
        job = await self.run_batch()
        self.assertEqual(job.state, "failed")
        self.assertEqual(
            job.status_line, "Prompt storage was restored; start the job again."
        )

    async def test_listing_error_fails_the_job(self):
        self.image("a.png", (1, 1, 1))
        with patch.object(
            self.api, "_get_gallery_files", side_effect=OSError("/secret/out")
        ):
            job = await self.run_batch()
        self.assertEqual(job.state, "failed")
        self.assertEqual(
            job.status_line, "The output folders could not be listed. Try again."
        )
        self.assertNotIn("/secret", json.dumps(job.report()))

    async def test_model_that_cannot_load_fails_the_job(self):
        self.image("a.png", (1, 1, 1))
        self.tagger.load_error = RuntimeError("no CUDA")
        job = await self.run_batch()
        self.assertEqual(job.state, "failed")
        self.assertEqual(
            job.status_line, "Auto-tagging failed. Check server logs for details."
        )
        self.assertIsNone(model_gate.holder())

    async def test_zero_targets_finish_with_tagged_zero(self):
        job = await self.run_batch()
        self.assertEqual(job.state, "finished")
        self.assertEqual(job.status_line, "Tagged 0 images")
        self.assertEqual((job.processed, job.total), (0, 0))

    async def test_runner_unloads_after_a_cancel_when_not_kept_in_memory(self):
        self.image("a.png", (1, 1, 1))
        owner = model_gate.new_owner("legacy-batch")
        self.assertTrue(model_gate.try_acquire(owner))
        run = jobs_module.AutotagRun(
            model_type="wd14-vit",
            existing="skip",
            options={**WD14, "use_gpu": True, "suggestion_limit": 10},
            keep_in_memory=False,
            backup_on=False,
            owner=owner,
        )

        from py.api.jobs import AUTOTAG_GROUP_NAMES, Job as RealJob

        job = RealJob(
            "autotag",
            "Tag output images",
            admission="records",
            groups=AUTOTAG_GROUP_NAMES,
        )
        real_targets = self.api._autotag_targets

        async def cancel_after_listing():
            targets = await real_targets()
            job.cancelled = True
            return targets

        with patch.object(
            self.api, "_autotag_targets", side_effect=cancel_after_listing
        ):
            await self.api._run_autotag_batch(run, job)
        self.assertEqual(job.state, "cancelled")
        self.assertEqual(self.tagger.unload_calls, 1)
        self.assertIsNone(model_gate.holder())

    async def test_time_left_uses_the_models_rate_only(self):
        for n in range(4):
            self.image(f"{n}.png", (n, n, n))
        await self.run_batch()
        for n in range(4, 8):
            self.image(f"{n}.png", (n, n, n))
        await self.relist()
        calls = []
        real = jobs_module._time_left

        def spy(*args):
            calls.append(args)
            return real(*args)

        with patch.object(jobs_module, "_time_left", side_effect=spy):
            job = await self.run_batch()
        self.assertEqual(job.counters["skipped"], 4)
        # (done, total, batches, elapsed): over the 4 images the model runs on.
        self.assertEqual(
            [(c[0], c[1], c[2]) for c in calls], [(n, 4, n) for n in range(4)]
        )
        # The shared D56 text is unchanged.
        self.assertEqual(admin_module._time_left(5, 10, 5, 300), " · about 5 min left")


class TestStartRefusals(BatchTestCase):
    async def test_unavailable_model_and_bad_bodies(self):
        self.tagger.downloaded["wd14-vit"] = False
        status, data = await self.start_batch()
        self.assertEqual((status, data["code"]), (409, "model-unavailable"))
        status, data = await self.start_batch(existing="replace_after_review")
        self.assertEqual((status, data["code"]), (400, "review-required"))
        status, _data = await self.start_batch(options={**WD14, "suggestion_limit": 3})
        self.assertEqual(status, 400)
        self.assertEqual(self.api._jobs.list(), [])
        self.assertIsNone(model_gate.holder())

    async def test_a_missing_backend_package_refuses_the_start(self):
        self.tagger.missing["gguf"] = ["llama-cpp-python"]
        status, data = await self.start_batch(model_type="gguf")
        self.assertEqual((status, data["code"]), (409, "model-unavailable"))
        self.assertEqual(data["success"], False)
        self.assertTrue(
            data["error"].startswith("GGUF JoyCaption needs llama-cpp-python."), data
        )
        self.assertIn("pip install llama-cpp-python", data["error"])
        self.assertEqual(self.api._jobs.list(), [])
        self.assertEqual(self.tagger.load_calls, [])
        self.assertIsNone(model_gate.holder())

    async def test_a_package_lost_after_the_start_fails_the_job_without_a_traceback(
        self,
    ):
        from py.autotag import MissingBackendPackages

        self.image("a.png", (1, 1, 1))
        self.tagger.load_error = MissingBackendPackages("gguf", ["llama-cpp-python"])
        with self.assertNoLogs(self.api.logger, level="ERROR"):
            job = await self.run_batch(model_type="gguf")
        self.assertEqual(job.state, "failed")
        self.assertTrue(
            job.status_line.startswith("GGUF JoyCaption needs"), job.status_line
        )
        self.assertTrue(any("pip install llama-cpp-python" in line for line in job.log))
        self.assertIsNone(model_gate.holder())

    async def test_model_busy_when_the_gate_is_held(self):
        owner = model_gate.new_owner("image-annotations")
        self.assertTrue(model_gate.try_acquire(owner))
        try:
            status, data = await self.start_batch()
        finally:
            model_gate.release(owner)
        self.assertEqual((status, data["code"]), (409, "model-busy"))


class TestBatchBeside(BatchTestCase):
    async def test_legacy_batch_and_unload_are_refused_while_the_job_runs(self):
        self.image("a.png", (1, 1, 1))
        entered, proceed = threading.Event(), threading.Event()
        self.tagger.block = (entered, proceed)
        status, data = await self.start_batch()
        self.assertEqual(status, 202, data)
        job = self.api._jobs.get(data["job"]["id"])
        try:
            await self.wait_for(entered)
            self.assertTrue(model_gate.holder().startswith("autotag-job:"))
            legacy = await self.client.post("/prompt_manager/autotag/start")
            self.assertEqual(legacy.status, 200)
            frames = [
                json.loads(line[len("data: ") :])
                for line in (await legacy.text()).splitlines()
                if line.startswith("data: ")
            ]
            self.assertEqual(
                frames,
                [
                    {
                        "type": "error",
                        "status": "error",
                        "message": "Tag output images already running",
                    }
                ],
            )
            unload = await self.client.post("/prompt_manager/autotag/unload")
            self.assertEqual((await unload.json())["operation"], "Tag output images")
        finally:
            self.tagger.block = None
            proceed.set()
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        self.assertEqual(job.state, "finished")

    async def test_language_model_batch_passes_instructions_per_call(self):
        self.image("a.png", (1, 1, 1))
        job = await self.run_batch(model_type="gguf")
        self.assertEqual(job.state, "finished", job.status_line)
        self.assertEqual(self.tagger.calls[0]["prompt"], "Tags please.")
        self.assertEqual(self.tagger.custom_prompt, "legacy prompt")

    async def test_inference_errors_are_fixed_strings_and_not_marked(self):
        self.image("a.png", (1, 1, 1))

        def explode(_path):
            raise RuntimeError("/srv/secret/model exploded")

        self.tagger.on_generate = explode
        job = await self.run_batch()
        self.assertEqual(
            self.rows(job, "errors"),
            [("a.png", "Auto-tagging failed. Check server logs for details.")],
        )
        self.assertNotIn("/srv/secret", json.dumps(job.report()))
        self.assertEqual(self.marked(), set())


class TestPreview(BatchTestCase):
    async def test_preview_counts_images_and_estimates_time(self):
        prompt_id = self.prompt("p")
        a = self.image("a.png", (1, 1, 1))
        self.image("b.png", (2, 2, 2))
        self.image("c.png", (3, 3, 3))
        self.link(prompt_id, a)
        (self.output_dir / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" * 8)
        status, fresh = await self.preview()
        self.assertEqual(status, 200, fresh)
        self.assertEqual(
            {k: fresh[k] for k in ("images", "already_tagged", "to_tag")},
            {"images": 3, "already_tagged": 0, "to_tag": 3},
        )
        self.assertNotIn("prompts", fresh)
        self.assertEqual(fresh["suggestion_limit"], 10)
        self.assertEqual(fresh["estimate_device"], "cpu")
        self.assertAlmostEqual(fresh["estimate_seconds"], 3 * 1.6)

        self.tagger.block = None
        await self.run_batch()
        self.image("d.png", (4, 4, 4))
        await self.relist()
        skip = (await self.preview("skip"))[1]
        self.assertEqual(
            {k: skip[k] for k in ("images", "already_tagged", "to_tag")},
            {"images": 4, "already_tagged": 3, "to_tag": 1},
        )
        merge = (await self.preview("merge"))[1]
        self.assertEqual(merge["to_tag"], 4)
        for model_type in ("gguf", "hf"):
            llm = (await self.preview(model_type=model_type))[1]
            self.assertIsNone(llm["estimate_seconds"])
            self.assertIsNone(llm["estimate_device"])
        self.assertEqual([s for s in _strings(skip) if _absolute(s)], [])

    async def test_preview_names_missing_backend_packages(self):
        self.image("a.png", (1, 1, 1))
        data = (await self.preview(model_type="gguf"))[1]
        self.assertEqual(data["model"]["missing_packages"], [])
        self.assertIsNone(data["model"]["install_command"])
        self.tagger.missing["gguf"] = ["llama-cpp-python"]
        data = (await self.preview(model_type="gguf"))[1]
        self.assertTrue(data["model"]["downloaded"])
        self.assertEqual(data["model"]["missing_packages"], ["llama-cpp-python"])
        self.assertEqual(
            data["model"]["install_command"], "pip install llama-cpp-python"
        )
        self.assertIn("DGGML_CUDA", data["model"]["install_note"])

    async def test_preview_uses_the_graphics_card_rate_when_wd14_runs_on_cuda(self):
        self.image("a.png", (1, 1, 1))
        self.tagger.wd14_device = lambda: "gpu"
        data = (await self.preview())[1]
        self.assertEqual(data["estimate_device"], "gpu")
        from py.tagging_defaults import WD14_GPU_SECONDS_PER_IMAGE

        self.assertAlmostEqual(data["estimate_seconds"], WD14_GPU_SECONDS_PER_IMAGE)

    async def test_preview_writes_nothing_and_opens_no_media_file(self):
        self.image("a.png", (1, 1, 1))
        await self.run_batch()
        self.image("b.png", (2, 2, 2))
        await self.relist()  # the listing, primed
        files = [self._temp_db.name + suffix for suffix in ("", "-wal")]
        before = [Path(f).read_bytes() if os.path.exists(f) else None for f in files]
        media = str(self.output_dir)
        real_open, real_stat = open, os.stat

        def guarded_open(file, *args, **kwargs):
            if str(file).startswith(media):
                raise AssertionError(f"opened {file}")
            return real_open(file, *args, **kwargs)

        def guarded_stat(path, *args, **kwargs):
            if str(path).startswith(media) and str(path) != media:
                raise AssertionError(f"stat {path}")
            return real_stat(path, *args, **kwargs)

        with patch("builtins.open", guarded_open), patch("os.stat", guarded_stat):
            status, data = await self.preview()
        self.assertEqual(status, 200, data)
        after = [Path(f).read_bytes() if os.path.exists(f) else None for f in files]
        self.assertEqual(after, before)
        self.assertEqual(data["to_tag"], 1)
        self.assertEqual(self.tagger.calls[1:], [])
