"""Image work runs on PromptManager's own pools, never the default executor (#523).

glibc keeps each thread's malloc arena at its high-water mark, so image work
spread over the default executor's threads (min(32, cpus + 4)) kept an
image-sized buffer per thread. Interactive image work (card facts, inspector
reads, view previews) runs on the fixed interactive image pool
(``pm-image``); jobs (scan metadata, thumbnail jobs, capture previews) on the
long-lived Worker threads pool (``pm-worker``). After a scan or thumbnail
job, glibc's free pages are released once (``malloc_trim``).
"""

import asyncio
import threading
from unittest.mock import patch

import py.api as api_package
import py.api.images as images_module
import py.api.inspection_service as inspection_service
import py.api.thumbnail_auto as auto
from utils import parallel

try:
    from tests.test_api_images import make_png
    from tests.test_thumbnail_auto import WAIT, AutoThumbnailCase, _entry
except ImportError:  # discovered with tests/ as the top-level directory
    from test_api_images import make_png
    from test_thumbnail_auto import WAIT, AutoThumbnailCase, _entry

IMAGE_THREAD = auto.POOL_PREFIX + "_"
JOB_THREAD = parallel.JOB_THREAD_PREFIX + "_"


class ImagePoolCase(AutoThumbnailCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.assertTrue(parallel.shutdown_job_pool(WAIT))
        self.addCleanup(parallel.shutdown_job_pool, WAIT)
        self.threads = []
        self.lock = threading.Lock()

    def spy(self, owner, name):
        """Record the thread every call of ``owner.name`` runs on."""
        original = getattr(owner, name)

        def recording(*args, **kwargs):
            with self.lock:
                self.threads.append(threading.current_thread().name)
            return original(*args, **kwargs)

        patcher = patch.object(owner, name, recording)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_threads(self, prefix):
        self.assertTrue(self.threads, "the spied call never ran")
        self.assertTrue(
            all(name.startswith(prefix) for name in self.threads), self.threads
        )

    async def run_job(self, path, body=None):
        response = await self.client.post(path, json=body or {})
        self.assertEqual(response.status, 202, await response.text())
        job = self.api._jobs.get((await response.json())["job"]["id"])
        await asyncio.wait_for(asyncio.shield(job.task), WAIT)
        return job


class TestInteractiveImagePool(ImagePoolCase):
    async def test_workspace_card_facts_are_read_on_the_image_pool(self):
        for index in range(5):
            make_png(self.output_dir / f"c{index}.png", size=(32, 32))
        self.spy(images_module, "_card_header")
        response = await self.client.get(
            "/prompt_manager/images/output?mode=workspace&limit=100"
        )
        self.assertEqual(response.status, 200, await response.text())
        images = (await response.json())["images"]
        self.assertEqual([image["width"] for image in images], [32] * 5)
        self.assert_threads(IMAGE_THREAD)
        self.assertEqual(len(self.threads), 5)

    async def test_cached_card_facts_need_no_image_pool_trip(self):
        make_png(self.output_dir / "c.png", size=(32, 32))
        url = "/prompt_manager/images/output?mode=workspace&limit=100"
        await (await self.client.get(url)).read()
        self.spy(images_module, "_card_header")
        response = await self.client.get(url)
        self.assertEqual((await response.json())["images"][0]["width"], 32)
        self.assertEqual(self.threads, [])

    async def test_the_inspector_reads_on_the_image_pool(self):
        make_png(self.output_dir / "i.png", size=(32, 32))
        self.spy(inspection_service, "read_inspection_file")
        response = await self.client.get("/prompt_manager/images/inspect/i.png?root=0")
        self.assertEqual(response.status, 200, await response.text())
        self.assert_threads(IMAGE_THREAD)

    async def test_view_previews_and_card_reads_share_three_threads(self):
        names = [f"v{i}.png" for i in range(auto.ON_DEMAND_LIMIT + 3)]
        for name in names:
            make_png(self.output_dir / name, size=(64, 64))
        calls = []
        original = self.api._generate_one

        def recording(*args, **kwargs):
            calls.append(threading.current_thread().name)
            return original(*args, **kwargs)

        self.api._generate_one = recording
        for name in names:
            url = _entry(self.api, self.output_dir, name)["thumbnail_url"]
            await (await self.client.get(url)).read()
        self.assertEqual(len(calls), len(names))
        self.assertTrue(all(name.startswith(IMAGE_THREAD) for name in calls), calls)
        self.assertLessEqual(len(self.api._image_pool_threads()), auto.IMAGE_POOL_SIZE)

    def test_map_image_keeps_order_and_runs_inline_on_a_pool_thread(self):
        self.assertEqual(self.api._map_image(lambda n: n * 2, [3, 1, 2]), [6, 2, 4])
        nested = self.api._image_pool().submit(
            self.api._map_image, lambda _: threading.current_thread().name, [1]
        )
        (name,) = nested.result(WAIT)
        self.assertTrue(name.startswith(IMAGE_THREAD), name)

    def test_map_image_after_a_close_runs_on_the_calling_thread(self):
        pool = self.api._image_pool()
        pool.shutdown(wait=True)
        here = threading.current_thread().name
        with patch.object(self.api, "_image_pool", return_value=pool):
            names = self.api._map_image(
                lambda _: threading.current_thread().name, [1, 2]
            )
        self.assertEqual(names, [here, here])

    async def test_run_image_after_a_close_uses_the_default_executor(self):
        pool = self.api._image_pool()
        pool.shutdown(wait=True)
        with patch.object(self.api, "_image_pool", return_value=pool):
            name = await self.api._run_image(lambda: threading.current_thread().name)
        self.assertFalse(name.startswith(IMAGE_THREAD), name)


class TestJobPool(ImagePoolCase):
    def test_scan_metadata_is_read_on_the_job_pool(self):
        paths = [
            make_png(self.output_dir / f"s{i}.png", size=(16, 16)) for i in range(4)
        ]
        self.spy(self.api, "_extract_comfyui_metadata")
        results = self.api._extract_batch_metadata_sync(paths)
        self.assertEqual([path for path, _ in results], paths)
        self.assert_threads(JOB_THREAD)

    async def test_thumbnail_job_writes_on_the_job_pool_and_trims_after(self):
        for index in range(3):
            make_png(self.output_dir / f"t{index}.png", size=(64, 64))
        self.spy(self.api, "_generate_one")
        with patch.object(api_package, "release_free_memory") as trim:
            job = await self.run_job("/prompt_manager/jobs/thumbnails")
        self.assertEqual(job.state, "finished")
        self.assert_threads(JOB_THREAD)
        trim.assert_called_once_with()

    async def test_scan_job_trims_after_it_finishes(self):
        make_png(self.output_dir / "s.png", size=(16, 16))
        states = []

        def trim():
            states.append(self.api._jobs.active("scan"))
            return True

        with patch.object(api_package, "release_free_memory", side_effect=trim):
            job = await self.run_job("/prompt_manager/jobs/scan")
        self.assertEqual(job.state, "finished")
        self.assertGreaterEqual(len(states), 1)
        self.assertIsNone(states[0], "trimmed after the scan finished")

    async def test_a_failing_trim_never_fails_the_job(self):
        make_png(self.output_dir / "t.png", size=(64, 64))
        with patch.object(
            api_package, "release_free_memory", side_effect=RuntimeError("no")
        ):
            job = await self.run_job("/prompt_manager/jobs/thumbnails")
        self.assertEqual(job.state, "finished")

    async def test_capture_previews_write_on_the_job_pool(self):
        path = make_png(self.output_dir / "a.png", size=(64, 64))
        self.spy(self.api, "_generate_one")
        self.assertEqual(self.api.queue_capture_thumbnail(str(path)), "queued")
        await self._idle()
        self.assert_threads(JOB_THREAD)
