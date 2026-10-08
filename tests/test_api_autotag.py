"""Route tests for the autotag endpoints.

The ML engine is replaced by a fake service so no model is ever loaded and
no network request is made.
"""

import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if "folder_paths" not in sys.modules:
    _stub = types.ModuleType("folder_paths")
    _stub.models_dir = tempfile.gettempdir()
    _stub.base_path = tempfile.gettempdir()
    _stub.get_output_directory = lambda: tempfile.gettempdir()
    sys.modules["folder_paths"] = _stub

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from database.operations import PromptDatabase
from py.api import PromptManagerAPI
from utils.hashing import generate_prompt_hash


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
        self.models_config = {"gguf": {}, "wd14-vit": {}}
        self._loaded_type = None
        self.generate_error = None
        self.download_result = True
        self.load_error = None

    def get_models_status(self):
        return {
            name: {"downloaded": self.downloaded, "model_path": None}
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
    """Stands up the PromptManager routes with a temp DB, temp output dir and a fake engine."""

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
        self.api = PromptManagerAPI()
        self.api.db = PromptDatabase(self._temp_db.name)
        self.api._get_all_output_dirs = lambda: [self.output_dir]
        self.api.add_routes(routes)
        app.router.add_routes(routes)
        return app

    async def tearDownAsync(self):
        self._service_patch.stop()
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
        from py.api.autotag_routes import _public_error

        self.assertEqual(_public_error(OSError(5, "I/O error")), "I/O error")
        self.assertEqual(_public_error(ValueError("plain")), "plain")

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

        data = await resp.json()
        self.assertTrue(data["success"])
        self.assertEqual([i["filename"] for i in data["images"]], ["a.png", "b.JPG"])
        self.assertEqual(data["count"], 2)
        self.assertIsNone(data["images"][0]["thumbnail_url"])
        self.assertEqual(
            data["images"][1]["thumbnail_url"],
            "/prompt_manager/images/serve/thumbnails/sub/b_thumb.JPG",
        )
        self.assertEqual(
            data["images"][1]["url"], "/prompt_manager/images/serve/sub/b.JPG"
        )

    async def test_missing_output_dir_is_404(self):
        self.api._find_comfyui_output_dir = lambda: None
        resp = await self.client.request("GET", "/prompt_manager/scan_output_dir")
        self.assertEqual(resp.status, 404)

    async def test_error_is_500(self):
        def boom():
            raise RuntimeError("x")

        self.api._find_comfyui_output_dir = boom
        resp = await self.client.request("GET", "/prompt_manager/scan_output_dir")
        self.assertEqual(resp.status, 500)


if __name__ == "__main__":
    unittest.main()
