"""Route tests for the autotag endpoints.

The ML engine is replaced by a fake service so no model is ever loaded and
no network request is made.
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock
from pathlib import Path
from unittest.mock import patch

_mock_server = MagicMock()
_mock_server.PromptServer.instance.routes = MagicMock()
sys.modules.setdefault("server", _mock_server)  # py.config imports it at module load

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from host_stubs import install_folder_paths_stub
except ImportError:  # imported as tests.test_api_autotag
    from tests.host_stubs import install_folder_paths_stub

install_folder_paths_stub()

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from database.operations import PromptDatabase
from py.api import PromptManagerAPI
from py.api.jobs import JobBusy
from utils.hashing import generate_prompt_hash

try:  # guarded so a missing module fails assertions, not test loading
    from py import model_gate
except ImportError:
    model_gate = None


# The engine's four model types (py/autotag.py MODELS), in its own order.
MODEL_TYPES = ("gguf", "hf", "wd14-swinv2", "wd14-vit")


class FakeAutotagService:
    """Stand-in for AutoTagService that records calls and returns fixed tags."""

    def __init__(self, tags=None, downloaded=True):
        self.tags = tags if tags is not None else ["1girl", "smile"]
        self.downloaded = downloaded
        self.generate_calls = []
        self.load_calls = []
        self.download_calls = []
        self.unload_calls = 0
        self.custom_prompt = ""
        self.default_prompt = "default prompt"
        self.wd14_general_threshold = 0.35
        self.wd14_character_threshold = 0.85
        self.models_config = {name: {} for name in MODEL_TYPES}
        self._loaded_type = None
        self.generate_error = None
        self.download_result = True
        self.load_error = None
        # Call counters and the thread each status read ran on.
        self.status_calls = 0
        self.status_threads = []
        self.status_paths = {}

    def get_models_status(self):
        self.status_calls += 1
        self.status_threads.append(threading.get_ident())
        return {
            name: {
                "downloaded": self.downloaded,
                "model_path": self.status_paths.get(name),
            }
            for name in self.models_config
        }

    def is_model_loaded(self):
        return self._loaded_type is not None

    def get_loaded_model_type(self):
        return self._loaded_type

    def load_model(self, model_type, use_gpu=True):
        if self.load_error is not None:
            raise self.load_error
        self.load_calls.append((model_type, use_gpu))
        self._loaded_type = model_type
        return True

    def unload_model(self):
        self.unload_calls += 1
        self._loaded_type = None

    def download_model(self, model_type, progress_callback=None):
        self.download_calls.append(model_type)
        if progress_callback:
            progress_callback("Downloading...", 50)
        return self.download_result

    def generate_tags(
        self, image_path, prompt=None, general_threshold=None, character_threshold=None
    ):
        self.generate_calls.append(
            (image_path, prompt, general_threshold, character_threshold)
        )
        if self.generate_error is not None:
            raise self.generate_error
        return list(self.tags)


class AutotagAPITestCase(AioHTTPTestCase):
    """Stands up the PromptManager routes with a temp DB, temp output dir and a
    fake engine."""

    async def get_application(self):
        self._temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self._temp_db.close()
        self.tmp_root = Path(tempfile.mkdtemp())
        self.output_dir = self.tmp_root / "output"
        self.output_dir.mkdir()
        self.secret_dir = self.tmp_root / "secret"
        self.secret_dir.mkdir()

        self.service = FakeAutotagService()
        self._service_patch = patch(
            "py.autotag.get_autotag_service", return_value=self.service
        )
        self._service_patch.start()

        app = web.Application()
        routes = web.RouteTableDef()
        self.api = PromptManagerAPI(db=PromptDatabase(self._temp_db.name))
        self.api._get_all_output_dirs = lambda: [self.output_dir]
        self.api.add_routes(routes)
        app.router.add_routes(routes)
        return app

    async def tearDownAsync(self):
        await super().tearDownAsync()  # closes the aiohttp test client
        # The automatic preview worker writes into the temp tree: stop it first.
        self.api.close_auto_thumbnails()
        self._service_patch.stop()
        if model_gate is not None:
            leaked = model_gate.holder()
            if leaked is not None:  # never leak the process-wide gate
                model_gate.release(leaked)
            self.assertIsNone(leaked, "a route left the model gate held")
        self.api.db.close_all()
        for path in (
            self._temp_db.name,
            self._temp_db.name + "-wal",
            self._temp_db.name + "-shm",
        ):
            if os.path.exists(path):
                os.unlink(path)
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    # ── helpers ────────────────────────────────────────────────────────

    def _save_prompt(self, text="Test prompt", tags=None):
        return self.api.db.save_prompt(
            text=text,
            category=None,
            tags=tags or [],
            rating=None,
            prompt_hash=generate_prompt_hash(text),
        )

    def _make_image(self, directory, name="img.png"):
        path = directory / name
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
        return path

    def _link_image(self, prompt_id, path):
        return self.api.db.link_image_to_prompt(prompt_id, str(path))

    async def _post_json(self, url, body):
        return await self.client.request(
            "POST",
            url,
            data=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )

    @staticmethod
    def _sse_events(text):
        events = []
        for line in text.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
        return events


class TestAutotagSingleContainment(AutotagAPITestCase):

    async def test_image_id_tags_the_linked_image(self):
        prompt_id = self._save_prompt()
        image = self._make_image(self.output_dir)
        image_id = self._link_image(prompt_id, image)

        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"image_id": image_id}
        )

        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["tags"], ["1girl", "smile"])
        self.assertEqual(data["prompt_id"], prompt_id)
        self.assertEqual(len(self.service.generate_calls), 1)
        self.assertEqual(
            os.path.realpath(self.service.generate_calls[0][0]),
            os.path.realpath(str(image)),
        )

    async def test_path_inside_output_dir_is_tagged(self):
        image = self._make_image(self.output_dir)

        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"path": str(image)}
        )

        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["tags"], ["1girl", "smile"])
        self.assertEqual(len(self.service.generate_calls), 1)

    async def test_path_outside_output_dir_is_forbidden(self):
        secret = self._make_image(self.secret_dir, "password.png")

        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"path": str(secret)}
        )

        self.assertEqual(resp.status, 403)
        data = await resp.json()
        self.assertFalse(data["success"])
        self.assertNotIn(str(self.secret_dir), data["error"])
        self.assertEqual(self.service.generate_calls, [])

    async def test_traversal_path_is_forbidden(self):
        self._make_image(self.secret_dir, "password.png")
        traversal = os.path.join(str(self.output_dir), "..", "secret", "password.png")

        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"path": traversal}
        )

        self.assertEqual(resp.status, 403)
        self.assertEqual(self.service.generate_calls, [])

    async def test_legacy_image_path_field_is_still_contained(self):
        secret = self._make_image(self.secret_dir, "password.png")

        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"image_path": str(secret)}
        )

        self.assertEqual(resp.status, 403)
        self.assertEqual(self.service.generate_calls, [])

    async def test_sibling_prefix_dir_is_forbidden(self):
        sibling = self.tmp_root / (self.output_dir.name + "2")
        sibling.mkdir()
        image = self._make_image(sibling, "x.png")

        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"path": str(image)}
        )

        self.assertEqual(resp.status, 403)
        self.assertEqual(self.service.generate_calls, [])

    async def test_symlink_escaping_output_dir_is_forbidden(self):
        secret = self._make_image(self.secret_dir, "password.png")
        link = self.output_dir / "link.png"
        try:
            link.symlink_to(secret)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported on this platform")

        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"path": str(link)}
        )

        self.assertEqual(resp.status, 403)
        self.assertEqual(self.service.generate_calls, [])

    async def test_directory_path_is_forbidden(self):
        sub = self.output_dir / "subdir"
        sub.mkdir()

        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"path": str(sub)}
        )

        self.assertEqual(resp.status, 403)
        self.assertEqual(self.service.generate_calls, [])

    async def test_image_id_row_pointing_outside_is_forbidden(self):
        prompt_id = self._save_prompt()
        secret = self._make_image(self.secret_dir, "password.png")
        image_id = self._link_image(prompt_id, secret)

        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"image_id": image_id}
        )

        self.assertEqual(resp.status, 403)
        data = await resp.json()
        self.assertNotIn(str(self.secret_dir), data["error"])
        self.assertEqual(self.service.generate_calls, [])

    async def test_non_media_extension_inside_output_dir_is_forbidden(self):
        notes = self.output_dir / "notes.txt"
        notes.write_text("not an image")

        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"path": str(notes)}
        )

        self.assertEqual(resp.status, 403)
        self.assertFalse((await resp.json())["success"])
        self.assertEqual(self.service.generate_calls, [])

    async def test_non_media_extension_via_image_id_is_forbidden(self):
        prompt_id = self._save_prompt()
        notes = self.output_dir / "notes.txt"
        notes.write_text("not an image")
        image_id = self._link_image(prompt_id, notes)

        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"image_id": image_id}
        )

        self.assertEqual(resp.status, 403)
        self.assertEqual(self.service.generate_calls, [])

    async def test_uppercase_media_extension_is_accepted(self):
        image = self._make_image(self.output_dir, "shout.JPEG")

        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"path": str(image)}
        )

        self.assertEqual(resp.status, 200)
        self.assertEqual(len(self.service.generate_calls), 1)

    async def test_missing_selector_is_400(self):
        resp = await self._post_json("/prompt_manager/autotag/single", {})
        self.assertEqual(resp.status, 400)
        self.assertFalse((await resp.json())["success"])

    async def test_unknown_image_id_is_404(self):
        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"image_id": 9999}
        )
        self.assertEqual(resp.status, 404)
        self.assertFalse((await resp.json())["success"])

    async def test_non_integer_image_id_is_400(self):
        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"image_id": "abc"}
        )
        self.assertEqual(resp.status, 400)


class TestPathContainmentHelper(unittest.TestCase):
    """Pure checks on the containment predicate used by autotag/single."""

    def setUp(self):
        from py.api.autotag_routes import path_is_within

        self.path_is_within = path_is_within
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "out"
        self.root.mkdir()

    def test_child_inside_root(self):
        self.assertTrue(self.path_is_within(self.root / "a" / "b.png", self.root))

    def test_root_itself_is_not_a_child(self):
        self.assertFalse(self.path_is_within(self.root, self.root))

    def test_prefix_sibling_is_outside(self):
        self.assertFalse(self.path_is_within(self.tmp / "out2" / "b.png", self.root))

    def test_dotdot_is_outside(self):
        self.assertFalse(
            self.path_is_within(self.root / ".." / "secret" / "b.png", self.root)
        )

    def test_case_differences_are_folded_by_normcase(self):
        upper = Path(str(self.root).upper()) / "B.PNG"
        with patch("os.path.normcase", side_effect=lambda p: p.lower()):
            self.assertTrue(self.path_is_within(upper, self.root))

    def test_separator_differences_do_not_matter(self):
        mixed = str(self.root / "a" / "b.png").replace(os.sep, "/")
        self.assertTrue(self.path_is_within(mixed, self.root))


class TestAutotagSideEffectRoutesArePostOnly(AutotagAPITestCase):

    async def test_get_download_is_405(self):
        resp = await self.client.request("GET", "/prompt_manager/autotag/download/gguf")
        self.assertEqual(resp.status, 405)
        self.assertEqual(self.service.download_calls, [])

    async def test_get_start_is_405(self):
        resp = await self.client.request("GET", "/prompt_manager/autotag/start")
        self.assertEqual(resp.status, 405)
        self.assertEqual(self.service.load_calls, [])

    async def test_post_download_streams_completion(self):
        resp = await self.client.request(
            "POST", "/prompt_manager/autotag/download/gguf"
        )

        self.assertEqual(resp.status, 200)
        events = self._sse_events(await resp.text())
        self.assertEqual(events[-1]["type"], "complete")
        self.assertEqual(self.service.download_calls, ["gguf"])

    async def test_post_start_with_no_images_completes(self):
        resp = await self.client.request(
            "POST", "/prompt_manager/autotag/start?model_type=gguf"
        )

        self.assertEqual(resp.status, 200)
        events = self._sse_events(await resp.text())
        self.assertEqual(events[-1]["type"], "complete")
        self.assertEqual(events[-1]["processed"], 0)
        self.assertEqual(self.service.load_calls, [("gguf", True)])

    async def test_models_status_stays_get(self):
        resp = await self.client.request("GET", "/prompt_manager/autotag/models")
        self.assertEqual(resp.status, 200)
        self.assertTrue((await resp.json())["success"])


class TestAutotagBatch(AutotagAPITestCase):
    """Batch tagging over the DB image list with SSE progress reporting."""

    def _seed(self):
        """Three prompts: A (one image), B (two images), C (missing file)."""
        a = self._save_prompt("prompt a")
        b = self._save_prompt("prompt b")
        c = self._save_prompt("prompt c")
        self._link_image(a, self._make_image(self.output_dir, "a1.png"))
        self._link_image(b, self._make_image(self.output_dir, "b1.png"))
        self._link_image(b, self._make_image(self.output_dir, "b2.png"))
        self._link_image(c, self.output_dir / "missing.png")
        return a, b, c

    async def _start(self, query="model_type=gguf", body=None):
        if body is None:
            resp = await self.client.request(
                "POST", f"/prompt_manager/autotag/start?{query}"
            )
        else:
            resp = await self._post_json(f"/prompt_manager/autotag/start?{query}", body)
        return resp, self._sse_events(await resp.text())

    async def test_batch_tags_each_prompt_once_and_reports_counts(self):
        a, b, c = self._seed()

        resp, events = await self._start()

        self.assertEqual(resp.status, 200)
        done = events[-1]
        self.assertEqual(done["type"], "complete")
        self.assertEqual(done["processed"], 2)
        self.assertEqual(done["tagged"], 2)
        self.assertEqual(done["skipped"], 2)
        self.assertEqual(done["errors"], 0)
        self.assertEqual(len(self.service.generate_calls), 2)
        self.assertEqual(self.api.db.get_prompt_by_id(a)["tags"], ["1girl", "smile"])
        self.assertEqual(self.api.db.get_prompt_by_id(b)["tags"], ["1girl", "smile"])
        self.assertEqual(self.api.db.get_prompt_by_id(c)["tags"], [])
        progress = [e for e in events if e["type"] == "progress"]
        self.assertTrue(all(0 <= e["progress"] <= 100 for e in progress))
        self.assertTrue(any("processed" in e for e in progress))

    async def test_rows_outside_output_dirs_are_skipped_and_counted(self):
        inside = self._save_prompt("inside")
        outside = self._save_prompt("outside")
        self._link_image(inside, self._make_image(self.output_dir, "in.png"))
        secret = self._make_image(self.secret_dir, "password.png")
        self._link_image(outside, secret)

        _, events = await self._start()

        done = events[-1]
        self.assertEqual(done["type"], "complete")
        self.assertEqual(done["processed"], 1)
        self.assertEqual(done["tagged"], 1)
        self.assertEqual(done["skipped"], 1)
        self.assertEqual(done["skipped_outside_roots"], 1)
        self.assertEqual(len(self.service.generate_calls), 1)
        self.assertEqual(
            os.path.realpath(self.service.generate_calls[0][0]),
            os.path.realpath(str(self.output_dir / "in.png")),
        )
        self.assertEqual(self.api.db.get_prompt_by_id(outside)["tags"], [])
        progress = [e for e in events if e["type"] == "progress" and "skipped" in e]
        self.assertTrue(progress)
        self.assertTrue(all("skipped_outside_roots" in e for e in progress))
        body = json.dumps(events)
        self.assertNotIn(str(self.secret_dir), body)

    async def test_second_start_while_running_is_409(self):
        self._seed()
        started = threading.Event()
        release = threading.Event()

        def slow_generate(image_path, **kwargs):
            started.set()
            release.wait(5)
            return ["slow"]

        self.service.generate_tags = slow_generate
        first = asyncio.ensure_future(self._start())
        try:
            await asyncio.get_event_loop().run_in_executor(None, started.wait, 5)
            self.assertTrue(started.is_set())

            resp = await self.client.request(
                "POST", "/prompt_manager/autotag/start?model_type=gguf"
            )

            self.assertEqual(resp.status, 409)
            self.assertEqual(
                await resp.json(),
                {"success": False, "error": "Auto-tagging is already running"},
            )
        finally:
            release.set()
            resp1, events = await first
        self.assertEqual(resp1.status, 200)
        self.assertEqual(events[-1]["type"], "complete")

    async def test_lock_is_released_after_a_run(self):
        self._seed()

        resp1, _ = await self._start()
        resp2, events = await self._start()

        self.assertEqual(resp1.status, 200)
        self.assertEqual(resp2.status, 200)
        self.assertEqual(events[-1]["type"], "complete")

    async def test_lock_is_released_when_the_run_fails(self):
        self._seed()
        self.service.load_error = RuntimeError("no model")

        _, events = await self._start()
        self.assertEqual(events[-1]["type"], "error")

        self.service.load_error = None
        resp, events = await self._start()
        self.assertEqual(resp.status, 200)
        self.assertEqual(events[-1]["type"], "complete")

    async def test_already_tagged_prompts_are_skipped_unless_asked(self):
        p = self._save_prompt("tagged", tags=["cat"])
        self._link_image(p, self._make_image(self.output_dir, "t.png"))

        _, events = await self._start("model_type=gguf&skip_tagged=true")
        self.assertEqual(events[-1]["skipped"], 1)
        self.assertEqual(self.service.generate_calls, [])

        _, events = await self._start("model_type=gguf&skip_tagged=false")
        self.assertEqual(events[-1]["tagged"], 1)
        self.assertEqual(
            self.api.db.get_prompt_by_id(p)["tags"], ["cat", "1girl", "smile"]
        )

    async def test_prompt_already_holding_the_tags_counts_as_skipped(self):
        p = self._save_prompt("same", tags=["1girl", "smile"])
        self._link_image(p, self._make_image(self.output_dir, "s.png"))

        _, events = await self._start("model_type=gguf&skip_tagged=false")

        self.assertEqual(events[-1]["skipped"], 1)
        self.assertEqual(events[-1]["tagged"], 0)

    async def test_generation_errors_are_counted_and_do_not_abort(self):
        self._seed()
        self.service.generate_error = RuntimeError("boom")

        _, events = await self._start()

        self.assertEqual(events[-1]["type"], "complete")
        # a1, b1 and b2 each fail: a failed prompt is retried on its next image
        self.assertEqual(events[-1]["errors"], 3)
        self.assertEqual(events[-1]["tagged"], 0)

    async def test_json_body_parameters_are_accepted(self):
        self._seed()

        _, events = await self._start(
            "", {"model_type": "wd14-vit", "general_threshold": 0.5, "prompt": "p"}
        )

        self.assertEqual(events[-1]["type"], "complete")
        self.assertEqual(self.service.load_calls, [("wd14-vit", True)])
        self.assertEqual(self.service.generate_calls[0][2], 0.5)
        self.assertEqual(self.service.custom_prompt, "p")

    async def test_keep_in_memory_false_unloads_after_batch(self):
        self._seed()

        _, events = await self._start("model_type=gguf&keep_in_memory=false")

        self.assertEqual(events[-1]["model_loaded"], False)
        self.assertEqual(self.service.unload_calls, 1)

    async def test_model_not_downloaded_is_an_error_event(self):
        self.service.downloaded = False

        _, events = await self._start()

        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(self.service.load_calls, [])

    async def test_load_failure_is_an_error_event_without_details(self):
        self.service.load_error = RuntimeError("/srv/models/secret.gguf is corrupt")

        _, events = await self._start()

        self.assertEqual(events[-1]["type"], "error")
        self.assertNotIn("/srv/models", events[-1]["message"])

    async def test_non_numeric_threshold_is_400(self):
        resp = await self.client.request(
            "POST", "/prompt_manager/autotag/start?general_threshold=abc"
        )
        self.assertEqual(resp.status, 400)

    async def test_invalid_json_body_is_400(self):
        resp = await self.client.request(
            "POST",
            "/prompt_manager/autotag/start",
            data="{not json",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status, 400)

    async def test_client_disconnect_stops_the_batch_and_unloads(self):
        self._seed()
        real_write = web.StreamResponse.write
        state = {"writes": 0}

        async def flaky_write(response, data):
            state["writes"] += 1
            if b"Processing " in data:
                raise ConnectionResetError("Cannot write to closing transport")
            return await real_write(response, data)

        with patch.object(web.StreamResponse, "write", flaky_write):
            try:
                resp = await self.client.request(
                    "POST", "/prompt_manager/autotag/start?keep_in_memory=false"
                )
                await resp.read()
            except Exception:
                pass

        self.assertEqual(len(self.service.generate_calls), 1)
        self.assertEqual(self.service.unload_calls, 1)


class TestAutotagModelsAndDownloadErrors(AutotagAPITestCase):

    async def test_models_error_is_500(self):
        def boom():
            raise RuntimeError("/srv/models unreadable")

        self.service.get_models_status = boom
        resp = await self.client.request("GET", "/prompt_manager/autotag/models")
        self.assertEqual(resp.status, 500)
        self.assertFalse((await resp.json())["success"])

    async def test_download_invalid_model_type_is_error_event(self):
        resp = await self.client.request(
            "POST", "/prompt_manager/autotag/download/nope"
        )
        events = self._sse_events(await resp.text())
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(self.service.download_calls, [])

    async def test_download_failure_is_error_event(self):
        self.service.download_result = False
        resp = await self.client.request(
            "POST", "/prompt_manager/autotag/download/gguf"
        )
        events = self._sse_events(await resp.text())
        self.assertEqual(events[-1], {"type": "error", "message": "Download failed"})

    async def test_download_exception_is_generic_error_event(self):
        def boom(model_type, progress_callback=None):
            raise OSError("/srv/models is full")

        self.service.download_model = boom
        resp = await self.client.request(
            "POST", "/prompt_manager/autotag/download/gguf"
        )
        events = self._sse_events(await resp.text())
        self.assertEqual(events[-1]["type"], "error")
        self.assertNotIn("/srv/models", events[-1]["message"])


class TestAutotagSingleEdgeCases(AutotagAPITestCase):

    async def test_missing_file_error_hides_the_server_path(self):
        import errno

        image = self._make_image(self.output_dir)
        self.service.generate_error = FileNotFoundError(
            errno.ENOENT, "Image not found", str(image)
        )

        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"path": str(image)}
        )

        self.assertEqual(resp.status, 500)
        data = await resp.json()
        self.assertFalse(data["success"])
        self.assertNotIn(str(self.output_dir), data["error"])
        self.assertIn("img.png", data["error"])

    async def test_generic_error_is_500(self):
        image = self._make_image(self.output_dir)
        self.service.generate_error = RuntimeError("model crashed")
        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"path": str(image)}
        )
        self.assertEqual(resp.status, 500)
        self.assertEqual((await resp.json())["error"], "model crashed")

    async def test_oserror_without_filename_uses_strerror(self):
        from py.api import _public_error

        self.assertEqual(_public_error(OSError(5, "I/O error")), "I/O error")
        self.assertEqual(_public_error(ValueError("plain")), "plain")
        self.assertIs(self.api._public_error, _public_error)

    async def test_path_with_nul_byte_is_forbidden(self):
        from py.api.autotag_routes import path_is_within

        self.assertFalse(path_is_within("bad\x00name.png", self.output_dir))
        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"path": str(self.output_dir / "a\x00b")}
        )
        self.assertEqual(resp.status, 403)

    async def test_boolean_image_id_is_400(self):
        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"image_id": True}
        )
        self.assertEqual(resp.status, 400)

    async def test_numeric_string_image_id_is_accepted(self):
        prompt_id = self._save_prompt()
        image_id = self._link_image(prompt_id, self._make_image(self.output_dir))
        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"image_id": str(image_id)}
        )
        self.assertEqual(resp.status, 200)

    async def test_non_object_body_is_400(self):
        resp = await self._post_json("/prompt_manager/autotag/single", [1, 2])
        self.assertEqual(resp.status, 400)

    async def test_invalid_json_is_400(self):
        resp = await self.client.request(
            "POST",
            "/prompt_manager/autotag/single",
            data="{nope",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(resp.status, 400)

    async def test_non_numeric_threshold_is_400(self):
        image = self._make_image(self.output_dir)
        resp = await self._post_json(
            "/prompt_manager/autotag/single",
            {"path": str(image), "general_threshold": "high"},
        )
        self.assertEqual(resp.status, 400)

    async def test_prompt_lookup_failure_still_returns_tags(self):
        image = self._make_image(self.output_dir)
        with patch.object(
            self.api.db, "get_prompt_id_for_image", side_effect=RuntimeError("db")
        ):
            resp = await self._post_json(
                "/prompt_manager/autotag/single", {"path": str(image)}
            )
        data = await resp.json()
        self.assertEqual(resp.status, 200)
        self.assertIsNone(data["prompt_id"])

    async def test_prompt_is_found_via_the_real_path(self):
        prompt_id = self._save_prompt()
        image = self._make_image(self.output_dir)
        self._link_image(prompt_id, image)
        dotted = os.path.join(str(self.output_dir), ".", "img.png")

        resp = await self._post_json("/prompt_manager/autotag/single", {"path": dotted})

        self.assertEqual((await resp.json())["prompt_id"], prompt_id)

    async def test_custom_prompt_and_model_switch(self):
        image = self._make_image(self.output_dir)
        self.service._loaded_type = "gguf"

        resp = await self._post_json(
            "/prompt_manager/autotag/single",
            {"path": str(image), "model_type": "wd14-vit", "prompt": "terse"},
        )

        self.assertEqual(resp.status, 200)
        self.assertEqual(self.service.load_calls, [("wd14-vit", True)])
        self.assertEqual(self.service.custom_prompt, "terse")

    async def test_image_id_without_stored_path_is_404(self):
        prompt_id = self._save_prompt()
        image_id = self._link_image(prompt_id, self._make_image(self.output_dir))
        with patch.object(
            self.api.db,
            "get_image_by_id",
            return_value={"id": image_id, "image_path": ""},
        ):
            resp = await self._post_json(
                "/prompt_manager/autotag/single", {"image_id": image_id}
            )
        self.assertEqual(resp.status, 404)


class TestApplyAutotag(AutotagAPITestCase):

    async def test_missing_prompt_id_is_400(self):
        resp = await self._post_json("/prompt_manager/autotag/apply", {"tags": ["a"]})
        self.assertEqual(resp.status, 400)

    async def test_no_tags_is_a_noop(self):
        resp = await self._post_json("/prompt_manager/autotag/apply", {"prompt_id": 1})
        data = await resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["message"], "No tags to apply")

    async def test_unknown_prompt_is_404(self):
        resp = await self._post_json(
            "/prompt_manager/autotag/apply", {"prompt_id": 999, "tags": ["a"]}
        )
        self.assertEqual(resp.status, 404)

    async def test_tags_are_merged_without_duplicates(self):
        prompt_id = self._save_prompt(tags=["cat"])

        resp = await self._post_json(
            "/prompt_manager/autotag/apply",
            {"prompt_id": prompt_id, "tags": ["cat", "dog"]},
        )

        data = await resp.json()
        self.assertEqual(data["added_tags"], ["dog"])
        self.assertEqual(data["total_tags"], 2)
        self.assertEqual(
            self.api.db.get_prompt_by_id(prompt_id)["tags"], ["cat", "dog"]
        )

    async def test_string_tags_from_db_are_split(self):
        prompt_id = self._save_prompt()
        with patch.object(
            self.api.db,
            "get_prompt_by_id",
            return_value={"id": prompt_id, "tags": "cat, dog"},
        ):
            resp = await self._post_json(
                "/prompt_manager/autotag/apply",
                {"prompt_id": prompt_id, "tags": ["dog", "owl"]},
            )
        data = await resp.json()
        self.assertEqual(data["added_tags"], ["owl"])
        self.assertEqual(data["total_tags"], 3)

    async def test_db_error_is_500(self):
        with patch.object(
            self.api.db, "get_prompt_by_id", side_effect=RuntimeError("db")
        ):
            resp = await self._post_json(
                "/prompt_manager/autotag/apply", {"prompt_id": 1, "tags": ["a"]}
            )
        self.assertEqual(resp.status, 500)


class TestAutotagErrorBodiesHideServerPaths(AutotagAPITestCase):
    """Every autotag handler reports exceptions through the shared public helper."""

    def _locked(self):
        return PermissionError(
            13, "Permission denied", str(self.secret_dir / "locked.db")
        )

    async def _assert_hidden(self, resp):
        self.assertEqual(resp.status, 500)
        body = await resp.text()
        self.assertFalse(json.loads(body)["success"])
        self.assertNotIn(str(self.secret_dir), body)
        self.assertIn("locked.db", body)

    async def test_models_error(self):
        def boom():
            raise self._locked()

        self.service.get_models_status = boom
        resp = await self.client.request("GET", "/prompt_manager/autotag/models")
        await self._assert_hidden(resp)

    async def test_apply_error(self):
        with patch.object(self.api.db, "get_prompt_by_id", side_effect=self._locked()):
            resp = await self._post_json(
                "/prompt_manager/autotag/apply", {"prompt_id": 1, "tags": ["a"]}
            )
        await self._assert_hidden(resp)

    async def test_unload_error(self):
        def boom():
            raise self._locked()

        self.service.is_model_loaded = boom
        resp = await self.client.request("POST", "/prompt_manager/autotag/unload")
        await self._assert_hidden(resp)


class TestUnloadAutotag(AutotagAPITestCase):

    async def test_nothing_loaded(self):
        resp = await self.client.request("POST", "/prompt_manager/autotag/unload")
        data = await resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["message"], "No model was loaded")

    async def test_unloads_loaded_model(self):
        self.service._loaded_type = "gguf"
        resp = await self.client.request("POST", "/prompt_manager/autotag/unload")
        data = await resp.json()
        self.assertEqual(data["message"], "GGUF model unloaded successfully")
        self.assertFalse(data["model_loaded"])
        self.assertEqual(self.service.unload_calls, 1)

    async def test_error_is_500(self):
        def boom():
            raise RuntimeError("x")

        self.service.is_model_loaded = boom
        resp = await self.client.request("POST", "/prompt_manager/autotag/unload")
        self.assertEqual(resp.status, 500)


class TestLegacyRoutesShareTheModelGate(AutotagAPITestCase):
    """While another model operation holds the gate, every legacy model route
    answers ``409 model-busy`` and touches no model (the one allowed legacy
    change)."""

    BUSY = {"success": False, "code": "model-busy"}

    def setUp(self):
        super().setUp()
        self.assertIsNotNone(model_gate, "py.model_gate is not implemented")

    async def _busy(self, resp):
        self.assertEqual(resp.status, 409)
        data = await resp.json()
        self.assertEqual({k: data[k] for k in self.BUSY}, self.BUSY)
        self.assertIn("busy", data["error"].lower())
        return data

    async def test_legacy_single_download_start_return_model_busy_while_gate_held(
        self,
    ):
        image = self._make_image(self.output_dir)
        self.service._loaded_type = "wd14-vit"
        self.assertTrue(model_gate.try_acquire("test-holder"))
        try:
            await self._busy(
                await self._post_json(
                    "/prompt_manager/autotag/single",
                    {"path": str(image), "model_type": "gguf"},
                )
            )
            await self._busy(
                await self.client.request(
                    "POST", "/prompt_manager/autotag/download/gguf"
                )
            )
            await self._busy(
                await self.client.request(
                    "POST", "/prompt_manager/autotag/start?model_type=gguf"
                )
            )
            await self._busy(
                await self.client.request("POST", "/prompt_manager/autotag/unload")
            )
            self.assertEqual(model_gate.holder(), "test-holder")
        finally:
            model_gate.release("test-holder")

        self.assertEqual(self.service.load_calls, [])
        self.assertEqual(self.service.generate_calls, [])
        self.assertEqual(self.service.download_calls, [])
        self.assertEqual(self.service.unload_calls, 0)
        self.assertEqual(self.service.get_loaded_model_type(), "wd14-vit")

        # A refused start released the batch lock: the next start runs.
        resp = await self.client.request(
            "POST", "/prompt_manager/autotag/start?model_type=gguf"
        )
        self.assertEqual(resp.status, 200)
        self.assertEqual(self._sse_events(await resp.text())[-1]["type"], "complete")

    async def test_legacy_routes_hold_the_gate_only_while_they_run(self):
        image = self._make_image(self.output_dir)
        seen = []

        def generate(image_path, **kwargs):
            seen.append(model_gate.holder())
            return ["x"]

        def download(model_type, progress_callback=None):
            seen.append(model_gate.holder())
            return True

        def load(model_type, use_gpu=True):
            seen.append(model_gate.holder())
            self.service._loaded_type = model_type
            return True

        self.service.generate_tags = generate
        self.service.download_model = download
        self.service.load_model = load

        resp = await self._post_json(
            "/prompt_manager/autotag/single", {"path": str(image)}
        )
        self.assertEqual(resp.status, 200)
        self.assertIsNone(model_gate.holder())
        resp = await self.client.request(
            "POST", "/prompt_manager/autotag/download/gguf"
        )
        self.assertEqual(self._sse_events(await resp.text())[-1]["type"], "complete")
        self.assertIsNone(model_gate.holder())
        resp = await self.client.request("POST", "/prompt_manager/autotag/unload")
        self.assertEqual(resp.status, 200)
        self.assertIsNone(model_gate.holder())

        self.assertEqual(len(seen), 3)
        # The download runs as the model_download job, which owns the gate.
        kinds = [owner.split(":", 1)[0] for owner in seen]
        self.assertEqual(kinds, ["legacy-single", "legacy-single", "model-download"])

    async def test_validation_errors_win_over_the_gate(self):
        self.assertTrue(model_gate.try_acquire("test-holder"))
        try:
            resp = await self._post_json("/prompt_manager/autotag/single", {})
            self.assertEqual(resp.status, 400)
            resp = await self.client.request(
                "POST", "/prompt_manager/autotag/start?general_threshold=x"
            )
            self.assertEqual(resp.status, 400)
        finally:
            model_gate.release("test-holder")

    async def test_batch_holds_the_gate_until_it_completes(self):
        prompt = self._save_prompt("p")
        self._link_image(prompt, self._make_image(self.output_dir, "a.png"))
        started = threading.Event()
        proceed = threading.Event()

        def slow_generate(image_path, **kwargs):
            started.set()
            proceed.wait(30)
            return ["slow"]

        self.service.generate_tags = slow_generate
        run = asyncio.ensure_future(
            self.client.request("POST", "/prompt_manager/autotag/start")
        )
        try:
            await asyncio.get_running_loop().run_in_executor(None, started.wait, 30)
            self.assertTrue(model_gate.holder().startswith("legacy-batch:"))
            await self._busy(
                await self.client.request("POST", "/prompt_manager/autotag/unload")
            )
        finally:
            proceed.set()
            resp = await run
            events = self._sse_events(await resp.text())
        self.assertEqual(events[-1]["type"], "complete")
        self.assertIsNone(model_gate.holder())

    async def test_models_response_carries_the_suggestion_limit(self):
        from py.config import AutotagConfig

        resp = await self.client.get("/prompt_manager/autotag/models")
        data = await resp.json()
        self.assertEqual(data["suggestion_limit"], 10)
        AutotagConfig.SUGGESTION_LIMIT = 25
        try:
            resp = await self.client.get("/prompt_manager/autotag/models")
            self.assertEqual((await resp.json())["suggestion_limit"], 25)
        finally:
            AutotagConfig.SUGGESTION_LIMIT = 10


class TestScanOutputDir(AutotagAPITestCase):

    async def test_lists_images_with_thumbnails(self):
        sub = self.output_dir / "sub"
        sub.mkdir()
        self._make_image(self.output_dir, "a.png")
        self._make_image(sub, "b.JPG")
        thumbs = self.output_dir / "thumbnails" / "sub"
        thumbs.mkdir(parents=True)
        (thumbs / "b_thumb.JPG").write_bytes(b"t")
        self._make_image(self.output_dir / "thumbnails", "ignored.png")
        self.api._find_comfyui_output_dir = lambda: str(self.output_dir)

        resp = await self.client.request("GET", "/prompt_manager/scan_output_dir")

        body = await resp.text()
        data = json.loads(body)
        self.assertTrue(data["success"])
        self.assertEqual([i["filename"] for i in data["images"]], ["a.png", "b.JPG"])
        self.assertEqual(data["count"], 2)
        self.assertIs(data["truncated"], False)
        self.assertIsNone(data["images"][0]["thumbnail_url"])
        # An older release's preview keeps its spelling; a case-insensitive
        # disk (Windows, macOS) finds it under the ".jpg" lookup first (#317).
        # Either way the URL names that file and is served.
        thumb_url = data["images"][1]["thumbnail_url"]
        self.assertRegex(
            thumb_url,
            r"^/prompt_manager/images/serve/thumbnails/sub/b_thumb\.(JPG|jpg)\?v=\d+$",
        )
        served = await self.client.request("GET", thumb_url)
        self.assertEqual(served.status, 200)
        self.assertEqual(await served.read(), b"t")
        self.assertEqual(
            data["images"][1]["url"], "/prompt_manager/images/serve/sub/b.JPG"
        )
        self.assertEqual(data["images"][1]["relative_path"], "sub/b.JPG")
        self.assertEqual(
            data["images"][1]["path"], self.api._public_path(sub / "b.JPG")
        )
        self.assertFalse(os.path.isabs(data["images"][1]["path"]))
        self.assertNotIn(str(self.tmp_root), body)

    async def test_symlinked_files_are_skipped(self):
        secret = self._make_image(self.secret_dir, "password.png")
        self._make_image(self.output_dir, "real.png")
        link = self.output_dir / "link.png"
        try:
            link.symlink_to(secret)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported on this platform")
        self.api._find_comfyui_output_dir = lambda: str(self.output_dir)

        data = await (
            await self.client.request("GET", "/prompt_manager/scan_output_dir")
        ).json()

        self.assertEqual([i["filename"] for i in data["images"]], ["real.png"])

    async def test_file_cap_marks_the_listing_truncated(self):
        for name in ("a.png", "b.png", "c.png"):
            self._make_image(self.output_dir, name)
        self.api._find_comfyui_output_dir = lambda: str(self.output_dir)

        with patch("py.api.autotag_routes._SCAN_MAX_FILES", 2):
            data = await (
                await self.client.request("GET", "/prompt_manager/scan_output_dir")
            ).json()

        self.assertEqual(data["count"], 2)
        self.assertIs(data["truncated"], True)

    async def test_depth_cap_stops_the_walk(self):
        deep = self.output_dir / "d1" / "d2" / "d3"
        deep.mkdir(parents=True)
        self._make_image(self.output_dir / "d1", "shallow.png")
        self._make_image(deep, "deep.png")
        self.api._find_comfyui_output_dir = lambda: str(self.output_dir)

        with patch("py.api.autotag_routes._SCAN_MAX_DEPTH", 1):
            data = await (
                await self.client.request("GET", "/prompt_manager/scan_output_dir")
            ).json()

        self.assertEqual([i["filename"] for i in data["images"]], ["shallow.png"])

    async def test_missing_output_dir_is_404(self):
        self.api._find_comfyui_output_dir = lambda: None
        resp = await self.client.request("GET", "/prompt_manager/scan_output_dir")
        self.assertEqual(resp.status, 404)

    async def test_error_is_500_without_the_server_path(self):
        def boom():
            raise PermissionError(
                13, "Permission denied", str(self.secret_dir / "locked.png")
            )

        self.api._find_comfyui_output_dir = boom
        resp = await self.client.request("GET", "/prompt_manager/scan_output_dir")
        self.assertEqual(resp.status, 500)
        body = await resp.text()
        self.assertFalse(json.loads(body)["success"])
        self.assertNotIn(str(self.secret_dir), body)
        self.assertIn("locked.png", body)


if __name__ == "__main__":
    unittest.main()


class TestGalleryReviewContract(AutotagAPITestCase):
    async def test_root_relative_gallery_targets_generate_and_apply_to_their_prompt(
        self,
    ):
        second = self.tmp_root / "second-output"
        second.mkdir()
        roots = [self.output_dir, second]
        self.api._get_all_output_dirs = lambda: roots
        for index, root in enumerate(roots):
            pid = self._save_prompt(f"root {index}")
            self._link_image(pid, self._make_image(root, "same.png"))
            response = await self._post_json(
                "/prompt_manager/autotag/single",
                {"path": "same.png", "root_index": index, "model_type": "gguf"},
            )
            self.assertEqual(response.status, 200)
            data = await response.json()
            self.assertEqual(data["prompt_id"], pid)
            self.assertEqual(
                self.service.generate_calls[-1][0], str((root / "same.png").resolve())
            )
            response = await self._post_json(
                "/prompt_manager/autotag/apply",
                {"prompt_id": data["prompt_id"], "tags": data["tags"]},
            )
            self.assertEqual(response.status, 200)
            self.assertEqual(
                set(self.api.db.get_prompt_by_id(pid)["tags"]), set(data["tags"])
            )

    async def test_gallery_unlinked_image_returns_no_prompt_id(self):
        self._make_image(self.output_dir)
        response = await self._post_json(
            "/prompt_manager/autotag/single", {"path": "img.png", "root_index": 0}
        )
        self.assertEqual(response.status, 200)
        self.assertIsNone((await response.json())["prompt_id"])

    async def test_gallery_root_selection_rejects_invalid_roots_and_escapes(self):
        self._make_image(self.output_dir)
        for root, path, expected in [
            (9, "img.png", 403),
            (-1, "img.png", 403),
            (True, "img.png", 400),
            ([], "img.png", 400),
            (0, "../secret/img.png", 403),
            (0, str(self.secret_dir / "img.png"), 403),
        ]:
            with self.subTest(root=root, path=path):
                response = await self._post_json(
                    "/prompt_manager/autotag/single", {"path": path, "root_index": root}
                )
                self.assertEqual(response.status, expected)

    async def test_gallery_root_selection_rejects_symlink_into_another_root(self):
        second = self.tmp_root / "second-output"
        second.mkdir()
        target = self._make_image(second)
        try:
            (self.output_dir / "link.png").symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        self.api._get_all_output_dirs = lambda: [self.output_dir, second]
        response = await self._post_json(
            "/prompt_manager/autotag/single", {"path": "link.png", "root_index": 0}
        )
        self.assertEqual(response.status, 403)


def _strings(value):
    """Every string inside a JSON value (keys included)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _absolute(text):
    import ntpath
    import posixpath
    import re

    return (
        posixpath.isabs(text)
        or ntpath.isabs(text)
        or re.match(r"^[A-Za-z]:[\\/]", text) is not None
    )


class TestModelStatus(AutotagAPITestCase):
    """GET /autotag/models: labels, sizes and the loaded flag, never a path."""

    async def _models(self):
        resp = await self.client.get("/prompt_manager/autotag/models")
        self.assertEqual(resp.status, 200)
        return await resp.json()

    async def test_models_status_has_no_absolute_paths(self):
        self.service.status_paths = {
            "wd14-vit": "/srv/models/WD14-ViT/model.onnx",
            "gguf": "C:\\models\\GGUF\\joycaption.gguf",
            "hf": "D:/hf-cache/models--fancyfeast",
        }
        data = await self._models()
        for model in data["models"].values():
            self.assertNotIn("model_path", model)
        leaked = [text for text in _strings(data) if _absolute(text)]
        self.assertEqual(leaked, [])

    async def test_models_status_adds_label_size_label_and_loaded(self):
        self.service._loaded_type = "wd14-vit"
        data = await self._models()
        models = data["models"]
        self.assertEqual(list(models), list(MODEL_TYPES))
        self.assertEqual(
            {name: (m["label"], m["size_label"]) for name, m in models.items()},
            {
                "gguf": ("GGUF JoyCaption", "~4.5 GB"),
                "hf": ("HuggingFace JoyCaption", "~16 GB"),
                "wd14-swinv2": ("WD14 SwinV2", "~400 MB"),
                "wd14-vit": ("WD14 ViT", "~400 MB"),
            },
        )
        self.assertEqual(
            {name: m["loaded"] for name, m in models.items()},
            {"gguf": False, "hf": False, "wd14-swinv2": False, "wd14-vit": True},
        )
        # Kept fields legacy pages and the workspace read.
        for model in models.values():
            self.assertTrue({"name", "description", "size", "downloaded"} <= set(model))
        self.assertTrue(data["model_loaded"])
        self.assertEqual(data["loaded_model_type"], "wd14-vit")

    async def test_opening_models_never_loads_or_downloads(self):
        await self._models()
        self.assertEqual(self.service.status_calls, 1)
        self.assertEqual(self.service.load_calls, [])
        self.assertEqual(self.service.download_calls, [])
        self.assertEqual(self.service.unload_calls, 0)
        self.assertEqual(self.service.generate_calls, [])

    async def test_models_status_reads_off_the_event_loop(self):
        await self._models()
        self.assertEqual(len(self.service.status_threads), 1)
        self.assertNotEqual(self.service.status_threads[0], threading.get_ident())


class TestModelBusyNamesTheOperation(AutotagAPITestCase):
    """A 409 model-busy names who holds the gate (``operation``)."""

    LABELS = {
        "autotag-job": "Tag output images",
        "legacy-batch": "Tag output images (legacy page)",
        "legacy-single": "Tagging one image (legacy page)",
        "legacy-unload": "Unloading the tagging model",
        "image-tag-job": "Auto-tag selected images",
        "image-annotations": "Generate suggestions",
    }

    async def test_model_busy_names_the_operation(self):
        self.service._loaded_type = "wd14-vit"
        for kind, label in self.LABELS.items():
            owner = model_gate.new_owner(kind)
            self.assertTrue(model_gate.try_acquire(owner))
            try:
                resp = await self.client.post("/prompt_manager/autotag/unload")
                data = await resp.json()
            finally:
                model_gate.release(owner)
            self.assertEqual(resp.status, 409, kind)
            self.assertEqual(data["code"], "model-busy", kind)
            self.assertEqual(data["operation"], label, kind)
            self.assertIn("busy", data["error"].lower())
        self.assertEqual(self.service.unload_calls, 0)

    async def test_unknown_or_freed_holder_is_another_operation(self):
        from py.api.autotag_routes import model_busy_response

        self.assertIsNone(model_gate.holder())
        freed = json.loads(model_busy_response().body)
        self.assertEqual(freed["operation"], "Another tagging operation")
        self.assertTrue(model_gate.try_acquire("test-holder"))
        try:
            unknown = json.loads(model_busy_response().body)
        finally:
            model_gate.release("test-holder")
        self.assertEqual(unknown["operation"], "Another tagging operation")

    async def test_unload_during_generate_is_409_model_busy(self):
        self.service._loaded_type = "wd14-vit"
        owner = model_gate.new_owner("image-annotations")
        self.assertTrue(model_gate.try_acquire(owner))
        try:
            resp = await self.client.post("/prompt_manager/autotag/unload")
            data = await resp.json()
        finally:
            model_gate.release(owner)
        self.assertEqual(resp.status, 409)
        self.assertEqual(data["operation"], "Generate suggestions")
        self.assertEqual(self.service.unload_calls, 0)
        self.assertEqual(self.service.get_loaded_model_type(), "wd14-vit")


class DownloadJobTestCase(AutotagAPITestCase):
    """Helpers for the ``model_download`` job: a download that waits for a
    signal, so a test sees the job while it runs."""

    TITLE = "Tagging model download · WD14 ViT"

    def setUp(self):
        super().setUp()
        self.started = threading.Event()
        self.proceed = threading.Event()

    def _hold_download(self, result=True, error=None):
        def download(model_type, progress_callback=None):
            self.service.download_calls.append(model_type)
            self.started.set()
            self.proceed.wait(30)
            if error is not None:
                raise error
            return result

        self.service.download_model = download

    async def _started(self):
        loop = asyncio.get_running_loop()
        self.assertTrue(await loop.run_in_executor(None, self.started.wait, 30))

    async def _start(self, model_type="wd14-vit"):
        return await self._post_json(
            "/prompt_manager/jobs/model-download", {"model_type": model_type}
        )

    async def _ended(self, job_id):
        job = self.api._jobs.get(job_id)
        self.proceed.set()
        await asyncio.wait_for(asyncio.shield(job.task), 30)
        return job


class TestModelDownloadJob(DownloadJobTestCase):

    async def test_download_job_is_indeterminate_and_not_cancellable(self):
        self._hold_download()
        resp = await self._start()
        self.assertEqual(resp.status, 202)
        summary = (await resp.json())["job"]
        try:
            await self._started()
            self.assertEqual(summary["kind"], "model_download")
            self.assertEqual(summary["title"], self.TITLE)
            self.assertFalse(summary["cancellable"])
            self.assertEqual(summary["admission"], "read")
            self.assertEqual(
                summary["scope"],
                {
                    "model_type": "wd14-vit",
                    "label": "WD14 ViT",
                    "size_label": "~400 MB",
                },
            )
            detail = (
                await (
                    await self.client.get(f"/prompt_manager/jobs/{summary['id']}")
                ).json()
            )["job"]
            self.assertEqual((detail["progress"], detail["total"]), (0, 0))
            self.assertEqual(detail["status_line"], "Downloading WD14 ViT (~400 MB)")
            cancel = await self.client.post(
                f"/prompt_manager/jobs/{summary['id']}/cancel"
            )
            self.assertFalse((await cancel.json())["cancellation_requested"])
        finally:
            job = await self._ended(summary["id"])
        self.assertEqual(job.state, "finished")
        self.assertEqual(job.status_line, "Downloaded WD14 ViT")
        self.assertEqual(self.service.download_calls, ["wd14-vit"])
        self.assertEqual(len(job.log), 2)

    async def test_start_body_is_checked_before_anything_runs(self):
        for body in (
            {"model_type": "nope"},
            {"model_type": "wd14-vit", "extra": 1},
            {},
            ["wd14-vit"],
            {"model_type": 3},
        ):
            resp = await self._post_json("/prompt_manager/jobs/model-download", body)
            self.assertEqual(resp.status, 400, body)
        resp = await self._post_json(
            "/prompt_manager/jobs/model-download",
            {"model_type": "wd14-vit", "pad": "x" * 2048},
        )
        self.assertIn(resp.status, (400, 413))
        self.assertEqual(self.service.download_calls, [])
        self.assertIsNone(model_gate.holder())
        self.assertEqual(self.api._jobs.list(), [])

    async def test_second_download_is_409_busy(self):
        self._hold_download()
        first = (await (await self._start()).json())["job"]
        try:
            await self._started()
            resp = await self._start("gguf")
            data = await resp.json()
            self.assertEqual(resp.status, 409)
            self.assertEqual(data["code"], "busy")
            self.assertEqual(data["running"]["title"], self.TITLE)
        finally:
            await self._ended(first["id"])
        self.assertEqual(self.service.download_calls, ["wd14-vit"])

    async def test_download_holds_gate_until_runner_exits(self):
        self._hold_download()
        self.service._loaded_type = "wd14-swinv2"
        first = (await (await self._start()).json())["job"]
        try:
            await self._started()
            self.assertTrue(model_gate.holder().startswith("model-download:"))
            resp = await self.client.post("/prompt_manager/autotag/unload")
            data = await resp.json()
            self.assertEqual(resp.status, 409)
            self.assertEqual(data["operation"], self.TITLE)
        finally:
            await self._ended(first["id"])
        self.assertIsNone(model_gate.holder())
        self.assertEqual(self.service.unload_calls, 0)

    async def test_download_releases_gate_when_start_is_refused(self):
        class Running:
            id = "other"
            title = "Database restore"

        with patch.object(self.api._jobs, "start", side_effect=JobBusy(Running())):
            resp = await self._start()
        self.assertEqual(resp.status, 409)
        self.assertEqual((await resp.json())["code"], "busy")
        self.assertIsNone(model_gate.holder())
        self.assertEqual(self.service.download_calls, [])

    async def test_gate_held_elsewhere_is_409_model_busy(self):
        owner = model_gate.new_owner("image-annotations")
        self.assertTrue(model_gate.try_acquire(owner))
        try:
            resp = await self._start()
            data = await resp.json()
        finally:
            model_gate.release(owner)
        self.assertEqual(resp.status, 409)
        self.assertEqual(data["operation"], "Generate suggestions")
        self.assertEqual(self.api._jobs.list(), [])

    async def test_failed_download_fails_the_job_without_paths(self):
        for outcome in ({"result": False}, {"error": OSError("/srv/models is full")}):
            self.started.clear()
            self.proceed.clear()
            self._hold_download(**outcome)
            summary = (await (await self._start()).json())["job"]
            await self._started()
            job = await self._ended(summary["id"])
            self.assertEqual(job.state, "failed", outcome)
            self.assertEqual(
                job.status_line, "The download failed. Check server logs for details."
            )
            report = await self.client.get(
                f"/prompt_manager/jobs/{summary['id']}/report"
            )
            text = await report.text()
            self.assertNotIn("/srv/models", text)
            leaked = [s for s in _strings(json.loads(text)) if _absolute(s)]
            self.assertEqual(leaked, [])
            self.assertIsNone(model_gate.holder())


class TestLegacyDownloadAttaches(DownloadJobTestCase):
    """POST /autotag/download/{type}: the legacy stream rides on the job."""

    GOLDEN_OK = [
        {"type": "progress", "progress": 0, "status": "Starting download..."},
        {"type": "complete", "progress": 100, "status": "Download complete"},
    ]
    GOLDEN_FAILED = [
        {"type": "progress", "progress": 0, "status": "Starting download..."},
        {"type": "error", "message": "Download failed"},
    ]

    async def test_legacy_download_events_unchanged(self):
        resp = await self.client.post("/prompt_manager/autotag/download/wd14-vit")
        self.assertEqual(resp.status, 200)
        self.assertEqual(self._sse_events(await resp.text()), self.GOLDEN_OK)
        jobs = self.api._jobs.list()
        self.assertEqual(
            [(j.kind, j.title, j.state) for j in jobs],
            [("model_download", self.TITLE, "finished")],
        )

        self.service.download_result = False
        resp = await self.client.post("/prompt_manager/autotag/download/wd14-vit")
        self.assertEqual(self._sse_events(await resp.text()), self.GOLDEN_FAILED)

    async def test_legacy_download_attaches_to_running_job(self):
        self._hold_download()
        summary = (await (await self._start()).json())["job"]
        await self._started()
        legacy = asyncio.ensure_future(
            self.client.post("/prompt_manager/autotag/download/wd14-vit")
        )
        try:
            # Wait until the legacy stream is subscribed to the running job.
            job = self.api._jobs.get(summary["id"])
            for _ in range(300):
                if job.subscriber_count:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(job.subscriber_count, 1)
        finally:
            await self._ended(summary["id"])
        resp = await legacy
        self.assertEqual(self._sse_events(await resp.text()), self.GOLDEN_OK)
        self.assertEqual(self.service.download_calls, ["wd14-vit"])
        self.assertEqual(len(self.api._jobs.list()), 1)

    async def test_legacy_download_of_other_type_is_409_model_busy(self):
        self._hold_download()
        summary = (await (await self._start()).json())["job"]
        try:
            await self._started()
            resp = await self.client.post("/prompt_manager/autotag/download/gguf")
            data = await resp.json()
        finally:
            await self._ended(summary["id"])
        self.assertEqual(resp.status, 409)
        self.assertEqual(data["code"], "model-busy")
        self.assertEqual(data["operation"], self.TITLE)
        self.assertEqual(self.service.download_calls, ["wd14-vit"])

    async def test_legacy_disconnect_keeps_the_job_running(self):
        self._hold_download()
        resp = await self.client.post("/prompt_manager/autotag/download/wd14-vit")
        first = await resp.content.readline()
        self.assertTrue(first.startswith(b"data: "))
        await self._started()
        resp.close()  # the page closed the modal
        (job,) = self.api._jobs.list()
        for _ in range(300):
            if not job.subscriber_count:
                break
            await asyncio.sleep(0.01)
        self.assertTrue(job.running)
        job = await self._ended(job.id)
        self.assertEqual(job.state, "finished")
        self.assertIsNone(model_gate.holder())
