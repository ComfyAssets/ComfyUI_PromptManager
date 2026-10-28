"""The legacy pages and the Tag image dialog tag images, never prompts (#379, PR 2).

- ``POST /autotag/start`` (legacy admin and gallery batch) starts the
  ``autotag`` job titled "Tag output images (legacy page)" and relays it as
  the legacy frames; closing the stream cancels it.
- ``POST /autotag/single`` suggests on a capture with the saved cap and
  answers the captured ``sha256``; ``existing: "skip"`` answers
  ``already_tagged`` for an auto-tagged image without the model gate.
- ``POST /autotag/apply`` unions into that image's tags and marks it; a
  ``prompt_id`` body is refused.
- A Tag image dialog save after Generate suggestions marks the image.

No real model runs: the per-image fake from test_autotag_batch answers.
"""

import asyncio
import hashlib
import json
import sqlite3
import threading
from unittest.mock import patch

from aiohttp import web

try:
    from test_autotag_batch import BatchTestCase, _absolute, _strings
except ImportError:
    from tests.test_autotag_batch import BatchTestCase, _absolute, _strings

from database.execution_history_schema import rotate_history_epoch
from py import model_gate
from py.api import autotag_routes, image_tag_jobs
from py.config import AutotagConfig

START = "/prompt_manager/autotag/start"
SINGLE = "/prompt_manager/autotag/single"
APPLY = "/prompt_manager/autotag/apply"
SUGGEST = "/prompt_manager/image-annotations/suggest"
OBSERVE = "/prompt_manager/image-annotations/observe"
TAGS = "/prompt_manager/image-annotations/tags"
WAIT = 30.0
LEGACY_TITLE = "Tag output images (legacy page)"


def frames_of(text):
    return [
        json.loads(line[len("data: ") :])
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


class LegacyTestCase(BatchTestCase):
    async def legacy(self, query="model_type=wd14-vit&skip_tagged=true"):
        response = await self.client.post(f"{START}?{query}")
        return response, frames_of(await response.text())

    def digest(self, path):
        return hashlib.sha256((self.output_dir / path).read_bytes()).hexdigest()

    async def single(self, body):
        response = await self.client.post(SINGLE, json=body)
        return response.status, await response.json()

    async def apply(self, body):
        response = await self.client.post(APPLY, json=body)
        return response.status, await response.json()

    def snapshot_prompts(self):
        conn = sqlite3.connect(self._temp_db.name)
        try:
            return [
                conn.execute(f"SELECT * FROM {t} ORDER BY 1,2").fetchall()
                for t in ("prompts", "prompt_tags")
            ]
        finally:
            conn.close()


class TestLegacyBatch(LegacyTestCase):
    async def test_the_legacy_batch_tags_each_image_and_relays_frames(self):
        prompt = self.prompt("one prompt", tags=["beach"])
        a = self.image("a.png", (1, 2, 3), sees=["bird"])
        b = self.image("b.png", (4, 5, 6), sees=["waves"])
        self.link(prompt, a)
        self.link(prompt, b)
        before = self.snapshot_prompts()
        ticks = iter(range(1000))
        with patch.object(
            autotag_routes, "_monotonic", side_effect=lambda: next(ticks)
        ):
            response, frames = await self.legacy()
        self.assertEqual(response.status, 200)
        self.assertEqual(self.stored("a.png")["tags"], ["bird"])
        self.assertEqual(self.stored("b.png")["tags"], ["waves"])
        self.assertEqual(self.snapshot_prompts(), before)
        phases = [f["status"] for f in frames if f.get("progress") in (0, 5, 8)]
        self.assertEqual(
            phases,
            sorted(
                phases,
                key=[
                    "Taking a safety backup…",
                    "Loading model…",
                    "Listing output images…",
                ].index,
            ),
        )
        self.assertIn("Listing output images…", phases)
        rows = [f for f in frames if f["type"] == "progress" and "processed" in f]
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(
                set(row),
                {"type", "progress", "status", "processed", "tagged", "skipped"},
            )
            self.assertTrue(10 <= row["progress"] <= 95, row)
        self.assertEqual(
            frames[-1],
            {
                "type": "complete",
                "progress": 100,
                "processed": 2,
                "tagged": 2,
                "skipped": 0,
                "errors": 0,
                "skipped_outside_roots": 0,
                "status": "Complete",
                "model_status": "Model kept in memory",
                "model_loaded": True,
            },
        )
        job = self.api._jobs.list()[0]
        self.assertEqual(
            (job.title, job.kind, job.state), (LEGACY_TITLE, "autotag", "finished")
        )
        self.assertEqual([s for s in _strings(frames) if _absolute(s)], [])

        # Skip: the marked images are skipped before the model runs.
        self.tagger.calls.clear()
        _, again = await self.legacy()
        self.assertEqual(again[-1]["skipped"], 2)
        self.assertEqual(again[-1]["processed"], 0)
        self.assertEqual(self.tagger.calls, [])

    async def test_no_still_images_and_keep_in_memory_false(self):
        _, frames = await self.legacy("model_type=wd14-vit&keep_in_memory=false")
        self.assertEqual(frames[-1]["status"], "No still images found")
        self.assertEqual(frames[-1]["model_loaded"], False)
        self.assertEqual(frames[-1]["model_status"], "Model unloaded")
        self.assertEqual(self.tagger.unload_calls, 1)

    async def test_language_model_options_come_from_the_page_or_the_template(self):
        self.image("a.png", (1, 1, 1))
        await self.legacy("model_type=gguf&prompt=Tags%20only")
        self.assertEqual(self.tagger.calls[-1]["prompt"], "Tags only")
        self.assertEqual(self.tagger.calls[-1]["limit"], 10)
        self.image("b.png", (2, 2, 2))
        await self.relist()
        await self.legacy("model_type=gguf&prompt=%20")
        self.assertEqual(self.tagger.calls[-1]["prompt"], AutotagConfig.TEMPLATE)
        self.assertEqual(self.tagger.custom_prompt, "legacy prompt")

    async def test_a_missing_backend_package_is_one_error_frame(self):
        self.tagger.missing["gguf"] = ["llama-cpp-python"]
        _, frames = await self.legacy("model_type=gguf")
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]["type"], "error")
        self.assertTrue(
            frames[0]["message"].startswith("GGUF JoyCaption needs llama-cpp-python.")
        )
        self.assertEqual(self.tagger.load_calls, [])

    async def test_legacy_single_refuses_a_missing_backend_package(self):
        from py.autotag import MissingBackendPackages

        self.image("a.png", (1, 1, 1))
        await self.relist()
        self.tagger.missing["gguf"] = ["llama-cpp-python"]
        status, data = await self.single(
            {"path": "a.png", "root_index": 0, "model_type": "gguf"}
        )
        self.assertEqual((status, data["code"]), (409, "model-unavailable"))
        self.assertIn("pip install llama-cpp-python", data["error"])
        self.assertEqual(self.tagger.load_calls, [])
        # Lost after the check: the same 409, no ERROR record, nothing held.
        self.tagger.missing["gguf"] = []
        self.tagger.load_error = MissingBackendPackages("gguf", ["llama-cpp-python"])
        with self.assertNoLogs(self.api.logger, level="ERROR"):
            status, data = await self.single(
                {"path": "a.png", "root_index": 0, "model_type": "gguf"}
            )
        self.assertEqual((status, data["code"]), (409, "model-unavailable"))
        self.assertIn("pip install llama-cpp-python", data["error"])
        self.assertIsNone(model_gate.holder())

    async def test_each_refusal_is_one_error_frame(self):
        self.tagger.downloaded["wd14-vit"] = False
        _, frames = await self.legacy()
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]["type"], "error")
        self.assertEqual(frames[0]["status"], "error")
        self.assertIn("WD14 ViT is not downloaded", frames[0]["message"])
        self.tagger.downloaded["wd14-vit"] = True

        _, frames = await self.legacy("model_type=wd14-vit&general_threshold=2")
        self.assertEqual(
            frames,
            [
                {
                    "type": "error",
                    "status": "error",
                    "message": "general_threshold must be between 0 and 1",
                }
            ],
        )

        owner = model_gate.new_owner("image-annotations")
        self.assertTrue(model_gate.try_acquire(owner))
        try:
            _, frames = await self.legacy()
        finally:
            model_gate.release(owner)
        self.assertEqual(frames[0]["message"], autotag_routes.MODEL_BUSY_ERROR)
        self.assertEqual(self.api._jobs.list(), [])
        self.assertIsNone(model_gate.holder())

        response = await self.client.post(f"{START}?general_threshold=abc")
        self.assertEqual(response.status, 400)

    async def test_busy_jobs_refuse_the_legacy_batch(self):
        self.image("a.png", (1, 1, 1))
        entered, proceed = threading.Event(), threading.Event()
        self.tagger.block = (entered, proceed)
        status, data = await self.start_batch()
        self.assertEqual(status, 202, data)
        job = self.api._jobs.get(data["job"]["id"])
        try:
            await self.wait_for(entered)
            _, frames = await self.legacy()
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
        finally:
            self.tagger.block = None
            proceed.set()
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)

        # A second legacy batch keeps its JSON 409.
        self.image("b.png", (2, 2, 2))
        await self.relist()
        entered, proceed = threading.Event(), threading.Event()
        self.tagger.block = (entered, proceed)
        first = asyncio.ensure_future(
            self.legacy("model_type=wd14-vit&skip_tagged=false")
        )
        try:
            await self.wait_for(entered)
            second = await self.client.post(f"{START}?model_type=wd14-vit")
            self.assertEqual(second.status, 409)
            self.assertEqual(
                await second.json(),
                {"success": False, "error": "Auto-tagging is already running"},
            )
        finally:
            self.tagger.block = None
            proceed.set()
        _, frames = await first
        self.assertEqual(frames[-1]["type"], "complete")

    async def test_a_legacy_start_that_loses_the_race_gets_the_json_409(self):
        # Two starts can both pass the title check before either registers its
        # job; the loser's busy refusal names the legacy batch and answers the
        # same JSON 409 as a second legacy batch.
        refusal = image_tag_jobs.Refusal(
            409,
            "busy",
            f"{autotag_routes.LEGACY_BATCH_TITLE} already running",
            web.json_response({}, status=409),
        )

        async def refused(**_kwargs):
            return None, refusal

        with patch.object(self.api, "_start_autotag", side_effect=refused):
            response = await self.client.post(f"{START}?model_type=wd14-vit")
        self.assertEqual(response.status, 409)
        self.assertEqual(
            await response.json(),
            {"success": False, "error": "Auto-tagging is already running"},
        )

    async def test_closing_the_stream_cancels_the_job(self):
        for n in range(3):
            self.image(f"{n}.png", (n, n, n))
        real_write = web.StreamResponse.write

        async def flaky_write(response, data):
            if b'"processed"' in data:
                raise ConnectionResetError("Cannot write to closing transport")
            return await real_write(response, data)

        with patch.object(web.StreamResponse, "write", flaky_write):
            try:
                response = await self.client.post(f"{START}?model_type=wd14-vit")
                await response.read()
            except Exception:  # nosec B110 - the client sees the dropped stream
                pass
        job = self.api._jobs.list()[0]
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        self.assertEqual(job.state, "cancelled")
        self.assertLess(job.counters["tagged"], 3)

    async def test_a_quiet_job_repeats_its_last_frame(self):
        self.image("a.png", (1, 1, 1))
        entered, proceed = threading.Event(), threading.Event()
        self.tagger.block = (entered, proceed)
        with patch.object(autotag_routes, "LEGACY_REPEAT_AFTER", 0.05):
            run = asyncio.ensure_future(self.legacy())
            await self.wait_for(entered)
            await asyncio.sleep(0.3)
            self.tagger.block = None
            proceed.set()
            _, frames = await run
        statuses = [f.get("status") for f in frames]
        repeated = [s for s in statuses if s and statuses.count(s) > 1]
        self.assertTrue(repeated, statuses)
        self.assertEqual(frames[-1]["type"], "complete")

    async def test_a_cancel_from_activity_ends_with_a_cancelled_frame(self):
        for n in range(2):
            self.image(f"{n}.png", (n, n, n))
        entered, proceed = threading.Event(), threading.Event()
        self.tagger.block = (entered, proceed)
        run = asyncio.ensure_future(self.legacy())
        try:
            await self.wait_for(entered)
            job = self.api._jobs.active("autotag")
            await self.client.post(f"/prompt_manager/jobs/{job.id}/cancel")
        finally:
            self.tagger.block = None
            proceed.set()
        _, frames = await run
        self.assertEqual(
            frames[-1],
            {
                "type": "cancelled",
                "message": "Cancelled. Tags already added were kept.",
            },
        )


class TestLegacySingleAndApply(LegacyTestCase):
    async def test_single_suggests_on_a_capture_and_apply_tags_only_that_image(self):
        prompt = self.prompt("shared prompt", tags=["keep"])
        a = self.image("a.png", (1, 2, 3), sees=["bird", "sky"])
        b = self.image("b.png", (4, 5, 6), sees=["cat"])
        image_a = self.link(prompt, a)
        self.link(prompt, b)
        self.saved_set("b.png", b.read_bytes(), ["hand"])
        before = self.snapshot_prompts()
        status, data = await self.single(
            {"image_id": image_a, "model_type": "wd14-vit"}
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["tags"], ["bird", "sky"])
        self.assertEqual(data["sha256"], self.digest("a.png"))
        self.assertEqual(data["image_tags"], [])
        self.assertEqual(data["prompt_id"], prompt)
        self.assertEqual(self.tagger.custom_prompt, "legacy prompt")
        # Inference read the private capture, not the file.
        self.assertNotIn(str(self.output_dir), self.tagger.read_paths[-1])
        status, applied = await self.apply(
            {"image_id": image_a, "sha256": data["sha256"], "tags": ["bird"]}
        )
        self.assertEqual(
            (status, applied),
            (200, {"success": True, "added_tags": ["bird"], "total_tags": 1}),
        )
        self.assertEqual(self.stored("a.png")["tags"], ["bird"])
        self.assertEqual(self.stored("b.png")["tags"], ["hand"])
        self.assertEqual(self.snapshot_prompts(), before)
        self.assertEqual(self.marked(), {self.locator("a.png")})

    async def test_skip_answers_already_tagged_without_the_gate(self):
        a = self.image("a.png", (1, 2, 3), sees=["bird"])
        await self.run_batch()
        self.tagger.calls.clear()
        owner = model_gate.new_owner("image-tag-job")
        self.assertTrue(model_gate.try_acquire(owner))
        try:
            status, data = await self.single(
                {
                    "path": "a.png",
                    "root_index": 0,
                    "existing": "skip",
                    "model_type": "wd14-vit",
                }
            )
        finally:
            model_gate.release(owner)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["already_tagged"], True)
        self.assertEqual(data["image_tags"], ["bird"])
        self.assertEqual(self.tagger.calls, [])
        # Hand tags alone do not count; merge always suggests.
        b = self.image("b.png", (9, 9, 9))
        self.saved_set("b.png", b.read_bytes(), ["hand"])
        status, data = await self.single(
            {"path": str(b), "existing": "skip", "model_type": "wd14-vit"}
        )
        self.assertNotIn("already_tagged", data)
        self.assertEqual(data["image_tags"], ["hand"])
        status, data = await self.single(
            {"path": str(a), "existing": "merge", "model_type": "wd14-vit"}
        )
        self.assertNotIn("already_tagged", data)

    async def test_single_uses_the_saved_cap_and_the_refusal_table(self):
        self.image("a.png", (1, 2, 3))
        AutotagConfig.SUGGESTION_LIMIT = 3
        status, data = await self.single(
            {"path": "a.png", "root_index": 0, "model_type": "wd14-vit"}
        )
        self.assertEqual((status, len(data["tags"])), (200, 3))
        (self.output_dir / "broken.png").write_bytes(b"not an image")
        await self.relist()
        status, data = await self.single(
            {"path": "broken.png", "root_index": 0, "model_type": "wd14-vit"}
        )
        self.assertEqual((status, data["code"]), (415, "not-a-still"))
        self.assertIn(self.locator("broken.png"), self.marked())
        self.tagger.downloaded["gguf"] = False
        status, data = await self.single(
            {"path": "a.png", "root_index": 0, "model_type": "gguf"}
        )
        self.assertEqual((status, data["code"]), (409, "model-unavailable"))
        self.api._capture_store().max_file = 16
        status, data = await self.single(
            {"path": "a.png", "root_index": 0, "model_type": "wd14-vit"}
        )
        self.assertEqual((status, data["code"]), (413, "too-large"))
        self.api._capture_store().max_file = 2**20
        owner = model_gate.new_owner("image-annotations")
        self.assertTrue(model_gate.try_acquire(owner))
        try:
            status, data = await self.single(
                {"path": "a.png", "root_index": 0, "model_type": "wd14-vit"}
            )
        finally:
            model_gate.release(owner)
        self.assertEqual((status, data["code"]), (409, "model-busy"))

    async def test_apply_body_and_answer_table(self):
        a = self.image("a.png", (1, 2, 3))
        digest = self.digest("a.png")
        status, data = await self.apply({"prompt_id": 1, "tags": ["a"]})
        self.assertEqual(
            (status, data["error"]), (400, "This page is out of date. Reload it.")
        )
        status, _ = await self.apply(
            {"path": "a.png", "root_index": 0, "sha256": "x", "tags": ["a"]}
        )
        self.assertEqual(status, 400)
        status, _ = await self.apply(
            {"path": "a.png", "root_index": 0, "sha256": digest, "tags": ["bad,name"]}
        )
        self.assertEqual(status, 400)
        status, _ = await self.apply({"path": "a.png", "sha256": digest, "tags": []})
        self.assertEqual(status, 403)  # a bare relative path is not inside a root
        # An empty reviewed list only marks the image.
        status, data = await self.apply({"path": str(a), "sha256": digest, "tags": []})
        self.assertEqual((status, data["added_tags"]), (200, []))
        self.assertEqual(self.marked(), {self.locator("a.png")})
        # The file changed since its tags were generated.
        status, data = await self.apply(
            {"path": str(a), "sha256": "0" * 64, "tags": ["x"]}
        )
        self.assertEqual((status, data["code"]), (409, "file-changed"))
        # Too many tags.
        full = self.image("full.png", (7, 7, 7))
        self.saved_set("full.png", full.read_bytes(), [f"t{i}" for i in range(100)])
        status, data = await self.apply(
            {"path": str(full), "sha256": self.digest("full.png"), "tags": ["one_more"]}
        )
        self.assertEqual((status, data["code"]), (409, "tag-limit"))
        # Gone.
        status, data = await self.apply(
            {"path": "gone.png", "root_index": 0, "sha256": digest, "tags": ["x"]}
        )
        self.assertIn(status, (403, 404))

    async def test_apply_after_a_restore_writes_nothing(self):
        a = self.image("a.png", (1, 2, 3))
        digest = self.digest("a.png")
        real = self.api.db.get_execution_epoch

        def stale_epoch():
            epoch = real()
            conn = sqlite3.connect(self._temp_db.name)
            try:
                rotate_history_epoch(conn)
            finally:
                conn.close()
            return epoch

        with patch.object(self.api.db, "get_execution_epoch", side_effect=stale_epoch):
            status, data = await self.apply(
                {"path": str(a), "sha256": digest, "tags": ["x"]}
            )
        self.assertEqual((status, data["code"]), (409, "storage-changed"))
        self.assertEqual(self.stored("a.png")["tags"], [])

    async def test_image_id_and_path_bodies_reach_the_same_bytes(self):
        prompt = self.prompt("p")
        a = self.image("sub/a.png", (1, 2, 3), sees=["x"])
        image_id = self.link(prompt, a)
        _, by_id = await self.single({"image_id": image_id, "model_type": "wd14-vit"})
        _, by_path = await self.single(
            {"path": "sub/a.png", "root_index": 0, "model_type": "wd14-vit"}
        )
        self.assertEqual(by_id["sha256"], by_path["sha256"])
        status, _ = await self.apply(
            {"image_id": image_id, "sha256": by_id["sha256"], "tags": ["x"]}
        )
        self.assertEqual(status, 200)
        status, data = await self.apply(
            {
                "path": "sub/a.png",
                "root_index": 0,
                "sha256": by_path["sha256"],
                "tags": ["y"],
            }
        )
        self.assertEqual((status, data["total_tags"]), (200, 2))


class TestDialogMarks(LegacyTestCase):
    """DEC4: a Tag image dialog save after Generate suggestions marks the image."""

    def target(self, path):
        return {
            "root_index": 0,
            "root_scope": self.api._gallery_root_scope(self.output_dir, 0),
            "path": path,
        }

    async def observe(self, path):
        response = await self.client.post(OBSERVE, json={"target": self.target(path)})
        data = await response.json()
        self.assertEqual(response.status, 200, data)
        return data

    async def suggest(self, observed, path):
        response = await self.client.post(
            SUGGEST,
            json={
                "target": self.target(path),
                "capture_token": observed["capture"]["token"],
                "expected_epoch": observed["library_epoch"],
                "model_type": "wd14-vit",
                "options": {"general_threshold": 0.35, "character_threshold": 0.85},
            },
        )
        self.assertEqual(response.status, 200, await response.text())

    async def save(self, observed, path, tags, revision=None):
        response = await self.client.put(
            TAGS,
            json={
                "target": self.target(path),
                "capture_token": observed["capture"]["token"],
                "sha256": observed["capture"]["sha256"],
                "expected_epoch": observed["library_epoch"],
                "expected_tag_revision": (
                    observed["tag_revision"] if revision is None else revision
                ),
                "tags": tags,
            },
        )
        return response.status, await response.json()

    async def test_save_after_generate_marks_and_a_skip_batch_leaves_it(self):
        self.image("d.png", (1, 2, 3), sees=["bird", "sky"])
        observed = await self.observe("d.png")
        await self.suggest(observed, "d.png")
        status, _ = await self.save(observed, "d.png", ["sky"])  # bird removed
        self.assertEqual(status, 200)
        self.assertEqual(self.marked(), {self.locator("d.png")})
        job = await self.run_batch(existing="skip")
        self.assertEqual(self.rows(job, "skipped"), [("d.png", "Already auto-tagged")])
        self.assertEqual(self.stored("d.png")["tags"], ["sky"])

    async def test_save_without_generate_does_not_mark(self):
        self.image("e.png", (1, 2, 3))
        observed = await self.observe("e.png")
        status, _ = await self.save(observed, "e.png", ["hand"])
        self.assertEqual(status, 200)
        self.assertEqual(self.marked(), set())

    async def test_a_conflict_then_a_saved_retry_marks(self):
        f = self.image("f.png", (1, 2, 3))
        observed = await self.observe("f.png")
        await self.suggest(observed, "f.png")
        self.saved_set("f.png", f.read_bytes(), ["other"])  # another writer
        status, data = await self.save(observed, "f.png", ["mine"])
        self.assertEqual((status, data["code"]), (409, "tag-conflict"))
        self.assertEqual(self.marked(), set())
        status, _ = await self.save(
            observed, "f.png", ["mine"], revision=data["tag_revision"]
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.marked(), {self.locator("f.png")})

    async def test_another_capture_does_not_inherit_the_mark(self):
        self.image("g.png", (1, 2, 3))
        self.image("h.png", (4, 5, 6))
        first = await self.observe("g.png")
        await self.suggest(first, "g.png")
        second = await self.observe("h.png")
        status, _ = await self.save(second, "h.png", ["x"])
        self.assertEqual(status, 200)
        self.assertEqual(self.marked(), set())
        self.assertIn(first["capture"]["token"], self.api._suggested_tokens)

    async def test_gone_tokens_are_dropped_on_the_next_insert_without_touching_clocks(
        self,
    ):
        self.image("i.png", (1, 2, 3))
        self.image("j.png", (4, 5, 6))
        first = await self.observe("i.png")
        await self.suggest(first, "i.png")
        store = self.api._capture_store()
        store.release(first["capture"]["token"])
        second = await self.observe("j.png")
        seen = []
        real_get = store.get
        with patch.object(
            store, "get", side_effect=lambda t: seen.append(t) or real_get(t)
        ):
            await self.suggest(second, "j.png")
        self.assertEqual(seen, [])
        self.assertEqual(self.api._suggested_tokens, {second["capture"]["token"]})
